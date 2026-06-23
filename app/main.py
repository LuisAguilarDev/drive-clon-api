from contextlib import asynccontextmanager

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1 import router as api_v1_router
from app.api.v1.auth import ORG_PROVISIONED_HEADER
from app.core.config import settings
from app.jobs.trash_purge import purge_expired_trash


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Las migraciones NO se aplican aquí: corren fuera del proceso del servidor
    # (servicio `migrate` en docker-compose, o `alembic upgrade head` a mano).
    # Acoplarlas al arranque ralentiza el boot y provoca carreras con réplicas.

    # Job periódico que borra definitivamente lo caducado de la papelera.
    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(
        purge_expired_trash,
        "interval",
        hours=settings.TRASH_PURGE_INTERVAL_HOURS,
        id="trash-purge",
    )
    scheduler.start()
    try:
        yield
    finally:
        scheduler.shutdown(wait=False)


app = FastAPI(lifespan=lifespan)
app.include_router(api_v1_router)

allowed_origins = [origin.strip() for origin in settings.ALLOWED_ORIGINS.split(",")]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    # El navegador sólo puede leer cabeceras personalizadas si se exponen.
    expose_headers=[ORG_PROVISIONED_HEADER],
)


@app.get("/")
def root():
    return {"status": "ok"}
