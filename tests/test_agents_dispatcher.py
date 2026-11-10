"""Dispatcher : les conversations d'attribution (S2 à S4, S6) sur la vraie base, sans réseau XMPP.

Le test joue tous les autres rôles : l'agent Commande (qui demande), l'Annuaire (qui répond à la
recherche) et les agents Livreur (qui proposent, refusent, confirment). Il lit ce que le Dispatcher
dépose dans sa file de sortie.
"""

import asyncio
import json
import time
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select

from aides_commandes import (
    FauxRedis,
    SessionPartagee,
    creer_commande,
    creer_livreur,
)
from livraison.agents import protocole as p
from livraison.agents.dispatcher import AgentDispatcher
from livraison.config import parametres
from livraison.persistance.enums import EtatCommande, Priorite, ResultatOffre, TypeEvenement
from livraison.persistance.modeles import Commande, Evenement, Offre
from livraison.services import attribution as svc
from livraison.services import evenements

pytestmark = pytest.mark.asyncio

ANNUAIRE = p.jid_annuaire()


@pytest.fixture(autouse=True)
def delais_courts(monkeypatch):
    monkeypatch.setattr(parametres, "attribution_collecte_s", 1.0)
    monkeypatch.setattr(parametres, "attribution_annuaire_attente_s", 0.5)
    monkeypatch.setattr(parametres, "attribution_confirmation_s", 1.0)
    monkeypatch.setattr(parametres, "attribution_marge_s", 0.3)
    monkeypatch.setattr(parametres, "livreur_charge_max", 3)


class Banc:
    """Le Dispatcher, et tout ce qu'il faut pour jouer les autres agents."""

    def __init__(self, session):
        self.session = session
        self.redis = FauxRedis()
        self.dispatcher = AgentDispatcher(
            p.jid_dispatcher(),
            "mdp",
            fabrique_session=SessionPartagee(session),
            annuaire_jid=ANNUAIRE,
            redis_fn=lambda: self.redis,
        )

    # -- ce que le Dispatcher reçoit
    def _entrant(self, de, performative, ontologie, contenu, fil):
        m = p.creer_message(p.jid_dispatcher(), performative, ontologie, contenu, thread=fil)
        m.sender = de
        self.dispatcher.recevoir(m)

    def demander(self, commande_id, tour=1, rayon=3000.0, *, de=None):
        fil = p.conversation(commande_id, tour)
        demande = p.DemandeAttribution(commande_id, tour, rayon).vers_contenu()
        self._entrant(de or p.jid_commande(commande_id), p.REQUEST, p.ONTO_ATTRIBUTION, demande, fil)

    def annuaire_repond(self, commande_id, tour, livreurs, *, de=ANNUAIRE):
        contenu = {"total": len(livreurs), "candidats": [{"livreur_id": i, "distance_m": 100.0} for i in livreurs]}
        self._entrant(de, p.INFORM, p.ONTO_RECHERCHE, contenu, p.conversation(commande_id, tour))

    def livreur_dit(self, livreur_id, performative, contenu, commande_id, tour=1):
        self._entrant(
            p.jid_livreur(livreur_id), performative, p.ONTO_CONTRACT_NET,
            contenu.vers_contenu(), p.conversation(commande_id, tour),
        )

    def propose(self, livreur_id, commande_id, cout, tour=1, charge=0):
        self.livreur_dit(
            livreur_id, p.PROPOSE, p.Proposition(commande_id, tour, cout, cout, 0.0, charge), commande_id, tour
        )

    def refuse(self, livreur_id, commande_id, motif=p.HORS_ZONE, tour=1):
        self.livreur_dit(livreur_id, p.REFUSE, p.Refus(commande_id, tour, motif), commande_id, tour)

    def confirme(self, livreur_id, commande_id, tour=1):
        self.livreur_dit(livreur_id, p.INFORM, p.Confirmation(commande_id, tour), commande_id, tour)

    def annule(self, livreur_id, commande_id, motif=p.DECLINEE, tour=1):
        self.livreur_dit(livreur_id, p.CANCEL, p.Annulation(commande_id, tour, motif), commande_id, tour)

    # -- ce que le Dispatcher envoie
    async def sortant(self, timeout=2.0):
        return await asyncio.wait_for(self.dispatcher._sortie.get(), timeout)

    async def sortants(self, nombre, timeout=2.0):
        return [await self.sortant(timeout) for _ in range(nombre)]

    async def attendre(self, performative, vers=None, timeout=2.0):
        """Prochain message sortant : doit avoir cette performative (et ce destinataire)."""
        m = await self.sortant(timeout)
        assert m.get_metadata("performative") == performative, (m.get_metadata("performative"), m.body)
        if vers is not None:
            assert str(m.to) == vers
        return m

    def rien_a_lire(self):
        return self.dispatcher._sortie.empty()

    async def fin(self, timeout=2.0):
        """Attend que toutes les conversations soient terminées."""
        async with asyncio.timeout(timeout):
            while self.dispatcher.conversations or self.dispatcher._taches:
                await asyncio.sleep(0.01)

    async def nettoyer(self):
        """Fin de test : laisse les conversations finir, puis coupe les restantes (avant la base)."""
        try:
            await self.fin(timeout=2.0)
        except TimeoutError:
            for tache in list(self.dispatcher._taches):
                tache.cancel()
            await asyncio.gather(*self.dispatcher._taches, return_exceptions=True)

    # -- la base
    async def offres(self, commande_id, tour=1):
        lignes = await self.session.execute(
            select(Offre).where(Offre.commande_id == uuid.UUID(commande_id), Offre.tour == tour)
        )
        return {str(o.livreur_id): o for o in lignes.scalars()}

    async def commande(self, commande_id):
        c = await self.session.get(Commande, uuid.UUID(commande_id))
        await self.session.refresh(c)
        return c

    async def messages_journalises(self, commande_id):
        lignes = await self.session.execute(
            select(Evenement).where(
                Evenement.commande_id == uuid.UUID(commande_id), Evenement.type == TypeEvenement.MESSAGE_AGENT
            )
        )
        return list(lignes.scalars())


