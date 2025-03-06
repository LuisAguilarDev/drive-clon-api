from dotenv import load_dotenv
from pydantic_settings import BaseSettings
from pathlib import Path

# Cargar las variables de entorno desde el archivo .env
env_path = Path(__file__).resolve().parent.parent.parent / '.env'
load_dotenv(dotenv_path=env_path)

# Definir la clase de configuración
class Settings(BaseSettings):
    DATABASE_URL: str = "postgresql://postgres:postgres@localhost:5432/DriveClon" # Carga directa desde el .env
    SECRET_KEY: str = "default_secret_key"  # Valor por defecto si no se define en el .env
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 20  # Valor por defecto
    ALGORITHM:str
    class Config:
        env_file = env_path  # Especifica el archivo .env
        case_sensitive = True

# Crear una instancia única de la configuración
settings = Settings()
