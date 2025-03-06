from app.db.database import Base
from sqlalchemy import Column, Integer, String

class Files(Base):
    __tablename__ = "files"
    id = Column(Integer,primary_key=True, index=True)
    address = Column(String, index=True)



