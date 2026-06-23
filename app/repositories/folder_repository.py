"""Acceso a datos de carpetas.

Toda consulta filtra por `org_id` (tenant) y por `status` (ÚNICO discriminador
del ciclo de vida: active/trashed/deleted). Las filas NUNCA se borran.
"""
from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.models.Folders import Folders
from app.models.resource_status import ResourceStatus


class FolderRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def find_root(self, owner_id: int, org_id: int) -> Folders | None:
        """Carpeta raíz del usuario (parent_id IS NULL), activa y de su org."""
        result = await self.db.execute(
            select(Folders).where(
                Folders.owner_id == owner_id,
                Folders.org_id == org_id,
                Folders.parent_id.is_(None),
                Folders.status == ResourceStatus.ACTIVE,
            )
        )
        return result.scalars().first()

    async def find_by_id(self, folder_id: int, org_id: int) -> Folders | None:
        """Carpeta ACTIVA por id, acotada al tenant del llamante (no cruza orgs)."""
        result = await self.db.execute(
            select(Folders).where(
                Folders.id == folder_id,
                Folders.org_id == org_id,
                Folders.status == ResourceStatus.ACTIVE,
            )
        )
        return result.scalars().first()

    async def list_children(self, parent_id: int, org_id: int) -> list[Folders]:
        """Subcarpetas activas de una carpeta, ordenadas por nombre."""
        result = await self.db.execute(
            select(Folders)
            .where(
                Folders.parent_id == parent_id,
                Folders.org_id == org_id,
                Folders.status == ResourceStatus.ACTIVE,
            )
            .order_by(Folders.name)
        )
        return list(result.scalars().all())

    # --- Mover a la papelera (soft delete) -------------------------------
    async def soft_delete_in_ids(
        self, folder_ids: list[int], org_id: int
    ) -> None:
        """Mueve a la papelera todas las carpetas activas cuyo id esté en la lista."""
        if not folder_ids:
            return
        await self.db.execute(
            update(Folders)
            .where(
                Folders.id.in_(folder_ids),
                Folders.org_id == org_id,
                Folders.status == ResourceStatus.ACTIVE,
            )
            .values(status=ResourceStatus.TRASHED, trashed_at=func.now())
        )
        await self.db.commit()

    # --- Papelera --------------------------------------------------------
    async def list_trashed(self, owner_id: int, org_id: int) -> list[Folders]:
        """Carpetas en papelera del usuario, sólo las de *nivel tope*.

        Una carpeta aparece sólo si su padre sigue activo; las subcarpetas de una
        carpeta en papelera se restauran/purgan junto a ella (no se muestran
        sueltas).
        """
        parent = aliased(Folders)
        result = await self.db.execute(
            select(Folders)
            .join(parent, Folders.parent_id == parent.id)
            .where(
                Folders.owner_id == owner_id,
                Folders.org_id == org_id,
                Folders.status == ResourceStatus.TRASHED,
                parent.status == ResourceStatus.ACTIVE,
            )
            .order_by(Folders.trashed_at.desc())
        )
        return list(result.scalars().all())

    async def find_trashed_by_id(
        self, folder_id: int, owner_id: int, org_id: int
    ) -> Folders | None:
        """Carpeta en papelera por id, acotada al usuario y al tenant."""
        result = await self.db.execute(
            select(Folders).where(
                Folders.id == folder_id,
                Folders.owner_id == owner_id,
                Folders.org_id == org_id,
                Folders.status == ResourceStatus.TRASHED,
            )
        )
        return result.scalars().first()

    async def find_any_by_id(
        self, folder_id: int, org_id: int
    ) -> Folders | None:
        """Carpeta por id SIN filtrar por estado. Sirve para resolver el destino
        al restaurar (saber si el padre original sigue activo)."""
        result = await self.db.execute(
            select(Folders).where(
                Folders.id == folder_id,
                Folders.org_id == org_id,
            )
        )
        return result.scalars().first()

    async def list_children_any_state(
        self, parent_id: int, org_id: int
    ) -> list[Folders]:
        """Subcarpetas (en cualquier estado) de una carpeta. Para recorrer
        subárboles completos al restaurar o purgar."""
        result = await self.db.execute(
            select(Folders).where(
                Folders.parent_id == parent_id,
                Folders.org_id == org_id,
            )
        )
        return list(result.scalars().all())

    async def list_trashed_before(self, cutoff: datetime) -> list[Folders]:
        """Carpetas tope en papelera movidas antes de `cutoff` (todas las orgs).
        Uso exclusivo del job de auto-purga."""
        parent = aliased(Folders)
        result = await self.db.execute(
            select(Folders)
            .join(parent, Folders.parent_id == parent.id)
            .where(
                Folders.status == ResourceStatus.TRASHED,
                Folders.trashed_at < cutoff,
                parent.status == ResourceStatus.ACTIVE,
            )
        )
        return list(result.scalars().all())

    async def restore_in_ids(
        self, folder_ids: list[int], org_id: int
    ) -> None:
        """Revive todas las carpetas en papelera cuyo id esté en la lista."""
        if not folder_ids:
            return
        await self.db.execute(
            update(Folders)
            .where(
                Folders.id.in_(folder_ids),
                Folders.org_id == org_id,
                Folders.status == ResourceStatus.TRASHED,
            )
            .values(status=ResourceStatus.ACTIVE, trashed_at=None)
        )
        await self.db.commit()

    async def reattach(
        self, folder_id: int, org_id: int, parent_id: int
    ) -> None:
        """Recoloca una carpeta bajo `parent_id` (al restaurar a la raíz cuando
        su padre original ya no está activo)."""
        await self.db.execute(
            update(Folders)
            .where(Folders.id == folder_id, Folders.org_id == org_id)
            .values(parent_id=parent_id)
        )
        await self.db.commit()

    # --- Borrado permanente (purga: BD conservada) -----------------------
    async def mark_purged_in_ids(
        self, folder_ids: list[int], org_id: int
    ) -> None:
        """Marca como purgadas las carpetas indicadas (status=deleted,
        deleted_at=now). NO borra las filas: se conservan para analítica."""
        if not folder_ids:
            return
        await self.db.execute(
            update(Folders)
            .where(
                Folders.id.in_(folder_ids),
                Folders.org_id == org_id,
                Folders.status != ResourceStatus.DELETED,
            )
            .values(status=ResourceStatus.DELETED, deleted_at=func.now())
        )
        await self.db.commit()

    async def create(
        self, name: str, org_id: int, owner_id: int, parent_id: int | None
    ) -> Folders:
        folder = Folders(
            name=name, org_id=org_id, owner_id=owner_id, parent_id=parent_id
        )
        self.db.add(folder)
        await self.db.commit()
        await self.db.refresh(folder)
        return folder
