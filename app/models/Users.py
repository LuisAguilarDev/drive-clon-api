from app.db.database import Base
from sqlalchemy import Column, Integer, String

class Users(Base):
    __tablename__ = "users"
    id = Column(Integer,primary_key=True, index=True)
    email=Column(String,unique=True)
    password = Column(String)