async def commande_en_recherche(session, **options):
    """Commande déjà ouverte par l'agent Commande : état « recherche_livreur », tour 1 préparé."""
    cid = await creer_commande(session, **options)
    await svc.preparer_tour(session, cid)
    return cid


@pytest_asyncio.fixture
async def banc(session):
    banc = Banc(session)
    yield banc
    await banc.nettoyer()


async def lancer_appel(banc, cid, livreurs, tour=1, *, consultes=None):
    """Demande, réponse de l'Annuaire, puis lecture des CFP : renvoie les CFP par livreur.

    `consultes` : nombre de livreurs réellement consultés, si la base en écarte (défaut : tous).
    """
    banc.demander(cid, tour)
    requete = await banc.attendre(p.QUERY_REF, ANNUAIRE)
    banc.annuaire_repond(cid, tour, livreurs)
    cfp = await banc.sortants(len(livreurs) if consultes is None else consultes)
    assert {m.get_metadata("performative") for m in cfp} == {p.CFP}
    return requete, {livreur_id_de(m): m for m in cfp}


def livreur_id_de(message):
    return p.livreur_id_depuis_jid(str(message.to))


# ================================================================================== S2 : cas nominal


async def test_attribution_nominale(banc):
    session = banc.session
    a, b, c = [await creer_livreur(session) for _ in range(3)]
    cid = await commande_en_recherche(session, poids_kg=2.4)
    requete, cfp = await lancer_appel(banc, cid, [a, b, c])

    recherche = p.Recherche.depuis_contenu(json.loads(requete.body))
    assert (recherche.rayon_m, recherche.poids_kg, recherche.exclure) == (3000.0, 3, ())  # 2,4 kg -> 3
    assert recherche.latitude == pytest.approx(5.3236)
    assert requete.thread == p.conversation(cid, 1)
    appel = p.AppelOffres.depuis_contenu(json.loads(cfp[a].body))
    assert (appel.commande_id, appel.tour, appel.rayon_m, appel.delai_s) == (cid, 1, 3000.0, 1.0)
    assert appel.enlevement == pytest.approx((5.3236, -4.0197)) and appel.poids_kg == 2.4
    assert "destinataire" not in cfp[a].body  # aucune donnée personnelle dans le CFP

    banc.propose(b, cid, 1020.0, charge=2)
    banc.propose(a, cid, 420.0)
    banc.refuse(c, cid, p.HORS_ZONE)

    # a gagne : ACCEPT à a, REJECT à b, rien pour c (il a refusé)
    accept = await banc.attendre(p.ACCEPT_PROPOSAL, p.jid_livreur(a))
    assert p.Decision.depuis_contenu(json.loads(accept.body)).delai_confirmation_s == 1.0
    await banc.attendre(p.REJECT_PROPOSAL, p.jid_livreur(b))
    assert (await banc.offres(cid))[a].resultat == ResultatOffre.RETENUE

    banc.confirme(a, cid)
    resultat = await banc.attendre(p.INFORM, p.jid_commande(cid))
    r = p.ResultatAttribution.depuis_contenu(json.loads(resultat.body))
    assert (r.resultat, r.livreur_id, r.tour) == (p.RESULTAT_ATTRIBUEE, a, 1)
    await banc.fin()

    commande = await banc.commande(cid)
    assert commande.etat == EtatCommande.ATTRIBUEE and str(commande.livreur_id) == a
    offres = await banc.offres(cid)
    assert {i: o.resultat for i, o in offres.items()} == {
        a: ResultatOffre.RETENUE, b: ResultatOffre.REJETEE, c: ResultatOffre.REFUSEE,
    }
    assert offres[c].motif_refus == p.HORS_ZONE and offres[a].cout == 420.0
    assert [e["type"] for e in banc.redis.publies] == [evenements.COMMANDE_ATTRIBUEE]
    assert banc.redis.publies[0]["livreur_id"] == a and banc.redis.ferme


