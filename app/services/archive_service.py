"""Lógica de negocio de la descarga de carpetas en ZIP (job asíncrono).

Separa la PETICIÓN del TRABAJO: el endpoint encola un job y responde al instante;
un worker arma el ZIP aparte, lo deja como objeto temporal en el bucket y se
sirve por URL prefirmada (el ancho de banda lo asume el almacenamiento, no el
backend). El armado nunca carga el ZIP entero en memoria: se ensambla en un
fichero temporal en disco leyendo cada fichero origen en streaming, y se sube por
multipart. Así la memoria queda acotada sea cual sea el tamaño del archivo.

Toda operación de cara al usuario se acota al `org_id` del llamante (igual que
`FilesService`); un job de otro tenant se trata como inexistente (404).
"""
import asyncio
import logging
import os
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID

from app.core.config import settings
from app.gateways.object_storage_gateway import ObjectStorageGateway
from app.models.DownloadJobs import DownloadJobs
from app.models.Folders import Folders
from app.models.download_job_status import DownloadJobStatus
from app.repositories.download_job_repository import DownloadJobRepository
from app.repositories.file_repository import FileRepository
from app.repositories.folder_repository import FolderRepository
from app.repositories.user_repository import UserRepository
from app.services.files_service import ResourceNotFound

logger = logging.getLogger(__name__)


@dataclass
class ArchiveJobView:
    """Vista de un job para la API: estado + (si está listo) URL de descarga."""

    id: UUID
    status: DownloadJobStatus
    name: str
    size_bytes: int | None
    download_url: str | None
    error: str | None


