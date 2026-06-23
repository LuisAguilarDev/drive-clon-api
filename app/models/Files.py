from sqlalchemy import BigInteger, Column, DateTime, ForeignKey, Integer, String, func

from app.db.database import Base


class Files(Base):
    """Fichero almacenado en MinIO y espejado en Postgres.

    El binario vive en MinIO bajo `object_key`; esta fila guarda los metadatos.
    Invariante multi-tenant: `org_id` aísla por organización y `folder_id` debe
    apuntar a una carpeta de la **misma** organización.
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
    # Soft delete: una fila está "viva" cuando deleted_at IS NULL.
    deleted_at = Column(DateTime(timezone=True), nullable=True, default=None)
