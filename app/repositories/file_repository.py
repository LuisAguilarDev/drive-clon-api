"""Acceso a datos de ficheros.

Toda consulta filtra por `org_id` (tenant) y por `status` (ÚNICO discriminador
del ciclo de vida: active/trashed/deleted). Las filas NUNCA se borran; el borrado
permanente sólo marca `status='deleted'` y limpia el binario de MinIO aparte.
"""
from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.Files import Files
from app.models.Folders import Folders
from app.models.Users import Users
from app.models.resource_status import ResourceStatus


class FileRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def list_by_folder(
        self, folder_id: int, org_id: int
    ) -> list[tuple[Files, str | None]]:
        """Ficheros activos de una carpeta junto al nombre de su propietario.

        Devuelve filas `(Files, owner_name)` para que la capa de servicio pueda
        construir la respuesta (incluido `is_me`) sin consultas extra por fichero.
        """
        result = await self.db.execute(
            select(Files, Users.name)
            .join(Users, Files.owner_id == Users.id)
            .where(
                Files.folder_id == folder_id,
                Files.org_id == org_id,
                Files.status == ResourceStatus.ACTIVE,
            )
            .order_by(Files.name)
        )
        return [(row[0], row[1]) for row in result.all()]

    async def find_by_id(self, file_id: int, org_id: int) -> Files | None:
        """Fichero ACTIVO por id, acotado al tenant del llamante (no cruza orgs)."""
        result = await self.db.execute(
            select(Files).where(
                Files.id == file_id,
                Files.org_id == org_id,
                Files.status == ResourceStatus.ACTIVE,
            )
        )
        return result.scalars().first()

    # --- Mover a la papelera (soft delete) -------------------------------
    async def soft_delete(self, file_id: int, org_id: int) -> bool:
        """Mueve un fichero a la papelera. Devuelve si afectó a algo.

        El binario en MinIO se conserva: el fichero queda recuperable.
        """
        result = await self.db.execute(
            update(Files)
            .where(
                Files.id == file_id,
                Files.org_id == org_id,
                Files.status == ResourceStatus.ACTIVE,
            )
            .values(status=ResourceStatus.TRASHED, trashed_at=func.now())
        )
        return (result.rowcount or 0) > 0

    async def soft_delete_in_folders(
        self, folder_ids: list[int], org_id: int
    ) -> None:
        """Mueve a la papelera todos los ficheros activos de las carpetas dadas."""
        if not folder_ids:
            return
        await self.db.execute(
            update(Files)
            .where(
                Files.folder_id.in_(folder_ids),
                Files.org_id == org_id,
                Files.status == ResourceStatus.ACTIVE,
            )
            .values(status=ResourceStatus.TRASHED, trashed_at=func.now())
        )

    # --- Papelera --------------------------------------------------------
    async def list_trashed(self, owner_id: int, org_id: int) -> list[Files]:
        """Ficheros en papelera del usuario borrados *individualmente*.

        Un fichero aparece en la papelera sólo si su carpeta sigue activa. Si la
        carpeta también está en la papelera, el fichero se restaura/purga junto a
        ella (cuelga de la carpeta, no se muestra suelto).
        """
        result = await self.db.execute(
            select(Files)
            .join(Folders, Files.folder_id == Folders.id)
            .where(
                Files.owner_id == owner_id,
                Files.org_id == org_id,
                Files.status == ResourceStatus.TRASHED,
                Folders.status == ResourceStatus.ACTIVE,
            )
            .order_by(Files.trashed_at.desc())
        )
        return list(result.scalars().all())

    async def find_trashed_by_id(
        self, file_id: int, owner_id: int, org_id: int
    ) -> Files | None:
        """Fichero en papelera por id, acotado al usuario y al tenant."""
        result = await self.db.execute(
            select(Files).where(
                Files.id == file_id,
                Files.owner_id == owner_id,
                Files.org_id == org_id,
                Files.status == ResourceStatus.TRASHED,
            )
        )
        return result.scalars().first()

    async def list_trashed_before(self, cutoff: datetime) -> list[Files]:
        """Ficheros en papelera movidos antes de `cutoff` (todas las orgs).

        Sólo los de carpeta activa (los de carpetas en papelera se purgan con
        ellas). Uso exclusivo del job de auto-purga.
        """
        result = await self.db.execute(
            select(Files)
            .join(Folders, Files.folder_id == Folders.id)
            .where(
                Files.status == ResourceStatus.TRASHED,
                Files.trashed_at < cutoff,
                Folders.status == ResourceStatus.ACTIVE,
            )
        )
        return list(result.scalars().all())

    async def restore(self, file_id: int, org_id: int, folder_id: int) -> None:
        """Revive un fichero (status=active) y lo coloca en `folder_id`."""
        await self.db.execute(
            update(Files)
            .where(Files.id == file_id, Files.org_id == org_id)
            .values(
                status=ResourceStatus.ACTIVE,
                trashed_at=None,
                folder_id=folder_id,
            )
        )

    async def restore_in_folders(
        self, folder_ids: list[int], org_id: int
    ) -> None:
        """Revive todos los ficheros en papelera de las carpetas dadas."""
        if not folder_ids:
            return
        await self.db.execute(
            update(Files)
            .where(
                Files.folder_id.in_(folder_ids),
                Files.org_id == org_id,
                Files.status == ResourceStatus.TRASHED,
            )
            .values(status=ResourceStatus.ACTIVE, trashed_at=None)
        )

    # --- Borrado permanente (purga: BD conservada + MinIO eliminado) -----
    async def object_keys_in_folders(
        self, folder_ids: list[int], org_id: int
    ) -> list[str]:
        """Claves MinIO de los ficheros aún no purgados de las carpetas dadas.

        Se usa antes de la purga para saber qué binarios borrar de MinIO; excluye
        los ya purgados (status='deleted'), que ya no tienen objeto.
        """
        if not folder_ids:
            return []
        result = await self.db.execute(
            select(Files.object_key).where(
                Files.folder_id.in_(folder_ids),
                Files.org_id == org_id,
                Files.status != ResourceStatus.DELETED,
            )
        )
        return list(result.scalars().all())

    async def mark_purged(self, file_id: int, org_id: int) -> None:
        """Marca un fichero como purgado (status=deleted, deleted_at=now).

        NO borra la fila: se conserva para analítica. El binario de MinIO se
        elimina aparte (en la capa de servicio).
        """
        await self.db.execute(
            update(Files)
            .where(
                Files.id == file_id,
                Files.org_id == org_id,
                Files.status != ResourceStatus.DELETED,
            )
            .values(status=ResourceStatus.DELETED, deleted_at=func.now())
        )

    async def mark_purged_in_folders(
        self, folder_ids: list[int], org_id: int
    ) -> None:
        """Marca como purgados todos los ficheros aún no purgados de las carpetas."""
        if not folder_ids:
            return
        await self.db.execute(
            update(Files)
            .where(
                Files.folder_id.in_(folder_ids),
                Files.org_id == org_id,
                Files.status != ResourceStatus.DELETED,
            )
            .values(status=ResourceStatus.DELETED, deleted_at=func.now())
        )

    # --- Cierre de cuenta (purga toda la org, conservando filas) ---------
    async def all_object_keys_in_org(self, org_id: int) -> list[str]:
        """Claves MinIO de todos los ficheros de la org aún con objeto (no
        purgados). Para borrar los binarios al cerrar la cuenta."""
        result = await self.db.execute(
            select(Files.object_key).where(
                Files.org_id == org_id,
                Files.status != ResourceStatus.DELETED,
            )
        )
        return list(result.scalars().all())

    async def mark_purged_in_org(self, org_id: int) -> None:
        """Marca como purgados todos los ficheros de la org (status=deleted,
        deleted_at=now). NO borra filas: se conservan para analítica."""
        await self.db.execute(
            update(Files)
            .where(
                Files.org_id == org_id,
                Files.status != ResourceStatus.DELETED,
            )
            .values(status=ResourceStatus.DELETED, deleted_at=func.now())
        )

    async def create(
        self,
        name: str,
        object_key: str,
        content_type: str | None,
        size_bytes: int | None,
        folder_id: int,
        org_id: int,
        owner_id: int,
    ) -> Files:
        file = Files(
            name=name,
            object_key=object_key,
            content_type=content_type,
            size_bytes=size_bytes,
            folder_id=folder_id,
            org_id=org_id,
            owner_id=owner_id,
        )
        self.db.add(file)
        # flush (no commit): emite el INSERT y rellena el id/created_at generados
        # dentro de la transacción; el commit único lo hace `get_db`.
        await self.db.flush()
        await self.db.refresh(file)
        return file
