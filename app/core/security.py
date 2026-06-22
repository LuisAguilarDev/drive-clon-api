"""Validación de Bearer tokens emitidos por Keycloak (resource server).

El backend no emite tokens: confía en los que firma Keycloak. Aquí se descargan
las claves públicas del realm (JWKS), se cachean, y se valida la firma RS256,
el emisor y la expiración de cada token entrante.
"""
from typing import Annotated, Any

import httpx
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

from app.core.config import settings

# La firma de un token sólo se puede validar con la clave pública correspondiente
# (identificada por su `kid`). Cacheamos el JWKS para no pegarle a Keycloak en
# cada request, y lo refrescamos si aparece un `kid` desconocido (rotación).
_jwks_cache: dict[str, Any] | None = None

_bearer_scheme = HTTPBearer(auto_error=True)


class AuthenticatedUser:
    """Claims relevantes extraídos del token de Keycloak."""

    def __init__(self, claims: dict[str, Any]):
        self.sub: str = claims["sub"]
        self.email: str | None = claims.get("email")
        self.name: str | None = claims.get("name")
        self.picture: str | None = claims.get("picture")
        self.claims = claims


async def _fetch_jwks(force: bool = False) -> dict[str, Any]:
    global _jwks_cache
    if _jwks_cache is not None and not force:
        return _jwks_cache
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(settings.jwks_url)
        response.raise_for_status()
        _jwks_cache = response.json()
    return _jwks_cache


async def _signing_key(token: str) -> dict[str, Any]:
    kid = jwt.get_unverified_header(token).get("kid")
    if not kid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token sin 'kid' en la cabecera.",
        )

    jwks = await _fetch_jwks()
    for key in jwks.get("keys", []):
        if key.get("kid") == kid:
            return key

    # `kid` desconocido: puede que Keycloak haya rotado claves. Reintenta una vez.
    jwks = await _fetch_jwks(force=True)
    for key in jwks.get("keys", []):
        if key.get("kid") == kid:
            return key

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="No se encontró la clave de firma del token.",
    )


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(_bearer_scheme)],
) -> AuthenticatedUser:
    token = credentials.credentials
    try:
        key = await _signing_key(token)
        claims = jwt.decode(
            token,
            key,
            algorithms=[key.get("alg", "RS256")],
            issuer=settings.issuer,
            # El SPA público emite tokens con aud="account"; validar el emisor y
            # la firma es suficiente para un resource server de este realm.
            options={"verify_aud": False},
        )
    except (JWTError, KeyError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token inválido o expirado.",
        ) from exc

    if "sub" not in claims:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token sin 'sub'.",
        )
    return AuthenticatedUser(claims)


CurrentUser = Annotated[AuthenticatedUser, Depends(get_current_user)]
