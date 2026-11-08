"""Agent Livreur dans le Contract Net : offres, refus motivés, réservation et confirmation en 30 s.

Sans réseau : on remet à l'agent des messages tout faits et on lit ce qu'il dépose dans sa file de
sortie. Le calcul d'itinéraire est remplacé par un faux (ou, pour OSRM en panne, par le vrai service
branché sur un transport qui échoue).
"""

import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from aides_commandes import FauxRedis
from livraison.agents import protocole as p
from livraison.agents.livreur import (
    ETAT_CONFIRMEE,
    ETAT_PROPOSEE,
    ETAT_RESERVEE,
    AgentLivreur,
)
from livraison.config import parametres
from livraison.services import evenements, itineraires
from livraison.services.itineraires import AucunItineraire, Itineraire

pytestmark = pytest.mark.asyncio

MAINTENANT = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)
PLATEAU = (5.3236, -4.0197)
PROCHE = (5.3300, -4.0100)  # enlèvement à ~1,3 km du Plateau
LOIN = (5.4500, -3.8000)  # à ~30 km
COMMANDE = str(uuid.uuid4())
LIVREUR = str(uuid.uuid4())
DISPATCHER = p.jid_dispatcher()


def faux_itineraire(duree_s=420.0, *, estime=False, erreur=None):
    appels = []

    async def fonction(depart, arrivee, vehicule, *, avec_trace=False, repli=True):
        appels.append((depart, arrivee, vehicule, repli))
        if erreur:
            raise erreur
        return Itineraire(distance_m=duree_s * 8, duree_s=duree_s, estime=estime)

    fonction.appels = appels
    return fonction


def creer_agent(**options) -> AgentLivreur:
    valeurs = {
        "livreur_id": LIVREUR,
        "type_vehicule": "moto",
        "capacite_kg": 20,
        "position": PLATEAU,
        "mesure_le": MAINTENANT - timedelta(seconds=5),
        "itineraire_fn": faux_itineraire(),
        "horloge": lambda: MAINTENANT,
        "redis_fn": FauxRedis,
        "confirmation_s": 30.0,
    }
    valeurs.update(options)
    return AgentLivreur(p.jid_livreur(LIVREUR), "mdp", **valeurs)


def appel(tour=1, enlevement=PROCHE, **options) -> p.AppelOffres:
    valeurs = {
        "commande_id": COMMANDE,
        "tour": tour,
        "enlevement": enlevement,
        "livraison": (5.36, -3.99),
        "taille": "petit",
        "priorite": "normale",
        "rayon_m": 3000.0,
        "delai_s": 3.0,
        "poids_kg": 2.0,
    }
    valeurs.update(options)
    return p.AppelOffres(**valeurs)


def message(performative, contenu, tour=1):
    m = p.creer_message(
        p.jid_livreur(LIVREUR),
        performative,
        p.ONTO_CONTRACT_NET,
        contenu.vers_contenu() if hasattr(contenu, "vers_contenu") else contenu,
        thread=p.conversation(COMMANDE, tour),
    )
    m.sender = DISPATCHER
    return m


def vider(agent) -> list:
    sortants = []
    while not agent._sortie.empty():
        sortants.append(agent._sortie.get_nowait())
    return [m for m in sortants if m.get_metadata("ontology") == p.ONTO_CONTRACT_NET]


async def un_message(agent):
    sortants = vider(agent)
    assert len(sortants) == 1, [m.get_metadata("performative") for m in sortants]
    return sortants[0]


# ============================================================================== CFP : l'offre


async def test_offre_nominale():
    itineraire = faux_itineraire(420.0)
    agent = creer_agent(itineraire_fn=itineraire)
    await agent.traiter_contract_net(message(p.CFP, appel()))
    reponse = await un_message(agent)
    assert reponse.get_metadata("performative") == p.PROPOSE
    assert reponse.thread == p.conversation(COMMANDE, 1) and str(reponse.to) == DISPATCHER
    offre = p.Proposition.depuis_contenu(json.loads(reponse.body))
    assert (offre.cout, offre.eta_s, offre.detour_s, offre.charge, offre.estime) == (420.0, 420.0, 0.0, 0, False)
    assert agent.offres[p.conversation(COMMANDE, 1)].etat == ETAT_PROPOSEE
    depart, arrivee, vehicule, repli = itineraire.appels[0]
    assert tuple(depart) == PLATEAU and tuple(arrivee) == PROCHE
    assert vehicule.value == "moto" and repli is True


