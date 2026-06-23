from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.Organizations import Organizations


class OrganizationRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def find_by_id(self, org_id: int) -> Organizations | None:
        result = await self.db.execute(
            select(Organizations).where(
                Organizations.id == org_id,
                Organizations.deleted_at.is_(None),
            )
        )
        return result.scalars().first()

    async def create(self, keycloak_org_id: str, name: str) -> Organizations:
        organization = Organizations(keycloak_org_id=keycloak_org_id, name=name)
        self.db.add(organization)
        # flush (no commit): emite el INSERT y rellena el id generado dentro de la
        # transacción; el commit único lo hace `get_db`.
        await self.db.flush()
        await self.db.refresh(organization)
        return organization

    async def soft_delete(self, org_id: int) -> None:
        """Marca la organización como borrada (`deleted_at`). La fila se CONSERVA
        para analítica; al borrar la cuenta el tenant queda inaccesible pero los
        datos se retienen."""
        await self.db.execute(
            update(Organizations)
            .where(
                Organizations.id == org_id,
                Organizations.deleted_at.is_(None),
            )
            .values(deleted_at=func.now())
        )
