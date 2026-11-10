"""Priorité urgente (F-35) : la règle, le tri de la file d'attente et l'ordre de création des agents."""

import asyncio
from datetime import UTC, datetime

import pytest

from aides_commandes import creer_commande, creer_livreur
from livraison.agents.gestionnaire_commandes import GestionnaireCommandes
from livraison.modele import attribution as regles
from livraison.persistance.enums import EtatCommande, Priorite
from livraison.services import attribution as svc


def _a(heure: int, minute: int = 0) -> datetime:
    return datetime(2026, 11, 8, heure, minute, tzinfo=UTC)


# ---------------------------------------------------------------------------------------- la règle


@pytest.mark.parametrize(
    ("priorite", "rang"),
    [
        ("urgente", 0),
        (Priorite.URGENTE, 0),
        ("normale", 1),
        (Priorite.NORMALE, 1),
        (None, 1),
        ("inconnue", 1),
    ],
)
def test_rang_de_priorite(priorite, rang):
    assert regles.rang_priorite(priorite) == rang


def test_une_urgente_a_un_rang_plus_petit_qu_une_normale():
    assert regles.rang_priorite(Priorite.URGENTE) < regles.rang_priorite(Priorite.NORMALE)


# ----------------------------------------------------------------------- le tri de la file (base)


async def test_la_file_met_les_urgentes_d_abord_puis_les_plus_anciennes(session):
    normale_ancienne = await creer_commande(session, cree_le=_a(10, 0))
    normale_recente = await creer_commande(session, cree_le=_a(10, 5))
    urgente_recente = await creer_commande(session, priorite=Priorite.URGENTE, cree_le=_a(10, 10))
    urgente_ancienne = await creer_commande(session, priorite=Priorite.URGENTE, cree_le=_a(9, 0))
    les_notres = {normale_ancienne, normale_recente, urgente_recente, urgente_ancienne}

    file = [i for i in await svc.lire_commandes_a_attribuer(session) if i in les_notres]

    assert file == [urgente_ancienne, urgente_recente, normale_ancienne, normale_recente]


async def test_la_file_ignore_toujours_les_commandes_qui_n_attendent_plus(session):
    livreur = await creer_livreur(session)  # une commande attribuée a toujours son livreur (contrainte)
    attribuee = await creer_commande(
        session, priorite=Priorite.URGENTE, etat=EtatCommande.ATTRIBUEE, livreur_id=livreur
    )
    en_attente = await creer_commande(session, priorite=Priorite.URGENTE)

    file = await svc.lire_commandes_a_attribuer(session)

    assert en_attente in file and attribuee not in file


async def test_le_tri_sql_suit_la_regle_pure(session):
    """Le tri de la base et `rang_priorite` doivent toujours dire la même chose."""
    ids = {
        Priorite.NORMALE: await creer_commande(session, priorite=Priorite.NORMALE, cree_le=_a(8)),
        Priorite.URGENTE: await creer_commande(session, priorite=Priorite.URGENTE, cree_le=_a(11)),
    }
    file = [i for i in await svc.lire_commandes_a_attribuer(session) if i in ids.values()]
    par_rang = sorted(ids, key=regles.rang_priorite)
    assert file == [ids[p] for p in par_rang]


# ------------------------------------------- l'ordre de création des agents Commande (réconciliation)


class _FauxAgentCommande:
    def __init__(self):
        self.fini = asyncio.Event()
        self.issue = None

    async def arreter(self):
        self.fini.set()


async def test_la_reconciliation_cree_les_agents_dans_l_ordre_de_la_file():
    crees: list[str] = []

    async def fabrique(commande_id):
        crees.append(commande_id)
        return _FauxAgentCommande()

    async def lecture():
        return ["urgente", "normale-1", "normale-2"]  # l'ordre rendu par la base (F-35)

    gestionnaire = GestionnaireCommandes(fabrique=fabrique, lire_a_attribuer=lecture, delai_grace_s=0.0)
    try:
        await gestionnaire.reconcilier()
        assert crees == ["urgente", "normale-1", "normale-2"]
    finally:
        await gestionnaire.arreter()