async def test_le_journal_garde_tous_les_messages_de_la_conversation(banc):
    session = banc.session
    a, b = await creer_livreur(session), await creer_livreur(session)
    cid = await commande_en_recherche(session)
    await lancer_appel(banc, cid, [a, b])
    banc.propose(a, cid, 100.0)
    banc.refuse(b, cid, p.SATURE)
    await banc.attendre(p.ACCEPT_PROPOSAL)
    banc.confirme(a, cid)
    await banc.attendre(p.INFORM)
    await banc.fin()
    journal = await banc.messages_journalises(cid)
    assert sorted(e.performative for e in journal) == sorted(
        [
            p.REQUEST,  # la demande de l'agent Commande
            p.QUERY_REF,  # la recherche dans l'Annuaire...
            p.INFORM,  # ...et sa réponse
            p.CFP,
            p.CFP,
            p.PROPOSE,
            p.REFUSE,
            p.ACCEPT_PROPOSAL,
            p.INFORM,  # la confirmation du livreur
            p.INFORM,  # le résultat annoncé à l'agent Commande
        ]
    )
    cfp = next(e for e in journal if e.performative == p.CFP)
    assert cfp.auteur == p.jid_dispatcher() and cfp.detail["conversation"] == p.conversation(cid, 1)
    assert cfp.detail["ontologie"] == p.ONTO_CONTRACT_NET and cfp.detail["contenu"]["tour"] == 1
    propose = next(e for e in journal if e.performative == p.PROPOSE)
    assert propose.auteur == p.jid_livreur(a) and propose.destinataire == p.jid_dispatcher()


