"""Agent Livreur : représente un livreur **en ligne** auprès des autres agents.

Il existe uniquement pendant que le livreur est en ligne (F-30) : le gestionnaire le crée au passage
en ligne et l'arrête au passage hors ligne. Il s'inscrit à l'Annuaire, lui signale chaque nouvelle
position et un battement régulier (pour qu'on sache qu'il est vivant), et se réinscrit tout seul si
l'Annuaire l'a oublié (redémarrage).

Il répond aussi aux appels d'offres du Dispatcher (Contract Net, docs/contract-net.md) : il calcule
son coût sur l'**itinéraire réel** jusqu'à l'enlèvement, ou refuse en donnant le motif. Quand le
Dispatcher le retient, il réserve une place de sa charge, fait proposer la course à son livreur
(événement `course_proposee`) et attend sa réponse pendant 30 secondes (F-34).

Les messages envoyés depuis l'extérieur de l'agent passent par une file : SPADE n'autorise l'envoi
qu'à l'intérieur d'un comportement.
"""

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from functools import reduce
from typing import Any

from spade.agent import Agent
from spade.behaviour import CyclicBehaviour, PeriodicBehaviour
from spade.message import Message
from spade.template import Template

from livraison.agents.protocole import (
    ACCEPT_PROPOSAL,
    CANCEL,
    CFP,
    DECLINEE,
    EXPIREE,
    FAILURE,
    GPS_ANCIEN,
    HORS_LIGNE,
    HORS_ZONE,
    INFORM,
    ITINERAIRE,
    NOT_UNDERSTOOD,
    ONTO_CONTRACT_NET,
    ONTO_DESINSCRIPTION,
    ONTO_INSCRIPTION,
    ONTO_MAJ,
    PROPOSE,
    REFUSE,
    REJECT_PROPOSAL,
    REQUEST,
    SATURE,
    TROP_LOURD,
    VEHICULE,
    Annulation,
    AppelOffres,
    Confirmation,
    Decision,
    Inscription,
    MessageInvalide,
    MiseAJour,
    Proposition,
    Refus,
    conversation,
    creer_message,
    jid_annuaire,
    jid_livreur,
    lire_contenu,
    repondre,
)
from livraison.config import parametres
from livraison.modele.cout import cout_itineraire
from livraison.persistance.enums import TypeVehicule
from livraison.services import evenements, itineraires
from livraison.services.itineraires import ErreurItineraire, Itineraire, Point
from livraison.services.redis_client import creer_redis

journal = logging.getLogger("livraison.agents.livreur")

_ONTOLOGIES_REPONSES = (ONTO_INSCRIPTION, ONTO_DESINSCRIPTION, ONTO_MAJ)

# Réponses possibles du livreur humain à la proposition qu'on lui fait
_CONFIRMEE = "confirmee"
_DECLINEE = "declinee"
_REJETEE = "rejetee"  # le Dispatcher a retiré l'offre pendant l'attente

ETAT_PROPOSEE = "proposee"  # PROPOSE envoyé, décision du Dispatcher attendue
ETAT_RESERVEE = "reservee"  # ACCEPT-PROPOSAL reçu : une place de charge est retenue, livreur sollicité
ETAT_CONFIRMEE = "confirmee"  # le livreur humain a pris la course
_DUREE_DE_VIE_PROPOSITION_S = 120.0  # une offre jamais décidée est oubliée après ce délai


@dataclass
class OffreEnCours:
    """Une offre faite par l'agent, de sa proposition jusqu'à la fin de la course."""

    commande_id: str
    tour: int
    etat: str = ETAT_PROPOSEE
    cree_le: float = field(default_factory=lambda: asyncio.get_running_loop().time())


