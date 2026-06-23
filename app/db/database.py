import asyncio
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import declarative_base

from app.core.config import settings

# Raíz del repo (parent de `app/`), para localizar alembic.ini.
ROOT_DIR = Path(__file__).resolve().parents[2]

# Motor async: `async_database_url` normaliza el driver a asyncpg aunque el
# .env traiga la forma síncrona (postgresql://).
engine = create_async_engine(settings.async_database_url, echo=False)

SessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    autoflush=False,
    expire_on_commit=False,
)

Base = declarative_base()


async def get_db():
    """Dependency de FastAPI: abre una `AsyncSession` por request y la cierra."""
    async with SessionLocal() as db:
        yield db


def _upgrade_to_head() -> None:
    """Aplica las migraciones de Alembic hasta `head` (síncrono)."""
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(ROOT_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT_DIR / "app" / "migrations"))
    cfg.set_main_option("sqlalchemy.url", settings.async_database_url)
    command.upgrade(cfg, "head")


async def run_migrations() -> None:
    """Aplica las migraciones al arrancar la app.

    Alembic es la única fuente de verdad del esquema. Se ejecuta en un hilo
    aparte porque su env.py usa `asyncio.run`, que no puede invocarse dentro del
    event loop ya activo del lifespan.
    """
    await asyncio.to_thread(_upgrade_to_head)