async def test_le_cout_penalise_la_charge(monkeypatch):
    monkeypatch.setattr(parametres, "attribution_penalite_charge_s", 300.0)
    agent = creer_agent(charge=2)
    await agent.traiter_contract_net(message(p.CFP, appel()))
    offre = p.Proposition.depuis_contenu(json.loads((await un_message(agent)).body))
    assert (offre.cout, offre.eta_s, offre.charge) == (420.0 + 600.0, 420.0, 2)


async def test_une_duree_estimee_est_signalee_dans_l_offre():
    agent = creer_agent(itineraire_fn=faux_itineraire(500.0, estime=True))
    await agent.traiter_contract_net(message(p.CFP, appel()))
    assert json.loads((await un_message(agent)).body)["estime"] is True


async def test_osrm_en_panne_l_offre_est_estimee_et_signalee():
    """Vrai service d'itinéraires, OSRM injoignable : repli à vol d'oiseau, jamais en silence."""

    def panne(requete):
        raise httpx.ConnectError("OSRM éteint")

    itineraires.utiliser_client(httpx.AsyncClient(transport=httpx.MockTransport(panne)))
    try:
        agent = creer_agent(itineraire_fn=itineraires.itineraire)
        await agent.traiter_contract_net(message(p.CFP, appel()))
        reponse = await un_message(agent)
    finally:
        itineraires.utiliser_client(None)
    assert reponse.get_metadata("performative") == p.PROPOSE
    offre = json.loads(reponse.body)
    assert offre["estime"] is True and offre["eta_s"] > 0


# ============================================================================== CFP : les refus


def cas_de_refus():
    ancien = MAINTENANT - timedelta(seconds=parametres.position_age_max_secondes + 1)
    return [
        (p.HORS_LIGNE, {"arret_en_cours": True}, {}),
        (p.VEHICULE, {}, {"type_vehicule": "voiture"}),
        (p.TROP_LOURD, {}, {"poids_kg": 50.0}),
        (p.SATURE, {"charge": parametres.livreur_charge_max}, {}),
        (p.GPS_ANCIEN, {"mesure_le": ancien}, {}),
        (p.GPS_ANCIEN, {"position": None, "mesure_le": None}, {}),
        (p.HORS_ZONE, {}, {"enlevement": LOIN}),
        (p.ITINERAIRE, {"itineraire_fn": faux_itineraire(erreur=AucunItineraire("pas de route"))}, {}),
    ]


@pytest.mark.parametrize(("motif", "agent_options", "appel_options"), cas_de_refus())
async def test_refus_motives(motif, agent_options, appel_options):
    agent = creer_agent(**{k: v for k, v in agent_options.items() if k != "arret_en_cours"})
    agent.arret_en_cours = agent_options.get("arret_en_cours", False)
    await agent.traiter_contract_net(message(p.CFP, appel(**appel_options)))
    reponse = await un_message(agent)
    assert reponse.get_metadata("performative") == p.REFUSE
    assert p.Refus.depuis_contenu(json.loads(reponse.body)).motif == motif
    assert not agent.offres  # un refus ne laisse aucune offre en cours


async def test_un_refus_n_appelle_pas_osrm():
    itineraire = faux_itineraire()
    agent = creer_agent(itineraire_fn=itineraire, charge=parametres.livreur_charge_max)
    await agent.traiter_contract_net(message(p.CFP, appel()))
    vider(agent)
    assert itineraire.appels == []


async def test_message_illisible_ou_fil_incoherent():
    agent = creer_agent()
    mauvais = message(p.CFP, {"commande_id": "x"})
    await agent.traiter_contract_net(mauvais)
    assert (await un_message(agent)).get_metadata("performative") == p.NOT_UNDERSTOOD
    incoherent = message(p.CFP, appel(tour=1))
    incoherent.thread = p.conversation(COMMANDE, 9)
    await agent.traiter_contract_net(incoherent)
    assert (await un_message(agent)).get_metadata("performative") == p.NOT_UNDERSTOOD
    assert not agent.offres