async def test_la_collecte_s_arrete_des_que_tout_le_monde_a_repondu(banc, monkeypatch):
    monkeypatch.setattr(parametres, "attribution_collecte_s", 5.0)
    a, b = await creer_livreur(banc.session), await creer_livreur(banc.session)
    cid = await commande_en_recherche(banc.session)
    await lancer_appel(banc, cid, [a, b])
    debut = time.monotonic()
    banc.propose(a, cid, 50.0)
    banc.propose(b, cid, 60.0)
    await banc.attendre(p.ACCEPT_PROPOSAL, p.jid_livreur(a), timeout=1.0)
    assert time.monotonic() - debut < 1.0  # pas besoin d'attendre les 5 s


async def test_a_cout_egal_la_premiere_offre_recue_gagne(banc):
    a, b = await creer_livreur(banc.session), await creer_livreur(banc.session)
    cid = await commande_en_recherche(banc.session)
    await lancer_appel(banc, cid, [a, b])
    banc.propose(b, cid, 200.0)
    banc.propose(a, cid, 200.0)
    await banc.attendre(p.ACCEPT_PROPOSAL, p.jid_livreur(b))


# ================================================================================== S3 : pas d'offre


async def test_aucun_candidat_dans_l_annuaire(banc):
    cid = await commande_en_recherche(banc.session)
    banc.demander(cid)
    await banc.attendre(p.QUERY_REF)
    banc.annuaire_repond(cid, 1, [])
    echec = await banc.attendre(p.FAILURE, p.jid_commande(cid))
    assert p.EchecAttribution.depuis_contenu(json.loads(echec.body)).motif == p.ECHEC_SANS_OFFRE
    await banc.fin()


async def test_candidat_hors_ligne_ou_non_autorise_en_base(banc):
    ok = await creer_livreur(banc.session)
    hors_ligne = await creer_livreur(banc.session, en_ligne=False)
    cid = await commande_en_recherche(banc.session)
    requete, cfp = await lancer_appel(banc, cid, [hors_ligne, ok], consultes=1)
    assert list(cfp) == [ok]  # l'Annuaire en proposait deux, la base n'en garde qu'un
    banc.refuse(ok, cid, p.SATURE)
    echec = await banc.attendre(p.FAILURE, p.jid_commande(cid))
    assert p.EchecAttribution.depuis_contenu(json.loads(echec.body)).motif == p.ECHEC_SANS_OFFRE


async def test_tout_le_monde_refuse(banc):
    a, b = await creer_livreur(banc.session), await creer_livreur(banc.session)
    cid = await commande_en_recherche(banc.session)
    await lancer_appel(banc, cid, [a, b])
    banc.refuse(a, cid, p.SATURE)
    banc.refuse(b, cid, p.GPS_ANCIEN)
    echec = await banc.attendre(p.FAILURE, p.jid_commande(cid))
    assert p.EchecAttribution.depuis_contenu(json.loads(echec.body)).motif == p.ECHEC_SANS_OFFRE
    await banc.fin()
    offres = await banc.offres(cid)
    assert {o.motif_refus for o in offres.values()} == {p.SATURE, p.GPS_ANCIEN}
    assert (await banc.commande(cid)).etat == EtatCommande.RECHERCHE_LIVREUR  # elle attend un nouveau tour


async def test_livreur_silencieux(banc, monkeypatch):
    monkeypatch.setattr(parametres, "attribution_collecte_s", 0.15)
    a, b = await creer_livreur(banc.session), await creer_livreur(banc.session)
    cid = await commande_en_recherche(banc.session)
    await lancer_appel(banc, cid, [a, b])
    banc.propose(a, cid, 100.0)  # b ne répond pas
    await banc.attendre(p.ACCEPT_PROPOSAL, p.jid_livreur(a))
    await banc.attendre(p.REJECT_PROPOSAL, p.jid_livreur(b))  # même silencieux, il est libéré
    banc.confirme(a, cid)
    await banc.attendre(p.INFORM)
    await banc.fin()
    assert (await banc.offres(cid))[b].resultat == ResultatOffre.EXPIREE


