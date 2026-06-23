"""Endpoints del sistema de ficheros (carpetas + ficheros).

Toda operación requiere un Bearer token válido y se acota al `org_id` del
llamante (multi-tenant). Las DTOs viven aquí; la lógica, en `FilesService`.
"""
from datetime import datetime
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import CurrentUser
from app.db.database import get_db
from app.gateways.object_storage_gateway import object_storage_gateway
from app.repositories.file_repository import FileRepository
from app.repositories.folder_repository import FolderRepository
from app.repositories.user_repository import UserRepository
from app.services.files_service import (
    FileDownload,
    FilesService,
    FolderListing,
    OperationNotAllowed,
    ResourceNotFound,
)

router = APIRouter(prefix="/files", tags=["files"])

db_dependency = Annotated[AsyncSession, Depends(get_db)]


def _build_service(db: AsyncSession) -> FilesService:
    return FilesService(
        user_repository=UserRepository(db),
        folder_repository=FolderRepository(db),
        file_repository=FileRepository(db),
        storage=object_storage_gateway,
    )


# --- DTOs ----------------------------------------------------------------
class FolderResponse(BaseModel):
    id: int
    name: str
    # NULL ⇒ carpeta raíz. Permite al frontend navegar "hacia arriba".
    parent_id: int | None = None


class OwnerResponse(BaseModel):
    id: int
    name: str
    is_me: bool


class FileResponse(BaseModel):
    id: int
    name: str
    content_type: str | None
    size_bytes: int | None
    owner: OwnerResponse
    created_at: datetime


class FolderListingResponse(BaseModel):
    folder: FolderResponse
    folders: list[FolderResponse]
    files: list[FileResponse]


class CreateFolderRequest(BaseModel):
    name: str
    parent_id: int


class TrashFolderResponse(BaseModel):
    id: int
    name: str
    parent_id: int | None = None
    deleted_at: datetime


class TrashFileResponse(BaseModel):
    id: int
    name: str
    content_type: str | None
    size_bytes: int | None
    deleted_at: datetime


class TrashListingResponse(BaseModel):
    folders: list[TrashFolderResponse]
    files: list[TrashFileResponse]


class RestoreFileResponse(BaseModel):
    id: int
    # Carpeta donde queda el fichero tras restaurar (su origen o la raíz).
    folder_id: int


class RestoreFolderResponse(BaseModel):
    id: int
    # Carpeta padre donde queda tras restaurar (su origen o la raíz).
    parent_id: int


# --- Helpers -------------------------------------------------------------
def _folder_response(folder) -> "FolderResponse":
    return FolderResponse(id=folder.id, name=folder.name, parent_id=folder.parent_id)


def _download_response(download: FileDownload) -> Response:
    """Respuesta binaria de descarga con nombre de fichero (RFC 5987)."""
    disposition = f"attachment; filename*=UTF-8''{quote(download.name)}"
    return Response(
        content=download.data,
        media_type=download.content_type or "application/octet-stream",
        headers={"Content-Disposition": disposition},
    )


def _to_listing_response(listing: FolderListing) -> FolderListingResponse:
    return FolderListingResponse(
        folder=_folder_response(listing.folder),
        folders=[_folder_response(f) for f in listing.subfolders],
        files=[
            FileResponse(
                id=item.file.id,
                name=item.file.name,
                content_type=item.file.content_type,
                size_bytes=item.file.size_bytes,
                owner=OwnerResponse(
                    id=item.owner.id, name=item.owner.name, is_me=item.owner.is_me
                ),
                created_at=item.file.created_at,
            )
            for item in listing.files
        ],
    )


# --- Endpoints -----------------------------------------------------------
@router.get("/root", response_model=FolderResponse)
async def get_root(user: CurrentUser, db: db_dependency):
    """Devuelve la carpeta raíz del usuario (id + nombre)."""
    service = _build_service(db)
    try:
        root = await service.get_root(user.sub)
    except ResourceNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    return _folder_response(root)


@router.get("", response_model=FolderListingResponse)
async def list_folder(
    user: CurrentUser, db: db_dependency, folder_id: int | None = None
):
    """Lista subcarpetas + ficheros de la carpeta (raíz si se omite `folder_id`)."""
    service = _build_service(db)
    try:
        listing = await service.list_folder(user.sub, folder_id)
    except ResourceNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    return _to_listing_response(listing)


@router.post("/folders", response_model=FolderResponse, status_code=status.HTTP_201_CREATED)
async def create_folder(
    user: CurrentUser, db: db_dependency, body: CreateFolderRequest
):
    """Crea una carpeta dentro de un padre del mismo tenant."""
    name = body.name.strip()
    if not name:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "El nombre es obligatorio.")
    service = _build_service(db)
    try:
        folder = await service.create_folder(user.sub, name, body.parent_id)
    except ResourceNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    return _folder_response(folder)


@router.get("/folders/{folder_id}/download")
async def download_folder(user: CurrentUser, db: db_dependency, folder_id: int):
    """Descarga una carpeta (y sus subcarpetas) como un ZIP con todos sus ficheros."""
    service = _build_service(db)
    try:
        download = await service.download_folder(user.sub, folder_id)
    except ResourceNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    return _download_response(download)


