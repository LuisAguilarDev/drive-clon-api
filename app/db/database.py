from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy_utils import database_exists, create_database
from app.core.config import settings

engine = create_engine(settings.DATABASE_URL)

if not database_exists(engine.url):
    create_database(engine.url)
    print("Database created!")
else:
    print("Database already exists!")
    
SessionLocal = sessionmaker(autocommit=False,autoflush=False,bind=engine)

Base = declarative_base()