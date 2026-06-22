from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.Users import Users


class UserRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def find_by_sub(self, keycloak_sub: str) -> Users | None:
        result = await self.db.execute(
            select(Users).where(Users.keycloak_sub == keycloak_sub)
        )
        return result.scalars().first()

    async def create(
        self,
        keycloak_sub: str,
        email: str,
        name: str = "",
        picture: str = "",
        org_id: int | None = None,
    ) -> Users:
        user = Users(
            keycloak_sub=keycloak_sub,
            email=email,
            name=name or "",
            picture=picture or "",
            org_id=org_id,
        )
        self.db.add(user)
        await self.db.commit()
        await self.db.refresh(user)
        return user

    async def set_org(self, user: Users, org_id: int) -> Users:
        user.org_id = org_id
        await self.db.commit()
        await self.db.refresh(user)
        return user

    async def update_profile(self, user: Users, name: str, picture: str) -> Users:
        """Sincroniza nombre/avatar desde el token si cambiaron (no pisa con vacío)."""
        changed = False
        if name and user.name != name:
            user.name = name
            changed = True
        if picture and user.picture != picture:
            user.picture = picture
            changed = True
        if changed:
            await self.db.commit()
            await self.db.refresh(user)
        return user