async def test_une_reponse_tardive_est_sans_effet(banc, monkeypatch):
    monkeypatch.setattr(parametres, "attribution_collecte_s", 0.15)
    a, b = await creer_livreur(banc.session), await creer_livreur(banc.session)
    cid = await commande_en_recherche(banc.session)
    await lancer_appel(banc, cid, [a, b])
    banc.propose(a, cid, 100.0)
    await banc.attendre(p.ACCEPT_PROPOSAL)
    await banc.attendre(p.REJECT_PROPOSAL, p.jid_livreur(b))
    banc.propose(b, cid, 1.0)  # b répond après la clôture : on l'ignore
    banc.confirme(a, cid)
    await banc.attendre(p.INFORM)
    await banc.fin()
    assert str((await banc.commande(cid)).livreur_id) == a


async def test_annuaire_muet(banc, monkeypatch):
    monkeypatch.setattr(parametres, "attribution_annuaire_attente_s", 0.05)
    cid = await commande_en_recherche(banc.session)
    banc.demander(cid)
    await banc.attendre(p.QUERY_REF)
    echec = await banc.attendre(p.FAILURE, p.jid_commande(cid))
    assert p.EchecAttribution.depuis_contenu(json.loads(echec.body)).motif == p.ECHEC_ANNUAIRE


async def test_une_fausse_reponse_d_annuaire_est_ecartee(banc, monkeypatch):
    monkeypatch.setattr(parametres, "attribution_annuaire_attente_s", 0.1)
    a = await creer_livreur(banc.session)
    cid = await commande_en_recherche(banc.session)
    banc.demander(cid)
    await banc.attendre(p.QUERY_REF)
    banc.annuaire_repond(cid, 1, [a], de="intrus@localhost")
    echec = await banc.attendre(p.FAILURE, p.jid_commande(cid))
    assert p.EchecAttribution.depuis_contenu(json.loads(echec.body)).motif == p.ECHEC_ANNUAIRE


# ================================================================================== S4 : le livreur ne confirme pas


@pytest.mark.parametrize(("motif", "resultat"), [(p.DECLINEE, ResultatOffre.DECLINEE), (p.SATURE, ResultatOffre.REFUSEE)])
async def test_le_livreur_decline(banc, motif, resultat):
    a, b = await creer_livreur(banc.session), await creer_livreur(banc.session)
    cid = await commande_en_recherche(banc.session)
    await lancer_appel(banc, cid, [a, b])
    banc.propose(a, cid, 100.0)
    banc.propose(b, cid, 200.0)
    await banc.attendre(p.ACCEPT_PROPOSAL, p.jid_livreur(a))
    await banc.attendre(p.REJECT_PROPOSAL, p.jid_livreur(b))
    banc.annule(a, cid, motif)
    reponse = await banc.attendre(p.INFORM, p.jid_commande(cid))
    r = p.ResultatAttribution.depuis_contenu(json.loads(reponse.body))
    assert (r.resultat, r.livreur_id, r.motif) == (p.RESULTAT_REATTRIBUTION, a, motif)
    await banc.fin()
    assert (await banc.offres(cid))[a].resultat == resultat
    commande = await banc.commande(cid)
    assert commande.etat == EtatCommande.RECHERCHE_LIVREUR and commande.livreur_id is None
    assert banc.redis.publies == []  # pas d'annonce « attribuée »


async def test_le_livreur_decline_il_est_exclu_au_tour_suivant(banc):
    a, b = await creer_livreur(banc.session), await creer_livreur(banc.session)
    cid = await commande_en_recherche(banc.session)
    await lancer_appel(banc, cid, [a, b])
    banc.propose(a, cid, 100.0)
    banc.propose(b, cid, 200.0)
    await banc.sortants(2)
    banc.annule(a, cid, p.DECLINEE)
    await banc.attendre(p.INFORM)
    await banc.fin()

    # tour 2 (lancé par l'agent Commande) : a est exclu de la recherche
    banc.demander(cid, tour=2)
    requete = await banc.attendre(p.QUERY_REF)
    assert p.Recherche.depuis_contenu(json.loads(requete.body)).exclure == (a,)
    banc.annuaire_repond(cid, 2, [a, b])  # l'Annuaire ne sait pas qu'on l'exclut : le Dispatcher filtre
    cfp = await banc.sortants(1)
    assert livreur_id_de(cfp[0]) == b  # seul b est consulté
    banc.propose(b, cid, 200.0, tour=2)
    await banc.attendre(p.ACCEPT_PROPOSAL, p.jid_livreur(b))


