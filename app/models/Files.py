from sqlalchemy import Column, ForeignKey, Integer, String

from app.db.database import Base


class Files(Base):
    __tablename__ = "files"

    id = Column(Integer, primary_key=True, index=True)
    address = Column(String, index=True)
    # Aislamiento de tenant: toda consulta de ficheros se filtra por `org_id`.
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=True, index=True)
