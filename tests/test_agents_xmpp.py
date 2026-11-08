"""Les agents pour de vrai : SPADE, un vrai serveur XMPP, et (pour le dernier test) un vrai Redis.

Ignorés si le serveur XMPP ne tourne pas : `spade run --host localhost` dans un autre terminal.
Chaque test a ses propres adresses d'agents, pour ne jamais gêner un Annuaire de développement.
"""

import asyncio
import json
import uuid
from datetime import UTC, datetime
from functools import partial

import pytest
import pytest_asyncio
from spade.agent import Agent
from spade.behaviour import OneShotBehaviour

from aides_xmpp import verifier_xmpp
from livraison.agents import protocole as p
from livraison.agents.annuaire import AgentAnnuaire
from livraison.agents.gestionnaire import GestionnaireAgents
from livraison.agents.livreur import AgentLivreur, creer_agent_livreur
from livraison.agents.protocole import Inscription, Recherche
from livraison.config import parametres
from livraison.services import evenements
from livraison.services.positions import CANAL_POSITIONS
from livraison.services.redis_client import creer_redis

pytestmark = pytest.mark.asyncio

PLATEAU = (5.3236, -4.0197)


@pytest_asyncio.fixture
async def xmpp():
    await verifier_xmpp()  # ignoré si le serveur est éteint ; diagnostic net s'il est inutilisable


@pytest_asyncio.fixture
async def annuaire(xmpp):
    agent = AgentAnnuaire(
        parametres.jid(f"annuaire-test-{uuid.uuid4().hex[:8]}"), parametres.xmpp_mot_de_passe_agents
    )
    await agent.start(auto_register=True)
    yield agent
    await agent.stop()


@pytest_asyncio.fixture
async def questionneur(xmpp):
    agent = Questionneur(
        parametres.jid(f"questionneur-{uuid.uuid4().hex[:8]}"), parametres.xmpp_mot_de_passe_agents
    )
    await agent.start(auto_register=True)
    yield agent
    await agent.stop()


class Questionneur(Agent):
    """Agent de test : envoie un message et garde la réponse."""

    class Interroger(OneShotBehaviour):
        def __init__(self, message):
            super().__init__()
            self.message = message

        async def run(self):
            await self.send(self.message)
            self.agent.reponse = await self.receive(timeout=5)
            self.agent.recue.set()

    async def poser(self, message):
        self.reponse = None
        self.recue = asyncio.Event()
        self.add_behaviour(self.Interroger(message))
        await asyncio.wait_for(self.recue.wait(), 8)
        return self.reponse


async def attendre(condition, delai=6.0):
    fin = asyncio.get_running_loop().time() + delai
    while asyncio.get_running_loop().time() < fin:
        if condition():
            return True
        await asyncio.sleep(0.05)
    return condition()


async def lancer_livreur(annuaire, livreur_id=None, **options):
    livreur_id = livreur_id or f"t{uuid.uuid4().hex[:10]}"
    options.setdefault("position", PLATEAU)
    options.setdefault("mesure_le", datetime.now(UTC))
    agent = AgentLivreur(
        p.jid_livreur(livreur_id),
        parametres.xmpp_mot_de_passe_agents,
        livreur_id=livreur_id,
        type_vehicule=options.pop("type_vehicule", "moto"),
        capacite_kg=options.pop("capacite_kg", 25),
        annuaire_jid=str(annuaire.jid),
        **options,
    )
    await agent.start(auto_register=True)
    return agent


# ============================================================ inscription et recherche


async def test_un_agent_livreur_s_inscrit_a_l_annuaire(annuaire):
    agent = await lancer_livreur(annuaire)
    try:
        await asyncio.wait_for(agent.inscrit.wait(), 6)
        entree = annuaire.registre.obtenir(agent.livreur_id)
        assert entree.type_vehicule == "moto" and entree.capacite_kg == 25
        assert (entree.position.latitude, entree.position.longitude) == PLATEAU
    finally:
        await agent.stop()


async def test_recherche_par_message_entre_deux_agents(annuaire, questionneur):
    proche = await lancer_livreur(annuaire, position=(5.3237, -4.0197))
    loin = await lancer_livreur(annuaire, position=(5.45, -4.0197))
    velo = await lancer_livreur(annuaire, type_vehicule="velo", capacite_kg=8)
    try:
        for agent in (proche, loin, velo):
            await asyncio.wait_for(agent.inscrit.wait(), 6)
        question = p.creer_message(
            str(annuaire.jid),
            p.QUERY_REF,
            p.ONTO_RECHERCHE,
            Recherche(*PLATEAU, 3000.0).vers_contenu(),
        )
        reponse = await questionneur.poser(question)
        assert reponse is not None and reponse.get_metadata("performative") == p.INFORM
        contenu = json.loads(reponse.body)
        trouves = [c["livreur_id"] for c in contenu["candidats"]]
        assert contenu["total"] == 2 and set(trouves) == {proche.livreur_id, velo.livreur_id}
        assert loin.livreur_id not in trouves
        assert trouves[0] == proche.livreur_id or trouves[0] == velo.livreur_id
    finally:
        for agent in (proche, loin, velo):
            await agent.stop()


async def test_la_position_suit_le_livreur(annuaire):
    agent = await lancer_livreur(annuaire)
    try:
        await asyncio.wait_for(agent.inscrit.wait(), 6)
        agent.position_recue(5.33, -4.01, datetime.now(UTC))
        assert await attendre(
            lambda: annuaire.registre.obtenir(agent.livreur_id).position.latitude == 5.33
        )
        agent.changer_charge(2)
        assert await attendre(lambda: annuaire.registre.obtenir(agent.livreur_id).charge == 2)
    finally:
        await agent.stop()


