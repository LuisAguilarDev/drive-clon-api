from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.db.database import run_migrations
from app.routes import auth
from app.routes.auth import ORG_PROVISIONED_HEADER


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Aplica las migraciones de Alembic antes de servir peticiones.
    await run_migrations()
    yield


app = FastAPI(lifespan=lifespan)
app.include_router(auth.router)

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
