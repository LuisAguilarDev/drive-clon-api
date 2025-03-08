from dotenv import load_dotenv
from pydantic_settings import BaseSettings
from pathlib import Path
import firebase_admin
from firebase_admin import credentials
import os

# Definir rutas globales
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
env_path = Path(__file__).resolve().parent.parent.parent / '.env'
cred_path = os.path.join(BASE_DIR, "serviceAccountKey.json")

# Cargar las variables de entorno desde el archivo .env
load_dotenv(dotenv_path=env_path)

# Definir la clase de configuración
class Settings(BaseSettings):
    DATABASE_URL: str = "postgresql://postgres:postgres@localhost:5432/DriveClon" # Carga directa desde el .env
    SECRET_KEY: str = "default_secret_key"  # Valor por defecto si no se define en el .env
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 20  # Valor por defecto
    ALGORITHM:str
    ALLOWED_ORIGINS: str = os.getenv("ALLOWED_ORIGINS", "*")
    class Config:
        env_file = env_path  # Especifica el archivo .env
        case_sensitive = True


# Cargar las credenciales
if not firebase_admin._apps:
    cred = credentials.Certificate(str(cred_path))
    firebase_admin.initialize_app(cred)

print("Firebase Admin SDK inicializado con éxito.")

# Crear una instancia única de la configuración
settings = Settings()
