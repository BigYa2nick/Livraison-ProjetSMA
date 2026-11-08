"""Dossier livreur : examen des pièces justificatives et motif de rejet (F-03).

- une seule pièce par type et par livreur ;
- type et taille du fichier, qui a examiné la pièce et quand ;
- un refus de pièce porte toujours un motif (contrainte dans la base) ;
- motif du rejet ou de la suspension d'un dossier.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("livreur", sa.Column("motif_rejet", sa.String(length=300), nullable=True))

    op.add_column("document_livreur", sa.Column("type_mime", sa.String(length=100), nullable=True))
    op.add_column("document_livreur", sa.Column("taille_octets", sa.Integer(), nullable=True))
    op.add_column(
        "document_livreur",
        sa.Column("examine_par_id", sa.Uuid(), sa.ForeignKey("utilisateur.id"), nullable=True),
    )
    op.add_column(
        "document_livreur", sa.Column("examine_le", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_unique_constraint(
        "uq_document_livreur_un_par_type", "document_livreur", ["livreur_id", "type"]
    )
    op.create_check_constraint(
        "motif_si_refuse", "document_livreur", "statut <> 'refuse' OR motif_refus IS NOT NULL"
    )


def downgrade() -> None:
    op.drop_constraint("motif_si_refuse", "document_livreur", type_="check")
    op.drop_constraint("uq_document_livreur_un_par_type", "document_livreur", type_="unique")
    op.drop_column("document_livreur", "examine_le")
    op.drop_column("document_livreur", "examine_par_id")
    op.drop_column("document_livreur", "taille_octets")
    op.drop_column("document_livreur", "type_mime")
    op.drop_column("livreur", "motif_rejet")
