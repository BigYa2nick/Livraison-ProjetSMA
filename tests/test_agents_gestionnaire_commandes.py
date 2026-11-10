"""Gestionnaires : agents Commande (création, retrait, réconciliation) et réponses des livreurs."""

import asyncio
import json

import pytest

from livraison.agents.gestionnaire import GestionnaireAgents
from livraison.agents.gestionnaire_commandes import GestionnaireCommandes
from livraison.services import evenements

pytestmark = pytest.mark.asyncio


class FauxAgentCommande:
    def __init__(self, commande_id):
        self.commande_id = commande_id
        self.fini = asyncio.Event()
        self.issue = None
        self.arrete = False

    async def arreter(self):
        self.arrete = True


class Atelier:
    """Un gestionnaire de commandes avec une base et une horloge factices."""

    def __init__(self, delai_grace_s=10.0):
        self.a_attribuer: list[str] = []
        self.crees: dict[str, FauxAgentCommande] = {}
        self.maintenant = 100.0
        self.echecs: set[str] = set()
        self.gestionnaire = GestionnaireCommandes(
            fabrique=self._fabrique,
            lire_a_attribuer=self._lire,
            horloge=lambda: self.maintenant,
            delai_grace_s=delai_grace_s,
        )

    async def _fabrique(self, commande_id):
        if commande_id in self.echecs:
            raise RuntimeError("XMPP injoignable")
        agent = FauxAgentCommande(commande_id)
        self.crees[commande_id] = agent
        return agent

    async def _lire(self):
        return list(self.a_attribuer)

    async def laisser_tourner(self):
        for _ in range(3):
            await asyncio.sleep(0)


@pytest.fixture
def atelier():
    return Atelier()


async def test_commande_validee_cree_l_agent(atelier):
    await atelier.gestionnaire.traiter_evenement({"type": evenements.COMMANDE_VALIDEE, "commande_id": "c1"})
    assert list(atelier.gestionnaire.agents) == ["c1"]
    await atelier.gestionnaire.traiter_evenement({"type": evenements.COMMANDE_VALIDEE, "commande_id": "c1"})
    assert len(atelier.crees) == 1  # un doublon ne crée rien


@pytest.mark.parametrize(
    "evenement",
    [{}, {"type": "livreur_en_ligne", "livreur_id": "x"}, {"type": evenements.COMMANDE_VALIDEE},
     {"type": evenements.COMMANDE_VALIDEE, "commande_id": ""}, {"type": evenements.COMMANDE_VALIDEE, "commande_id": 5}],
)
async def test_evenements_qui_ne_creent_rien(atelier, evenement):
    await atelier.gestionnaire.traiter_evenement(evenement)
    assert not atelier.gestionnaire.agents


async def test_un_agent_qui_ne_demarre_pas_n_arrete_pas_les_autres(atelier):
    atelier.echecs.add("mauvaise")
    atelier.a_attribuer = ["mauvaise", "bonne"]
    crees, _ = await atelier.gestionnaire.reconcilier()
    assert crees == ["bonne"] and list(atelier.gestionnaire.agents) == ["bonne"]


async def test_un_agent_qui_a_fini_est_retire(atelier):
    await atelier.gestionnaire.creer("c1")
    agent = atelier.crees["c1"]
    agent.issue = "attribuee"
    agent.fini.set()
    await atelier.laisser_tourner()
    assert "c1" not in atelier.gestionnaire.agents and agent.arrete


async def test_reconciliation_cree_les_commandes_qui_attendent_un_livreur(atelier):
    atelier.a_attribuer = ["c1", "c2"]
    crees, retires = await atelier.gestionnaire.reconcilier()
    assert (sorted(crees), retires) == (["c1", "c2"], [])
    assert await atelier.gestionnaire.reconcilier() == ([], [])  # idempotente


async def test_reconciliation_retire_les_agents_dont_la_commande_n_attend_plus(atelier):
    atelier.a_attribuer = ["c1", "c2"]
    await atelier.gestionnaire.reconcilier()
    atelier.a_attribuer = ["c2"]
    atelier.maintenant += 60  # le délai de grâce est passé
    _, retires = await atelier.gestionnaire.reconcilier()
    assert retires == ["c1"] and atelier.crees["c1"].arrete and not atelier.crees["c2"].arrete


async def test_delai_de_grace_protege_un_agent_cree_par_evenement(atelier):
    """L'événement arrive avant que la base ne reflète la commande : la réconciliation n'y touche pas."""
    await atelier.gestionnaire.creer("c1")  # la base ne la liste pas encore
    atelier.a_attribuer = []
    atelier.maintenant += 5
    assert await atelier.gestionnaire.reconcilier() == ([], [])
    atelier.maintenant += 6
    _, retires = await atelier.gestionnaire.reconcilier()
    assert retires == ["c1"]


