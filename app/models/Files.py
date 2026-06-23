from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    Enum as SQLEnum,
    ForeignKey,
    Integer,
    String,
    func,
)

from app.db.database import Base
from app.models.resource_status import ResourceStatus


class Files(Base):
    """Fichero almacenado en MinIO y espejado en Postgres.

    El binario vive en MinIO bajo `object_key`; esta fila guarda los metadatos.
    Invariante multi-tenant: `org_id` aísla por organización y `folder_id` debe
    apuntar a una carpeta de la **misma** organización.

    Ciclo de vida: ver `ResourceStatus`. La fila NUNCA se borra (se conserva para
    analítica); el borrado permanente sólo elimina el binario de MinIO y marca
    `status = DELETED`.
    """

    __tablename__ = "files"

    id = Column(Integer, primary_key=True, index=True)
    # Nombre visible, con extensión (p. ej. "budget.xlsx").
    name = Column(String, nullable=False)
    # Clave del objeto en MinIO: "{org_id}/{folder_id}/{uuid}-{name}".
    object_key = Column(String, nullable=False)
    content_type = Column(String, nullable=True)
    size_bytes = Column(BigInteger, nullable=True)
    folder_id = Column(Integer, ForeignKey("folders.id"), nullable=False, index=True)
    # Aislamiento de tenant: toda consulta de ficheros se filtra por `org_id`.
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    # Estado del ciclo de vida: ÚNICO discriminador de las búsquedas.
    status = Column(
        SQLEnum(
            ResourceStatus,
            name="resource_status",
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
        server_default=ResourceStatus.ACTIVE.value,
        default=ResourceStatus.ACTIVE,
        index=True,
    )
    # Metadata (analítica): cuándo se movió a la papelera y cuándo se purgó de
    # MinIO. No se usan para filtrar visibilidad, sólo `status`.
    trashed_at = Column(DateTime(timezone=True), nullable=True, default=None)
    deleted_at = Column(DateTime(timezone=True), nullable=True, default=None)
