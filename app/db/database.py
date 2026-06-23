from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import declarative_base

from app.core.config import settings

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
    """Dependency de FastAPI: una `AsyncSession` y UN `commit` por request.

    La unidad de trabajo vive en el borde de la petición, no en los repositorios:
    éstos sólo hacen `flush()` (para obtener ids generados). Así una operación de
    servicio que abarca varias escrituras es atómica —si algo falla, `rollback`
    deshace TODO— en vez de dejar commits parciales (árboles corruptos).
    """
    async with SessionLocal() as db:
        try:
            yield db
            await db.commit()
        except Exception:
            await db.rollback()
            raise