async def test_le_livreur_ne_repond_pas_dans_les_delais(banc):
    a = await creer_livreur(banc.session)
    cid = await commande_en_recherche(banc.session)
    await lancer_appel(banc, cid, [a])
    banc.propose(a, cid, 100.0)
    await banc.attendre(p.ACCEPT_PROPOSAL, p.jid_livreur(a))
    # ni confirmation ni annulation : 1 s + 0,3 s de marge
    await banc.attendre(p.REJECT_PROPOSAL, p.jid_livreur(a), timeout=3.0)  # libère la réservation de l'agent
    reponse = await banc.attendre(p.INFORM, p.jid_commande(cid))
    r = p.ResultatAttribution.depuis_contenu(json.loads(reponse.body))
    assert (r.resultat, r.motif) == (p.RESULTAT_REATTRIBUTION, p.EXPIREE)
    await banc.fin()
    assert (await banc.offres(cid))[a].resultat == ResultatOffre.DECLINEE


async def test_commande_annulee_pendant_la_confirmation(banc):
    a = await creer_livreur(banc.session)
    cid = await commande_en_recherche(banc.session)
    await lancer_appel(banc, cid, [a])
    banc.propose(a, cid, 100.0)
    await banc.attendre(p.ACCEPT_PROPOSAL)
    (await banc.session.get(Commande, uuid.UUID(cid))).etat = EtatCommande.ANNULEE
    await banc.session.flush()
    banc.confirme(a, cid)
    await banc.attendre(p.REJECT_PROPOSAL, p.jid_livreur(a))  # le livreur récupère sa place
    echec = await banc.attendre(p.FAILURE, p.jid_commande(cid))
    assert p.EchecAttribution.depuis_contenu(json.loads(echec.body)).motif == p.ECHEC_COMMANDE
    await banc.fin()
    assert (await banc.commande(cid)).etat == EtatCommande.ANNULEE and banc.redis.publies == []


# ================================================================================== S6 : concurrence et capacité


async def test_un_livreur_sature_en_base_est_ecarte_meme_s_il_est_le_moins_cher(banc, monkeypatch):
    monkeypatch.setattr(parametres, "livreur_charge_max", 1)
    sature, libre = await creer_livreur(banc.session), await creer_livreur(banc.session)
    await creer_commande(banc.session, etat=EtatCommande.ENLEVEE, livreur_id=sature)  # sa charge en base : 1
    cid = await commande_en_recherche(banc.session)
    await lancer_appel(banc, cid, [sature, libre])
    banc.propose(sature, cid, 10.0)  # l'agent ignorait sa charge : l'offre est la moins chère
    banc.propose(libre, cid, 500.0)
    await banc.attendre(p.ACCEPT_PROPOSAL, p.jid_livreur(libre))
    await banc.attendre(p.REJECT_PROPOSAL, p.jid_livreur(sature))
    offres = await banc.offres(cid)
    assert offres[sature].resultat == ResultatOffre.REJETEE and offres[libre].resultat == ResultatOffre.RETENUE


async def test_toutes_les_offres_ecartees_par_la_base(banc, monkeypatch):
    monkeypatch.setattr(parametres, "livreur_charge_max", 1)
    a = await creer_livreur(banc.session)
    await creer_commande(banc.session, etat=EtatCommande.EN_LIVRAISON, livreur_id=a)
    cid = await commande_en_recherche(banc.session)
    await lancer_appel(banc, cid, [a])
    banc.propose(a, cid, 10.0)
    await banc.attendre(p.REJECT_PROPOSAL, p.jid_livreur(a))
    echec = await banc.attendre(p.FAILURE, p.jid_commande(cid))
    assert p.EchecAttribution.depuis_contenu(json.loads(echec.body)).motif == p.ECHEC_SANS_OFFRE


