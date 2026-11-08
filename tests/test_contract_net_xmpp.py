"""Le Contract Net pour de bon : agents SPADE, serveur XMPP réel, base PostgreSQL réelle.

Annuaire, Dispatcher, agent Commande et agents Livreur échangent de vrais messages XMPP. Seuls OSRM
(un faux serveur de routes), Redis (un faux) et le livreur humain (le test) sont simulés.
Ignorés si le serveur XMPP ne tourne pas : `spade run --host localhost` dans un autre terminal.
"""

import asyncio
import re
import uuid
from datetime import UTC, datetime

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select

from aides_commandes import FauxRedis, SessionPartagee, creer_commande, creer_livreur
from aides_xmpp import verifier_xmpp
from livraison.agents import protocole as p
from livraison.agents.annuaire import AgentAnnuaire
from livraison.agents.commande import ABANDONNEE, ATTRIBUEE, EPUISEE, AgentCommande
from livraison.agents.dispatcher import AgentDispatcher
from livraison.agents.livreur import AgentLivreur
from livraison.config import parametres
from livraison.persistance.enums import EtatCommande, ResultatOffre
from livraison.persistance.modeles import Commande, Offre
from livraison.services import evenements, itineraires
from livraison.services import attribution as svc

pytestmark = pytest.mark.asyncio

PLATEAU = (5.3236, -4.0197)
VITESSE_MS = 8.0  # le faux OSRM roule à 8 m/s (29 km/h) en ligne droite


def decaler(nord_m: float) -> tuple[float, float]:
    """Un point à `nord_m` mètres au nord du Plateau."""
    return (PLATEAU[0] + nord_m / 111_195.0, PLATEAU[1])


def faux_osrm(requete: httpx.Request) -> httpx.Response:
    """Répond comme OSRM : durée = distance à vol d'oiseau / vitesse."""
    coordonnees = re.search(r"/route/v1/\w+/([^?]+)", requete.url.path)
    (lon1, lat1), (lon2, lat2) = (map(float, c.split(",")) for c in coordonnees.group(1).split(";"))
    distance = itineraires.distance_vol_d_oiseau_m(
        itineraires.Point(lat1, lon1), itineraires.Point(lat2, lon2)
    )
    return httpx.Response(
        200,
        json={"code": "Ok", "routes": [{"distance": distance, "duration": distance / VITESSE_MS}]},
    )


@pytest_asyncio.fixture
async def xmpp():
    await verifier_xmpp()  # ignoré si le serveur est éteint ; diagnostic net s'il est inutilisable


@pytest.fixture(autouse=True)
def reglages(monkeypatch):
    monkeypatch.setattr(parametres, "attribution_collecte_s", 2.0)
    monkeypatch.setattr(parametres, "attribution_annuaire_attente_s", 2.0)
    monkeypatch.setattr(parametres, "attribution_confirmation_s", 3.0)
    monkeypatch.setattr(parametres, "attribution_marge_s", 1.0)
    monkeypatch.setattr(parametres, "attribution_pause_s", 0.1)
    monkeypatch.setattr(parametres, "attribution_tours_max", 4)
    monkeypatch.setattr(parametres, "attribution_penalite_charge_s", 300.0)
    itineraires.utiliser_client(httpx.AsyncClient(transport=httpx.MockTransport(faux_osrm)))
    yield
    itineraires.utiliser_client(None)


