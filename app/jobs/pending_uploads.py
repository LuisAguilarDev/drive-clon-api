"""Job programado de limpieza de subidas pendientes.

Borra las subidas que quedaron en estado PENDING (se pidió la URL prefirmada pero
el cliente nunca confirmó la subida) más de `PENDING_UPLOAD_TIMEOUT_HOURS`:
elimina el objeto huérfano del almacenamiento (si llegó) y la fila. Lo dispara
APScheduler desde el lifespan de la app (ver `app/main.py`). Abre su propia
`AsyncSession` porque corre fuera del ciclo request/response.
"""
import logging

from app.core.config import settings
from app.db.database import SessionLocal
from app.gateways.object_storage_gateway import object_storage_gateway
from app.repositories.file_repository import FileRepository
from app.repositories.folder_repository import FolderRepository
from app.repositories.user_repository import UserRepository
from app.services.files_service import FilesService

logger = logging.getLogger(__name__)


async def purge_stale_pending_uploads() -> None:
    """Limpia subidas pendientes caducadas en todas las organizaciones."""
    async with SessionLocal() as db:
        service = FilesService(
            user_repository=UserRepository(db),
            folder_repository=FolderRepository(db),
            file_repository=FileRepository(db),
            storage=object_storage_gateway,
        )
        try:
            purged = await service.purge_stale_pending(
                settings.PENDING_UPLOAD_TIMEOUT_HOURS
            )
            # El job corre fuera de `get_db`: es dueño de su unidad de trabajo.
            await db.commit()
            if purged:
                logger.info("Limpieza de subidas pendientes: %d eliminadas.", purged)
        except Exception:
            await db.rollback()
            logger.exception("Fallo en la limpieza de subidas pendientes.")