async def test_arret_du_gestionnaire(atelier):
    atelier.a_attribuer = ["c1", "c2"]
    await atelier.gestionnaire.reconcilier()
    await atelier.gestionnaire.arreter()
    assert not atelier.gestionnaire.agents and all(a.arrete for a in atelier.crees.values())


# ======================================================== le gestionnaire des livreurs


class FauxLivreur:
    def __init__(self):
        self.reponses = []
        self.arrete = False

    def position_recue(self, *args): ...

    def confirmation_recue(self, commande_id, accepte):
        self.reponses.append((commande_id, accepte))
        return True

    async def arreter(self):
        self.arrete = True


@pytest.fixture
def livreurs():
    agents = {}

    async def fabrique(
        livreur_id, type_vehicule, capacite_kg, *, charge=0, position=None, mesure_le=None
    ):
        agents[livreur_id] = FauxLivreur()
        return agents[livreur_id]

    async def lecture():
        return []

    async def lire_charge(_):
        return 0

    gestionnaire = GestionnaireAgents(
        fabrique=fabrique, lire_en_ligne=lecture, lire_charge=lire_charge, delai_grace_s=0
    )
    return gestionnaire, agents


async def test_les_reponses_du_livreur_arrivent_a_son_agent(livreurs):
    gestionnaire, agents = livreurs
    await gestionnaire.traiter_evenement(
        {"type": "livreur_en_ligne", "livreur_id": "L1", "type_vehicule": "moto", "capacite_kg": 20}
    )
    await gestionnaire.traiter_evenement({"type": evenements.COURSE_CONFIRMEE, "livreur_id": "L1", "commande_id": "c1"})
    await gestionnaire.traiter_message_brut(
        "evenements",
        json.dumps({"type": evenements.COURSE_DECLINEE, "livreur_id": "L1", "commande_id": "c2"}),
    )
    assert agents["L1"].reponses == [("c1", True), ("c2", False)]


@pytest.mark.parametrize(
    "evenement",
    [
        {"type": evenements.COURSE_CONFIRMEE, "livreur_id": "inconnu", "commande_id": "c1"},  # pas d'agent
        {"type": evenements.COURSE_CONFIRMEE, "livreur_id": "L1"},  # pas de commande
        {"type": evenements.COURSE_DECLINEE, "livreur_id": "L1", "commande_id": 12},
    ],
)
async def test_reponses_inutilisables_sont_ignorees(livreurs, evenement):
    gestionnaire, agents = livreurs
    await gestionnaire.traiter_evenement(
        {"type": "livreur_en_ligne", "livreur_id": "L1", "type_vehicule": "moto", "capacite_kg": 20}
    )
    await gestionnaire.traiter_evenement(evenement)
    assert agents["L1"].reponses == []


async def test_les_annonces_des_agents_sont_ignorees_sans_bruit(livreurs, caplog):
    gestionnaire, _ = livreurs
    for type_ in evenements.EVENEMENTS_DES_AGENTS:
        await gestionnaire.traiter_evenement({"type": type_, "commande_id": "c1"})  # sans livreur_id
    assert "sans livreur_id" not in caplog.text


async def test_commande_validee_est_transmis_au_gestionnaire_des_commandes():
    atelier = Atelier()

    async def lecture():
        return []

    gestionnaire = GestionnaireAgents(lire_en_ligne=lecture, commandes=atelier.gestionnaire)
    await gestionnaire.traiter_evenement({"type": evenements.COMMANDE_VALIDEE, "commande_id": "c9"})
    assert list(atelier.gestionnaire.agents) == ["c9"]
    # la réconciliation des livreurs déclenche celle des commandes
    atelier.a_attribuer = ["c9", "c10"]
    await gestionnaire.reconcilier()
    assert sorted(atelier.gestionnaire.agents) == ["c10", "c9"]
    await gestionnaire.arreter()
    assert not atelier.gestionnaire.agents


async def test_sans_gestionnaire_de_commandes_l_evenement_est_ignore():
    async def lecture():
        return []

    gestionnaire = GestionnaireAgents(lire_en_ligne=lecture)
    await gestionnaire.traiter_evenement({"type": evenements.COMMANDE_VALIDEE, "commande_id": "c9"})  # sans effet
    assert await gestionnaire.reconcilier() == ([], [])
