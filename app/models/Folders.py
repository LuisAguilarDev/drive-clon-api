from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, func

from app.db.database import Base


class Folders(Base):
    """Carpeta del sistema de ficheros. La carpeta raíz es la que tiene
    `parent_id IS NULL` (no hay flag `is_root`: el padre nulo es el marcador).

    Invariante multi-tenant: `org_id` aísla por organización y `parent_id` debe
    apuntar a otra carpeta de la **misma** organización.
    """

    __tablename__ = "folders"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    # Aislamiento de tenant: toda consulta de carpetas se filtra por `org_id`.
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    # NULL ⇒ carpeta raíz. La unicidad de raíz por usuario se garantiza con un
    # índice único parcial (ver migración): `WHERE parent_id IS NULL AND deleted_at IS NULL`.
    parent_id = Column(Integer, ForeignKey("folders.id"), nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    # Soft delete: una fila está "viva" cuando deleted_at IS NULL.
    deleted_at = Column(DateTime(timezone=True), nullable=True, default=None)
