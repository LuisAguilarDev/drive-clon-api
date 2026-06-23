"""resource status lifecycle (active/trashed/deleted)

Introduce una única columna discriminadora `status` (ENUM nativo de Postgres) en
`files` y `folders` como fuente de verdad de las búsquedas, más `trashed_at`
(metadata). `deleted_at` pasa a significar "purgado de MinIO". Las filas ya no se
borran: el borrado permanente sólo elimina el binario y marca `status='deleted'`.

Backfill desde el modelo anterior, donde `deleted_at IS NOT NULL` significaba
"en la papelera": esos registros pasan a `status='trashed'`, su fecha se mueve a
`trashed_at` y `deleted_at` se limpia (no estaban purgados de MinIO).

Revision ID: 2c3d4e5f6a7b
Revises: 1a2b3c4d5e6f
Create Date: 2026-06-22 22:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "2c3d4e5f6a7b"
down_revision: Union[str, None] = "1a2b3c4d5e6f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Tipo ENUM compartido por `files` y `folders`. `create_type=False`: se crea/borra
# explícitamente (una sola vez) para no chocar al usarlo en dos tablas.
resource_status = postgresql.ENUM(
    "active", "trashed", "deleted", name="resource_status", create_type=False
)

TABLES = ("files", "folders")


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()
    resource_status.create(bind, checkfirst=True)

    for table in TABLES:
        op.add_column(
            table,
            sa.Column(
                "status",
                resource_status,
                nullable=False,
                server_default="active",
            ),
        )
        op.add_column(
            table,
            sa.Column("trashed_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index(op.f(f"ix_{table}_status"), table, ["status"])

    # Backfill: en el modelo anterior `deleted_at != NULL` = en la papelera.
    for table in TABLES:
        op.execute(
            f"UPDATE {table} SET status = 'trashed', trashed_at = deleted_at, "
            f"deleted_at = NULL WHERE deleted_at IS NOT NULL"
        )

    # Reconstruye el índice único de raíz: "vivo" ahora = status = 'active'.
    op.drop_index("uq_folders_single_root", table_name="folders")
    op.create_index(
        "uq_folders_single_root",
        "folders",
        ["owner_id"],
        unique=True,
        postgresql_where=sa.text("parent_id IS NULL AND status = 'active'"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    # Vuelve al índice anterior (vivo = deleted_at IS NULL).
    op.drop_index("uq_folders_single_root", table_name="folders")

    # Devuelve la fecha de papelera a `deleted_at` para el modelo anterior. Las
    # filas purgadas (status='deleted') ya tenían `deleted_at`, así que se quedan.
    for table in TABLES:
        op.execute(
            f"UPDATE {table} SET deleted_at = trashed_at "
            f"WHERE status = 'trashed'"
        )

    op.create_index(
        "uq_folders_single_root",
        "folders",
        ["owner_id"],
        unique=True,
        postgresql_where=sa.text("parent_id IS NULL AND deleted_at IS NULL"),
    )

    for table in TABLES:
        op.drop_index(op.f(f"ix_{table}_status"), table_name=table)
        op.drop_column(table, "trashed_at")
        op.drop_column(table, "status")

    resource_status.drop(op.get_bind(), checkfirst=True)
