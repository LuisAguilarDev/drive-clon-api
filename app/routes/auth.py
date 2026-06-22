"""Endpoints de sesión.

El backend es un resource server: valida el Bearer token de Keycloak y, en la
primera llamada de cada usuario, provisiona su organización (una por usuario).
"""
from typing import Annotated

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import CurrentUser
from app.db.database import SessionLocal
from app.gateways.keycloak_admin_gateway import keycloak_admin_gateway
from app.repositories.organization_repository import OrganizationRepository
from app.repositories.user_repository import UserRepository
from app.services.ensure_organization_service import EnsureOrganizationService

router = APIRouter(prefix="/auth", tags=["auth"])

# Cuando el backend acaba de crear la organización, el token actual del usuario
# todavía no trae el claim. El frontend reacciona a esta cabecera forzando un
# refresh transparente (keycloak.updateToken) para que el token nuevo lo incluya.
ORG_PROVISIONED_HEADER = "X-Org-Provisioned"


async def get_db():
    async with SessionLocal() as db:
        yield db


db_dependency = Annotated[AsyncSession, Depends(get_db)]


class OrganizationResponse(BaseModel):
    id: int
    name: str
    keycloak_org_id: str


class SessionResponse(BaseModel):
    email: str | None
    name: str | None
    picture: str | None
    organization: OrganizationResponse


@router.get("/session", response_model=SessionResponse)
async def get_session(user: CurrentUser, db: db_dependency, response: Response):
    """Devuelve el perfil + organización del usuario, creándola si no existe."""
    service = EnsureOrganizationService(
        user_repository=UserRepository(db),
        organization_repository=OrganizationRepository(db),
        keycloak_admin=keycloak_admin_gateway,
    )
    result = await service.ensure(
        keycloak_sub=user.sub,
        email=user.email or "",
        name=user.name or "",
        picture=user.picture or "",
    )

    if result.provisioned:
        response.headers[ORG_PROVISIONED_HEADER] = "true"

    return SessionResponse(
        email=user.email,
        name=user.name,
        picture=user.picture,
        organization=OrganizationResponse(
            id=result.organization.id,
            name=result.organization.name,
            keycloak_org_id=result.organization.keycloak_org_id,
        ),
    )
