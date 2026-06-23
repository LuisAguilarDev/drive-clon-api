"""Gateway hacia el almacenamiento de objetos (MinIO, S3-compatible).

Abstrae la subida del binario. El resto del código depende de la interfaz
`ObjectStorageGateway`, no de la implementación concreta de MinIO.
"""
import asyncio
import io
from abc import ABC, abstractmethod

from minio import Minio
from minio.deleteobjects import DeleteObject

from app.core.config import settings


class ObjectStorageGateway(ABC):
    """Abstract base class para el almacenamiento de objetos."""

    @abstractmethod
    async def put_object(
        self, object_key: str, data: bytes, content_type: str | None
    ) -> None:
        """Sube un objeto (bytes) bajo la clave indicada en el bucket privado."""

    @abstractmethod
    async def get_object(self, object_key: str) -> bytes:
        """Descarga el binario del objeto indicado del bucket privado."""

    @abstractmethod
    async def remove_objects(self, object_keys: list[str]) -> None:
        """Borra DEFINITIVAMENTE del bucket los objetos indicados (idempotente)."""


class MinioObjectStorageGateway(ObjectStorageGateway):
    """Implementación contra MinIO usando su SDK oficial (cliente síncrono)."""

    def __init__(self) -> None:
        self._client = Minio(
            settings.MINIO_ENDPOINT,
            access_key=settings.MINIO_ACCESS_KEY,
            secret_key=settings.MINIO_SECRET_KEY,
            secure=settings.MINIO_SECURE,
        )
        self._bucket = settings.MINIO_BUCKET

    async def put_object(
        self, object_key: str, data: bytes, content_type: str | None
    ) -> None:
        # El SDK de MinIO es síncrono: se descarga a un hilo para no bloquear el
        # event loop. El bucket lo crea `minio-init` (docker-compose).
        await asyncio.to_thread(
            self._client.put_object,
            self._bucket,
            object_key,
            io.BytesIO(data),
            len(data),
            content_type or "application/octet-stream",
        )

    async def get_object(self, object_key: str) -> bytes:
        # El SDK síncrono se descarga a un hilo para no bloquear el event loop.
        return await asyncio.to_thread(self._get_object_sync, object_key)

    def _get_object_sync(self, object_key: str) -> bytes:
        response = self._client.get_object(self._bucket, object_key)
        try:
            return response.read()
        finally:
            # El SDK exige cerrar y liberar la conexión al pool tras leer.
            response.close()
            response.release_conn()

    async def remove_objects(self, object_keys: list[str]) -> None:
        # El SDK síncrono se descarga a un hilo para no bloquear el event loop.
        if not object_keys:
            return
        await asyncio.to_thread(self._remove_objects_sync, object_keys)

    def _remove_objects_sync(self, object_keys: list[str]) -> None:
        # `remove_objects` es perezoso: devuelve un generador de errores que hay
        # que CONSUMIR para que el borrado se ejecute realmente. Borrar una clave
        # inexistente no es error, así que la operación es idempotente.
        delete_list = [DeleteObject(key) for key in object_keys]
        errors = self._client.remove_objects(self._bucket, delete_list)
        failed = [f"{err.object_name}: {err.message}" for err in errors]
        if failed:
            # No se silencia: si MinIO no pudo borrar, se propaga para reintentar.
            raise RuntimeError(
                "No se pudieron borrar objetos de MinIO: " + "; ".join(failed)
            )


# Instancia única reutilizable (mantiene el pool de conexiones del SDK).
object_storage_gateway: ObjectStorageGateway = MinioObjectStorageGateway()
