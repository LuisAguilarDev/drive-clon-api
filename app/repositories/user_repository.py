from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.Users import Users


class UserRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def find_by_sub(self, keycloak_sub: str) -> Users | None:
        result = await self.db.execute(
            select(Users).where(
                Users.keycloak_sub == keycloak_sub,
                Users.deleted_at.is_(None),
            )
        )
        return result.scalars().first()

    async def find_by_email(self, email: str) -> Users | None:
        result = await self.db.execute(
            select(Users).where(
                Users.email == email,
                Users.deleted_at.is_(None),
            )
        )
        return result.scalars().first()

    async def relink_sub(self, user: Users, keycloak_sub: str) -> Users:
        """Revincula un usuario existente a un nuevo `keycloak_sub`.

        Keycloak (H2 en memoria en dev) puede resetearse y emitir un `sub` nuevo
        para la misma persona. El email (verificado por Google) es la identidad
        estable, así que actualizamos el `sub` en vez de duplicar el usuario.
        """
        user.keycloak_sub = keycloak_sub
        await self.db.flush()
        await self.db.refresh(user)
        return user

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
        # flush (no commit): emite el INSERT y rellena el id generado dentro de la
        # transacción; el commit único lo hace `get_db`.
        await self.db.flush()
        await self.db.refresh(user)
        return user

    async def set_org(self, user: Users, org_id: int) -> Users:
        user.org_id = org_id
        await self.db.flush()
        await self.db.refresh(user)
        return user

    async def anonymize(self, user: Users) -> None:
        """Anonimiza la PII del usuario y lo marca como borrado (`deleted_at`).

        La fila se CONSERVA (los datos hacen falta para analítica); sólo se elimina
        la información personal. `email` y `keycloak_sub` se sustituyen por valores
        únicos (respetan los UNIQUE y evitan que un futuro login los reconcilie).
        """
        token = uuid4().hex
        user.keycloak_sub = f"deleted-{token}"
        user.email = f"deleted-{token}@anonymized.local"
        user.name = ""
        user.picture = ""
        user.deleted_at = func.now()
        await self.db.flush()

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
            await self.db.flush()
            await self.db.refresh(user)
        return user
