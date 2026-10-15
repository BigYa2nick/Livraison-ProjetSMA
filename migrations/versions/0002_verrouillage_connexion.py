"""Verrouillage des comptes après trop d'échecs de connexion (NF-05).

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "utilisateur",
        sa.Column("echecs_connexion", sa.SmallInteger(), server_default="0", nullable=False),
    )
    op.add_column(
        "utilisateur", sa.Column("verrouille_jusqu_le", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("utilisateur", "verrouille_jusqu_le")
    op.drop_column("utilisateur", "echecs_connexion")