class ArchiveService:
    def __init__(
        self,
        user_repository: UserRepository,
        folder_repository: FolderRepository,
        file_repository: FileRepository,
        download_job_repository: DownloadJobRepository,
        storage: ObjectStorageGateway,
    ):
        self._users = user_repository
        self._folders = folder_repository
        self._files = file_repository
        self._jobs = download_job_repository
        self._storage = storage

    # --- Cara al usuario (request / poll) --------------------------------
    async def request_archive(
        self, keycloak_sub: str, folder_id: int | None
    ) -> DownloadJobs:
        """Encola un job para empaquetar una carpeta del tenant del llamante."""
        user = await self._resolve_user(keycloak_sub)
        folder = await self._resolve_folder(user, folder_id)
        expires_at = datetime.now(timezone.utc) + timedelta(
            hours=settings.ARCHIVE_RETENTION_HOURS
        )
        return await self._jobs.create(
            org_id=user.org_id,
            owner_id=user.id,
            folder_id=folder.id,
            name=f"{folder.name}.zip",
            expires_at=expires_at,
        )

    async def get_job(self, keycloak_sub: str, job_id: UUID) -> ArchiveJobView:
        """Estado de un job. Si está listo y no ha caducado, adjunta una URL
        prefirmada de descarga; si ya caducó, lo marca EXPIRED."""
        user = await self._resolve_user(keycloak_sub)
        job = await self._jobs.find_by_id(job_id, user.org_id)
        if job is None:
            raise ResourceNotFound("El job de descarga no existe.")

        status = job.status
        download_url: str | None = None
        if job.status == DownloadJobStatus.READY:
            if self._is_expired(job):
                await self._jobs.mark_expired(job.id)
                status = DownloadJobStatus.EXPIRED
            else:
                download_url = await self._storage.presign_get(
                    job.object_key,
                    job.name,
                    settings.ARCHIVE_URL_TTL_SECONDS,
                )
        return ArchiveJobView(
            id=job.id,
            status=status,
            name=job.name,
            size_bytes=job.size_bytes,
            download_url=download_url,
            error=job.error,
        )

    # --- Worker (procesa la cola; opera por `org_id` del job) ------------
    async def build(self, job: DownloadJobs) -> None:
        """Arma el ZIP de un job ya reclamado y lo marca READY (o FAILED).

        El job llega en estado 'processing' (lo dejó `claim_next`). El armado
        pesado corre en un hilo para no bloquear el bucle de eventos del worker.
        """
        folder = await self._folders.find_by_id(job.folder_id, job.org_id)
        if folder is None:
            await self._jobs.mark_failed(
                job.id, "La carpeta ya no está disponible."
            )
            return

        try:
            entries = await self._collect_files(folder, job.org_id, prefix="")
            object_key = f"_archives/{job.org_id}/{job.id}.zip"
            tmp_path, size = await asyncio.to_thread(self._assemble_zip, entries)
            try:
                await self._storage.upload_file(
                    object_key, tmp_path, "application/zip"
                )
            finally:
                await asyncio.to_thread(os.remove, tmp_path)
            await self._jobs.mark_ready(job.id, object_key, size)
            logger.info(
                "ZIP listo: job=%s carpeta=%s ficheros=%d tamaño=%d",
                job.id,
                folder.id,
                len(entries),
                size,
            )
        except Exception as exc:
            # No se propaga: el worker debe seguir procesando otros jobs. El
            # motivo queda en la fila para que el usuario lo vea.
            logger.exception("Fallo armando el ZIP del job %s", job.id)
            await self._jobs.mark_failed(job.id, str(exc))

    # --- Armado del ZIP (síncrono, corre en un hilo) ---------------------
    def _assemble_zip(self, entries: list[tuple[str, str]]) -> tuple[str, int]:
        """Ensambla el ZIP en un fichero temporal en disco y devuelve (ruta,
        tamaño). Memoria acotada: cada fichero origen se lee y escribe en trozos,
        nunca entero en RAM; el ZIP se acumula en disco, no en memoria."""
        fd, tmp_path = tempfile.mkstemp(suffix=".zip")
        os.close(fd)
        try:
            with zipfile.ZipFile(
                tmp_path, "w", zipfile.ZIP_DEFLATED, allowZip64=True
            ) as archive:
                for arcname, object_key in entries:
                    with archive.open(arcname, "w") as dest:
                        for chunk in self._storage.open_object_stream(object_key):
                            dest.write(chunk)
        except Exception:
            os.remove(tmp_path)
            raise
        return tmp_path, os.path.getsize(tmp_path)

    async def _collect_files(
        self, folder: Folders, org_id: int, prefix: str
    ) -> list[tuple[str, str]]:
        """Lista recursiva de `(ruta_relativa, object_key)` bajo una carpeta."""
        entries: list[tuple[str, str]] = [
            (f"{prefix}{file.name}", file.object_key)
            for file, _owner_name in await self._files.list_by_folder(
                folder.id, org_id
            )
        ]
        for subfolder in await self._folders.list_children(folder.id, org_id):
            entries.extend(
                await self._collect_files(
                    subfolder, org_id, f"{prefix}{subfolder.name}/"
                )
            )
        return entries

    # --- Helpers ---------------------------------------------------------
    def _is_expired(self, job: DownloadJobs) -> bool:
        return (
            job.expires_at is not None
            and datetime.now(timezone.utc) >= job.expires_at
        )

    async def _resolve_user(self, keycloak_sub: str):
        user = await self._users.find_by_sub(keycloak_sub)
        if user is None or user.org_id is None:
            raise ResourceNotFound("Usuario no provisionado.")
        return user

    async def _resolve_folder(self, user, folder_id: int | None) -> Folders:
        """Resuelve la carpeta a empaquetar, acotada al tenant. None ⇒ raíz."""
        if folder_id is None:
            root = await self._folders.find_root(user.id, user.org_id)
            if root is None:
                raise ResourceNotFound("El usuario no tiene carpeta raíz.")
            return root
        folder = await self._folders.find_by_id(folder_id, user.org_id)
        if folder is None:
            raise ResourceNotFound("La carpeta no existe en esta organización.")
        return folder
