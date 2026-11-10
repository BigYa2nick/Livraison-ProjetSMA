"""Gestionnaire d'agents : création et retrait selon les événements, réconciliation avec la base."""

import asyncio
import json
import logging
import uuid
from datetime import UTC, datetime

import pytest

from livraison.agents.gestionnaire import (
    GestionnaireAgents,
    LivreurEnLigne,
    lire_livreurs_en_ligne,
)
from livraison.config import parametres
from livraison.persistance.enums import Role, TypeVehicule
from livraison.persistance.modeles import Utilisateur
from livraison.services import evenements, livreurs
from livraison.services.positions import CANAL_POSITIONS
from livraison.services.redis_client import creer_redis

pytestmark = pytest.mark.asyncio

L1 = LivreurEnLigne("livreur-1", "moto", 25)
L2 = LivreurEnLigne("livreur-2", "velo", 10)
MESURE = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)


class FauxAgent:
    def __init__(self, livreur_id, position=None, mesure_le=None, charge=0):
        self.livreur_id, self.position, self.mesure_le = livreur_id, position, mesure_le
        self.charge = charge
        self.positions: list[tuple] = []
        self.arrete = False
        self.erreur_a_l_arret = False

    def position_recue(self, latitude, longitude, mesure_le):
        self.positions.append((latitude, longitude, mesure_le))

    async def arreter(self):
        if self.erreur_a_l_arret:
            raise RuntimeError("arrêt cassé")
        self.arrete = True


class FauxRedisLecture:
    def __init__(self, positions=None):
        self.positions = positions or {}

    async def hgetall(self, cle):
        return self.positions.get(cle, {})

    async def aclose(self):
        pass


class Horloge:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class Banc:
    """Un gestionnaire avec de faux agents, une fausse base et une fausse horloge."""

    def __init__(self, en_ligne=(), positions=None, grace=30.0, redis_reel=False, charges=None):
        self.en_ligne = list(en_ligne)
        self.horloge = Horloge()
        self.crees: list[FauxAgent] = []
        self.refuser: set[str] = set()
        self.charges = charges or {}
        self.gestionnaire = GestionnaireAgents(
            fabrique=self.fabrique,
            lire_en_ligne=self.lire,
            lire_charge=self.lire_charge,
            creer_redis_fn=creer_redis if redis_reel else (lambda: FauxRedisLecture(positions)),
            horloge=self.horloge,
            delai_grace_s=grace,
        )

    async def fabrique(
        self, livreur_id, type_vehicule, capacite_kg, *, charge=0, position=None, mesure_le=None
    ):
        if livreur_id in self.refuser:
            raise ConnectionError("XMPP injoignable")
        agent = FauxAgent(livreur_id, position, mesure_le, charge)
        self.crees.append(agent)
        return agent

    async def lire(self):
        return list(self.en_ligne)

    async def lire_charge(self, livreur_id):
        return self.charges.get(livreur_id, 0)

    @property
    def agents(self):
        return self.gestionnaire.agents


def en_ligne(livreur_id="livreur-1", **extra):
    return {
        "type": evenements.LIVREUR_EN_LIGNE,
        "livreur_id": livreur_id,
        "type_vehicule": "moto",
        "capacite_kg": 25,
        **extra,
    }


# ============================================================ événements


async def test_livreur_en_ligne_cree_un_agent_une_seule_fois():
    b = Banc()
    await b.gestionnaire.traiter_evenement(en_ligne())
    await b.gestionnaire.traiter_evenement(en_ligne())  # annonce en double
    assert list(b.agents) == ["livreur-1"] and len(b.crees) == 1


async def test_agent_cree_avec_la_derniere_position_connue():
    cle = "position:livreur-1"
    redis = {cle: {"latitude": "5.33", "longitude": "-4.01", "mesure_le": MESURE.isoformat()}}
    b = Banc(positions=redis)
    await b.gestionnaire.traiter_evenement(en_ligne())
    assert b.crees[0].position == (5.33, -4.01) and b.crees[0].mesure_le == MESURE


async def test_reconciliation_reconstruit_la_charge_du_livreur():
    b = Banc(en_ligne=[L1], charges={"livreur-1": 2})
    assert await b.gestionnaire.reconcilier() == (["livreur-1"], [])
    assert b.crees[0].charge == 2


