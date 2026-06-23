"""Gateway hacia la Admin API de Keycloak.

Abstrae las operaciones de organización que el backend necesita. La capa de
negocio depende de la interfaz `KeycloakAdminGateway`, nunca de la
implementación HTTP concreta.
"""
import json
import time
from abc import ABC, abstractmethod

import httpx

from app.core.config import settings

# Segundos de margen antes de la expiración para renovar el token de admin de
# forma proactiva (evita usar un token a punto de caducar a mitad de request).
_TOKEN_EXPIRY_MARGIN = 30


class KeycloakAdminGateway(ABC):
    @abstractmethod
    async def create_organization(self, name: str, alias: str, domain: str) -> str:
        """Crea una organización y devuelve su id en Keycloak."""

    @abstractmethod
    async def add_member(self, organization_id: str, user_id: str) -> None:
        """Añade un usuario (por su `sub`) como miembro de la organización."""

    @abstractmethod
    async def delete_user(self, user_id: str) -> None:
        """Borra un usuario (por su `sub`) de Keycloak para eliminar su PII.
        Tolera que ya no exista (idempotente)."""


class HttpKeycloakAdminGateway(KeycloakAdminGateway):
    """Implementación contra la REST Admin API usando un service account
    (client_credentials)."""

    def __init__(self) -> None:
        self._access_token: str | None = None
        self._token_expires_at: float = 0.0

    async def _get_admin_token(self) -> str:
        if self._access_token and time.monotonic() < self._token_expires_at:
            return self._access_token

        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(
                settings.token_url,
                data={
                    "grant_type": "client_credentials",
                    "client_id": settings.KEYCLOAK_ADMIN_CLIENT_ID,
                    "client_secret": settings.KEYCLOAK_ADMIN_CLIENT_SECRET,
                },
            )
            response.raise_for_status()
            payload = response.json()

        self._access_token = payload["access_token"]
        self._token_expires_at = (
            time.monotonic() + payload.get("expires_in", 60) - _TOKEN_EXPIRY_MARGIN
        )
        return self._access_token

    async def _headers(self) -> dict[str, str]:
        token = await self._get_admin_token()
        return {"Authorization": f"Bearer {token}"}

    async def create_organization(self, name: str, alias: str, domain: str) -> str:
        headers = await self._headers()
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(
                f"{settings.admin_base_url}/organizations",
                headers={**headers, "Content-Type": "application/json"},
                json={
                    "name": name,
                    "alias": alias,
                    "domains": [{"name": domain, "verified": False}],
                },
            )
        response.raise_for_status()

        # Keycloak devuelve 201 con la url del recurso en la cabecera Location;
        # el id de la organización es el último segmento.
        location = response.headers.get("Location", "")
        organization_id = location.rstrip("/").rsplit("/", 1)[-1]
        if not organization_id:
            raise RuntimeError("Keycloak no devolvió el id de la organización creada.")
        return organization_id

    async def add_member(self, organization_id: str, user_id: str) -> None:
        headers = await self._headers()
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(
                f"{settings.admin_base_url}/organizations/{organization_id}/members",
                headers={**headers, "Content-Type": "application/json"},
                # El endpoint espera el id del usuario como string JSON en el body.
                content=json.dumps(user_id),
            )
        response.raise_for_status()

    async def delete_user(self, user_id: str) -> None:
        headers = await self._headers()
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.delete(
                f"{settings.admin_base_url}/users/{user_id}",
                headers=headers,
            )
        if response.status_code != 404:
            response.raise_for_status()


# Instancia única reutilizable (mantiene cacheado el token de admin).
keycloak_admin_gateway: KeycloakAdminGateway = HttpKeycloakAdminGateway()
