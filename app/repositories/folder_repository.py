"""Acceso a datos de carpetas. Toda consulta filtra por `org_id` (tenant) y por
`deleted_at IS NULL` (soft delete)."""
from datetime import datetime

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.models.Folders import Folders


class FolderRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def find_root(self, owner_id: int, org_id: int) -> Folders | None:
        """Carpeta raíz del usuario (parent_id IS NULL), viva y de su org."""
        result = await self.db.execute(
            select(Folders).where(
                Folders.owner_id == owner_id,
                Folders.org_id == org_id,
                Folders.parent_id.is_(None),
                Folders.deleted_at.is_(None),
            )
        )
        return result.scalars().first()

    async def find_by_id(self, folder_id: int, org_id: int) -> Folders | None:
        """Carpeta por id, acotada al tenant del llamante (no cruza orgs)."""
        result = await self.db.execute(
            select(Folders).where(
                Folders.id == folder_id,
                Folders.org_id == org_id,
                Folders.deleted_at.is_(None),
            )
        )
        return result.scalars().first()

    async def list_children(self, parent_id: int, org_id: int) -> list[Folders]:
        """Subcarpetas vivas de una carpeta, ordenadas por nombre."""
        result = await self.db.execute(
            select(Folders)
            .where(
                Folders.parent_id == parent_id,
                Folders.org_id == org_id,
                Folders.deleted_at.is_(None),
            )
            .order_by(Folders.name)
        )
        return list(result.scalars().all())

    async def soft_delete_in_ids(
        self, folder_ids: list[int], org_id: int
    ) -> None:
        """Marca como borradas todas las carpetas vivas cuyo id esté en la lista."""
        if not folder_ids:
            return
        await self.db.execute(
            update(Folders)
            .where(
                Folders.id.in_(folder_ids),
                Folders.org_id == org_id,
                Folders.deleted_at.is_(None),
            )
            .values(deleted_at=func.now())
        )
        await self.db.commit()

    # --- Papelera (soft delete) ------------------------------------------
    async def list_trashed(self, owner_id: int, org_id: int) -> list[Folders]:
        """Carpetas en papelera del usuario, sólo las de *nivel tope*.

        Una carpeta aparece sólo si su padre sigue vivo; las subcarpetas de una
        carpeta borrada se restauran/purgan junto a ella (no se muestran sueltas).
        """
        parent = aliased(Folders)
        result = await self.db.execute(
            select(Folders)
            .outerjoin(parent, Folders.parent_id == parent.id)
            .where(
                Folders.owner_id == owner_id,
                Folders.org_id == org_id,
                Folders.deleted_at.is_not(None),
                parent.deleted_at.is_(None),
            )
            .order_by(Folders.deleted_at.desc())
        )
        return list(result.scalars().all())

    async def find_trashed_by_id(
        self, folder_id: int, owner_id: int, org_id: int
    ) -> Folders | None:
        """Carpeta borrada por id, acotada al usuario y al tenant."""
        result = await self.db.execute(
            select(Folders).where(
                Folders.id == folder_id,
                Folders.owner_id == owner_id,
                Folders.org_id == org_id,
                Folders.deleted_at.is_not(None),
            )
        )
        return result.scalars().first()

    async def find_any_by_id(
        self, folder_id: int, org_id: int
    ) -> Folders | None:
        """Carpeta por id SIN filtrar soft delete. Sirve para resolver el destino
        al restaurar (saber si el padre original sigue existiendo y vivo)."""
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
        """Subcarpetas (vivas o borradas) de una carpeta. Para recorrer subárboles
        completos al restaurar o purgar."""
        result = await self.db.execute(
            select(Folders).where(
                Folders.parent_id == parent_id,
                Folders.org_id == org_id,
            )
        )
        return list(result.scalars().all())

    async def list_trashed_before(self, cutoff: datetime) -> list[Folders]:
        """Carpetas tope en papelera borradas antes de `cutoff` (todas las orgs).
        Uso exclusivo del job de auto-purga."""
        parent = aliased(Folders)
        result = await self.db.execute(
            select(Folders)
            .outerjoin(parent, Folders.parent_id == parent.id)
            .where(
                Folders.deleted_at.is_not(None),
                Folders.deleted_at < cutoff,
                parent.deleted_at.is_(None),
            )
        )
        return list(result.scalars().all())

    async def restore_in_ids(
        self, folder_ids: list[int], org_id: int
    ) -> None:
        """Revive todas las carpetas borradas cuyo id esté en la lista."""
        if not folder_ids:
            return
        await self.db.execute(
            update(Folders)
            .where(
                Folders.id.in_(folder_ids),
                Folders.org_id == org_id,
                Folders.deleted_at.is_not(None),
            )
            .values(deleted_at=None)
        )
        await self.db.commit()

    async def reattach(
        self, folder_id: int, org_id: int, parent_id: int
    ) -> None:
        """Recoloca una carpeta bajo `parent_id` (al restaurar a la raíz cuando
        su padre original ya no existe)."""
        await self.db.execute(
            update(Folders)
            .where(Folders.id == folder_id, Folders.org_id == org_id)
            .values(parent_id=parent_id)
        )
        await self.db.commit()

    # --- Borrado físico (purga definitiva) -------------------------------
    async def hard_delete_in_ids(
        self, folder_ids: list[int], org_id: int
    ) -> None:
        """Borra DEFINITIVAMENTE las filas de las carpetas indicadas.

        Postgres comprueba las FKs (parent_id autorreferenciado) al final de la
        sentencia, por lo que borrar padres e hijos en un único DELETE es seguro.
        """
        if not folder_ids:
            return
        await self.db.execute(
            delete(Folders).where(
                Folders.id.in_(folder_ids),
                Folders.org_id == org_id,
            )
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