class AgentLivreur(Agent):
    def __init__(
        self,
        jid: str,
        password: str,
        *,
        livreur_id: str,
        type_vehicule: str,
        capacite_kg: int,
        charge: int = 0,
        position: tuple[float, float] | None = None,
        mesure_le: datetime | None = None,
        annuaire_jid: str | None = None,
        port: int | None = None,
        itineraire_fn: Callable[..., Awaitable[Itineraire]] | None = None,
        horloge: Callable[[], datetime] | None = None,
        redis_fn: Callable[[], Any] | None = None,
        confirmation_s: float | None = None,
    ):
        super().__init__(jid, password, port=port or parametres.xmpp_port)
        self.livreur_id = livreur_id
        self.annuaire_jid = annuaire_jid or jid_annuaire()
        self.type_vehicule = type_vehicule
        self.capacite_kg = capacite_kg
        self.charge = charge
        self.position = position
        self.mesure_le = mesure_le
        self.inscrit = asyncio.Event()
        self.desinscrit = asyncio.Event()
        self._sortie: asyncio.Queue[Message] = asyncio.Queue()
        # Contract Net : l'itinéraire, l'horloge, Redis et le délai se remplacent dans les tests.
        self._itineraire = itineraire_fn or itineraires.itineraire
        self._horloge = horloge or (lambda: datetime.now(UTC))
        self._redis_fn = redis_fn or creer_redis
        self.confirmation_s = (
            parametres.attribution_confirmation_s if confirmation_s is None else confirmation_s
        )
        self.arret_en_cours = False
        self.offres: dict[str, OffreEnCours] = {}  # par fil de conversation `<commande>:<tour>`
        self._attentes: dict[str, asyncio.Future[str]] = {}  # par commande : réponse du livreur
        self._taches: set[asyncio.Task] = set()

    # -- état annoncé ----------------------------------------------------------------------------

    @property
    def inscription(self) -> Inscription:
        return Inscription(
            livreur_id=self.livreur_id,
            type_vehicule=self.type_vehicule,
            capacite_kg=self.capacite_kg,
            charge=self.charge,
            position=self.position,
            mesure_le=self.mesure_le,
        )

    def _mise_a_jour(self) -> MiseAJour:
        return MiseAJour(
            livreur_id=self.livreur_id,
            position=self.position,
            mesure_le=self.mesure_le,
            charge=self.charge,
        )

    # -- comportements ---------------------------------------------------------------------------

    class Emettre(CyclicBehaviour):
        """Envoie les messages que le reste du programme a déposés dans la file."""

        async def run(self):
            message = await self.agent._sortie.get()
            await self.send(message)

    class Recevoir(CyclicBehaviour):
        async def run(self):
            message = await self.receive(timeout=1)
            if message is not None:
                self.agent.traiter(message)

    class RecevoirContractNet(CyclicBehaviour):
        """Reçoit les messages du Dispatcher ; chacun est traité dans sa propre tâche.

        Un calcul d'itinéraire peut durer jusqu'au délai d'OSRM : traiter les appels d'offres un par
        un ferait rater la fenêtre de collecte dès que plusieurs commandes arrivent ensemble.
        """

        async def run(self):
            message = await self.receive(timeout=1)
            if message is not None:
                self.agent.lancer(self.agent.traiter_contract_net(message))

    class Battement(PeriodicBehaviour):
        async def run(self):
            self.agent.annoncer_etat()

    async def setup(self):
        modele = reduce(
            lambda a, b: a | b, (Template(metadata={"ontology": o}) for o in _ONTOLOGIES_REPONSES)
        )
        self.add_behaviour(self.Emettre())
        self.add_behaviour(self.Recevoir(), modele)
        self.add_behaviour(
            self.RecevoirContractNet(), Template(metadata={"ontology": ONTO_CONTRACT_NET})
        )
        # SPADE compare `start_at` à datetime.now() sans fuseau :
        # une date « aware » ferait planter le comportement.
        premier = datetime.now() + timedelta(seconds=parametres.agents_battement_s)
        self.add_behaviour(self.Battement(period=parametres.agents_battement_s, start_at=premier))
        self.demander_inscription()

    # -- messages vers l'Annuaire (déposés dans la file) -----------------------------------------

    def _vers_annuaire(self, performative: str, ontologie: str, contenu: dict) -> None:
        self._sortie.put_nowait(
            creer_message(
                self.annuaire_jid,
                performative,
                ontologie,
                contenu,
                thread=f"{ontologie}:{self.livreur_id}:{uuid.uuid4().hex[:8]}",
            )
        )

    def demander_inscription(self) -> None:
        self.inscrit.clear()
        self._vers_annuaire(REQUEST, ONTO_INSCRIPTION, self.inscription.vers_contenu())

    def annoncer_etat(self) -> None:
        self._vers_annuaire(INFORM, ONTO_MAJ, self._mise_a_jour().vers_contenu())

    def position_recue(self, latitude: float, longitude: float, mesure_le: datetime) -> None:
        """Nouvelle position du livreur. Une position plus ancienne que la dernière est ignorée."""
        if self.mesure_le is not None and mesure_le < self.mesure_le:
            return
        self.position = (latitude, longitude)
        self.mesure_le = mesure_le
        self.annoncer_etat()

    def changer_charge(self, charge: int) -> None:
        if charge < 0:
            raise ValueError("La charge ne peut pas être négative.")
        self.charge = charge
        self.annoncer_etat()

    async def desinscrire(self, delai_s: float = 2.0) -> bool:
        """Se retire de l'Annuaire et attend sa confirmation. Renvoie False si elle n'arrive pas."""
        self.desinscrit.clear()
        self._vers_annuaire(REQUEST, ONTO_DESINSCRIPTION, {})
        try:
            await asyncio.wait_for(self.desinscrit.wait(), delai_s)
        except TimeoutError:
            journal.warning(
                "Livreur %s : désinscription non confirmée par l'Annuaire", self.livreur_id
            )
            return False
        return True

    async def arreter(self) -> None:
        """Arrêt propre : refuser tout nouvel appel d'offres, se désinscrire, puis couper XMPP."""
        self.arret_en_cours = True
        await self.desinscrire()
        for tache in list(self._taches):
            tache.cancel()
        await self.stop()

    # -- réponses de l'Annuaire ------------------------------------------------------------------

    def traiter(self, message: Message) -> None:
        ontologie = message.get_metadata("ontology")
        performative = message.get_metadata("performative")
        if performative == INFORM and ontologie == ONTO_INSCRIPTION:
            self.inscrit.set()
        elif performative == INFORM and ontologie == ONTO_DESINSCRIPTION:
            self.desinscrit.set()
        elif performative == FAILURE and ontologie == ONTO_MAJ:
            journal.info("Livreur %s inconnu de l'Annuaire : réinscription", self.livreur_id)
            self.demander_inscription()
        elif performative in (FAILURE, NOT_UNDERSTOOD):
            journal.error(
                "Livreur %s : l'Annuaire a répondu %s (%s)",
                self.livreur_id,
                performative,
                message.body,
            )


    # -- Contract Net : appels d'offres du Dispatcher --------------------------------------------

    def lancer(self, coroutine: Coroutine[Any, Any, Any]) -> asyncio.Task:
        """Exécute un traitement en tâche de fond, sans jamais en perdre l'erreur."""
        tache = asyncio.get_running_loop().create_task(coroutine)
        self._taches.add(tache)
        tache.add_done_callback(self._tache_terminee)
        return tache

    def _tache_terminee(self, tache: asyncio.Task) -> None:
        self._taches.discard(tache)
        if not tache.cancelled() and tache.exception() is not None:
            journal.error(
                "Livreur %s : erreur dans un traitement", self.livreur_id, exc_info=tache.exception()
            )

    async def traiter_contract_net(self, message: Message) -> None:
        performative = message.get_metadata("performative")
        try:
            if performative == CFP:
                await self._repondre_cfp(message)
            elif performative == ACCEPT_PROPOSAL:
                await self._accepter(message)
            elif performative == REJECT_PROPOSAL:
                self._rejeter(message)
            else:
                journal.warning(
                    "Livreur %s : message %s inattendu dans le Contract Net", self.livreur_id, performative
                )
        except MessageInvalide as e:
            journal.warning("Livreur %s : message illisible (%s)", self.livreur_id, e)
            self._sortie.put_nowait(repondre(message, NOT_UNDERSTOOD, {"erreur": str(e)}))

    async def evaluer(self, appel: AppelOffres) -> Proposition | Refus:
        """Décide d'une offre ou d'un refus motivé pour un appel d'offres (sans rien envoyer).

        Contrôles dans l'ordre : hors ligne, véhicule, poids, saturation, ancienneté du GPS, zone,
        puis itinéraire réel. Le coût vient de `modele/cout.py` (durée jusqu'à l'enlèvement +
        pénalité par course portée).
        """

        def refus(motif: str, detail: str | None = None) -> Refus:
            return Refus(appel.commande_id, appel.tour, motif, detail)

        if self.arret_en_cours:
            return refus(HORS_LIGNE)
        if appel.type_vehicule is not None and appel.type_vehicule != self.type_vehicule:
            return refus(VEHICULE, f"véhicule exigé : {appel.type_vehicule}")
        try:
            vehicule = TypeVehicule(self.type_vehicule)
        except ValueError:
            return refus(VEHICULE, f"véhicule inconnu : {self.type_vehicule}")
        if appel.poids_kg is not None and appel.poids_kg > self.capacite_kg:
            return refus(TROP_LOURD, f"{appel.poids_kg} kg pour {self.capacite_kg} kg de capacité")
        if self.charge >= parametres.livreur_charge_max:
            return refus(SATURE, f"{self.charge} courses déjà portées")
        age_max = timedelta(seconds=parametres.position_age_max_secondes)
        if self.position is None or self.mesure_le is None or self._horloge() - self.mesure_le > age_max:
            return refus(GPS_ANCIEN)
        depart, enlevement = Point(*self.position), Point(*appel.enlevement)
        if itineraires.distance_vol_d_oiseau_m(depart, enlevement) > appel.rayon_m:
            return refus(HORS_ZONE)
        try:
            trajet = await self._itineraire(depart, enlevement, vehicule, repli=True)
        except (ErreurItineraire, ValueError) as e:
            return refus(ITINERAIRE, f"{type(e).__name__}: {e}")
        cout = cout_itineraire(
            trajet.duree_s, self.charge, penalite_s=parametres.attribution_penalite_charge_s
        )
        return Proposition(
            appel.commande_id, appel.tour, cout, trajet.duree_s, 0.0, self.charge, trajet.estime
        )

    @staticmethod
    def _verifier_fil(message: Message, commande_id: str, tour: int) -> None:
        """Le fil du message doit être `<commande_id>:<tour>`, comme son contenu."""
        if message.thread != conversation(commande_id, tour):
            raise MessageInvalide(f"fil de conversation incohérent : {message.thread!r}")

    async def _repondre_cfp(self, message: Message) -> None:
        appel = AppelOffres.depuis_contenu(lire_contenu(message))
        self._verifier_fil(message, appel.commande_id, appel.tour)
        verdict = await self.evaluer(appel)
        if isinstance(verdict, Proposition):
            self._purger_offres_perimees()
            self.offres[message.thread] = OffreEnCours(appel.commande_id, appel.tour)
            self._sortie.put_nowait(repondre(message, PROPOSE, verdict.vers_contenu()))
        else:
            self._sortie.put_nowait(repondre(message, REFUSE, verdict.vers_contenu()))

    def _purger_offres_perimees(self) -> None:
        """Oublie les offres jamais décidées (Dispatcher tombé en route) : elles ne doivent pas s'accumuler."""
        maintenant = asyncio.get_running_loop().time()
        for fil, offre in list(self.offres.items()):
            if offre.etat == ETAT_PROPOSEE and maintenant - offre.cree_le > _DUREE_DE_VIE_PROPOSITION_S:
                del self.offres[fil]

    async def _accepter(self, message: Message) -> None:
        """Le Dispatcher nous retient : réserver une place, faire proposer la course, attendre 30 s."""
        decision = Decision.depuis_contenu(lire_contenu(message))
        self._verifier_fil(message, decision.commande_id, decision.tour)
        fil = message.thread
        offre = self.offres.get(fil)
        if (
            offre is None
            or offre.etat != ETAT_PROPOSEE
            or (offre.commande_id, offre.tour) != (decision.commande_id, decision.tour)
        ):
            self._sortie.put_nowait(
                repondre(
                    message,
                    FAILURE,
                    {"commande_id": decision.commande_id, "tour": decision.tour, "motif": "proposition_inconnue"},
                )
            )
            return
        if self.charge >= parametres.livreur_charge_max:  # une autre course s'est glissée entre-temps
            del self.offres[fil]
            self._sortie.put_nowait(
                repondre(message, CANCEL, Annulation(offre.commande_id, offre.tour, SATURE).vers_contenu())
            )
            return

        offre.etat = ETAT_RESERVEE
        self.changer_charge(self.charge + 1)
        delai = decision.delai_confirmation_s or self.confirmation_s
        attente: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        self._attentes[offre.commande_id] = attente
        try:
            await self._annoncer_course(offre, delai)
            try:
                reponse = await asyncio.wait_for(attente, delai)
            except TimeoutError:
                reponse = EXPIREE
        finally:
            self._attentes.pop(offre.commande_id, None)

        if reponse == _CONFIRMEE:
            offre.etat = ETAT_CONFIRMEE
            self._sortie.put_nowait(
                repondre(message, INFORM, Confirmation(offre.commande_id, offre.tour).vers_contenu())
            )
            return
        # Le livreur a décliné, n'a pas répondu, ou le Dispatcher a retiré l'offre : la place se libère.
        self.offres.pop(fil, None)
        self.changer_charge(max(0, self.charge - 1))
        if reponse != _REJETEE:
            motif = DECLINEE if reponse == _DECLINEE else EXPIREE
            self._sortie.put_nowait(
                repondre(message, CANCEL, Annulation(offre.commande_id, offre.tour, motif).vers_contenu())
            )

    def _rejeter(self, message: Message) -> None:
        decision = Decision.depuis_contenu(lire_contenu(message))
        self._verifier_fil(message, decision.commande_id, decision.tour)
        offre = self.offres.get(message.thread)
        if offre is None or (offre.commande_id, offre.tour) != (decision.commande_id, decision.tour):
            return
        if offre.etat == ETAT_PROPOSEE:
            del self.offres[message.thread]
        elif offre.etat == ETAT_RESERVEE:
            attente = self._attentes.get(offre.commande_id)
            if attente is not None and not attente.done():
                attente.set_result(_REJETEE)  # _accepter libère la place
        else:  # confirmée puis retirée (commande annulée pendant la confirmation)
            self.liberer_course(offre.commande_id)

    async def _annoncer_course(self, offre: OffreEnCours, delai_s: float) -> None:
        """Annonce à l'API que la course est proposée au livreur (l'application la lui présente)."""
        expire_le = self._horloge() + timedelta(seconds=delai_s)
        redis = self._redis_fn()
        try:
            await evenements.publier(
                redis,
                evenements.COURSE_PROPOSEE,
                livreur_id=self.livreur_id,
                commande_id=offre.commande_id,
                tour=offre.tour,
                expire_le=expire_le.isoformat(),
            )
        finally:
            await redis.aclose()

    def confirmation_recue(self, commande_id: str, accepte: bool) -> bool:
        """Réponse du livreur humain, transmise par le gestionnaire (événements Redis).

        Renvoie False si aucune proposition n'attend cette réponse (délai dépassé, offre retirée).
        """
        attente = self._attentes.get(commande_id)
        if attente is None or attente.done():
            journal.info(
                "Livreur %s : réponse sur la commande %s sans proposition en attente (en retard ?)",
                self.livreur_id,
                commande_id,
            )
            return False
        attente.set_result(_CONFIRMEE if accepte else _DECLINEE)
        return True

    def liberer_course(self, commande_id: str) -> bool:
        """Libère la place d'une course portée (terminée, ou retirée après confirmation).

        Les lots suivants (enlèvement, livraison) l'appelleront à la fin de chaque course.
        """
        for fil, offre in list(self.offres.items()):
            if offre.commande_id == commande_id and offre.etat in (ETAT_RESERVEE, ETAT_CONFIRMEE):
                del self.offres[fil]
                self.changer_charge(max(0, self.charge - 1))
                return True
        return False


async def creer_agent_livreur(
    livreur_id: str,
    type_vehicule: str,
    capacite_kg: int,
    *,
    position: tuple[float, float] | None = None,
    mesure_le: datetime | None = None,
    annuaire_jid: str | None = None,
) -> AgentLivreur:
    """Crée l'agent d'un livreur et le connecte au serveur XMPP."""
    agent = AgentLivreur(
        jid_livreur(livreur_id),
        parametres.xmpp_mot_de_passe_agents,
        livreur_id=livreur_id,
        type_vehicule=type_vehicule,
        capacite_kg=capacite_kg,
        position=position,
        mesure_le=mesure_le,
        annuaire_jid=annuaire_jid,
    )
    await agent.start(auto_register=True)
    return agent
