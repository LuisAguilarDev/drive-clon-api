"""Job programado de auto-purga de la papelera.

Borra DEFINITIVAMENTE (BD + MinIO) los elementos que llevan más de
`TRASH_RETENTION_DAYS` días en la papelera. Lo dispara APScheduler desde el
lifespan de la app (ver `app/main.py`). Abre su propia `AsyncSession` porque
corre fuera del ciclo request/response.
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


async def purge_expired_trash() -> None:
    """Purga la papelera caducada en todas las organizaciones."""
    async with SessionLocal() as db:
        service = FilesService(
            user_repository=UserRepository(db),
            folder_repository=FolderRepository(db),
            file_repository=FileRepository(db),
            storage=object_storage_gateway,
        )
        try:
            purged = await service.purge_expired(settings.TRASH_RETENTION_DAYS)
            if purged:
                logger.info(
                    "Auto-purga de papelera: %d elementos borrados.", purged
                )
        except Exception:
            # Un fallo del job no debe tumbar el scheduler: se registra y se
            # reintenta en la siguiente ejecución programada.
            logger.exception("Fallo en la auto-purga de la papelera.")
