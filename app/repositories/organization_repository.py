from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.Organizations import Organizations


class OrganizationRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def find_by_id(self, org_id: int) -> Organizations | None:
        result = await self.db.execute(
            select(Organizations).where(Organizations.id == org_id)
        )
        return result.scalars().first()

    async def create(self, keycloak_org_id: str, name: str) -> Organizations:
        organization = Organizations(keycloak_org_id=keycloak_org_id, name=name)
        self.db.add(organization)
        await self.db.commit()
        await self.db.refresh(organization)
        return organization
