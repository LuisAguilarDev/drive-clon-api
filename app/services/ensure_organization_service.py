"""Servicio compartido: garantiza que cada usuario tenga su organización.

Modelo elegido: **una organización por usuario** (tenant personal). En la
primera petición autenticada de un usuario nuevo, se crea su organización en
Keycloak vía service account, se le añade como miembro y se espeja en Postgres.

Idempotente: si el usuario ya tiene organización en la BD, no hace nada.
"""
from dataclasses import dataclass

from app.gateways.keycloak_admin_gateway import KeycloakAdminGateway
from app.models.Organizations import Organizations
from app.models.Users import Users
from app.repositories.organization_repository import OrganizationRepository
from app.repositories.user_repository import UserRepository

# Dominio sintético y único por usuario. Keycloak exige que los dominios de las
# organizaciones sean únicos, así que se deriva del `sub` (no del dominio real
# del email, que sería compartido entre usuarios de, p. ej., gmail.com).
_ORG_DOMAIN_SUFFIX = "users.driveclon.local"


@dataclass
class EnsureOrganizationResult:
    organization: Organizations
    user: Users
    # True sólo cuando la organización se acaba de crear en esta llamada: la
    # presentación lo usa para señalar al frontend que refresque su token.
    provisioned: bool


class EnsureOrganizationService:
    def __init__(
        self,
        user_repository: UserRepository,
        organization_repository: OrganizationRepository,
        keycloak_admin: KeycloakAdminGateway,
    ):
        self._users = user_repository
        self._organizations = organization_repository
        self._keycloak_admin = keycloak_admin

    async def ensure(
        self, keycloak_sub: str, email: str, name: str = "", picture: str = ""
    ) -> EnsureOrganizationResult:
        user = await self._users.find_by_sub(keycloak_sub)

        if user and user.org_id is not None:
            organization = await self._organizations.find_by_id(user.org_id)
            if organization is not None:
                # Mantiene el espejo local al día con los claims del token.
                user = await self._users.update_profile(user, name, picture)
                return EnsureOrganizationResult(organization, user, provisioned=False)

        # Crear la organización en Keycloak y añadir al usuario como miembro.
        display_name = self._display_name(email, name)
        alias = f"user-{keycloak_sub}"
        domain = f"{keycloak_sub}.{_ORG_DOMAIN_SUFFIX}"

        keycloak_org_id = await self._keycloak_admin.create_organization(
            name=display_name, alias=alias, domain=domain
        )
        await self._keycloak_admin.add_member(keycloak_org_id, keycloak_sub)

        # Espejar en Postgres y vincular al usuario.
        organization = await self._organizations.create(
            keycloak_org_id=keycloak_org_id, name=display_name
        )
        if user is None:
            user = await self._users.create(
                keycloak_sub=keycloak_sub,
                email=email,
                name=name,
                picture=picture,
                org_id=organization.id,
            )
        else:
            user = await self._users.set_org(user, organization.id)

        return EnsureOrganizationResult(organization, user, provisioned=True)

    @staticmethod
    def _display_name(email: str, name: str) -> str:
        if name:
            return f"{name}'s Drive"
        local_part = email.split("@", 1)[0] if email else "user"
        return f"{local_part}'s Drive"
