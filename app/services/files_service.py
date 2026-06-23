"""Lógica de negocio del sistema de ficheros (carpetas + ficheros).

Orquesta repositorios y el gateway de almacenamiento. Toda operación se acota al
`org_id` del llamante, resuelto desde el **espejo en Postgres** (por `sub` del
token), nunca desde el claim del token. Carpetas/ficheros ajenos al tenant se
tratan como inexistentes (`ResourceNotFound` ⇒ 404), sin filtrar existencia.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from app.core.config import settings
from app.gateways.object_storage_gateway import ObjectStorageGateway
from app.models.Files import Files
from app.models.Folders import Folders
from app.models.resource_status import ResourceStatus
from app.repositories.file_repository import FileRepository
from app.repositories.folder_repository import FolderRepository
from app.repositories.user_repository import UserRepository


class ResourceNotFound(Exception):
    """La carpeta/fichero no existe o no pertenece al tenant del llamante."""


class OperationNotAllowed(Exception):
    """La operación es válida pero no permitida (p. ej. borrar la carpeta raíz)."""


class UploadTooLarge(Exception):
    """El fichero supera el tamaño máximo permitido (`MAX_UPLOAD_SIZE_BYTES`)."""


@dataclass
class FileOwner:
    id: int
    name: str
    is_me: bool


@dataclass
class FileItem:
    file: Files
    owner: FileOwner


@dataclass
class FolderListing:
    folder: Folders
    subfolders: list[Folders]
    files: list[FileItem]


@dataclass
class FileDownload:
    """Binario listo para enviar al cliente como descarga."""

    name: str
    content_type: str | None
    data: bytes


@dataclass
class UploadTicket:
    """Fila PENDING recién creada + URL prefirmada para que el cliente suba el
    binario directo al almacenamiento (el backend no toca los bytes)."""

    file: Files
    upload_url: str


@dataclass
class TrashListing:
    """Contenido de la papelera del usuario: items borrados a su nivel tope."""

    folders: list[Folders]
    files: list[Files]


class FilesService:
    def __init__(
        self,
        user_repository: UserRepository,
        folder_repository: FolderRepository,
        file_repository: FileRepository,
        storage: ObjectStorageGateway,
    ):
        self._users = user_repository
        self._folders = folder_repository
        self._files = file_repository
        self._storage = storage

    async def get_root(self, keycloak_sub: str) -> Folders:
        """Carpeta raíz del usuario. Se asume provisionada en el login."""
        user = await self._resolve_user(keycloak_sub)
        root = await self._folders.find_root(user.id, user.org_id)
        if root is None:
            # Defensa en profundidad: el login (EnsureOrganizationService) la crea.
            raise ResourceNotFound("El usuario no tiene carpeta raíz.")
        return root

    async def list_folder(
        self, keycloak_sub: str, folder_id: int | None
    ) -> FolderListing:
        """Lista subcarpetas + ficheros de una carpeta (raíz si `folder_id` es None)."""
        user = await self._resolve_user(keycloak_sub)
        folder = await self._resolve_folder(user, folder_id)

        subfolders = await self._folders.list_children(folder.id, user.org_id)
        rows = await self._files.list_by_folder(folder.id, user.org_id)
        files = [
            FileItem(
                file=file,
                owner=FileOwner(
                    id=file.owner_id,
                    name=owner_name or "",
                    is_me=file.owner_id == user.id,
                ),
            )
            for file, owner_name in rows
        ]
        return FolderListing(folder=folder, subfolders=subfolders, files=files)

    async def create_folder(
        self, keycloak_sub: str, name: str, parent_id: int
    ) -> Folders:
        """Crea una subcarpeta dentro de un padre del mismo tenant."""
        user = await self._resolve_user(keycloak_sub)
        # Valida que el padre exista, esté vivo y pertenezca al tenant.
        await self._resolve_folder(user, parent_id)
        return await self._folders.create(
            name=name,
            org_id=user.org_id,
            owner_id=user.id,
            parent_id=parent_id,
        )

    async def init_upload(
        self,
        keycloak_sub: str,
        folder_id: int,
        filename: str,
        content_type: str | None,
        size_bytes: int,
    ) -> UploadTicket:
        """Inicia una subida: valida tamaño y tenant, crea la fila PENDING y
        devuelve una URL prefirmada para que el cliente suba el binario DIRECTO al
        almacenamiento. El backend nunca recibe los bytes."""
        if size_bytes > settings.MAX_UPLOAD_SIZE_BYTES:
            raise UploadTooLarge(
                f"El fichero supera el máximo de "
                f"{settings.MAX_UPLOAD_SIZE_BYTES} bytes."
            )
        user = await self._resolve_user(keycloak_sub)
        folder = await self._resolve_folder(user, folder_id)

        # Clave única e irrepetible; el uuid evita colisiones por nombre repetido.
        object_key = f"{user.org_id}/{folder.id}/{uuid4()}-{filename}"
        file = await self._files.create_pending(
            name=filename,
            object_key=object_key,
            content_type=content_type,
            size_bytes=size_bytes,
            folder_id=folder.id,
            org_id=user.org_id,
            owner_id=user.id,
        )
        upload_url = await self._storage.presign_put(
            object_key, settings.UPLOAD_URL_TTL_SECONDS
        )
        return UploadTicket(file=file, upload_url=upload_url)

    async def confirm_upload(self, keycloak_sub: str, file_id: int) -> Files:
        """Confirma una subida: verifica que el binario llegó al almacenamiento y
        pasa la fila PENDING → ACTIVE con el tamaño REAL. Si el objeto no existe
        (subida fallida) o excede el máximo, no se activa."""
        user = await self._resolve_user(keycloak_sub)
        file = await self._files.find_pending_by_id(
            file_id, user.id, user.org_id
        )
        if file is None:
            raise ResourceNotFound("No hay una subida pendiente con ese id.")

        stat = await self._storage.stat(file.object_key)
        if stat is None:
            raise OperationNotAllowed("La subida no llegó al almacenamiento.")
        if stat.size > settings.MAX_UPLOAD_SIZE_BYTES:
            # El cliente declaró un tamaño válido pero subió algo mayor: se
            # rechaza y se limpia el objeto para no dejar basura.
            await self._storage.remove_objects([file.object_key])
            await self._files.delete_pending(file.id, user.org_id)
            raise UploadTooLarge(
                f"El fichero supera el máximo de "
                f"{settings.MAX_UPLOAD_SIZE_BYTES} bytes."
            )

        await self._files.activate(file.id, user.org_id, stat.size)
        file.status = ResourceStatus.ACTIVE
        file.size_bytes = stat.size
        return file

    async def purge_stale_pending(self, timeout_hours: int) -> int:
        """Limpia subidas que quedaron PENDING (nunca confirmadas) más de
        `timeout_hours`: borra el objeto huérfano (si existe) y la fila. Lo invoca
        el job programado. Devuelve cuántas se limpiaron."""
        cutoff = datetime.now(timezone.utc) - timedelta(hours=timeout_hours)
        purged = 0
        for file in await self._files.list_stale_pending(cutoff):
            await self._storage.remove_objects([file.object_key])
            await self._files.delete_pending(file.id, file.org_id)
            purged += 1
        return purged

    async def download_file(self, keycloak_sub: str, file_id: int) -> FileDownload:
        """Recupera el binario de un fichero del tenant del llamante."""
        user = await self._resolve_user(keycloak_sub)
        file = await self._files.find_by_id(file_id, user.org_id)
        if file is None:
            raise ResourceNotFound("El fichero no existe en esta organización.")
        data = await self._storage.get_object(file.object_key)
        return FileDownload(
            name=file.name, content_type=file.content_type, data=data
        )

    async def delete_file(self, keycloak_sub: str, file_id: int) -> None:
        """Borra (soft delete) un fichero del tenant del llamante."""
        user = await self._resolve_user(keycloak_sub)
        deleted = await self._files.soft_delete(file_id, user.org_id)
        if not deleted:
            raise ResourceNotFound("El fichero no existe en esta organización.")

    async def delete_folder(self, keycloak_sub: str, folder_id: int) -> None:
        """Borra (soft delete) una carpeta con sus subcarpetas y ficheros.

        La carpeta raíz no se puede borrar. El borrado es lógico y recursivo:
        se marca el subárbol completo de carpetas y todos sus ficheros.
        """
        user = await self._resolve_user(keycloak_sub)
        folder = await self._resolve_folder(user, folder_id)
        if folder.parent_id is None:
            raise OperationNotAllowed("No se puede borrar la carpeta raíz.")

        folder_ids = await self._collect_subtree_ids(folder, user.org_id)
        await self._files.soft_delete_in_folders(folder_ids, user.org_id)
        await self._folders.soft_delete_in_ids(folder_ids, user.org_id)

    # --- Papelera --------------------------------------------------------
    async def list_trash(self, keycloak_sub: str) -> TrashListing:
        """Papelera del usuario: items borrados a su nivel tope (los hijos de una
        carpeta borrada cuelgan de ella y no se listan sueltos)."""
        user = await self._resolve_user(keycloak_sub)
        folders = await self._folders.list_trashed(user.id, user.org_id)
        files = await self._files.list_trashed(user.id, user.org_id)
        return TrashListing(folders=folders, files=files)

    async def restore_file(self, keycloak_sub: str, file_id: int) -> int:
        """Restaura un fichero de la papelera. Devuelve el `folder_id` donde
        queda: su carpeta original si sigue viva, o la raíz si ya no existe."""
        user = await self._resolve_user(keycloak_sub)
        file = await self._files.find_trashed_by_id(file_id, user.id, user.org_id)
        if file is None:
            raise ResourceNotFound("El fichero no está en la papelera.")

        target_folder_id = await self._restore_target_folder_id(
            user, file.folder_id
        )
        await self._files.restore(file.id, user.org_id, target_folder_id)
        return target_folder_id

    async def restore_folder(self, keycloak_sub: str, folder_id: int) -> int:
        """Restaura una carpeta y todo su subárbol borrado. Devuelve el
        `parent_id` donde queda: su padre original si sigue vivo, o la raíz."""
        user = await self._resolve_user(keycloak_sub)
        folder = await self._folders.find_trashed_by_id(
            folder_id, user.id, user.org_id
        )
        if folder is None:
            raise ResourceNotFound("La carpeta no está en la papelera.")

        target_parent_id = await self._restore_target_folder_id(
            user, folder.parent_id
        )
        # Revive el subárbol completo (subcarpetas borradas + sus ficheros).
        folder_ids = await self._collect_subtree_ids_any_state(folder, user.org_id)
        await self._folders.restore_in_ids(folder_ids, user.org_id)
        await self._files.restore_in_folders(folder_ids, user.org_id)
        if folder.parent_id != target_parent_id:
            await self._folders.reattach(folder.id, user.org_id, target_parent_id)
        return target_parent_id

    async def purge_file(self, keycloak_sub: str, file_id: int) -> None:
        """Borra DEFINITIVAMENTE un fichero de la papelera (BD + MinIO)."""
        user = await self._resolve_user(keycloak_sub)
        file = await self._files.find_trashed_by_id(file_id, user.id, user.org_id)
        if file is None:
            raise ResourceNotFound("El fichero no está en la papelera.")
        await self._purge_file(file, user.org_id)

    async def purge_folder(self, keycloak_sub: str, folder_id: int) -> None:
        """Borra DEFINITIVAMENTE una carpeta de la papelera y su subárbol."""
        user = await self._resolve_user(keycloak_sub)
        folder = await self._folders.find_trashed_by_id(
            folder_id, user.id, user.org_id
        )
        if folder is None:
            raise ResourceNotFound("La carpeta no está en la papelera.")
        await self._purge_folder(folder, user.org_id)

    async def empty_trash(self, keycloak_sub: str) -> None:
        """Vacía la papelera del usuario: borrado definitivo de todo (BD + MinIO)."""
        user = await self._resolve_user(keycloak_sub)
        for folder in await self._folders.list_trashed(user.id, user.org_id):
            await self._purge_folder(folder, user.org_id)
        for file in await self._files.list_trashed(user.id, user.org_id):
            await self._purge_file(file, user.org_id)

    async def purge_expired(self, retention_days: int) -> int:
        """Borra definitivamente lo que lleve más de `retention_days` días en la
        papelera, en TODAS las organizaciones. Devuelve cuántos items tope se
        purgaron. Lo invoca el job programado (no una petición de usuario)."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
        purged = 0
        # Primero las carpetas: su purga arrastra los ficheros del subárbol.
        for folder in await self._folders.list_trashed_before(cutoff):
            await self._purge_folder(folder, folder.org_id)
            purged += 1
        for file in await self._files.list_trashed_before(cutoff):
            await self._purge_file(file, file.org_id)
            purged += 1
        return purged

    async def _purge_file(self, file: Files, org_id: int) -> None:
        """Borra el binario de MinIO y marca la fila como purgada (conservada).
        Si MinIO falla, la fila sigue en papelera para reintentar (nunca quedan
        objetos huérfanos sin referencia)."""
        await self._storage.remove_objects([file.object_key])
        await self._files.mark_purged(file.id, org_id)

    async def _purge_folder(self, folder: Folders, org_id: int) -> None:
        """Purga una carpeta y su subárbol: elimina los binarios de MinIO y marca
        las filas como purgadas (status=deleted). Las filas se conservan para
        analítica."""
        folder_ids = await self._collect_subtree_ids_any_state(folder, org_id)
        object_keys = await self._files.object_keys_in_folders(folder_ids, org_id)
        await self._storage.remove_objects(object_keys)
        await self._files.mark_purged_in_folders(folder_ids, org_id)
        await self._folders.mark_purged_in_ids(folder_ids, org_id)

    async def _restore_target_folder_id(
        self, user, original_folder_id: int | None
    ) -> int:
        """Carpeta destino al restaurar: la original si sigue activa, o la raíz si
        ya no lo está (así una restauración nunca falla por padre ausente)."""
        root = await self._folders.find_root(user.id, user.org_id)
        if root is None:
            raise ResourceNotFound("El usuario no tiene carpeta raíz.")
        if original_folder_id is None:
            return root.id
        original = await self._folders.find_any_by_id(
            original_folder_id, user.org_id
        )
        if original is None or original.status != ResourceStatus.ACTIVE:
            return root.id
        return original_folder_id

    async def _collect_subtree_ids_any_state(
        self, folder: Folders, org_id: int
    ) -> list[int]:
        """Ids del subárbol completo, incluidas subcarpetas borradas. Necesario
        para restaurar o purgar en bloque (a diferencia de `_collect_subtree_ids`,
        que sólo recorre carpetas vivas)."""
        ids = [folder.id]
        for child in await self._folders.list_children_any_state(folder.id, org_id):
            ids.extend(await self._collect_subtree_ids_any_state(child, org_id))
        return ids

    async def _collect_subtree_ids(
        self, folder: Folders, org_id: int
    ) -> list[int]:
        """Ids de la carpeta y, recursivamente, de todas sus subcarpetas vivas."""
        ids = [folder.id]
        for subfolder in await self._folders.list_children(folder.id, org_id):
            ids.extend(await self._collect_subtree_ids(subfolder, org_id))
        return ids

    async def _resolve_user(self, keycloak_sub: str):
        user = await self._users.find_by_sub(keycloak_sub)
        if user is None or user.org_id is None:
            # Sin espejo/organización no hay tenant que aislar.
            raise ResourceNotFound("Usuario no provisionado.")
        return user

    async def _resolve_folder(self, user, folder_id: int | None) -> Folders:
        """Resuelve la carpeta destino, acotada al tenant. None ⇒ raíz."""
        if folder_id is None:
            root = await self._folders.find_root(user.id, user.org_id)
            if root is None:
                raise ResourceNotFound("El usuario no tiene carpeta raíz.")
            return root

        folder = await self._folders.find_by_id(folder_id, user.org_id)
        if folder is None:
            raise ResourceNotFound("La carpeta no existe en esta organización.")
        return folder