class Plateforme:
    """Annuaire + Dispatcher + livreurs + commandes, tous connectés au vrai serveur XMPP."""

    def __init__(self, session):
        self.session = session
        self.bd = SessionPartagee(session)  # une seule session de test, utilisée à tour de rôle
        self.redis = FauxRedis()
        suffixe = uuid.uuid4().hex[:8]
        self.annuaire_jid = parametres.jid(f"annuaire-test-{suffixe}")
        self.dispatcher_jid = parametres.jid(f"dispatcher-test-{suffixe}")
        self.annuaire = None
        self.dispatcher = None
        self.livreurs: dict[str, AgentLivreur] = {}
        self.commandes: list[AgentCommande] = []
        self.deja_vus: list[dict] = []  # propositions déjà traitées par le livreur humain

    async def demarrer(self):
        mdp = parametres.xmpp_mot_de_passe_agents
        self.annuaire = AgentAnnuaire(self.annuaire_jid, mdp)
        await self.annuaire.start(auto_register=True)
        self.dispatcher = AgentDispatcher(
            self.dispatcher_jid, mdp, fabrique_session=self.bd, annuaire_jid=self.annuaire_jid,
            redis_fn=lambda: self.redis,
        )
        await self.dispatcher.start(auto_register=True)

    async def ajouter_livreur(self, position, *, charge=0, vehicule="moto") -> str:
        async with self.bd() as session:
            livreur_id = await creer_livreur(session)
        agent = AgentLivreur(
            p.jid_livreur(livreur_id), parametres.xmpp_mot_de_passe_agents,
            livreur_id=livreur_id, type_vehicule=vehicule, capacite_kg=25, position=position,
            mesure_le=datetime.now(UTC), charge=charge, annuaire_jid=self.annuaire_jid,
            redis_fn=lambda: self.redis,
        )
        await agent.start(auto_register=True)
        self.livreurs[livreur_id] = agent
        async with asyncio.timeout(5):  # attendre l'inscription dans l'Annuaire
            while livreur_id not in self.annuaire.registre:
                await asyncio.sleep(0.05)
        return livreur_id

    async def lancer_commande(self, **options) -> tuple[str, AgentCommande]:
        async with self.bd() as session:
            cid = await creer_commande(session, **options)
        agent = AgentCommande(
            p.jid_commande(cid), parametres.xmpp_mot_de_passe_agents, commande_id=cid,
            fabrique_session=self.bd, dispatcher_jid=self.dispatcher_jid, redis_fn=lambda: self.redis,
        )
        await agent.start(auto_register=True)
        self.commandes.append(agent)
        return cid, agent

    async def livreur_humain(self, livreur_id, accepte: bool, cid: str, timeout=8.0):
        """Le livreur humain répond dès que l'application lui a proposé la course."""
        async with asyncio.timeout(timeout):
            while not any(
                e["type"] == evenements.COURSE_PROPOSEE
                and e["livreur_id"] == livreur_id
                and e["commande_id"] == cid
                and e not in self.deja_vus
                for e in self.redis.publies
            ):
                await asyncio.sleep(0.02)
        self.deja_vus.extend(
            e for e in self.redis.publies if e["type"] == evenements.COURSE_PROPOSEE and e["livreur_id"] == livreur_id
        )
        assert self.livreurs[livreur_id].confirmation_recue(cid, accepte)

    async def arreter(self):
        for commande in self.commandes:
            await commande.arreter()
        for livreur in self.livreurs.values():
            await livreur.arreter()
        if self.dispatcher is not None:
            await self.dispatcher.arreter()
        if self.annuaire is not None:
            await self.annuaire.stop()

    # Le test lit la base comme les agents : à tour de rôle, avec la même session.
    async def offres(self, cid, tour=1):
        async with self.bd() as session:
            lignes = await session.execute(
                select(Offre).where(Offre.commande_id == uuid.UUID(cid), Offre.tour == tour)
            )
            return {str(o.livreur_id): o for o in lignes.scalars()}

    async def commande(self, cid):
        async with self.bd() as session:
            c = await session.get(Commande, uuid.UUID(cid))
            await session.refresh(c)
            return c

    async def charge_en_base(self, livreur_id):
        async with self.bd() as session:
            return await svc.charge_en_base(session, livreur_id)


@pytest_asyncio.fixture
async def plateforme(xmpp, session):
    plateforme = Plateforme(session)
    await plateforme.demarrer()
    yield plateforme
    await plateforme.arreter()


async def issue(agent: AgentCommande, timeout=20.0):
    await asyncio.wait_for(agent.fini.wait(), timeout)
    return agent.issue


# ========================================================================================= scénarios


async def test_le_moins_cher_gagne_sur_l_itineraire_reel(plateforme):
    """S2 : A est près, B est loin, C est près mais déjà chargé (2 courses). A gagne."""
    a = await plateforme.ajouter_livreur(decaler(800))
    b = await plateforme.ajouter_livreur(decaler(2500))
    c = await plateforme.ajouter_livreur(decaler(300), charge=2)
    cid, agent = await plateforme.lancer_commande()

    await plateforme.livreur_humain(a, True, cid)
    assert await issue(agent) == ATTRIBUEE

    commande = await plateforme.commande(cid)
    assert commande.etat == EtatCommande.ATTRIBUEE and str(commande.livreur_id) == a
    offres = await plateforme.offres(cid)
    assert offres[a].cout == pytest.approx(800 / VITESSE_MS, rel=0.05) and offres[a].resultat == ResultatOffre.RETENUE
    assert offres[b].cout == pytest.approx(2500 / VITESSE_MS, rel=0.05) and offres[b].resultat == ResultatOffre.REJETEE
    # C est le plus proche (300 m) mais sa charge de 2 course ajoute 2 x 300 s : il coûte plus cher que A
    assert offres[c].cout == pytest.approx(300 / VITESSE_MS + 600, rel=0.05)
    assert offres[c].cout > offres[a].cout and offres[c].resultat == ResultatOffre.REJETEE
    assert plateforme.livreurs[a].charge == 1  # une course de plus pour A
    assert plateforme.livreurs[c].charge == 2 and plateforme.livreurs[b].charge == 0  # inchangées
    assert {e["type"] for e in plateforme.redis.publies} >= {evenements.COURSE_PROPOSEE, evenements.COMMANDE_ATTRIBUEE}


