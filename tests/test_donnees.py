"""Tests de structure du modèle de données (sans base de données)."""

from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from livraison.persistance import Base
from livraison.persistance.enums import ETATS_FINAUX, EtatCommande

TABLES_ATTENDUES = {
    "utilisateur", "code_verification", "livreur", "document_livreur", "position",
    "adresse", "zone_service", "tarif", "commande", "offre", "paiement", "facture",
    "evaluation", "evenement",
}  # fmt: skip


def test_toutes_les_tables_sont_declarees():
    assert set(Base.metadata.tables) == TABLES_ATTENDUES


def test_chaque_table_a_une_cle_primaire():
    for nom, table in Base.metadata.tables.items():
        assert table.primary_key.columns, f"{nom} n'a pas de clé primaire"


def test_le_ddl_postgresql_se_genere_pour_toutes_les_tables():
    for table in Base.metadata.sorted_tables:
        ddl = str(CreateTable(table).compile(dialect=postgresql.dialect()))
        assert table.name in ddl


def test_les_montants_sont_des_entiers_en_xof():
    colonnes_montant = [
        (t.name, c.name)
        for t in Base.metadata.tables.values()
        for c in t.columns
        if c.name.endswith("_xof")
    ]
    assert colonnes_montant, "aucune colonne de montant trouvée"
    for table, colonne in colonnes_montant:
        assert Base.metadata.tables[table].c[colonne].type.python_type is int


def test_toutes_les_contraintes_ont_un_nom():
    for table in Base.metadata.tables.values():
        for contrainte in table.constraints:
            assert contrainte.name, f"contrainte sans nom dans {table.name}"


def test_le_cycle_de_vie_couvre_le_cahier_des_charges():
    # § 3.4 : 8 états du parcours normal + Annulée, Échec de livraison, Litige.
    assert len(EtatCommande) == 11
    assert ETATS_FINAUX <= set(EtatCommande)
    assert EtatCommande.LIVREE not in ETATS_FINAUX  # une commande livrée doit encore être clôturée