"""Acceso a datos de ficheros. Toda consulta filtra por `org_id` (tenant) y por
`deleted_at IS NULL` (soft delete)."""
from datetime import datetime

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.Files import Files
from app.models.Folders import Folders
from app.models.Users import Users


class FileRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def list_by_folder(
        self, folder_id: int, org_id: int
    ) -> list[tuple[Files, str | None]]:
        """Ficheros vivos de una carpeta junto al nombre de su propietario.

        Devuelve filas `(Files, owner_name)` para que la capa de servicio pueda
        construir la respuesta (incluido `is_me`) sin consultas extra por fichero.
        """
        result = await self.db.execute(
            select(Files, Users.name)
            .join(Users, Files.owner_id == Users.id)
            .where(
                Files.folder_id == folder_id,
                Files.org_id == org_id,
                Files.deleted_at.is_(None),
            )
            .order_by(Files.name)
        )
        return [(row[0], row[1]) for row in result.all()]

    async def find_by_id(self, file_id: int, org_id: int) -> Files | None:
        """Fichero por id, acotado al tenant del llamante (no cruza orgs)."""
        result = await self.db.execute(
            select(Files).where(
                Files.id == file_id,
                Files.org_id == org_id,
                Files.deleted_at.is_(None),
            )
        )
        return result.scalars().first()

    async def soft_delete(self, file_id: int, org_id: int) -> bool:
        """Marca un fichero como borrado (soft delete). Devuelve si afectó a algo.

        El binario en MinIO se conserva: el borrado es lógico, coherente con el
        resto del modelo (`deleted_at IS NULL` = vivo).
        """
        result = await self.db.execute(
            update(Files)
            .where(
                Files.id == file_id,
                Files.org_id == org_id,
                Files.deleted_at.is_(None),
            )
            .values(deleted_at=func.now())
        )
        await self.db.commit()
        return (result.rowcount or 0) > 0

    async def soft_delete_in_folders(
        self, folder_ids: list[int], org_id: int
    ) -> None:
        """Marca como borrados todos los ficheros vivos de las carpetas dadas."""
        if not folder_ids:
            return
        await self.db.execute(
            update(Files)
            .where(
                Files.folder_id.in_(folder_ids),
                Files.org_id == org_id,
                Files.deleted_at.is_(None),
            )
            .values(deleted_at=func.now())
        )
        await self.db.commit()

    # --- Papelera (soft delete) ------------------------------------------
    async def list_trashed(self, owner_id: int, org_id: int) -> list[Files]:
        """Ficheros en papelera del usuario borrados *individualmente*.

        Un fichero aparece en la papelera sólo si su carpeta sigue viva. Si la
        carpeta también está borrada, el fichero se restaura/purga junto a ella
        (se muestra colgando de la carpeta, no suelto).
        """
        result = await self.db.execute(
            select(Files)
            .join(Folders, Files.folder_id == Folders.id)
            .where(
                Files.owner_id == owner_id,
                Files.org_id == org_id,
                Files.deleted_at.is_not(None),
                Folders.deleted_at.is_(None),
            )
            .order_by(Files.deleted_at.desc())
        )
        return list(result.scalars().all())

    async def find_trashed_by_id(
        self, file_id: int, owner_id: int, org_id: int
    ) -> Files | None:
        """Fichero borrado por id, acotado al usuario y al tenant."""
        result = await self.db.execute(
            select(Files).where(
                Files.id == file_id,
                Files.owner_id == owner_id,
                Files.org_id == org_id,
                Files.deleted_at.is_not(None),
            )
        )
        return result.scalars().first()

    async def list_trashed_before(self, cutoff: datetime) -> list[Files]:
        """Ficheros en papelera borrados antes de `cutoff` (todas las orgs).

        Sólo los de carpeta viva (los de carpetas borradas se purgan con ellas).
        Uso exclusivo del job de auto-purga.
        """
        result = await self.db.execute(
            select(Files)
            .join(Folders, Files.folder_id == Folders.id)
            .where(
                Files.deleted_at.is_not(None),
                Files.deleted_at < cutoff,
                Folders.deleted_at.is_(None),
            )
        )
        return list(result.scalars().all())

    async def restore(self, file_id: int, org_id: int, folder_id: int) -> None:
        """Revive un fichero y lo coloca en `folder_id` (su carpeta o la raíz)."""
        await self.db.execute(
            update(Files)
            .where(Files.id == file_id, Files.org_id == org_id)
            .values(deleted_at=None, folder_id=folder_id)
        )
        await self.db.commit()

    async def restore_in_folders(
        self, folder_ids: list[int], org_id: int
    ) -> None:
        """Revive todos los ficheros borrados de las carpetas dadas."""
        if not folder_ids:
            return
        await self.db.execute(
            update(Files)
            .where(
                Files.folder_id.in_(folder_ids),
                Files.org_id == org_id,
                Files.deleted_at.is_not(None),
            )
            .values(deleted_at=None)
        )
        await self.db.commit()

    # --- Borrado físico (purga definitiva: BD + MinIO) -------------------
    async def object_keys_in_folders(
        self, folder_ids: list[int], org_id: int
    ) -> list[str]:
        """Claves MinIO de todos los ficheros (vivos o no) de las carpetas dadas.

        Se usa antes de la purga para saber qué binarios borrar de MinIO.
        """
        if not folder_ids:
            return []
        result = await self.db.execute(
            select(Files.object_key).where(
                Files.folder_id.in_(folder_ids),
                Files.org_id == org_id,
            )
        )
        return list(result.scalars().all())

    async def hard_delete(self, file_id: int, org_id: int) -> None:
        """Borra DEFINITIVAMENTE la fila del fichero (sin tocar MinIO)."""
        await self.db.execute(
            delete(Files).where(Files.id == file_id, Files.org_id == org_id)
        )
        await self.db.commit()

    async def hard_delete_in_folders(
        self, folder_ids: list[int], org_id: int
    ) -> None:
        """Borra DEFINITIVAMENTE las filas de todos los ficheros de las carpetas."""
        if not folder_ids:
            return
        await self.db.execute(
            delete(Files).where(
                Files.folder_id.in_(folder_ids),
                Files.org_id == org_id,
            )
        )
        await self.db.commit()

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
        await self.db.commit()
        await self.db.refresh(file)
        return file
