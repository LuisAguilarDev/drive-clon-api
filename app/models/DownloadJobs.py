import uuid

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    Enum as SQLEnum,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID

from app.db.database import Base
from app.models.download_job_status import DownloadJobStatus


class DownloadJobs(Base):
    """Job asíncrono de empaquetado de una carpeta en ZIP.

    La tabla hace de COLA además de registro durable: el worker reclama el
    siguiente `queued` con `FOR UPDATE SKIP LOCKED` (varios workers no se pisan)
    y la fila guarda el resultado (`object_key` del ZIP temporal, tamaño, error).

    Invariante multi-tenant: `org_id` aísla por organización; un job de otra org
    se resuelve como inexistente (404) sin filtrar existencia.
    """

    __tablename__ = "download_jobs"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    # Aislamiento de tenant: toda consulta de jobs se filtra por `org_id`.
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    # Carpeta de origen que se empaqueta.
    folder_id = Column(Integer, ForeignKey("folders.id"), nullable=False)
    # Nombre del ZIP de descarga (p. ej. "Fotos.zip"); se fija al encolar para no
    # depender de que la carpeta siga existiendo al consultar el job.
    name = Column(String, nullable=False)
    status = Column(
        SQLEnum(
            DownloadJobStatus,
            name="download_job_status",
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
        server_default=DownloadJobStatus.QUEUED.value,
        default=DownloadJobStatus.QUEUED,
    )
    # Clave del ZIP temporal en el bucket; NULL hasta que el job está READY.
    object_key = Column(String, nullable=True)
    size_bytes = Column(BigInteger, nullable=True)
    # Motivo del fallo cuando status=FAILED.
    error = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    completed_at = Column(DateTime(timezone=True), nullable=True)
    # A partir de cuándo se considera caducado el ZIP (alineado con el ciclo de
    # vida del bucket que borra el objeto temporal).
    expires_at = Column(DateTime(timezone=True), nullable=True)
    # Cuándo lo reclamó un worker: si lleva demasiado en 'processing' (worker
    # caído), el reaper lo devuelve a la cola usando este sello.
    locked_at = Column(DateTime(timezone=True), nullable=True)
