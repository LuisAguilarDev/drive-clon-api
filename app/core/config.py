from pathlib import Path

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

# El .env vive en la raíz del repo (no commiteado). Cargarlo explícitamente
# permite ejecutar el backend tanto dentro como fuera de Docker.
env_path = Path(__file__).resolve().parent.parent.parent / ".env"
load_dotenv(dotenv_path=env_path)


class Settings(BaseSettings):
    """Configuración del backend.

    La autenticación la delega 100% en Keycloak: este servicio actúa como
    *resource server* (valida el Bearer token vía JWKS) y usa un *service
    account* (client confidencial) para hablar con la Admin API de Keycloak.
    Ningún secreto se hardcodea: todo llega por variables de entorno.
    """

    # --- Base de datos ----------------------------------------------------
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/driveclon"

    # --- CORS -------------------------------------------------------------
    ALLOWED_ORIGINS: str = "http://localhost:5173"

    # --- Keycloak (validación de tokens del SPA) --------------------------
    # URL INTERNA: la que usa el backend para hablar con Keycloak server-to-server
    # (JWKS, token endpoint, Admin API). En Docker: http://keycloak:8080.
    KEYCLOAK_URL: str = "http://localhost:8080"
    # URL PÚBLICA: la que ve el navegador y aparece como `iss` en los tokens. En
    # Docker el backend está en otra red, así que el issuer NO coincide con la
    # URL interna; por eso se valida contra esta. Por defecto = KEYCLOAK_URL.
    KEYCLOAK_PUBLIC_URL: str = ""
    KEYCLOAK_REALM: str = "driveclon"
    KEYCLOAK_CLIENT_ID: str = "driveclon-ui"  # client público (azp del token)

    # --- Keycloak Admin API (service account del backend) -----------------
    KEYCLOAK_ADMIN_CLIENT_ID: str = "driveclon-backend"
    KEYCLOAK_ADMIN_CLIENT_SECRET: str = ""

    model_config = SettingsConfigDict(
        env_file=str(env_path),
        case_sensitive=True,
        extra="ignore",
    )

    @property
    def async_database_url(self) -> str:
        """Garantiza el driver async (asyncpg) aunque el .env traiga el sync."""
        url = self.DATABASE_URL
        if url.startswith("postgresql://"):
            return url.replace("postgresql://", "postgresql+asyncpg://", 1)
        return url

    @property
    def realm_base_url(self) -> str:
        """Base interna del realm (server-to-server)."""
        return f"{self.KEYCLOAK_URL}/realms/{self.KEYCLOAK_REALM}"

    @property
    def jwks_url(self) -> str:
        return f"{self.realm_base_url}/protocol/openid-connect/certs"

    @property
    def token_url(self) -> str:
        return f"{self.realm_base_url}/protocol/openid-connect/token"

    @property
    def issuer(self) -> str:
        """Issuer esperado en los tokens (URL pública). Cae a la interna si no
        se define una pública distinta (p. ej. ejecutando todo en localhost)."""
        public_url = self.KEYCLOAK_PUBLIC_URL or self.KEYCLOAK_URL
        return f"{public_url}/realms/{self.KEYCLOAK_REALM}"

    @property
    def admin_base_url(self) -> str:
        return f"{self.KEYCLOAK_URL}/admin/realms/{self.KEYCLOAK_REALM}"


settings = Settings()
