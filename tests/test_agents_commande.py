"""Agent Commande : tours, relances avec rayon élargi, réattribution, alerte du répartiteur (S3, S4)."""

import asyncio
import json
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select

from aides_commandes import FauxRedis, SessionPartagee, creer_commande, creer_livreur
from livraison.agents import protocole as p
from livraison.agents.commande import ABANDONNEE, ATTRIBUEE, EPUISEE, AgentCommande
from livraison.config import parametres
from livraison.persistance.enums import EtatCommande, TypeEvenement
from livraison.persistance.modeles import Commande, Evenement
from livraison.services import attribution as svc
from livraison.services import evenements

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def reglages(monkeypatch):
    monkeypatch.setattr(parametres, "attribution_pause_s", 0.02)
    monkeypatch.setattr(parametres, "attribution_tours_max", 3)


class Banc:
    """L'agent Commande, et un faux Dispatcher joué par le test."""

    def __init__(self, session, commande_id, **options):
        self.session = session
        self.commande_id = commande_id
        self.redis = FauxRedis()
        self.agent = AgentCommande(
            p.jid_commande(commande_id),
            "mdp",
            commande_id=commande_id,
            fabrique_session=SessionPartagee(session),
            redis_fn=lambda: self.redis,
            **options,
        )

    def demarrer(self):
        return self.agent.demarrer()

    async def demande(self, timeout=2.0) -> p.DemandeAttribution:
        m = await asyncio.wait_for(self.agent._sortie.get(), timeout)
        assert m.get_metadata("performative") == p.REQUEST and str(m.to) == p.jid_dispatcher()
        assert m.get_metadata("ontology") == p.ONTO_ATTRIBUTION
        demande = p.DemandeAttribution.depuis_contenu(json.loads(m.body))
        assert m.thread == p.conversation(self.commande_id, demande.tour)
        return demande

    def _entrant(self, performative, contenu, tour, *, de=None):
        m = p.creer_message(
            p.jid_commande(self.commande_id), performative, p.ONTO_RESULTAT, contenu.vers_contenu(),
            thread=p.conversation(self.commande_id, tour),
        )
        m.sender = de or p.jid_dispatcher()
        self.agent.recevoir(m)

    def attribuee(self, tour, livreur="L1"):
        self._entrant(p.INFORM, p.ResultatAttribution(self.commande_id, tour, p.RESULTAT_ATTRIBUEE, livreur), tour)

    def reattribution(self, tour, motif=p.DECLINEE, livreur="L1"):
        self._entrant(
            p.INFORM, p.ResultatAttribution(self.commande_id, tour, p.RESULTAT_REATTRIBUTION, livreur, motif), tour
        )

    def echec(self, tour, motif=p.ECHEC_SANS_OFFRE, detail=None):
        self._entrant(p.FAILURE, p.EchecAttribution(self.commande_id, tour, motif, detail), tour)

    async def terminer(self, timeout=2.0):
        await asyncio.wait_for(self.agent.fini.wait(), timeout)
        return self.agent.issue

    async def commande(self):
        c = await self.session.get(Commande, uuid.UUID(self.commande_id))
        await self.session.refresh(c)
        return c


@pytest_asyncio.fixture
async def banc(session):
    cid = await creer_commande(session)
    b = Banc(session, cid)
    yield b
    if b.agent._tache is not None and not b.agent._tache.done():
        b.agent._tache.cancel()
        await asyncio.gather(b.agent._tache, return_exceptions=True)


async def test_attribuee_au_premier_tour(banc):
    banc.demarrer()
    demande = await banc.demande()
    assert (demande.tour, demande.rayon_m) == (1, float(parametres.attribution_rayon_initial_m))
    assert (await banc.commande()).etat == EtatCommande.RECHERCHE_LIVREUR  # écrit avant la demande
    banc.attribuee(1)
    assert await banc.terminer() == ATTRIBUEE
    assert banc.agent.tour == 1 and banc.redis.publies == []


async def test_aucun_livreur_le_rayon_s_elargit_puis_le_repartiteur_est_alerte(banc):
    banc.demarrer()
    rayons = []
    for tour in (1, 2, 3):
        demande = await banc.demande()
        assert demande.tour == tour
        rayons.append(demande.rayon_m)
        banc.echec(tour)
    assert await banc.terminer() == EPUISEE
    assert rayons == [3000.0, 6000.0, 12000.0]
    commande = await banc.commande()
    assert commande.etat == EtatCommande.RECHERCHE_LIVREUR and commande.tentatives_attribution == 3
    alertes = (
        await banc.session.execute(
            select(Evenement).where(
                Evenement.commande_id == commande.id, Evenement.type == TypeEvenement.NOTIFICATION
            )
        )
    ).scalars().all()
    assert [a.detail for a in alertes] == [{"destinataire": "repartiteur", "motif": "aucun_livreur_trouve", "tours": 3}]
    assert [e["type"] for e in banc.redis.publies] == [evenements.ATTRIBUTION_ECHOUEE]
    assert (banc.redis.publies[0]["commande_id"], banc.redis.publies[0]["tours"]) == (banc.commande_id, 3)
    assert banc.agent._sortie.empty()  # pas de quatrième demande


