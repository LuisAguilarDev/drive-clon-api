"""Acceso a datos de los jobs de descarga (cola de empaquetado en ZIP).

La tabla `download_jobs` es a la vez cola y registro durable. La reclamación usa
`FOR UPDATE SKIP LOCKED` (patrón estándar de cola sobre Postgres): N workers
pueden reclamar en paralelo y cada uno se lleva una fila distinta, sin broker
externo y sin doble procesamiento.

Las consultas de cara al usuario filtran por `org_id` (tenant). Las del worker
operan a nivel de sistema (sin `org_id`), porque procesan trabajo de cualquier
organización; el aislamiento ya se garantizó al encolar.
"""
from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.DownloadJobs import DownloadJobs
from app.models.download_job_status import DownloadJobStatus

# Canal de NOTIFY: el worker hace LISTEN para despertarse al instante cuando se
# encola un job, en vez de esperar al siguiente sondeo.
NOTIFY_CHANNEL = "download_jobs_new"


class DownloadJobRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(
        self,
        org_id: int,
        owner_id: int,
        folder_id: int,
        name: str,
        expires_at: datetime,
    ) -> DownloadJobs:
        job = DownloadJobs(
            org_id=org_id,
            owner_id=owner_id,
            folder_id=folder_id,
            name=name,
            status=DownloadJobStatus.QUEUED,
            expires_at=expires_at,
        )
        self.db.add(job)
        # flush (no commit): emite el INSERT y rellena el id/created_at; el commit
        # único lo hace `get_db`.
        await self.db.flush()
        await self.db.refresh(job)
        # Avisa al worker (se entrega al confirmar la transacción). Canal fijo, no
        # hay interpolación de datos del usuario.
        await self.db.execute(text(f"NOTIFY {NOTIFY_CHANNEL}"))
        return job

    async def find_by_id(self, job_id: UUID, org_id: int) -> DownloadJobs | None:
        """Job por id acotado al tenant del llamante (no cruza organizaciones)."""
        result = await self.db.execute(
            select(DownloadJobs).where(
                DownloadJobs.id == job_id,
                DownloadJobs.org_id == org_id,
            )
        )
        return result.scalars().first()

    # --- Cola (uso del worker, a nivel de sistema) -----------------------
    async def claim_next(self) -> DownloadJobs | None:
        """Reclama atómicamente el siguiente job en cola y lo marca processing.

        `FOR UPDATE SKIP LOCKED` salta las filas que otro worker ya tiene
        bloqueadas, así que la reclamación concurrente nunca devuelve el mismo
        job dos veces. Devuelve None si la cola está vacía.
        """
        row = (
            await self.db.execute(
                text(
                    """
                    UPDATE download_jobs
                    SET status = 'processing', locked_at = now()
                    WHERE id = (
                        SELECT id FROM download_jobs
                        WHERE status = 'queued'
                        ORDER BY created_at
                        FOR UPDATE SKIP LOCKED
                        LIMIT 1
                    )
                    RETURNING id
                    """
                )
            )
        ).first()
        if row is None:
            return None
        return await self.find_internal(row[0])

    async def find_internal(self, job_id: UUID) -> DownloadJobs | None:
        """Job por id SIN filtrar por tenant. Uso exclusivo del worker."""
        result = await self.db.execute(
            select(DownloadJobs).where(DownloadJobs.id == job_id)
        )
        return result.scalars().first()

    async def reap_stale(self, stale_seconds: int) -> int:
        """Devuelve a la cola los jobs atascados en 'processing' (worker caído).

        Hace de visibility-timeout: si un worker reclamó un job y murió antes de
        terminarlo, tras `stale_seconds` vuelve a 'queued' para reintentarlo.
        Devuelve cuántos se recuperaron.
        """
        result = await self.db.execute(
            text(
                """
                UPDATE download_jobs
                SET status = 'queued', locked_at = NULL
                WHERE status = 'processing'
                  AND locked_at < now() - make_interval(secs => :secs)
                """
            ),
            {"secs": stale_seconds},
        )
        return result.rowcount or 0

    # --- Transiciones de estado ------------------------------------------
    async def mark_ready(
        self, job_id: UUID, object_key: str, size_bytes: int
    ) -> None:
        await self.db.execute(
            update(DownloadJobs)
            .where(DownloadJobs.id == job_id)
            .values(
                status=DownloadJobStatus.READY,
                object_key=object_key,
                size_bytes=size_bytes,
                error=None,
                completed_at=func.now(),
            )
        )

    async def mark_failed(self, job_id: UUID, error: str) -> None:
        await self.db.execute(
            update(DownloadJobs)
            .where(DownloadJobs.id == job_id)
            .values(
                status=DownloadJobStatus.FAILED,
                error=error,
                completed_at=func.now(),
            )
        )

    async def mark_expired(self, job_id: UUID) -> None:
        await self.db.execute(
            update(DownloadJobs)
            .where(DownloadJobs.id == job_id)
            .values(status=DownloadJobStatus.EXPIRED)
        )
