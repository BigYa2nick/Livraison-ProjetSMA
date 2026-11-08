"""Agent Commande : suit l'attribution d'une commande jusqu'à ce qu'un livreur la prenne.

Il existe de la validation de la commande jusqu'à son attribution (docs/conception/
03-sequences-protocole.md, S2 à S4). Les lots suivants lui feront suivre aussi l'enlèvement et la
livraison. Son rôle :

- ouvrir chaque **tour** (numéro et rayon de recherche écrits en base, D-04), puis demander au
  Dispatcher de lancer l'appel d'offres ;
- après un tour **sans offre**, patienter puis relancer avec un rayon élargi (F-33) ;
- après un livreur qui **décline** ou laisse passer les 30 secondes, relancer tout de suite : le
  Dispatcher écarte ce livreur (F-34) ;
- après `ATTRIBUTION_TOURS_MAX` tours sans succès, **alerter le répartiteur** (réattribution manuelle,
  F-62) et s'arrêter : la commande reste en recherche de livreur.

Les messages envoyés depuis l'extérieur d'un comportement passent par une file, comme pour l'agent
Livreur : SPADE n'autorise l'envoi qu'à l'intérieur d'un comportement.
"""

import asyncio
import logging
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import Any

from spade.agent import Agent
from spade.behaviour import CyclicBehaviour
from spade.message import Message
from spade.template import Template

from livraison.agents.protocole import (
    ECHEC_COMMANDE,
    ECHEC_INTERNE,
    FAILURE,
    INFORM,
    ONTO_ATTRIBUTION,
    ONTO_RESULTAT,
    REQUEST,
    RESULTAT_ATTRIBUEE,
    DemandeAttribution,
    EchecAttribution,
    MessageInvalide,
    ResultatAttribution,
    conversation,
    creer_message,
    jid_commande,
    jid_dispatcher,
    lire_contenu,
)
from livraison.config import parametres
from livraison.modele import attribution as regles
from livraison.persistance.session import FabriqueSession
from livraison.services import attribution as bd
from livraison.services import evenements
from livraison.services.redis_client import creer_redis

journal = logging.getLogger("livraison.agents.commande")

# Issues possibles de l'agent
ATTRIBUEE = "attribuee"  # un livreur a confirmé
EPUISEE = "epuisee"  # tous les tours ont été faits : le répartiteur est alerté
ABANDONNEE = "abandonnee"  # la commande n'est plus à attribuer (annulée, introuvable...)

_MARGE_REPONSE_S = 5.0  # au-delà des délais du Dispatcher : écritures en base, transport XMPP


