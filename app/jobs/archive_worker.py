"""Worker que procesa la cola de empaquetado en ZIP (`download_jobs`).

Corre como proceso APARTE (servicio `worker` en docker-compose), independiente
del API y escalable por separado. Reclama jobs con `FOR UPDATE SKIP LOCKED`, así
que pueden correr varias réplicas sin pisarse. Se despierta al instante vía
LISTEN/NOTIFY cuando se encola un job y, como red de seguridad, sondea cada
`ARCHIVE_POLL_INTERVAL_SECONDS` (ese sondeo también dispara la recuperación de
jobs atascados de workers caídos).

Ejecutar: `python -m app.jobs.archive_worker`.
"""
import asyncio
import logging

import asyncpg

from app.core.config import settings
from app.db.database import SessionLocal
from app.gateways.object_storage_gateway import object_storage_gateway
from app.repositories.download_job_repository import (
    NOTIFY_CHANNEL,
    DownloadJobRepository,
)
from app.repositories.file_repository import FileRepository
from app.repositories.folder_repository import FolderRepository
from app.repositories.user_repository import UserRepository
from app.services.archive_service import ArchiveService

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def _build_service(db) -> ArchiveService:
    return ArchiveService(
        user_repository=UserRepository(db),
        folder_repository=FolderRepository(db),
        file_repository=FileRepository(db),
        download_job_repository=DownloadJobRepository(db),
        storage=object_storage_gateway,
    )


async def _reap_stale() -> None:
    """Devuelve a la cola los jobs atascados en 'processing' (worker caído)."""
    stale_seconds = settings.ARCHIVE_STALE_TIMEOUT_MINUTES * 60
    async with SessionLocal() as db:
        repo = DownloadJobRepository(db)
        recovered = await repo.reap_stale(stale_seconds)
        await db.commit()
        if recovered:
            logger.info("Recuperados %d jobs atascados a la cola.", recovered)


async def _process_one() -> bool:
    """Reclama y procesa UN job. Devuelve True si había algo que procesar."""
    # 1) Reclamar en su propia transacción y COMMITEAR: persiste 'processing' y
    #    libera el lock de fila ANTES del trabajo pesado, para no bloquear la cola
    #    ni al reaper mientras se arma el ZIP.
    async with SessionLocal() as db:
        job = await DownloadJobRepository(db).claim_next()
        await db.commit()
    if job is None:
        return False
    # 2) Armar el ZIP en una sesión nueva; el servicio marca READY/FAILED. El
    #    `job` viene desacoplado, pero `expire_on_commit=False` mantiene legibles
    #    sus atributos (id/org_id/folder_id), que es lo único que se lee.
    async with SessionLocal() as db:
        try:
            await _build_service(db).build(job)
            await db.commit()
        except Exception:
            await db.rollback()
            logger.exception("Error inesperado procesando el job %s", job.id)
    return True


async def _drain() -> None:
    """Procesa jobs hasta vaciar la cola."""
    while await _process_one():
        pass


async def _make_listener() -> asyncpg.Connection | None:
    """Conexión dedicada para LISTEN. asyncpg no entiende el esquema
    `postgresql+asyncpg`, así que se normaliza a `postgresql`."""
    dsn = settings.DATABASE_URL.replace("+asyncpg", "")
    try:
        return await asyncpg.connect(dsn)
    except Exception:
        logger.warning(
            "No se pudo abrir LISTEN/NOTIFY; se trabajará sólo por sondeo.",
            exc_info=True,
        )
        return None


async def run_worker() -> None:
    logger.info("Worker de empaquetado ZIP iniciado.")
    poll = settings.ARCHIVE_POLL_INTERVAL_SECONDS
    wake = asyncio.Event()

    listener = await _make_listener()
    if listener is not None:
        # El callback corre en el mismo bucle de eventos: despertar es seguro.
        await listener.add_listener(NOTIFY_CHANNEL, lambda *_: wake.set())
        logger.info("Escuchando NOTIFY en el canal '%s'.", NOTIFY_CHANNEL)

    try:
        while True:
            # Limpiar ANTES de trabajar: si llega un NOTIFY mientras procesamos,
            # el evento queda armado y volvemos a vaciar la cola sin esperar.
            wake.clear()
            try:
                await _reap_stale()
                await _drain()
            except Exception:
                logger.exception("Fallo en el ciclo del worker; se reintenta.")
            # Esperar un NOTIFY o el timeout de sondeo, lo que ocurra antes.
            try:
                await asyncio.wait_for(wake.wait(), timeout=poll)
            except asyncio.TimeoutError:
                pass
    finally:
        if listener is not None:
            await listener.close()


if __name__ == "__main__":
    asyncio.run(run_worker())