async def test_livreur_hors_ligne_retire_et_arrete_l_agent():
    b = Banc()
    await b.gestionnaire.traiter_evenement(en_ligne())
    agent = b.crees[0]
    await b.gestionnaire.traiter_evenement(
        {"type": evenements.LIVREUR_HORS_LIGNE, "livreur_id": "livreur-1"}
    )
    assert b.agents == {} and agent.arrete
    # un « hors ligne » pour un livreur sans agent n'est pas une erreur
    await b.gestionnaire.traiter_evenement(
        {"type": evenements.LIVREUR_HORS_LIGNE, "livreur_id": "autre"}
    )


async def test_un_agent_qui_s_arrete_mal_est_quand_meme_retire(caplog):
    b = Banc()
    await b.gestionnaire.traiter_evenement(en_ligne())
    b.crees[0].erreur_a_l_arret = True
    with caplog.at_level(logging.ERROR, logger="livraison.agents.gestionnaire"):
        await b.gestionnaire.traiter_evenement(
            {"type": evenements.LIVREUR_HORS_LIGNE, "livreur_id": "livreur-1"}
        )
    assert b.agents == {} and "arrêt incomplet" in caplog.text


async def test_agent_qui_ne_demarre_pas_ne_bloque_pas_les_autres(caplog):
    b = Banc()
    b.refuser = {"livreur-1"}
    with caplog.at_level(logging.ERROR, logger="livraison.agents.gestionnaire"):
        await b.gestionnaire.traiter_evenement(en_ligne("livreur-1"))
    await b.gestionnaire.traiter_evenement(en_ligne("livreur-2"))
    assert list(b.agents) == ["livreur-2"] and "démarrage impossible" in caplog.text


async def test_position_transmise_a_l_agent():
    b = Banc()
    await b.gestionnaire.traiter_evenement(en_ligne())
    await b.gestionnaire.traiter_evenement(
        {
            "type": "position",
            "livreur_id": "livreur-1",
            "latitude": 5.3,
            "longitude": -4.0,
            "mesure_le": MESURE.isoformat(),
        }
    )
    assert b.crees[0].positions == [(5.3, -4.0, MESURE)]


async def test_position_d_un_livreur_sans_agent_ou_illisible_est_ignoree(caplog):
    b = Banc()
    await b.gestionnaire.traiter_evenement(
        {
            "type": "position",
            "livreur_id": "fantome",
            "latitude": 5.3,
            "longitude": -4.0,
            "mesure_le": MESURE.isoformat(),
        }
    )
    await b.gestionnaire.traiter_evenement(en_ligne())
    with caplog.at_level(logging.WARNING, logger="livraison.agents.gestionnaire"):
        await b.gestionnaire.traiter_evenement(
            {"type": "position", "livreur_id": "livreur-1", "latitude": "x"}
        )
    assert b.crees[0].positions == [] and "illisible" in caplog.text


@pytest.mark.parametrize(
    "evenement",
    [
        {},
        {"type": evenements.LIVREUR_EN_LIGNE},  # sans livreur_id
        {"type": evenements.LIVREUR_EN_LIGNE, "livreur_id": "a"},  # sans véhicule
        {
            "type": evenements.LIVREUR_EN_LIGNE,
            "livreur_id": "a",
            "type_vehicule": "moto",
            "capacite_kg": 0,
        },
        {
            "type": evenements.LIVREUR_EN_LIGNE,
            "livreur_id": "a",
            "type_vehicule": 5,
            "capacite_kg": 10,
        },
        {"type": "inconnu", "livreur_id": "a"},
    ],
)
async def test_evenements_incomplets_ou_inconnus_sont_ignores(evenement):
    b = Banc()
    await b.gestionnaire.traiter_evenement(evenement)
    assert b.agents == {}


@pytest.mark.parametrize("brut", ["pas du json", "[1, 2]", "42", "null"])
async def test_message_redis_illisible_est_ignore(brut):
    b = Banc()
    await b.gestionnaire.traiter_message_brut("evenements", brut)
    assert b.agents == {}


# ============================================================ réconciliation avec la base


async def test_reconciliation_cree_les_manquants_et_retire_les_surnumeraires():
    b = Banc(en_ligne=[L1, L2])
    crees, retires = await b.gestionnaire.reconcilier()
    assert sorted(crees) == ["livreur-1", "livreur-2"] and retires == []
    b.en_ligne = [L2]  # livreur-1 est parti sans que l'événement n'arrive
    b.horloge.t += 31  # le délai de grâce est passé
    crees, retires = await b.gestionnaire.reconcilier()
    assert crees == [] and retires == ["livreur-1"] and list(b.agents) == ["livreur-2"]