async def test_attribuee_apres_deux_tours_vides(banc):
    banc.demarrer()
    for tour in (1, 2):
        await banc.demande()
        banc.echec(tour)
    demande = await banc.demande()
    assert demande.tour == 3
    banc.attribuee(3)
    assert await banc.terminer() == ATTRIBUEE


async def test_le_livreur_decline_relance_immediate_au_meme_rayon(banc, monkeypatch):
    monkeypatch.setattr(parametres, "attribution_pause_s", 30.0)  # une pause serait vue comme un blocage
    livreur = await creer_livreur(banc.session)
    banc.demarrer()
    await banc.demande()
    # le Dispatcher a retenu le livreur, qui a décliné : l'offre est « déclinée » en base
    await svc.enregistrer_reponses(banc.session, banc.commande_id, 1, propositions={livreur: (1.0, 1)}, refus={})
    await svc.retenir_offre(banc.session, banc.commande_id, 1, livreur)
    await svc.declarer_non_prise(banc.session, banc.commande_id, 1, livreur, "declinee")
    banc.reattribution(1, p.DECLINEE, livreur)
    demande = await banc.demande(timeout=1.0)  # tout de suite, sans pause
    assert (demande.tour, demande.rayon_m) == (2, float(parametres.attribution_rayon_initial_m))
    banc.attribuee(2)
    assert await banc.terminer() == ATTRIBUEE


async def test_un_tour_qui_echoue_apres_un_decline_compte_dans_les_tours(banc):
    """Les tours sont comptés ensemble : refus humain ou tour vide, la limite est la même."""
    banc.demarrer()
    await banc.demande()
    banc.reattribution(1, p.EXPIREE)
    await banc.demande()
    banc.reattribution(2, p.DECLINEE)
    await banc.demande()
    banc.reattribution(3, p.DECLINEE)  # le dernier tour est passé : plus de tour 4
    assert await banc.terminer() == EPUISEE


async def test_commande_invalide_abandon_sans_alerte(banc):
    banc.demarrer()
    await banc.demande()
    banc.echec(1, p.ECHEC_COMMANDE, "état « annulee »")
    assert await banc.terminer() == ABANDONNEE
    assert banc.redis.publies == []


async def test_commande_annulee_avant_le_premier_tour(banc):
    (await banc.session.get(Commande, uuid.UUID(banc.commande_id))).etat = EtatCommande.ANNULEE
    await banc.session.flush()
    banc.demarrer()
    assert await banc.terminer() == ABANDONNEE
    assert banc.agent._sortie.empty()


async def test_tours_deja_epuises_au_demarrage(banc, monkeypatch):
    monkeypatch.setattr(parametres, "attribution_tours_max", 2)
    (await banc.session.get(Commande, uuid.UUID(banc.commande_id))).tentatives_attribution = 2
    await banc.session.flush()
    banc.demarrer()
    assert await banc.terminer() == EPUISEE
    assert banc.redis.publies[0]["tours"] == 2 and banc.agent._sortie.empty()


async def test_le_dispatcher_ne_repond_pas(session):
    cid = await creer_commande(session)
    banc = Banc(session, cid, attente_max_s=0.1)
    tache = banc.demarrer()
    for tour in (1, 2, 3):
        assert (await banc.demande()).tour == tour  # chaque silence compte comme un tour raté
    assert await banc.terminer() == EPUISEE
    await tache


async def test_les_messages_d_un_autre_expediteur_ou_d_un_autre_tour_sont_ignores(banc):
    banc.demarrer()
    await banc.demande()
    banc._entrant(
        p.INFORM,
        p.ResultatAttribution(banc.commande_id, 1, p.RESULTAT_ATTRIBUEE, "L1"),
        1,
        de=p.jid_livreur(str(uuid.uuid4())),  # un intrus se fait passer pour le Dispatcher
    )
    banc.attribuee(7)  # réponse à un tour qui n'est pas le nôtre
    await asyncio.sleep(0.1)
    assert not banc.agent.fini.is_set()
    banc.attribuee(1)
    assert await banc.terminer() == ATTRIBUEE


async def test_une_reponse_illisible_compte_comme_un_tour_rate(banc):
    banc.demarrer()
    await banc.demande()
    mauvais = p.creer_message(
        p.jid_commande(banc.commande_id), p.INFORM, p.ONTO_RESULTAT, {"n": "importe quoi"},
        thread=p.conversation(banc.commande_id, 1),
    )
    mauvais.sender = p.jid_dispatcher()
    banc.agent.recevoir(mauvais)
    assert (await banc.demande()).tour == 2  # on relance après la pause
