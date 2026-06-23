"""Servicio: cierre (borrado) de la cuenta del usuario.

Política: se ELIMINAN los binarios de MinIO (contenido personal), pero se
CONSERVAN las filas/metadatos de la BD para analítica. En concreto:
- Se borran de MinIO todos los binarios de la organización del usuario y sus
  ficheros se marcan `status='deleted'` (la fila se conserva); las carpetas igual.
- Se anonimiza la PII del usuario (email, nombre, foto, sub) y se marca con
  `deleted_at`.
- Se marca su organización con `deleted_at`.
- Se elimina el usuario de Keycloak (best-effort), para borrar su PII del IdP.

Tras esto, un nuevo login del mismo email crea una cuenta nueva y limpia (el sub y
el email originales ya no coinciden con ninguna fila viva).
"""
import logging

from app.gateways.keycloak_admin_gateway import KeycloakAdminGateway
from app.gateways.object_storage_gateway import ObjectStorageGateway
from app.repositories.file_repository import FileRepository
from app.repositories.folder_repository import FolderRepository
from app.repositories.organization_repository import OrganizationRepository
from app.repositories.user_repository import UserRepository

logger = logging.getLogger(__name__)


class AccountService:
    def __init__(
        self,
        user_repository: UserRepository,
        organization_repository: OrganizationRepository,
        folder_repository: FolderRepository,
        file_repository: FileRepository,
        storage: ObjectStorageGateway,
        keycloak_admin: KeycloakAdminGateway,
    ):
        self._users = user_repository
        self._organizations = organization_repository
        self._folders = folder_repository
        self._files = file_repository
        self._storage = storage
        self._keycloak_admin = keycloak_admin

    async def delete_account(self, keycloak_sub: str) -> None:
        """Cierra la cuenta del usuario. Idempotente: si no existe (o ya está
        cerrada), no hace nada."""
        user = await self._users.find_by_sub(keycloak_sub)
        if user is None:
            return

        org_id = user.org_id

        # Modelo "una org por usuario": borra el contenido del tenant.
        if org_id is not None:
            # 1. Binarios de MinIO primero: si falla, las filas siguen sin purgar
            #    y se puede reintentar (no quedan objetos huérfanos sin marcar).
            object_keys = await self._files.all_object_keys_in_org(org_id)
            await self._storage.remove_objects(object_keys)
            # 2. Marca ficheros y carpetas como purgados (filas conservadas).
            await self._files.mark_purged_in_org(org_id)
            await self._folders.mark_purged_in_org(org_id)
            # 3. Marca la organización como borrada.
            await self._organizations.soft_delete(org_id)

        # 4. Anonimiza la PII del usuario y lo marca como borrado (fila conservada).
        await self._users.anonymize(user)

        # 5. Best-effort: elimina la identidad (PII) del usuario en Keycloak.
        await self._cleanup_keycloak_user(keycloak_sub)

    async def _cleanup_keycloak_user(self, keycloak_sub: str) -> None:
        try:
            await self._keycloak_admin.delete_user(keycloak_sub)
        except Exception:
            # La anonimización local ya es la fuente de verdad: un fallo de
            # limpieza en Keycloak no debe abortar el cierre de la cuenta.
            logger.warning(
                "No se pudo borrar el usuario en Keycloak.", exc_info=True
            )