async def test_le_livreur_decline_la_course_va_au_suivant(plateforme):
    """S4 : A décline ; un nouveau tour attribue la course à B, et A n'est plus sollicité."""
    a = await plateforme.ajouter_livreur(decaler(500))
    b = await plateforme.ajouter_livreur(decaler(1500))
    cid, agent = await plateforme.lancer_commande()

    await plateforme.livreur_humain(a, False, cid)
    await plateforme.livreur_humain(b, True, cid)
    assert await issue(agent) == ATTRIBUEE

    commande = await plateforme.commande(cid)
    assert str(commande.livreur_id) == b and commande.tentatives_attribution == 2
    assert (await plateforme.offres(cid, 1))[a].resultat == ResultatOffre.DECLINEE
    assert a not in await plateforme.offres(cid, 2)  # exclu du tour 2
    assert (await plateforme.offres(cid, 2))[b].resultat == ResultatOffre.RETENUE
    assert plateforme.livreurs[a].charge == 0  # la place de A a été rendue


async def test_personne_ne_confirme_dans_le_delai(plateforme):
    """F-34 : le délai expire, le livreur est exclu ; sans autre livreur, le répartiteur est alerté."""
    a = await plateforme.ajouter_livreur(decaler(500))
    cid, agent = await plateforme.lancer_commande()

    assert await issue(agent, timeout=40.0) == EPUISEE  # A expire (3 s), puis plus personne
    assert (await plateforme.offres(cid, 1))[a].resultat == ResultatOffre.DECLINEE
    commande = await plateforme.commande(cid)
    assert commande.etat == EtatCommande.RECHERCHE_LIVREUR and commande.livreur_id is None
    assert any(e["type"] == evenements.ATTRIBUTION_ECHOUEE for e in plateforme.redis.publies)
    assert plateforme.livreurs[a].charge == 0


async def test_le_rayon_s_elargit_jusqu_a_trouver_un_livreur(plateforme):
    """S3 : le seul livreur est à 15 km. Rayons : 3, 6, 12 km (vides), puis 20 km : il est trouvé."""
    lointain = await plateforme.ajouter_livreur(decaler(15_000))
    cid, agent = await plateforme.lancer_commande()

    await plateforme.livreur_humain(lointain, True, cid, timeout=20.0)
    assert await issue(agent) == ATTRIBUEE
    commande = await plateforme.commande(cid)
    assert commande.tentatives_attribution == 4 and commande.rayon_recherche_m == 20_000
    assert str(commande.livreur_id) == lointain


async def test_aucun_livreur_nulle_part(plateforme):
    cid, agent = await plateforme.lancer_commande()
    assert await issue(agent) == EPUISEE
    commande = await plateforme.commande(cid)
    assert commande.tentatives_attribution == 4 and commande.etat == EtatCommande.RECHERCHE_LIVREUR


async def test_deux_commandes_pour_un_seul_livreur_disponible(plateforme, monkeypatch):
    """S6 : un livreur, une place. La première commande l'obtient ; la seconde attend une place."""
    monkeypatch.setattr(parametres, "livreur_charge_max", 1)
    livreur = await plateforme.ajouter_livreur(decaler(500))
    c1, agent1 = await plateforme.lancer_commande()
    c2, agent2 = await plateforme.lancer_commande()

    # une seule des deux obtient la proposition ; le livreur humain confirme celle qu'on lui propose
    async with asyncio.timeout(10):
        while not any(e["type"] == evenements.COURSE_PROPOSEE for e in plateforme.redis.publies):
            await asyncio.sleep(0.02)
    gagnante = next(e["commande_id"] for e in plateforme.redis.publies if e["type"] == evenements.COURSE_PROPOSEE)
    assert plateforme.livreurs[livreur].confirmation_recue(gagnante, True)
    agent_gagnant, agent_perdant = (agent1, agent2) if gagnante == c1 else (agent2, agent1)
    perdante = c2 if gagnante == c1 else c1

    assert await issue(agent_gagnant) == ATTRIBUEE
    assert await issue(agent_perdant, timeout=30.0) == EPUISEE  # jamais de double attribution
    assert (await plateforme.commande(gagnante)).etat == EtatCommande.ATTRIBUEE
    perdante_en_base = await plateforme.commande(perdante)
    assert perdante_en_base.etat == EtatCommande.RECHERCHE_LIVREUR and perdante_en_base.livreur_id is None
    assert await plateforme.charge_en_base(livreur) == 1


async def test_commande_annulee_pendant_la_recherche(plateforme):
    cid, agent = await plateforme.lancer_commande(etat=EtatCommande.ANNULEE)
    assert await issue(agent) == ABANDONNEE