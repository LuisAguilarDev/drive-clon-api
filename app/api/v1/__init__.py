"""Router agregador de la versión 1 de la API.

Monta todos los módulos de v1 bajo el prefijo común `/api/v1`. Cada módulo
(`auth`, `files`, …) define su propio sub-prefijo (`/auth`, `/files`).
"""
from fastapi import APIRouter

from app.api.v1 import auth, files

router = APIRouter(prefix="/api/v1")
router.include_router(auth.router)
router.include_router(files.router)