async def test_deux_commandes_visent_le_meme_livreur_en_meme_temps(banc, monkeypatch):
    """S6 : le livreur L n'a de place que pour une course. L'une des commandes l'obtient, l'autre non."""
    monkeypatch.setattr(parametres, "livreur_charge_max", 1)
    livreur = await creer_livreur(banc.session)
    c1, c2 = await commande_en_recherche(banc.session), await commande_en_recherche(banc.session)
    for cid in (c1, c2):
        banc.demander(cid)
    await banc.sortants(2)  # deux QUERY-REF
    for cid in (c1, c2):
        banc.annuaire_repond(cid, 1, [livreur])
    await banc.sortants(2)  # deux CFP
    for cid in (c1, c2):
        banc.propose(livreur, cid, 100.0)
    sortants = await banc.sortants(3)  # ACCEPT pour l'un ; REJECT + FAILURE pour l'autre
    par_type = sorted(m.get_metadata("performative") for m in sortants)
    assert par_type == sorted([p.ACCEPT_PROPOSAL, p.REJECT_PROPOSAL, p.FAILURE])
    gagnante = p.Decision.depuis_contenu(json.loads(next(m for m in sortants if m.get_metadata("performative") == p.ACCEPT_PROPOSAL).body)).commande_id
    perdante = c2 if gagnante == c1 else c1
    banc.confirme(livreur, gagnante)
    await banc.attendre(p.INFORM, p.jid_commande(gagnante))
    await banc.fin()
    assert (await banc.commande(gagnante)).etat == EtatCommande.ATTRIBUEE
    assert (await banc.commande(perdante)).etat == EtatCommande.RECHERCHE_LIVREUR
    assert await svc.charge_en_base(banc.session, livreur) == 1


async def test_l_urgente_passe_avant_la_normale_pour_le_meme_livreur(banc, monkeypatch):
    """F-35 : les deux commandes attendent de décider en même temps, la normale ayant proposé la première."""
    monkeypatch.setattr(parametres, "livreur_charge_max", 1)
    livreur = await creer_livreur(banc.session)
    normale = await commande_en_recherche(banc.session)
    urgente = await commande_en_recherche(banc.session, priorite=Priorite.URGENTE)
    for cid in (normale, urgente):
        banc.demander(cid)
    await banc.sortants(2)  # deux QUERY-REF
    for cid in (normale, urgente):
        banc.annuaire_repond(cid, 1, [livreur])
    await banc.sortants(2)  # deux CFP

    verrou = banc.dispatcher._verrou_decision
    async with verrou(0):  # on tient la décision : les deux conversations s'y retrouvent ensemble
        for cid in (normale, urgente):  # la normale propose la première : sans priorité, elle gagnerait
            banc.propose(livreur, cid, 100.0)
        async with asyncio.timeout(2.0):
            while verrou.en_attente < 2:
                await asyncio.sleep(0.01)

    sortants = await banc.sortants(3)  # ACCEPT pour l'urgente ; REJECT + FAILURE pour la normale
    accept = next(m for m in sortants if m.get_metadata("performative") == p.ACCEPT_PROPOSAL)
    assert p.Decision.depuis_contenu(json.loads(accept.body)).commande_id == urgente
    banc.confirme(livreur, urgente)
    await banc.attendre(p.INFORM, p.jid_commande(urgente))
    await banc.fin()
    assert (await banc.commande(urgente)).etat == EtatCommande.ATTRIBUEE
    assert (await banc.commande(normale)).etat == EtatCommande.RECHERCHE_LIVREUR
    assert await svc.charge_en_base(banc.session, livreur) == 1


# ================================================================================== robustesse


async def test_demande_d_un_autre_agent_est_refusee(banc):
    cid = await commande_en_recherche(banc.session)
    banc.demander(cid, de=p.jid_livreur(str(uuid.uuid4())))
    await banc.attendre(p.NOT_UNDERSTOOD)
    assert not banc.dispatcher.conversations


