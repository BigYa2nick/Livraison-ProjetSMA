"""Schéma initial : comptes, livreurs, commandes, offres, paiements, factures, journal.

Revision ID: 0001
Revises:
Create Date: 2026-10-11
"""

from collections.abc import Sequence

import geoalchemy2
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")
    op.create_table(
        "code_verification",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("telephone", sa.String(length=20), nullable=False),
        sa.Column("code_hash", sa.String(length=255), nullable=False),
        sa.Column("tentatives", sa.SmallInteger(), server_default="0", nullable=False),
        sa.Column("expire_le", sa.DateTime(timezone=True), nullable=False),
        sa.Column("utilise_le", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "cree_le", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.CheckConstraint(
            "tentatives >= 0", name=op.f("ck_code_verification_tentatives_positives")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_code_verification")),
    )
    op.create_index(
        "ix_code_verification_telephone_cree_le",
        "code_verification",
        ["telephone", "cree_le"],
        unique=False,
    )
    op.create_table(
        "tarif",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("nom", sa.String(length=100), nullable=False),
        sa.Column("part_fixe_xof", sa.Integer(), nullable=False),
        sa.Column("prix_km_xof", sa.Integer(), nullable=False),
        sa.Column("supplement_moyen_xof", sa.Integer(), server_default="0", nullable=False),
        sa.Column("supplement_grand_xof", sa.Integer(), server_default="0", nullable=False),
        sa.Column("supplement_urgent_xof", sa.Integer(), server_default="0", nullable=False),
        sa.Column("supplement_nuit_xof", sa.Integer(), server_default="0", nullable=False),
        sa.Column("nuit_debut_h", sa.SmallInteger(), server_default="22", nullable=False),
        sa.Column("nuit_fin_h", sa.SmallInteger(), server_default="6", nullable=False),
        sa.Column("actif", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "cree_le", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.CheckConstraint(
            "nuit_debut_h BETWEEN 0 AND 23 AND nuit_fin_h BETWEEN 0 AND 23",
            name=op.f("ck_tarif_heures_valides"),
        ),
        sa.CheckConstraint(
            "part_fixe_xof >= 0 AND prix_km_xof >= 0 AND supplement_moyen_xof >= 0 AND supplement_grand_xof >= 0 AND supplement_urgent_xof >= 0 AND supplement_nuit_xof >= 0",
            name=op.f("ck_tarif_montants_positifs"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tarif")),
    )
    op.create_index(
        "uq_tarif_un_seul_actif", "tarif", ["actif"], unique=True, postgresql_where=sa.text("actif")
    )
    op.create_table(
        "utilisateur",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("telephone", sa.String(length=20), nullable=False),
        sa.Column("nom", sa.String(length=100), nullable=True),
        sa.Column("prenom", sa.String(length=100), nullable=True),
        sa.Column(
            "role",
            sa.Enum(
                "client",
                "livreur",
                "repartiteur",
                "administrateur",
                name="role",
                native_enum=False,
                length=30,
            ),
            nullable=False,
        ),
        sa.Column(
            "etat",
            sa.Enum(
                "actif", "suspendu", "supprime", name="etatcompte", native_enum=False, length=30
            ),
            server_default="actif",
            nullable=False,
        ),
        sa.Column("mot_de_passe_hash", sa.String(length=255), nullable=True),
        sa.Column(
            "cree_le", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "maj_le", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("derniere_connexion_le", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "etat IN ('actif', 'suspendu', 'supprime')", name=op.f("ck_utilisateur_etatcompte")
        ),
        sa.CheckConstraint(
            "role IN ('client', 'livreur', 'repartiteur', 'administrateur')",
            name=op.f("ck_utilisateur_role"),
        ),
        sa.CheckConstraint(
            "telephone ~ '^\\+[0-9]{8,15}$'", name=op.f("ck_utilisateur_telephone_e164")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_utilisateur")),
        sa.UniqueConstraint("telephone", name=op.f("uq_utilisateur_telephone")),
    )
    op.create_table(
        "zone_service",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("nom", sa.String(length=100), nullable=False),
        sa.Column(
            "geometrie",
            geoalchemy2.types.Geography(
                geometry_type="MULTIPOLYGON",
                srid=4326,
                dimension=2,
                spatial_index=False,
                from_text="ST_GeogFromText",
                name="geography",
                nullable=False,
            ),
            nullable=False,
        ),
        sa.Column("active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "cree_le", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_zone_service")),
        sa.UniqueConstraint("nom", name=op.f("uq_zone_service_nom")),
    )
    op.create_index(
        "ix_zone_service_geometrie",
        "zone_service",
        ["geometrie"],
        unique=False,
        postgresql_using="gist",
    )
    op.create_table(
        "adresse",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("client_id", sa.Uuid(), nullable=False),
        sa.Column("libelle", sa.String(length=50), nullable=False),
        sa.Column("texte", sa.String(length=300), nullable=False),
        sa.Column(
            "localisation",
            geoalchemy2.types.Geography(
                geometry_type="POINT",
                srid=4326,
                dimension=2,
                spatial_index=False,
                from_text="ST_GeogFromText",
                name="geography",
                nullable=False,
            ),
            nullable=False,
        ),
        sa.Column(
            "cree_le", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["client_id"],
            ["utilisateur.id"],
            name=op.f("fk_adresse_client_id_utilisateur"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_adresse")),
    )
    op.create_index(op.f("ix_adresse_client_id"), "adresse", ["client_id"], unique=False)
    op.create_table(
        "livreur",
        sa.Column("utilisateur_id", sa.Uuid(), nullable=False),
        sa.Column(
            "type_vehicule",
            sa.Enum("moto", "voiture", "velo", name="typevehicule", native_enum=False, length=30),
            nullable=False,
        ),
        sa.Column("capacite_kg", sa.Integer(), nullable=False),
        sa.Column("immatriculation", sa.String(length=30), nullable=True),
        sa.Column(
            "statut_validation",
            sa.Enum(
                "en_attente",
                "valide",
                "rejete",
                "suspendu",
                name="statutvalidation",
                native_enum=False,
                length=30,
            ),
            server_default="en_attente",
            nullable=False,
        ),
        sa.Column("valide_par_id", sa.Uuid(), nullable=True),
        sa.Column("valide_le", sa.DateTime(timezone=True), nullable=True),
        sa.Column("en_ligne", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("note_moyenne", sa.Numeric(precision=3, scale=2), nullable=True),
        sa.Column("nb_evaluations", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "derniere_position",
            geoalchemy2.types.Geography(
                geometry_type="POINT",
                srid=4326,
                dimension=2,
                spatial_index=False,
                from_text="ST_GeogFromText",
                name="geography",
            ),
            nullable=True,
        ),
        sa.Column("position_maj_le", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "cree_le", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.CheckConstraint(
            "statut_validation IN ('en_attente', 'valide', 'rejete', 'suspendu')",
            name=op.f("ck_livreur_statutvalidation"),
        ),
        sa.CheckConstraint(
            "type_vehicule IN ('moto', 'voiture', 'velo')", name=op.f("ck_livreur_typevehicule")
        ),
        sa.CheckConstraint("capacite_kg > 0", name=op.f("ck_livreur_capacite_positive")),
        sa.CheckConstraint(
            "note_moyenne BETWEEN 0 AND 5", name=op.f("ck_livreur_note_entre_0_et_5")
        ),
        sa.ForeignKeyConstraint(
            ["utilisateur_id"],
            ["utilisateur.id"],
            name=op.f("fk_livreur_utilisateur_id_utilisateur"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["valide_par_id"], ["utilisateur.id"], name=op.f("fk_livreur_valide_par_id_utilisateur")
        ),
        sa.PrimaryKeyConstraint("utilisateur_id", name=op.f("pk_livreur")),
    )
    op.create_index(
        "ix_livreur_derniere_position",
        "livreur",
        ["derniere_position"],
        unique=False,
        postgresql_using="gist",
    )
    op.create_index(
        "ix_livreur_en_ligne",
        "livreur",
        ["en_ligne"],
        unique=False,
        postgresql_where=sa.text("en_ligne"),
    )
    op.create_table(
        "commande",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("client_id", sa.Uuid(), nullable=False),
        sa.Column("livreur_id", sa.Uuid(), nullable=True),
        sa.Column("tarif_id", sa.Uuid(), nullable=True),
        sa.Column(
            "etat",
            sa.Enum(
                "creee",
                "validee",
                "recherche_livreur",
                "attribuee",
                "enlevee",
                "en_livraison",
                "livree",
                "cloturee",
                "annulee",
                "echec_livraison",
                "litige",
                name="etatcommande",
                native_enum=False,
                length=30,
            ),
            server_default="creee",
            nullable=False,
        ),
        sa.Column(
            "priorite",
            sa.Enum("normale", "urgente", name="priorite", native_enum=False, length=30),
            server_default="normale",
            nullable=False,
        ),
        sa.Column("adresse_enlevement", sa.String(length=300), nullable=False),
        sa.Column(
            "enlevement",
            geoalchemy2.types.Geography(
                geometry_type="POINT",
                srid=4326,
                dimension=2,
                spatial_index=False,
                from_text="ST_GeogFromText",
                name="geography",
                nullable=False,
            ),
            nullable=False,
        ),
        sa.Column("adresse_livraison", sa.String(length=300), nullable=False),
        sa.Column(
            "livraison",
            geoalchemy2.types.Geography(
                geometry_type="POINT",
                srid=4326,
                dimension=2,
                spatial_index=False,
                from_text="ST_GeogFromText",
                name="geography",
                nullable=False,
            ),
            nullable=False,
        ),
        sa.Column("destinataire_nom", sa.String(length=100), nullable=False),
        sa.Column("destinataire_telephone", sa.String(length=20), nullable=False),
        sa.Column("distance_m", sa.Integer(), nullable=True),
        sa.Column("duree_estimee_s", sa.Integer(), nullable=True),
        sa.Column(
            "taille_colis",
            sa.Enum("petit", "moyen", "grand", name="taillecolis", native_enum=False, length=30),
            server_default="petit",
            nullable=False,
        ),
        sa.Column("poids_kg", sa.Numeric(precision=6, scale=2), nullable=True),
        sa.Column("description_colis", sa.String(length=300), nullable=True),
        sa.Column("consignes", sa.Text(), nullable=True),
        sa.Column("prix_xof", sa.Integer(), nullable=False),
        sa.Column("programmee_pour", sa.DateTime(timezone=True), nullable=True),
        sa.Column("tentatives_attribution", sa.SmallInteger(), server_default="0", nullable=False),
        sa.Column("rayon_recherche_m", sa.Integer(), nullable=True),
        sa.Column("code_confirmation_hash", sa.String(length=255), nullable=True),
        sa.Column("photo_preuve", sa.String(length=500), nullable=True),
        sa.Column(
            "cree_le", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("validee_le", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attribuee_le", sa.DateTime(timezone=True), nullable=True),
        sa.Column("enlevee_le", sa.DateTime(timezone=True), nullable=True),
        sa.Column("livree_le", sa.DateTime(timezone=True), nullable=True),
        sa.Column("terminee_le", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "maj_le", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.CheckConstraint(
            "etat IN ('creee', 'validee', 'recherche_livreur', 'attribuee', 'enlevee', 'en_livraison', 'livree', 'cloturee', 'annulee', 'echec_livraison', 'litige')",
            name=op.f("ck_commande_etatcommande"),
        ),
        sa.CheckConstraint(
            "etat NOT IN ('attribuee', 'enlevee', 'en_livraison') OR livreur_id IS NOT NULL",
            name=op.f("ck_commande_livreur_si_en_cours"),
        ),
        sa.CheckConstraint("priorite IN ('normale', 'urgente')", name=op.f("ck_commande_priorite")),
        sa.CheckConstraint(
            "taille_colis IN ('petit', 'moyen', 'grand')", name=op.f("ck_commande_taillecolis")
        ),
        sa.CheckConstraint("prix_xof >= 0", name=op.f("ck_commande_prix_positif")),
        sa.CheckConstraint(
            "tentatives_attribution >= 0", name=op.f("ck_commande_tentatives_positives")
        ),
        sa.ForeignKeyConstraint(
            ["client_id"], ["utilisateur.id"], name=op.f("fk_commande_client_id_utilisateur")
        ),
        sa.ForeignKeyConstraint(
            ["livreur_id"], ["livreur.utilisateur_id"], name=op.f("fk_commande_livreur_id_livreur")
        ),
        sa.ForeignKeyConstraint(
            ["tarif_id"], ["tarif.id"], name=op.f("fk_commande_tarif_id_tarif")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_commande")),
    )
    op.create_index(
        "ix_commande_client_id_cree_le", "commande", ["client_id", "cree_le"], unique=False
    )
    op.create_index(
        "ix_commande_enlevement", "commande", ["enlevement"], unique=False, postgresql_using="gist"
    )
    op.create_index(
        "ix_commande_etat_priorite_cree_le",
        "commande",
        ["etat", "priorite", "cree_le"],
        unique=False,
    )
    op.create_index("ix_commande_livreur_id_etat", "commande", ["livreur_id", "etat"], unique=False)
    op.create_table(
        "document_livreur",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("livreur_id", sa.Uuid(), nullable=False),
        sa.Column(
            "type",
            sa.Enum(
                "piece_identite",
                "permis",
                "carte_grise",
                "photo",
                name="typedocument",
                native_enum=False,
                length=30,
            ),
            nullable=False,
        ),
        sa.Column("chemin_fichier", sa.String(length=500), nullable=False),
        sa.Column(
            "statut",
            sa.Enum(
                "en_attente",
                "accepte",
                "refuse",
                name="statutdocument",
                native_enum=False,
                length=30,
            ),
            server_default="en_attente",
            nullable=False,
        ),
        sa.Column("motif_refus", sa.String(length=300), nullable=True),
        sa.Column(
            "cree_le", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.CheckConstraint(
            "statut IN ('en_attente', 'accepte', 'refuse')",
            name=op.f("ck_document_livreur_statutdocument"),
        ),
        sa.CheckConstraint(
            "type IN ('piece_identite', 'permis', 'carte_grise', 'photo')",
            name=op.f("ck_document_livreur_typedocument"),
        ),
        sa.ForeignKeyConstraint(
            ["livreur_id"],
            ["livreur.utilisateur_id"],
            name=op.f("fk_document_livreur_livreur_id_livreur"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_document_livreur")),
    )
    op.create_index(
        op.f("ix_document_livreur_livreur_id"), "document_livreur", ["livreur_id"], unique=False
    )
    op.create_table(
        "position",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("livreur_id", sa.Uuid(), nullable=False),
        sa.Column(
            "localisation",
            geoalchemy2.types.Geography(
                geometry_type="POINT",
                srid=4326,
                dimension=2,
                spatial_index=False,
                from_text="ST_GeogFromText",
                name="geography",
                nullable=False,
            ),
            nullable=False,
        ),
        sa.Column("precision_m", sa.Float(), nullable=True),
        sa.Column("enregistre_le", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "recu_le", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["livreur_id"],
            ["livreur.utilisateur_id"],
            name=op.f("fk_position_livreur_id_livreur"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_position")),
    )
    op.create_index(
        "ix_position_livreur_id_enregistre_le",
        "position",
        ["livreur_id", "enregistre_le"],
        unique=False,
    )
    op.create_table(
        "evaluation",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("commande_id", sa.Uuid(), nullable=False),
        sa.Column("client_id", sa.Uuid(), nullable=False),
        sa.Column("livreur_id", sa.Uuid(), nullable=False),
        sa.Column("note", sa.SmallInteger(), nullable=False),
        sa.Column("commentaire", sa.String(length=500), nullable=True),
        sa.Column(
            "cree_le", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.CheckConstraint("note BETWEEN 1 AND 5", name=op.f("ck_evaluation_note_entre_1_et_5")),
        sa.ForeignKeyConstraint(
            ["client_id"], ["utilisateur.id"], name=op.f("fk_evaluation_client_id_utilisateur")
        ),
        sa.ForeignKeyConstraint(
            ["commande_id"], ["commande.id"], name=op.f("fk_evaluation_commande_id_commande")
        ),
        sa.ForeignKeyConstraint(
            ["livreur_id"],
            ["livreur.utilisateur_id"],
            name=op.f("fk_evaluation_livreur_id_livreur"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_evaluation")),
        sa.UniqueConstraint("commande_id", name=op.f("uq_evaluation_commande_id")),
    )
    op.create_index(op.f("ix_evaluation_livreur_id"), "evaluation", ["livreur_id"], unique=False)
    op.create_table(
        "evenement",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("commande_id", sa.Uuid(), nullable=True),
        sa.Column(
            "type",
            sa.Enum(
                "changement_etat",
                "message_agent",
                "incident",
                "paiement",
                "notification",
                "action_manuelle",
                name="typeevenement",
                native_enum=False,
                length=30,
            ),
            nullable=False,
        ),
        sa.Column("auteur", sa.String(length=100), nullable=False),
        sa.Column("destinataire", sa.String(length=100), nullable=True),
        sa.Column("performative", sa.String(length=30), nullable=True),
        sa.Column(
            "detail",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "cree_le", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.CheckConstraint(
            "type IN ('changement_etat', 'message_agent', 'incident', 'paiement', 'notification', 'action_manuelle')",
            name=op.f("ck_evenement_typeevenement"),
        ),
        sa.ForeignKeyConstraint(
            ["commande_id"], ["commande.id"], name=op.f("fk_evenement_commande_id_commande")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_evenement")),
    )
    op.create_index(
        "ix_evenement_commande_id_cree_le", "evenement", ["commande_id", "cree_le"], unique=False
    )
    op.create_index("ix_evenement_type_cree_le", "evenement", ["type", "cree_le"], unique=False)
    op.create_table(
        "facture",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("numero", sa.Integer(), sa.Identity(always=False), nullable=False),
        sa.Column("commande_id", sa.Uuid(), nullable=False),
        sa.Column("montant_xof", sa.Integer(), nullable=False),
        sa.Column("tva_xof", sa.Integer(), server_default="0", nullable=False),
        sa.Column("chemin_pdf", sa.String(length=500), nullable=True),
        sa.Column(
            "emise_le", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.CheckConstraint(
            "montant_xof >= 0 AND tva_xof >= 0", name=op.f("ck_facture_montants_positifs")
        ),
        sa.ForeignKeyConstraint(
            ["commande_id"], ["commande.id"], name=op.f("fk_facture_commande_id_commande")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_facture")),
        sa.UniqueConstraint("commande_id", name=op.f("uq_facture_commande_id")),
        sa.UniqueConstraint("numero", name=op.f("uq_facture_numero")),
    )
    op.create_table(
        "offre",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("commande_id", sa.Uuid(), nullable=False),
        sa.Column("livreur_id", sa.Uuid(), nullable=False),
        sa.Column("tour", sa.SmallInteger(), server_default="1", nullable=False),
        sa.Column("cout", sa.Float(), nullable=True),
        sa.Column("eta_s", sa.Integer(), nullable=True),
        sa.Column(
            "resultat",
            sa.Enum(
                "proposee",
                "retenue",
                "rejetee",
                "refusee",
                "declinee",
                "expiree",
                name="resultatoffre",
                native_enum=False,
                length=30,
            ),
            server_default="proposee",
            nullable=False,
        ),
        sa.Column("motif_refus", sa.String(length=200), nullable=True),
        sa.Column(
            "cree_le", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("decide_le", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "resultat IN ('proposee', 'retenue', 'rejetee', 'refusee', 'declinee', 'expiree')",
            name=op.f("ck_offre_resultatoffre"),
        ),
        sa.CheckConstraint("tour >= 1", name=op.f("ck_offre_tour_positif")),
        sa.ForeignKeyConstraint(
            ["commande_id"],
            ["commande.id"],
            name=op.f("fk_offre_commande_id_commande"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["livreur_id"], ["livreur.utilisateur_id"], name=op.f("fk_offre_livreur_id_livreur")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_offre")),
        sa.UniqueConstraint(
            "commande_id", "tour", "livreur_id", name="uq_offre_commande_tour_livreur"
        ),
    )
    op.create_index(op.f("ix_offre_commande_id"), "offre", ["commande_id"], unique=False)
    op.create_index(
        "uq_offre_une_retenue_par_tour",
        "offre",
        ["commande_id", "tour"],
        unique=True,
        postgresql_where=sa.text("resultat = 'retenue'"),
    )
    op.create_table(
        "paiement",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("commande_id", sa.Uuid(), nullable=False),
        sa.Column(
            "mode",
            sa.Enum(
                "mobile_money",
                "carte",
                "especes",
                name="modepaiement",
                native_enum=False,
                length=30,
            ),
            nullable=False,
        ),
        sa.Column("montant_xof", sa.Integer(), nullable=False),
        sa.Column("montant_rembourse_xof", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "etat",
            sa.Enum(
                "en_attente",
                "confirme",
                "echoue",
                "rembourse",
                name="etatpaiement",
                native_enum=False,
                length=30,
            ),
            server_default="en_attente",
            nullable=False,
        ),
        sa.Column("reference_prestataire", sa.String(length=100), nullable=True),
        sa.Column(
            "cree_le", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("confirme_le", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "etat IN ('en_attente', 'confirme', 'echoue', 'rembourse')",
            name=op.f("ck_paiement_etatpaiement"),
        ),
        sa.CheckConstraint(
            "mode IN ('mobile_money', 'carte', 'especes')", name=op.f("ck_paiement_modepaiement")
        ),
        sa.CheckConstraint(
            "montant_rembourse_xof BETWEEN 0 AND montant_xof",
            name=op.f("ck_paiement_remboursement_valide"),
        ),
        sa.CheckConstraint("montant_xof >= 0", name=op.f("ck_paiement_montant_positif")),
        sa.ForeignKeyConstraint(
            ["commande_id"], ["commande.id"], name=op.f("fk_paiement_commande_id_commande")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_paiement")),
        sa.UniqueConstraint(
            "reference_prestataire", name=op.f("uq_paiement_reference_prestataire")
        ),
    )
    op.create_index(op.f("ix_paiement_commande_id"), "paiement", ["commande_id"], unique=False)
    op.create_index(
        "uq_paiement_un_confirme_par_commande",
        "paiement",
        ["commande_id"],
        unique=True,
        postgresql_where=sa.text("etat = 'confirme'"),
    )

    # Journal en ajout seul (NF-06, NF-09) : aucune ligne ne peut être modifiée ni supprimée.
    op.execute(
        """
        CREATE FUNCTION interdire_modification_journal() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'La table % est en ajout seul (UPDATE et DELETE interdits)', TG_TABLE_NAME;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER evenement_ajout_seul
        BEFORE UPDATE OR DELETE ON evenement
        FOR EACH ROW EXECUTE FUNCTION interdire_modification_journal()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS evenement_ajout_seul ON evenement")
    op.execute("DROP FUNCTION IF EXISTS interdire_modification_journal()")
    op.drop_index(
        "uq_paiement_un_confirme_par_commande",
        table_name="paiement",
        postgresql_where=sa.text("etat = 'confirme'"),
    )
    op.drop_index(op.f("ix_paiement_commande_id"), table_name="paiement")
    op.drop_table("paiement")
    op.drop_index(
        "uq_offre_une_retenue_par_tour",
        table_name="offre",
        postgresql_where=sa.text("resultat = 'retenue'"),
    )
    op.drop_index(op.f("ix_offre_commande_id"), table_name="offre")
    op.drop_table("offre")
    op.drop_table("facture")
    op.drop_index("ix_evenement_type_cree_le", table_name="evenement")
    op.drop_index("ix_evenement_commande_id_cree_le", table_name="evenement")
    op.drop_table("evenement")
    op.drop_index(op.f("ix_evaluation_livreur_id"), table_name="evaluation")
    op.drop_table("evaluation")
    op.drop_index("ix_position_livreur_id_enregistre_le", table_name="position")
    op.drop_table("position")
    op.drop_index(op.f("ix_document_livreur_livreur_id"), table_name="document_livreur")
    op.drop_table("document_livreur")
    op.drop_index("ix_commande_livreur_id_etat", table_name="commande")
    op.drop_index("ix_commande_etat_priorite_cree_le", table_name="commande")
    op.drop_index("ix_commande_enlevement", table_name="commande", postgresql_using="gist")
    op.drop_index("ix_commande_client_id_cree_le", table_name="commande")
    op.drop_table("commande")
    op.drop_index("ix_livreur_en_ligne", table_name="livreur", postgresql_where=sa.text("en_ligne"))
    op.drop_index("ix_livreur_derniere_position", table_name="livreur", postgresql_using="gist")
    op.drop_table("livreur")
    op.drop_index(op.f("ix_adresse_client_id"), table_name="adresse")
    op.drop_table("adresse")
    op.drop_index("ix_zone_service_geometrie", table_name="zone_service", postgresql_using="gist")
    op.drop_table("zone_service")
    op.drop_table("utilisateur")
    op.drop_index("uq_tarif_un_seul_actif", table_name="tarif", postgresql_where=sa.text("actif"))
    op.drop_table("tarif")
    op.drop_index("ix_code_verification_telephone_cree_le", table_name="code_verification")
    op.drop_table("code_verification")