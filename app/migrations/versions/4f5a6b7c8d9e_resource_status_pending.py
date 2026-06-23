"""add 'pending' to resource_status (presigned upload)

Añade el valor `pending` al ENUM `resource_status`: una subida con URL prefirmada
crea primero la fila en estado `pending` (binario aún no confirmado en MinIO) y
pasa a `active` al confirmar. Postgres permite añadir un valor a un ENUM dentro de
una transacción (PG12+) siempre que no se use en la misma transacción.

El downgrade no puede "quitar" un valor de un ENUM, así que recrea el tipo sin
`pending` (borrando antes las filas `pending`, que son subidas nunca completadas).

Revision ID: 4f5a6b7c8d9e
Revises: 3e4f5a6b7c8d
Create Date: 2026-06-23 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "4f5a6b7c8d9e"
down_revision: Union[str, None] = "3e4f5a6b7c8d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLES = ("files", "folders")


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("ALTER TYPE resource_status ADD VALUE IF NOT EXISTS 'pending'")


def downgrade() -> None:
    """Downgrade schema."""
    # Las subidas nunca confirmadas no tienen binario fiable: se descartan.
    op.execute("DELETE FROM files WHERE status = 'pending'")
    # El índice único parcial de raíz tiene un predicado sobre `status`; hay que
    # quitarlo antes de reescribir el tipo de la columna (si no, su predicado
    # compara la columna del tipo viejo con un literal del nuevo) y recrearlo.
    op.drop_index("uq_folders_single_root", table_name="folders")
    # Postgres no soporta quitar un valor de un ENUM: se recrea el tipo. Como el
    # tipo lo comparten `files` y `folders`, se reconvierten ambas columnas.
    op.execute("ALTER TYPE resource_status RENAME TO resource_status_old")
    op.execute(
        "CREATE TYPE resource_status AS ENUM ('active', 'trashed', 'deleted')"
    )
    for table in TABLES:
        op.execute(f"ALTER TABLE {table} ALTER COLUMN status DROP DEFAULT")
        op.execute(
            f"ALTER TABLE {table} ALTER COLUMN status TYPE resource_status "
            f"USING status::text::resource_status"
        )
        op.execute(
            f"ALTER TABLE {table} ALTER COLUMN status SET DEFAULT 'active'"
        )
    op.execute("DROP TYPE resource_status_old")
    op.create_index(
        "uq_folders_single_root",
        "folders",
        ["owner_id"],
        unique=True,
        postgresql_where=sa.text("parent_id IS NULL AND status = 'active'"),
    )
