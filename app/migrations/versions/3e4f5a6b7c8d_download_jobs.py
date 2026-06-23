"""download jobs queue (async folder ZIP)

Crea la tabla `download_jobs`, que hace de cola + registro durable de los jobs
asíncronos que empaquetan una carpeta en ZIP. El worker reclama el siguiente
trabajo con `FOR UPDATE SKIP LOCKED` sobre `status='queued'`; el índice parcial
`ix_download_jobs_queued` mantiene esa reclamación barata sin importar cuántas
filas terminadas (ready/failed/expired) se acumulen.

Usa `gen_random_uuid()` (incluida en el core de Postgres 13+) para la PK.

Revision ID: 3e4f5a6b7c8d
Revises: 2c3d4e5f6a7b
Create Date: 2026-06-23 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "3e4f5a6b7c8d"
down_revision: Union[str, None] = "2c3d4e5f6a7b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# `create_type=False`: el ENUM se crea/borra explícitamente para controlar el
# orden respecto a la creación de la tabla.
download_job_status = postgresql.ENUM(
    "queued",
    "processing",
    "ready",
    "failed",
    "expired",
    name="download_job_status",
    create_type=False,
)


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()
    download_job_status.create(bind, checkfirst=True)

    op.create_table(
        "download_jobs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "org_id",
            sa.Integer,
            sa.ForeignKey("organizations.id"),
            nullable=False,
        ),
        sa.Column(
            "owner_id", sa.Integer, sa.ForeignKey("users.id"), nullable=False
        ),
        sa.Column(
            "folder_id", sa.Integer, sa.ForeignKey("folders.id"), nullable=False
        ),
        sa.Column("name", sa.String, nullable=False),
        sa.Column(
            "status",
            download_job_status,
            nullable=False,
            server_default="queued",
        ),
        sa.Column("object_key", sa.String, nullable=True),
        sa.Column("size_bytes", sa.BigInteger, nullable=True),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(op.f("ix_download_jobs_org_id"), "download_jobs", ["org_id"])
    op.create_index(
        op.f("ix_download_jobs_owner_id"), "download_jobs", ["owner_id"]
    )
    # Índice parcial: sólo cubre las filas reclamables. La consulta de
    # reclamación (ORDER BY created_at, FOR UPDATE SKIP LOCKED) es index-only y
    # no se degrada al crecer el histórico de jobs terminados.
    op.create_index(
        "ix_download_jobs_queued",
        "download_jobs",
        ["created_at"],
        postgresql_where=sa.text("status = 'queued'"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_download_jobs_queued", table_name="download_jobs")
    op.drop_index(op.f("ix_download_jobs_owner_id"), table_name="download_jobs")
    op.drop_index(op.f("ix_download_jobs_org_id"), table_name="download_jobs")
    op.drop_table("download_jobs")
    download_job_status.drop(op.get_bind(), checkfirst=True)