# ============================================================ désinscription, oubli, panne


async def test_arret_propre_retire_le_livreur_de_l_annuaire(annuaire):
    agent = await lancer_livreur(annuaire)
    await asyncio.wait_for(agent.inscrit.wait(), 6)
    await agent.arreter()
    assert agent.desinscrit.is_set()
    assert agent.livreur_id not in annuaire.registre


async def test_l_agent_se_reinscrit_si_l_annuaire_l_a_oublie(annuaire):
    agent = await lancer_livreur(annuaire)
    try:
        await asyncio.wait_for(agent.inscrit.wait(), 6)
        annuaire.registre.desinscrire(agent.livreur_id)  # l'Annuaire a « redémarré »
        agent.annoncer_etat()  # le battement suivant
        assert await attendre(lambda: agent.livreur_id in annuaire.registre), "pas de réinscription"
    finally:
        await agent.stop()


async def test_un_agent_mort_sans_prevenir_est_purge_par_l_annuaire(xmpp, monkeypatch):
    # Réglages posés AVANT de démarrer les agents : ils lisent la configuration à leur démarrage.
    monkeypatch.setattr(parametres, "agents_battement_s", 0.2)
    monkeypatch.setattr(parametres, "annuaire_ttl_s", 0.7)
    annuaire = AgentAnnuaire(
        parametres.jid(f"annuaire-test-{uuid.uuid4().hex[:8]}"), parametres.xmpp_mot_de_passe_agents
    )
    await annuaire.start(auto_register=True)
    vivant = await lancer_livreur(annuaire)
    mort = await lancer_livreur(annuaire)
    try:
        await asyncio.wait_for(vivant.inscrit.wait(), 6)
        await asyncio.wait_for(mort.inscrit.wait(), 6)
        await mort.stop()  # coupure brutale : aucune désinscription
        assert await attendre(lambda: mort.livreur_id not in annuaire.registre, 6), "pas purgé"
        # L'agent vivant continue d'envoyer son battement : il n'est pas purgé.
        await asyncio.sleep(1.5)
        assert vivant.livreur_id in annuaire.registre
    finally:
        await vivant.stop()
        await annuaire.stop()


async def test_usurpation_d_identite_refusee(annuaire, questionneur):
    victime = f"v{uuid.uuid4().hex[:8]}"
    contenu = Inscription(victime, "moto", 25, 0, PLATEAU, datetime.now(UTC)).vers_contenu()
    reponse = await questionneur.poser(
        p.creer_message(str(annuaire.jid), p.REQUEST, p.ONTO_INSCRIPTION, contenu)
    )
    assert reponse.get_metadata("performative") == p.FAILURE
    assert json.loads(reponse.body)["motif"] == "identite"
    assert victime not in annuaire.registre


async def test_message_incomprehensible_recu_par_un_vrai_agent(annuaire, questionneur):
    message = p.creer_message(str(annuaire.jid), p.REQUEST, p.ONTO_INSCRIPTION)
    message.body = "ceci n'est pas du JSON"
    reponse = await questionneur.poser(message)
    assert reponse.get_metadata("performative") == p.NOT_UNDERSTOOD
    # et l'Annuaire fonctionne toujours
    agent = await lancer_livreur(annuaire)
    try:
        await asyncio.wait_for(agent.inscrit.wait(), 6)
    finally:
        await agent.stop()


# ============================================================ toute la chaîne


async def test_chaine_complete_redis_gestionnaire_agent_annuaire(annuaire, redis_test, monkeypatch):
    """Un livreur passe en ligne (événement Redis) : son agent apparaît dans l'Annuaire, suit sa
    position, puis disparaît quand il passe hors ligne."""
    monkeypatch.setattr(parametres, "agents_reconciliation_s", 3600)

    async def aucun_livreur():
        return []

    gestionnaire = GestionnaireAgents(
        fabrique=partial(creer_agent_livreur, annuaire_jid=str(annuaire.jid)),
        lire_en_ligne=aucun_livreur,
        creer_redis_fn=creer_redis,
        delai_grace_s=3600,
    )
    tache = asyncio.create_task(gestionnaire.executer())
    identifiant = f"c{uuid.uuid4().hex[:10]}"
    try:
        await asyncio.sleep(0.3)
        await evenements.publier(
            redis_test,
            evenements.LIVREUR_EN_LIGNE,
            livreur_id=identifiant,
            type_vehicule="moto",
            capacite_kg=25,
        )
        assert await attendre(lambda: identifiant in annuaire.registre, 8), (
            "livreur absent de l'Annuaire"
        )
        position = {
            "type": "position",
            "livreur_id": identifiant,
            "latitude": 5.34,
            "longitude": -4.0,
            "mesure_le": datetime.now(UTC).isoformat(),
        }
        await redis_test.publish(CANAL_POSITIONS, json.dumps(position))
        assert await attendre(
            lambda: (
                (e := annuaire.registre.obtenir(identifiant)) is not None
                and e.position is not None
                and e.position.latitude == 5.34
            )
        ), "la position n'est pas arrivée jusqu'à l'Annuaire"
        await evenements.publier(redis_test, evenements.LIVREUR_HORS_LIGNE, livreur_id=identifiant)
        assert await attendre(lambda: identifiant not in annuaire.registre, 8), (
            "livreur toujours présent"
        )
        assert identifiant not in gestionnaire.agents
    finally:
        await gestionnaire.arreter()
        tache.cancel()
        await asyncio.gather(tache, return_exceptions=True)