@router.get("/{file_id}/download")
async def download_file(user: CurrentUser, db: db_dependency, file_id: int):
    """Descarga el binario de un fichero del tenant del llamante."""
    service = _build_service(db)
    try:
        download = await service.download_file(user.sub, file_id)
    except ResourceNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    return _download_response(download)


@router.post("", response_model=FileResponse, status_code=status.HTTP_201_CREATED)
async def upload_file(
    user: CurrentUser,
    db: db_dependency,
    folder_id: Annotated[int, Form()],
    file: Annotated[UploadFile, File()],
):
    """Sube un fichero (multipart) a una carpeta del tenant del llamante."""
    filename = (file.filename or "").strip()
    if not filename:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "El fichero necesita un nombre.")
    data = await file.read()
    service = _build_service(db)
    try:
        created = await service.upload_file(
            keycloak_sub=user.sub,
            folder_id=folder_id,
            filename=filename,
            content_type=file.content_type,
            data=data,
        )
    except ResourceNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    return FileResponse(
        id=created.id,
        name=created.name,
        content_type=created.content_type,
        size_bytes=created.size_bytes,
        owner=OwnerResponse(id=created.owner_id, name=user.name or "", is_me=True),
        created_at=created.created_at,
    )


# --- Papelera ------------------------------------------------------------
# Nota: `/trash` se declara ANTES que `/{file_id}` para que FastAPI no intente
# interpretar "trash" como un id entero.
@router.get("/trash", response_model=TrashListingResponse)
async def list_trash(user: CurrentUser, db: db_dependency):
    """Papelera del usuario: ficheros y carpetas borrados (a su nivel tope)."""
    service = _build_service(db)
    try:
        trash = await service.list_trash(user.sub)
    except ResourceNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    return TrashListingResponse(
        folders=[
            TrashFolderResponse(
                id=f.id,
                name=f.name,
                parent_id=f.parent_id,
                deleted_at=f.deleted_at,
            )
            for f in trash.folders
        ],
        files=[
            TrashFileResponse(
                id=f.id,
                name=f.name,
                content_type=f.content_type,
                size_bytes=f.size_bytes,
                deleted_at=f.deleted_at,
            )
            for f in trash.files
        ],
    )


@router.delete("/trash", status_code=status.HTTP_204_NO_CONTENT)
async def empty_trash(user: CurrentUser, db: db_dependency):
    """Vacía la papelera del usuario: borrado definitivo (BD + MinIO)."""
    service = _build_service(db)
    try:
        await service.empty_trash(user.sub)
    except ResourceNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/folders/{folder_id}/restore", response_model=RestoreFolderResponse)
async def restore_folder(user: CurrentUser, db: db_dependency, folder_id: int):
    """Restaura una carpeta y su subárbol (a su sitio, o a la raíz si el padre
    original ya no existe)."""
    service = _build_service(db)
    try:
        parent_id = await service.restore_folder(user.sub, folder_id)
    except ResourceNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    return RestoreFolderResponse(id=folder_id, parent_id=parent_id)


@router.post("/{file_id}/restore", response_model=RestoreFileResponse)
async def restore_file(user: CurrentUser, db: db_dependency, file_id: int):
    """Restaura un fichero (a su carpeta, o a la raíz si ésta ya no existe)."""
    service = _build_service(db)
    try:
        folder_id = await service.restore_file(user.sub, file_id)
    except ResourceNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    return RestoreFileResponse(id=file_id, folder_id=folder_id)


@router.delete(
    "/folders/{folder_id}/permanent", status_code=status.HTTP_204_NO_CONTENT
)
async def purge_folder(user: CurrentUser, db: db_dependency, folder_id: int):
    """Borra DEFINITIVAMENTE una carpeta de la papelera y su subárbol (BD + MinIO)."""
    service = _build_service(db)
    try:
        await service.purge_folder(user.sub, folder_id)
    except ResourceNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/{file_id}/permanent", status_code=status.HTTP_204_NO_CONTENT)
async def purge_file(user: CurrentUser, db: db_dependency, file_id: int):
    """Borra DEFINITIVAMENTE un fichero de la papelera (BD + MinIO)."""
    service = _build_service(db)
    try:
        await service.purge_file(user.sub, file_id)
    except ResourceNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- Borrado (soft delete: mueve a la papelera) --------------------------
@router.delete("/folders/{folder_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_folder(user: CurrentUser, db: db_dependency, folder_id: int):
    """Borra una carpeta (con subcarpetas y ficheros) del tenant del llamante."""
    service = _build_service(db)
    try:
        await service.delete_folder(user.sub, folder_id)
    except OperationNotAllowed as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except ResourceNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/{file_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_file(user: CurrentUser, db: db_dependency, file_id: int):
    """Borra un fichero del tenant del llamante."""
    service = _build_service(db)
    try:
        await service.delete_file(user.sub, file_id)
    except ResourceNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)
