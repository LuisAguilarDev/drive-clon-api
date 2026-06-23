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

    # --- MinIO (almacenamiento de objetos, S3-compatible) -----------------
    # Endpoint INTERNO host:puerto SIN esquema (el SDK lo añade según
    # MINIO_SECURE). Lo usan backend y worker para hablar con el almacenamiento
    # server-to-server (subir, descargar, multipart, borrar).
    MINIO_ENDPOINT: str = "localhost:9000"
    MINIO_ACCESS_KEY: str = "minioadmin"
    MINIO_SECRET_KEY: str = "minioadmin"
    MINIO_BUCKET: str = "driveclon"
    # En local va sobre HTTP; en producción debería ir sobre TLS.
    MINIO_SECURE: bool = False
    # Endpoint PÚBLICO con el que se FIRMAN las URLs prefirmadas que abrirá el
    # navegador. Mismo problema que `KEYCLOAK_PUBLIC_URL`: dentro de Docker el
    # almacenamiento es `minio:9000`, pero el navegador no resuelve ese host;
    # en local se expone vía un túnel (p. ej. ngrok al puerto 9000) y se pone
    # aquí su URL (`https://xxxx.ngrok-free.app`). Vacío ⇒ se usa el interno
    # (válido si el navegador alcanza MINIO_ENDPOINT directamente). En producción
    # con S3 real no hay desdoblamiento: es la propia URL del bucket.
    # OJO: SigV4 firma la cabecera `Host`; el túnel debe REENVIAR el Host
    # original a MinIO (ngrok: `--host-header=preserve`) o la firma no validará.
    MINIO_PUBLIC_ENDPOINT: str = ""

    # --- Subida de ficheros (URL prefirmada directa al almacenamiento) ----
    # Tamaño máximo de subida (bytes). Se valida en el boundary al pedir la URL
    # prefirmada y se reverifica con el tamaño real del objeto al confirmar.
    # Por defecto 5 GiB.
    MAX_UPLOAD_SIZE_BYTES: int = 5 * 1024 * 1024 * 1024
    # TTL (segundos) de la URL prefirmada de subida (PUT).
    UPLOAD_URL_TTL_SECONDS: int = 600
    # Horas tras las que una subida que quedó en 'pending' (nunca confirmada) se
    # limpia (fila + objeto huérfano), por el job programado.
    PENDING_UPLOAD_TIMEOUT_HOURS: int = 24

    # --- Descarga de carpetas en ZIP (job asíncrono) ----------------------
    # TTL (segundos) de la URL prefirmada de descarga del ZIP ya generado.
    ARCHIVE_URL_TTL_SECONDS: int = 300
    # Horas que el ZIP generado sigue disponible antes de considerarse expirado
    # (alinear con la regla de ciclo de vida del bucket sobre `_archives/`, que
    # borra el objeto temporal).
    ARCHIVE_RETENTION_HOURS: int = 24
    # Cada cuántos segundos sondea el worker la cola si no le llega un NOTIFY.
    ARCHIVE_POLL_INTERVAL_SECONDS: int = 5
    # Minutos tras los cuales un job atascado en 'processing' (worker caído a
    # mitad) se recupera devolviéndolo a la cola.
    ARCHIVE_STALE_TIMEOUT_MINUTES: int = 15

    # --- Papelera (soft delete + auto-purga) ------------------------------
    # Días que un elemento permanece en la papelera antes de borrarse
    # definitivamente (BD + MinIO) por el job programado.
    TRASH_RETENTION_DAYS: int = 30
    # Cada cuántas horas se ejecuta el job de auto-purga de la papelera.
    TRASH_PURGE_INTERVAL_HOURS: int = 24

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