class AgentCommande(Agent):
    def __init__(
        self,
        jid: str,
        password: str,
        *,
        commande_id: str,
        fabrique_session: Callable[[], AbstractAsyncContextManager[Any]] = FabriqueSession,
        dispatcher_jid: str | None = None,
        redis_fn: Callable[[], Any] | None = None,
        attente_max_s: float | None = None,
        port: int | None = None,
    ):
        super().__init__(jid, password, port=port or parametres.xmpp_port)
        self.commande_id = commande_id
        self.dispatcher_jid = dispatcher_jid or jid_dispatcher()
        self._bd = fabrique_session
        self._redis_fn = redis_fn or creer_redis
        self._attente_max_s = attente_max_s
        self._sortie: asyncio.Queue[Message] = asyncio.Queue()
        self._entrants: asyncio.Queue[Message] = asyncio.Queue()
        self.fini = asyncio.Event()  # levé quand l'agent a terminé son travail
        self.issue: str | None = None
        self.tour = 0  # dernier tour lancé
        self._tache: asyncio.Task | None = None

    # -- comportements ---------------------------------------------------------------------------

    class Emettre(CyclicBehaviour):
        async def run(self):
            message = await self.agent._sortie.get()
            await self.send(message)

    class Recevoir(CyclicBehaviour):
        async def run(self):
            message = await self.receive(timeout=1)
            if message is not None:
                self.agent.recevoir(message)

    async def setup(self):
        self.add_behaviour(self.Emettre())
        self.add_behaviour(self.Recevoir(), Template(metadata={"ontology": ONTO_RESULTAT}))
        self.demarrer()

    def demarrer(self) -> asyncio.Task:
        """Lance le pilotage de la commande (appelé par `setup`, ou directement dans les tests)."""
        self._tache = asyncio.get_running_loop().create_task(self.piloter())
        return self._tache

    async def arreter(self) -> None:
        if self._tache is not None and not self._tache.done():
            self._tache.cancel()
            await asyncio.gather(self._tache, return_exceptions=True)
        await self.stop()

    def recevoir(self, message: Message) -> None:
        """Ne garde que ce que le Dispatcher nous envoie."""
        if str(message.sender.bare) != self.dispatcher_jid:
            journal.warning("Commande %s : message de %s ignoré", self.commande_id, message.sender)
            return
        self._entrants.put_nowait(message)

    # -- pilotage --------------------------------------------------------------------------------

    @property
    def attente_max_s(self) -> float:
        """Temps qu'on accorde au Dispatcher pour répondre : il couvre la confirmation du livreur."""
        if self._attente_max_s is not None:
            return self._attente_max_s
        return (
            parametres.attribution_annuaire_attente_s
            + parametres.attribution_collecte_s
            + parametres.attribution_confirmation_s
            + parametres.attribution_marge_s
            + _MARGE_REPONSE_S
        )

    async def piloter(self) -> None:
        try:
            self.issue = await self._boucle()
        except asyncio.CancelledError:
            raise
        except Exception:
            journal.exception("Commande %s : arrêt sur erreur", self.commande_id)
            raise
        finally:
            self.fini.set()

    async def _boucle(self) -> str:
        while True:
            try:
                async with self._bd() as session:
                    tour = await bd.preparer_tour(session, self.commande_id)
                    await session.commit()
            except bd.ToursEpuises as e:
                self.tour = e.tours
                await self._alerter()
                return EPUISEE
            except bd.AttributionImpossible as e:
                journal.info("Commande %s : plus à attribuer (%s)", self.commande_id, e)
                return ABANDONNEE
            self.tour = tour.tour

            reponse = await self._demander(tour.tour, tour.rayon_m)
            if isinstance(reponse, ResultatAttribution):
                if reponse.resultat == RESULTAT_ATTRIBUEE:
                    journal.info("Commande %s attribuée au livreur %s", self.commande_id, reponse.livreur_id)
                    return ATTRIBUEE
                journal.info(
                    "Commande %s : le livreur %s n'a pas pris la course (%s), nouveau tour",
                    self.commande_id,
                    reponse.livreur_id,
                    reponse.motif,
                )
                continue  # un autre livreur tout de suite, sans pause

            if reponse.motif == ECHEC_COMMANDE:
                journal.info("Commande %s abandonnée : %s", self.commande_id, reponse.detail)
                return ABANDONNEE
            journal.info(
                "Commande %s : tour %d sans livreur (%s)", self.commande_id, tour.tour, reponse.motif
            )
            if regles.tours_epuises(tour.tour, parametres.attribution_tours_max):
                await self._alerter()
                return EPUISEE
            await asyncio.sleep(parametres.attribution_pause_s)  # puis un rayon élargi

    async def _demander(self, tour: int, rayon_m: int) -> ResultatAttribution | EchecAttribution:
        """Demande l'attribution au Dispatcher et attend sa réponse sur ce tour."""
        fil = conversation(self.commande_id, tour)
        self._sortie.put_nowait(
            creer_message(
                self.dispatcher_jid,
                REQUEST,
                ONTO_ATTRIBUTION,
                DemandeAttribution(self.commande_id, tour, float(rayon_m)).vers_contenu(),
                thread=fil,
            )
        )
        loop = asyncio.get_running_loop()
        fin = loop.time() + self.attente_max_s
        while True:
            reste = fin - loop.time()
            if reste <= 0:
                return EchecAttribution(
                    self.commande_id, tour, ECHEC_INTERNE, "le Dispatcher n'a pas répondu"
                )
            try:
                message = await asyncio.wait_for(self._entrants.get(), reste)
            except TimeoutError:
                continue
            if message.thread != fil:
                continue  # réponse d'un tour précédent, arrivée en retard
            try:
                contenu = lire_contenu(message)
                performative = message.get_metadata("performative")
                if performative == INFORM:
                    return ResultatAttribution.depuis_contenu(contenu)
                if performative == FAILURE:
                    return EchecAttribution.depuis_contenu(contenu)
                raise MessageInvalide(f"performative inattendue : {performative}")
            except MessageInvalide as e:
                journal.warning("Commande %s : réponse illisible (%s)", self.commande_id, e)
                return EchecAttribution(self.commande_id, tour, ECHEC_INTERNE, str(e))

    async def _alerter(self) -> None:
        """Aucun livreur après tous les tours : alerte pour le répartiteur (console, F-62)."""
        async with self._bd() as session:
            await bd.signaler_echec(session, self.commande_id, self.tour)
            await session.commit()
        journal.warning("Commande %s : aucun livreur après %d tours", self.commande_id, self.tour)
        redis = self._redis_fn()
        try:
            await evenements.publier(
                redis, evenements.ATTRIBUTION_ECHOUEE, commande_id=self.commande_id, tours=self.tour
            )
        finally:
            await redis.aclose()


async def creer_agent_commande(commande_id: str, *, dispatcher_jid: str | None = None) -> AgentCommande:
    """Crée l'agent d'une commande et le connecte au serveur XMPP."""
    agent = AgentCommande(
        jid_commande(commande_id),
        parametres.xmpp_mot_de_passe_agents,
        commande_id=commande_id,
        dispatcher_jid=dispatcher_jid,
    )
    await agent.start(auto_register=True)
    return agent