async def test_demande_au_fil_incoherent_est_refusee(banc):
    cid = await commande_en_recherche(banc.session)
    m = p.creer_message(
        p.jid_dispatcher(), p.REQUEST, p.ONTO_ATTRIBUTION,
        p.DemandeAttribution(cid, 1, 3000.0).vers_contenu(), thread=p.conversation(cid, 7),
    )
    m.sender = p.jid_commande(cid)
    banc.dispatcher.recevoir(m)
    await banc.attendre(p.NOT_UNDERSTOOD)


@pytest.mark.parametrize("etat", [EtatCommande.ANNULEE, EtatCommande.VALIDEE, EtatCommande.ATTRIBUEE])
async def test_commande_qui_n_est_pas_en_recherche(banc, etat):
    livreur = await creer_livreur(banc.session)
    cid = await creer_commande(banc.session, etat=etat, livreur_id=livreur if etat == EtatCommande.ATTRIBUEE else None)
    banc.demander(cid)
    echec = await banc.attendre(p.FAILURE, p.jid_commande(cid))
    assert p.EchecAttribution.depuis_contenu(json.loads(echec.body)).motif == p.ECHEC_COMMANDE
    await banc.fin()


async def test_commande_inconnue(banc):
    cid = str(uuid.uuid4())
    banc.demander(cid)
    echec = await banc.attendre(p.FAILURE, p.jid_commande(cid))
    assert p.EchecAttribution.depuis_contenu(json.loads(echec.body)).motif == p.ECHEC_COMMANDE
    await banc.fin()


async def test_message_d_un_livreur_non_consulte_est_ecarte(banc, monkeypatch):
    monkeypatch.setattr(parametres, "attribution_collecte_s", 0.2)
    a, intrus = await creer_livreur(banc.session), await creer_livreur(banc.session)
    cid = await commande_en_recherche(banc.session)
    await lancer_appel(banc, cid, [a])
    banc.propose(intrus, cid, 1.0)  # n'a pas reçu de CFP
    banc.propose(a, cid, 100.0)
    await banc.attendre(p.ACCEPT_PROPOSAL, p.jid_livreur(a))
    assert intrus not in await banc.offres(cid)


async def test_reponse_a_un_autre_tour_est_ignoree(banc, monkeypatch):
    monkeypatch.setattr(parametres, "attribution_collecte_s", 0.2)
    a = await creer_livreur(banc.session)
    cid = await commande_en_recherche(banc.session)
    await lancer_appel(banc, cid, [a])
    banc.livreur_dit(a, p.PROPOSE, p.Proposition(cid, 9, 1.0, 1.0, 0.0, 0), cid, tour=1)  # contenu : tour 9
    await banc.attendre(p.REJECT_PROPOSAL, p.jid_livreur(a))  # il est resté « silencieux » : libéré
    echec = await banc.attendre(p.FAILURE, p.jid_commande(cid))
    assert p.EchecAttribution.depuis_contenu(json.loads(echec.body)).motif == p.ECHEC_SANS_OFFRE


async def test_une_demande_en_double_est_ignoree(banc):
    cid = await commande_en_recherche(banc.session)
    banc.demander(cid)
    banc.demander(cid)
    await banc.attendre(p.QUERY_REF)
    await asyncio.sleep(0.05)
    assert banc.rien_a_lire() and len(banc.dispatcher.conversations) == 1


async def test_une_erreur_interne_est_signalee_a_l_agent_commande(banc, monkeypatch):
    cid = await commande_en_recherche(banc.session)

    async def panne(*args, **kwargs):
        raise RuntimeError("la base est tombée")

    monkeypatch.setattr(svc, "lire_commande", panne)
    banc.demander(cid)
    echec = await banc.attendre(p.FAILURE, p.jid_commande(cid))
    assert p.EchecAttribution.depuis_contenu(json.loads(echec.body)).motif == p.ECHEC_INTERNE
    await banc.fin()
    assert not banc.dispatcher.conversations  # rien ne reste ouvert