async def test_delai_de_grace_protege_un_agent_cree_par_evenement():
    b = Banc(en_ligne=[])  # la base n'est pas encore à jour
    await b.gestionnaire.traiter_evenement(en_ligne())
    assert await b.gestionnaire.reconcilier() == ([], [])  # trop récent pour être retiré
    assert list(b.agents) == ["livreur-1"]
    b.horloge.t += 31
    assert await b.gestionnaire.reconcilier() == ([], ["livreur-1"])


async def test_delai_de_grace_protege_un_retrait_recent():
    b = Banc(en_ligne=[L1])  # la base dit encore « en ligne »
    await b.gestionnaire.traiter_evenement(en_ligne())
    await b.gestionnaire.traiter_evenement(
        {"type": evenements.LIVREUR_HORS_LIGNE, "livreur_id": "livreur-1"}
    )
    assert await b.gestionnaire.reconcilier() == ([], [])  # on ne le recrée pas aussitôt
    b.horloge.t += 31
    crees, _ = await b.gestionnaire.reconcilier()
    assert crees == ["livreur-1"]  # la base reste la vérité


async def test_arret_du_gestionnaire_retire_tous_les_agents():
    b = Banc()
    await b.gestionnaire.traiter_evenement(en_ligne("livreur-1"))
    await b.gestionnaire.traiter_evenement(en_ligne("livreur-2"))
    await b.gestionnaire.arreter()
    assert b.agents == {} and all(a.arrete for a in b.crees)


# ============================================================ base de données réelle


async def test_lecture_des_livreurs_en_ligne(session):
    utilisateurs = []
    for i, en_ligne_ in enumerate([True, False, True]):
        u = Utilisateur(telephone=f"+22598{i:06d}", role=Role.LIVREUR, nom="X", prenom="Y")
        session.add(u)
        await session.flush()
        livreur = await livreurs.definir_profil(
            session, u.id, TypeVehicule.VELO if i == 2 else TypeVehicule.MOTO, 10 + i, None
        )
        livreur.en_ligne = en_ligne_
        utilisateurs.append(u)
    await session.flush()
    trouves = {x.livreur_id: x for x in await lire_livreurs_en_ligne(session)}
    assert set(trouves) == {str(utilisateurs[0].id), str(utilisateurs[2].id)}
    assert trouves[str(utilisateurs[2].id)] == LivreurEnLigne(str(utilisateurs[2].id), "velo", 12)


# ============================================================ avec un vrai Redis


async def attendre(condition, delai=5.0):
    fin = asyncio.get_running_loop().time() + delai
    while asyncio.get_running_loop().time() < fin:
        if condition():
            return True
        await asyncio.sleep(0.05)
    return condition()


async def test_boucle_redis_de_bout_en_bout(redis_test, monkeypatch):
    monkeypatch.setattr(parametres, "agents_reconciliation_s", 3600)
    b = Banc(grace=3600, redis_reel=True)
    tache = asyncio.create_task(b.gestionnaire.executer())
    try:
        await asyncio.sleep(0.3)  # le temps de s'abonner
        identifiant = str(uuid.uuid4())
        await evenements.publier(
            redis_test,
            evenements.LIVREUR_EN_LIGNE,
            livreur_id=identifiant,
            type_vehicule="moto",
            capacite_kg=25,
        )
        assert await attendre(lambda: identifiant in b.agents), "l'agent n'a pas été créé"
        position = {
            "type": "position",
            "livreur_id": identifiant,
            "latitude": 5.31,
            "longitude": -4.02,
            "mesure_le": MESURE.isoformat(),
        }
        await redis_test.publish(CANAL_POSITIONS, json.dumps(position))
        agent = b.agents[identifiant]
        assert await attendre(lambda: agent.positions), "la position n'a pas été transmise"
        await evenements.publier(redis_test, evenements.LIVREUR_HORS_LIGNE, livreur_id=identifiant)
        assert await attendre(lambda: identifiant not in b.agents), "l'agent n'a pas été retiré"
        assert agent.arrete
    finally:
        await b.gestionnaire.arreter()
        tache.cancel()
        await asyncio.gather(tache, return_exceptions=True)
