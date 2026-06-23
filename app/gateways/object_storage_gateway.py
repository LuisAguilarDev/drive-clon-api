"""Gateway hacia el almacenamiento de objetos (MinIO, S3-compatible).

Abstrae el almacenamiento binario. El resto del código depende de la interfaz
`ObjectStorageGateway`, no de la implementación concreta de MinIO. Como MinIO
habla el protocolo de S3, migrar a S3 real es cambiar configuración (endpoint y
credenciales), no código: las URLs prefirmadas, el multipart y el ciclo de vida
del bucket son API estándar de S3.
"""
import asyncio
from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import timedelta
from urllib.parse import quote, urlparse

from minio import Minio
from minio.deleteobjects import DeleteObject
from minio.error import S3Error

from app.core.config import settings

# Tamaño de trozo al leer/subir en streaming. 5 MiB es el mínimo de parte que
# admite el multipart de S3/MinIO (salvo la última), así que sirve para ambos.
_CHUNK_SIZE = 5 * 1024 * 1024


@dataclass
class ObjectStat:
    """Metadatos de un objeto en el almacenamiento (existe ⇒ subida completada)."""

    size: int
    content_type: str | None


class ObjectStorageGateway(ABC):
    """Abstract base class para el almacenamiento de objetos."""

    @abstractmethod
    async def presign_put(self, object_key: str, expires_seconds: int) -> str:
        """URL prefirmada de SUBIDA (PUT) directa del navegador al almacenamiento.

        Se firma con el endpoint PÚBLICO. No fija el Content-Type en la firma (el
        navegador puede enviar el suyo), así que no hay riesgo de desajuste.
        """

    @abstractmethod
    async def stat(self, object_key: str) -> ObjectStat | None:
        """Metadatos del objeto, o None si no existe. Sirve para confirmar que la
        subida prefirmada llegó realmente al almacenamiento."""

    @abstractmethod
    async def get_object(self, object_key: str) -> bytes:
        """Descarga el binario del objeto indicado del bucket privado."""

    @abstractmethod
    async def remove_objects(self, object_keys: list[str]) -> None:
        """Borra DEFINITIVAMENTE del bucket los objetos indicados (idempotente)."""

    @abstractmethod
    def open_object_stream(self, object_key: str) -> Iterator[bytes]:
        """Lee un objeto en streaming (trozos), sin cargarlo entero en memoria.

        SÍNCRONO a propósito: se consume dentro de un hilo de trabajo (el armado
        del ZIP es código bloqueante) para no acoplar el bucle de eventos.
        """

    @abstractmethod
    async def upload_file(
        self, object_key: str, file_path: str, content_type: str | None
    ) -> None:
        """Sube un fichero LOCAL al bucket (multipart automático, memoria acotada)."""

    @abstractmethod
    async def presign_get(
        self, object_key: str, download_name: str, expires_seconds: int
    ) -> str:
        """URL prefirmada de descarga (GET) válida un tiempo limitado.

        Se firma con el endpoint PÚBLICO para que el navegador pueda abrirla; el
        nombre de descarga viaja en `response-content-disposition`.
        """


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
        # Cliente SOLO para firmar URLs públicas: apunta al endpoint que ve el
        # navegador (túnel/ngrok en local, la URL real del bucket en prod). Si no
        # hay endpoint público configurado, se reutiliza el interno.
        self._public_client = self._build_public_client()

    def _build_public_client(self) -> Minio:
        raw = settings.MINIO_PUBLIC_ENDPOINT.strip()
        if not raw:
            return self._client
        if "://" in raw:
            parsed = urlparse(raw)
            endpoint = parsed.netloc
            secure = parsed.scheme == "https"
        else:
            endpoint = raw
            secure = settings.MINIO_SECURE
        return Minio(
            endpoint,
            access_key=settings.MINIO_ACCESS_KEY,
            secret_key=settings.MINIO_SECRET_KEY,
            secure=secure,
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

    def open_object_stream(self, object_key: str) -> Iterator[bytes]:
        response = self._client.get_object(self._bucket, object_key)
        try:
            yield from response.stream(_CHUNK_SIZE)
        finally:
            response.close()
            response.release_conn()

    async def upload_file(
        self, object_key: str, file_path: str, content_type: str | None
    ) -> None:
        # `fput_object` trocea el fichero en partes y hace multipart si hace
        # falta: la memoria no crece con el tamaño del fichero.
        await asyncio.to_thread(
            self._client.fput_object,
            self._bucket,
            object_key,
            file_path,
            content_type or "application/octet-stream",
        )

    async def presign_put(self, object_key: str, expires_seconds: int) -> str:
        return await asyncio.to_thread(
            self._public_client.presigned_put_object,
            self._bucket,
            object_key,
            timedelta(seconds=expires_seconds),
        )

    async def stat(self, object_key: str) -> ObjectStat | None:
        return await asyncio.to_thread(self._stat_sync, object_key)

    def _stat_sync(self, object_key: str) -> ObjectStat | None:
        try:
            info = self._client.stat_object(self._bucket, object_key)
        except S3Error as exc:
            # Objeto inexistente ⇒ subida no completada (no es un error fatal).
            if exc.code in ("NoSuchKey", "NoSuchObject"):
                return None
            raise
        return ObjectStat(size=info.size, content_type=info.content_type)

    async def presign_get(
        self, object_key: str, download_name: str, expires_seconds: int
    ) -> str:
        return await asyncio.to_thread(
            self._presign_get_sync, object_key, download_name, expires_seconds
        )

    def _presign_get_sync(
        self, object_key: str, download_name: str, expires_seconds: int
    ) -> str:
        # Fuerza nombre y tipo en la respuesta del objeto (RFC 5987 para el
        # nombre), así el navegador descarga "<carpeta>.zip" como adjunto.
        disposition = f"attachment; filename*=UTF-8''{quote(download_name)}"
        return self._public_client.presigned_get_object(
            self._bucket,
            object_key,
            expires=timedelta(seconds=expires_seconds),
            response_headers={
                "response-content-disposition": disposition,
                "response-content-type": "application/zip",
            },
        )

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