async def test_performative_inattendue_est_ignoree():
    agent = creer_agent()
    await agent.traiter_contract_net(message(p.INFORM, {"x": 1}))
    assert vider(agent) == []


# ============================================================================== ACCEPT : 30 secondes


async def proposer(agent, tour=1):
    await agent.traiter_contract_net(message(p.CFP, appel(tour=tour), tour))
    assert (await un_message(agent)).get_metadata("performative") == p.PROPOSE


async def accepter(agent, tour=1, delai=None):
    """Envoie ACCEPT-PROPOSAL (tâche de fond) et attend que l'agent attende son livreur."""
    tache = agent.lancer(
        agent.traiter_contract_net(message(p.ACCEPT_PROPOSAL, p.Decision(COMMANDE, tour, delai), tour))
    )
    for _ in range(200):
        if COMMANDE in agent._attentes or tache.done():
            break
        await asyncio.sleep(0.005)
    return tache


async def test_le_livreur_confirme():
    redis = FauxRedis()
    agent = creer_agent(redis_fn=lambda: redis)
    await proposer(agent)
    tache = await accepter(agent)
    assert agent.charge == 1  # place réservée dès l'ACCEPT
    assert agent.offres[p.conversation(COMMANDE, 1)].etat == ETAT_RESERVEE
    (annonce,) = redis.publies
    assert annonce["type"] == evenements.COURSE_PROPOSEE and redis.ferme
    assert (annonce["livreur_id"], annonce["commande_id"], annonce["tour"]) == (LIVREUR, COMMANDE, 1)
    assert annonce["expire_le"] == (MAINTENANT + timedelta(seconds=30)).isoformat()

    assert agent.confirmation_recue(COMMANDE, True) is True
    await tache
    reponse = await un_message(agent)
    assert reponse.get_metadata("performative") == p.INFORM
    assert p.Confirmation.depuis_contenu(json.loads(reponse.body)).tour == 1
    assert agent.charge == 1 and agent.offres[p.conversation(COMMANDE, 1)].etat == ETAT_CONFIRMEE


async def test_le_livreur_decline():
    agent = creer_agent()
    await proposer(agent)
    tache = await accepter(agent)
    assert agent.confirmation_recue(COMMANDE, False)
    await tache
    reponse = await un_message(agent)
    assert reponse.get_metadata("performative") == p.CANCEL
    assert p.Annulation.depuis_contenu(json.loads(reponse.body)).motif == p.DECLINEE
    assert agent.charge == 0 and not agent.offres  # la place est rendue


async def test_le_delai_expire():
    agent = creer_agent(confirmation_s=0.05)
    await proposer(agent)
    tache = await accepter(agent)
    await tache
    reponse = await un_message(agent)
    assert p.Annulation.depuis_contenu(json.loads(reponse.body)).motif == p.EXPIREE
    assert agent.charge == 0 and not agent.offres
    assert agent.confirmation_recue(COMMANDE, True) is False  # réponse arrivée trop tard


async def test_le_delai_vient_de_l_accept_quand_il_est_donne():
    agent = creer_agent(confirmation_s=60.0)
    await proposer(agent)
    tache = await accepter(agent, delai=0.05)
    await tache
    assert p.Annulation.depuis_contenu(json.loads((await un_message(agent)).body)).motif == p.EXPIREE


async def test_offre_retiree_pendant_l_attente():
    agent = creer_agent()
    await proposer(agent)
    tache = await accepter(agent)
    await agent.traiter_contract_net(message(p.REJECT_PROPOSAL, p.Decision(COMMANDE, 1)))
    await tache
    assert vider(agent) == []  # le Dispatcher a déjà tourné la page : aucune réponse
    assert agent.charge == 0 and not agent.offres


async def test_accept_sans_offre_connue():
    agent = creer_agent()
    await agent.traiter_contract_net(message(p.ACCEPT_PROPOSAL, p.Decision(COMMANDE, 1)))
    reponse = await un_message(agent)
    assert reponse.get_metadata("performative") == p.FAILURE
    assert json.loads(reponse.body)["motif"] == "proposition_inconnue"
    assert agent.charge == 0


