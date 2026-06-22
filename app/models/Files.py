from sqlalchemy import Column, DateTime, ForeignKey, Integer, String

from app.db.database import Base


class Files(Base):
    __tablename__ = "files"

    id = Column(Integer, primary_key=True, index=True)
    address = Column(String, index=True)
    # Aislamiento de tenant: toda consulta de ficheros se filtra por `org_id`.
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=True, index=True)
    # Soft delete: una fila está "viva" cuando deleted_at IS NULL.
    deleted_at = Column(DateTime(timezone=True), nullable=True, default=None)