async def test_accept_alors_que_le_livreur_est_devenu_sature(monkeypatch):
    monkeypatch.setattr(parametres, "livreur_charge_max", 1)
    agent = creer_agent()
    await proposer(agent)
    agent.charge = 1  # une autre course a été confirmée entre-temps
    await agent.traiter_contract_net(message(p.ACCEPT_PROPOSAL, p.Decision(COMMANDE, 1)))
    reponse = await un_message(agent)
    assert reponse.get_metadata("performative") == p.CANCEL
    assert p.Annulation.depuis_contenu(json.loads(reponse.body)).motif == p.SATURE
    assert agent.charge == 1 and not agent.offres


async def test_un_second_accept_ne_reserve_pas_deux_fois():
    agent = creer_agent()
    await proposer(agent)
    tache = await accepter(agent)
    await agent.traiter_contract_net(message(p.ACCEPT_PROPOSAL, p.Decision(COMMANDE, 1)))
    assert (await un_message(agent)).get_metadata("performative") == p.FAILURE
    assert agent.charge == 1
    agent.confirmation_recue(COMMANDE, True)
    await tache


# ============================================================================== REJECT


async def test_reject_d_une_offre_non_retenue():
    agent = creer_agent()
    await proposer(agent)
    await agent.traiter_contract_net(message(p.REJECT_PROPOSAL, p.Decision(COMMANDE, 1)))
    assert not agent.offres and agent.charge == 0 and vider(agent) == []


async def test_reject_d_une_offre_inconnue_est_sans_effet():
    agent = creer_agent()
    await agent.traiter_contract_net(message(p.REJECT_PROPOSAL, p.Decision(COMMANDE, 1)))
    assert agent.charge == 0 and vider(agent) == []


async def test_reject_apres_confirmation_libere_la_place():
    """La commande a été annulée pendant la confirmation : le Dispatcher retire l'offre."""
    agent = creer_agent()
    await proposer(agent)
    tache = await accepter(agent)
    agent.confirmation_recue(COMMANDE, True)
    await tache
    vider(agent)
    assert agent.charge == 1
    await agent.traiter_contract_net(message(p.REJECT_PROPOSAL, p.Decision(COMMANDE, 1)))
    assert agent.charge == 0 and not agent.offres


async def test_liberer_course_rend_la_place():
    agent = creer_agent()
    await proposer(agent)
    tache = await accepter(agent)
    agent.confirmation_recue(COMMANDE, True)
    await tache
    assert agent.liberer_course(COMMANDE) is True and agent.charge == 0
    assert agent.liberer_course(COMMANDE) is False  # rien d'autre à libérer


async def test_les_offres_jamais_decidees_sont_oubliees(monkeypatch):
    agent = creer_agent()
    await proposer(agent)
    vieille = agent.offres[p.conversation(COMMANDE, 1)]
    vieille.cree_le -= 1000
    await proposer(agent, tour=2)  # un nouvel appel d'offres purge la vieille offre
    assert list(agent.offres) == [p.conversation(COMMANDE, 2)]


async def test_une_confirmation_sans_proposition_en_attente():
    agent = creer_agent()
    assert agent.confirmation_recue(COMMANDE, True) is False


async def test_les_appels_d_offres_se_traitent_en_parallele():
    """Deux appels d'offres simultanés : l'itinéraire du premier n'empêche pas le second."""
    ouverte = asyncio.Event()
    appels = []

    async def lent(depart, arrivee, vehicule, *, avec_trace=False, repli=True):
        appels.append(arrivee)
        if len(appels) == 1:
            await ouverte.wait()  # le premier attend que le second soit arrivé
        else:
            ouverte.set()
        return Itineraire(1000.0, 100.0)

    agent = creer_agent(itineraire_fn=lent)
    autre = str(uuid.uuid4())
    premier = agent.lancer(agent.traiter_contract_net(message(p.CFP, appel())))
    second = message(p.CFP, appel(commande_id=autre))
    second.thread = p.conversation(autre, 1)
    deuxieme = agent.lancer(agent.traiter_contract_net(second))
    await asyncio.wait_for(asyncio.gather(premier, deuxieme), 2)
    assert len(vider(agent)) == 2
