"""Agent Dispatcher : mène les appels d'offres (Contract Net) et désigne le livreur de chaque commande.

C'est le seul agent qui attribue (décision D-06) : les agents Livreur proposent, lui décide. Une
conversation d'attribution (`<commande_id>:<tour>`, docs/conception/03-sequences-protocole.md, S2 à S4) :

1. l'agent Commande lui demande d'attribuer la commande (REQUEST) ;
2. il interroge l'Annuaire (QUERY-REF), écarte les livreurs qui ont déjà décliné cette commande et ne
   garde que ceux que la base déclare en ligne et autorisés à livrer ;
3. il envoie un CFP à chacun et collecte les PROPOSE / REFUSE pendant `ATTRIBUTION_COLLECTE_S` ;
4. il écrit les offres en base, retient la moins chère (les garde-fous de la base, dont la charge du
   livreur, peuvent la refuser : on passe alors à la suivante), envoie ACCEPT-PROPOSAL au gagnant et
   REJECT-PROPOSAL aux autres ;
5. il attend la confirmation du livreur humain (30 s + une marge) : confirmée, la commande passe à
   `attribuee` ; déclinée ou expirée, l'offre est marquée `declinee` et l'agent Commande lance un
   nouveau tour sans ce livreur.

Plusieurs conversations tournent en parallèle (une tâche chacune). Tous les messages d'une conversation
sont journalisés dans la table `evenement` (F-63). Voir docs/contract-net.md.
"""

import asyncio
import logging
import math
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from functools import reduce
from typing import Any

from spade.agent import Agent
from spade.behaviour import CyclicBehaviour
from spade.message import Message
from spade.template import Template

from livraison.agents.protocole import (
    ACCEPT_PROPOSAL,
    CANCEL,
    CFP,
    ECHEC_ANNUAIRE,
    ECHEC_COMMANDE,
    ECHEC_INTERNE,
    ECHEC_SANS_OFFRE,
    FAILURE,
    INFORM,
    NOT_UNDERSTOOD,
    ONTO_ATTRIBUTION,
    ONTO_CONTRACT_NET,
    ONTO_RECHERCHE,
    ONTO_RESULTAT,
    PROPOSE,
    QUERY_REF,
    REFUSE,
    REJECT_PROPOSAL,
    REQUEST,
    RESULTAT_ATTRIBUEE,
    RESULTAT_REATTRIBUTION,
    Annulation,
    AppelOffres,
    Confirmation,
    DemandeAttribution,
    Decision,
    EchecAttribution,
    MessageInvalide,
    Proposition,
    Recherche,
    Refus,
    ResultatAttribution,
    conversation,
    creer_message,
    jid_annuaire,
    jid_commande,
    jid_livreur,
    lire_contenu,
    livreur_id_depuis_jid,
    repondre,
)
from livraison.config import parametres
from livraison.modele import attribution as regles
from livraison.modele.cout import Offre
from livraison.persistance.enums import EtatCommande
from livraison.persistance.session import FabriqueSession
from livraison.services import attribution as bd
from livraison.services import evenements
from livraison.services.redis_client import creer_redis

journal = logging.getLogger("livraison.agents.dispatcher")

_ONTOLOGIES = (ONTO_ATTRIBUTION, ONTO_CONTRACT_NET, ONTO_RECHERCHE)


@dataclass
class Conversation:
    """Une conversation d'attribution en cours : ses messages entrants et son journal en attente."""

    fil: str
    commande_id: str
    tour: int
    commande_jid: str  # l'agent Commande qui a fait la demande
    entrants: asyncio.Queue[Message] = field(default_factory=asyncio.Queue)
    a_journaliser: list[bd.EntreeJournal] = field(default_factory=list)


class AgentDispatcher(Agent):
    def __init__(
        self,
        jid: str,
        password: str,
        *,
        fabrique_session: Callable[[], AbstractAsyncContextManager[Any]] = FabriqueSession,
        annuaire_jid: str | None = None,
        redis_fn: Callable[[], Any] | None = None,
        port: int | None = None,
    ):
        super().__init__(jid, password, port=port or parametres.xmpp_port)
        self.annuaire_jid = annuaire_jid or jid_annuaire()
        self._bd = fabrique_session  # `async with self._bd() as session` : une session courte
        self._redis_fn = redis_fn or creer_redis
        self._sortie: asyncio.Queue[Message] = asyncio.Queue()
        self.conversations: dict[str, Conversation] = {}
        # Les décisions « quel livreur pour cette commande » se prennent une par une (D-06) ; la base
        # verrouille aussi la ligne du livreur, c'est la garantie qui survit à un second Dispatcher.
        self._verrou_decision = asyncio.Lock()
        self._taches: set[asyncio.Task] = set()

    # -- comportements ---------------------------------------------------------------------------

    class Emettre(CyclicBehaviour):
        """Envoie les messages que les conversations ont déposés dans la file."""

        async def run(self):
            message = await self.agent._sortie.get()
            await self.send(message)

    class Recevoir(CyclicBehaviour):
        async def run(self):
            message = await self.receive(timeout=1)
            if message is not None:
                self.agent.recevoir(message)

    async def setup(self):
        modele = reduce(
            lambda a, b: a | b, (Template(metadata={"ontology": o}) for o in _ONTOLOGIES)
        )
        self.add_behaviour(self.Emettre())
        self.add_behaviour(self.Recevoir(), modele)
        journal.info("Dispatcher prêt (%s)", self.jid)

    async def arreter(self) -> None:
        for tache in list(self._taches):
            tache.cancel()
        await self.stop()

    # -- aiguillage des messages ---------------------------------------------------------------------

    @property
    def _mon_jid(self) -> str:
        return str(self.jid).split("/")[0]

    def recevoir(self, message: Message) -> None:
        """Une nouvelle demande lance une conversation ; tout autre message rejoint la sienne."""
        ontologie = message.get_metadata("ontology")
        performative = message.get_metadata("performative")
        if ontologie == ONTO_ATTRIBUTION:
            if performative == REQUEST:
                self.lancer(self._conversation(message))
            else:
                journal.warning("Dispatcher : %s inattendu pour %s", performative, ontologie)
            return
        file = self.conversations.get(message.thread or "")
        if file is None:
            journal.info(
                "Dispatcher : message %s hors conversation (fil %s), ignoré", performative, message.thread
            )
            return
        file.entrants.put_nowait(message)

    def lancer(self, coroutine) -> asyncio.Task:
        tache = asyncio.get_running_loop().create_task(coroutine)
        self._taches.add(tache)
        tache.add_done_callback(self._tache_terminee)
        return tache

    def _tache_terminee(self, tache: asyncio.Task) -> None:
        self._taches.discard(tache)
        if not tache.cancelled() and tache.exception() is not None:
            journal.error("Dispatcher : conversation interrompue", exc_info=tache.exception())

    # -- envoi, réception et journal d'une conversation ----------------------------------------------------

    def _noter(self, conv: Conversation, auteur: str, destinataire: str, message: Message) -> None:
        try:
            contenu = lire_contenu(message)
        except MessageInvalide:
            contenu = {"corps_illisible": message.body}
        conv.a_journaliser.append(
            bd.EntreeJournal(
                auteur=auteur,
                destinataire=destinataire,
                performative=message.get_metadata("performative") or "?",
                detail={
                    "ontologie": message.get_metadata("ontology"),
                    "conversation": conv.fil,
                    "contenu": contenu,
                },
            )
        )

    def _envoyer(self, conv: Conversation, message: Message) -> None:
        self._noter(conv, self._mon_jid, str(message.to), message)
        self._sortie.put_nowait(message)

    def _vers(self, conv: Conversation, destinataire: str, performative: str, ontologie: str, contenu) -> None:
        self._envoyer(
            conv,
            creer_message(
                destinataire, performative, ontologie, contenu.vers_contenu(), thread=conv.fil
            ),
        )

    async def _attendre(
        self,
        conv: Conversation,
        delai_s: float,
        *,
        ontologie: str,
        accepte: Callable[[Message], bool],
    ) -> Message | None:
        """Prochain message de la conversation qui répond à `accepte`, ou None au bout de `delai_s`."""
        loop = asyncio.get_running_loop()
        fin = loop.time() + delai_s
        while True:
            reste = fin - loop.time()
            if reste <= 0:
                return None
            try:
                message = await asyncio.wait_for(conv.entrants.get(), reste)
            except TimeoutError:
                return None
            emetteur = str(message.sender.bare)
            if message.get_metadata("ontology") != ontologie or not accepte(message):
                journal.warning(
                    "Dispatcher : message %s de %s écarté (conversation %s)",
                    message.get_metadata("performative"),
                    emetteur,
                    conv.fil,
                )
                continue
            self._noter(conv, emetteur, self._mon_jid, message)
            return message

    async def _ecrire_journal(self, conv: Conversation, session) -> None:
        entrees, conv.a_journaliser = conv.a_journaliser, []
        await bd.journaliser_messages(session, conv.commande_id, entrees)

    # -- une conversation d'attribution ------------------------------------------------------------------------

    async def _conversation(self, demande_message: Message) -> None:
        try:
            demande = DemandeAttribution.depuis_contenu(lire_contenu(demande_message))
            if demande_message.thread != conversation(demande.commande_id, demande.tour):
                raise MessageInvalide(f"fil incohérent : {demande_message.thread!r}")
            emetteur = str(demande_message.sender.bare)
            if emetteur != jid_commande(demande.commande_id):
                raise MessageInvalide(f"{emetteur} ne peut pas demander l'attribution de cette commande")
        except MessageInvalide as e:
            journal.warning("Dispatcher : demande refusée (%s)", e)
            self._sortie.put_nowait(repondre(demande_message, NOT_UNDERSTOOD, {"erreur": str(e)}))
            return
        fil = demande_message.thread
        if fil in self.conversations:
            journal.warning("Dispatcher : conversation %s déjà en cours, demande ignorée", fil)
            return
        conv = Conversation(fil, demande.commande_id, demande.tour, emetteur)
        self.conversations[fil] = conv
        self._noter(conv, emetteur, self._mon_jid, demande_message)
        try:
            await self._mener(conv, demande)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 — la commande ne doit jamais rester sans réponse
            journal.exception("Dispatcher : erreur pendant la conversation %s", fil)
            self._echec(conv, ECHEC_INTERNE, f"{type(e).__name__}: {e}")
        finally:
            try:
                async with self._bd() as session:
                    await self._ecrire_journal(conv, session)
                    await session.commit()
            except Exception:  # noqa: BLE001
                journal.exception("Dispatcher : journal de %s non écrit", fil)
            self.conversations.pop(fil, None)

    def _echec(self, conv: Conversation, motif: str, detail: str | None = None) -> None:
        self._vers(
            conv,
            conv.commande_jid,
            FAILURE,
            ONTO_RESULTAT,
            EchecAttribution(conv.commande_id, conv.tour, motif, detail),
        )

    async def _mener(self, conv: Conversation, demande: DemandeAttribution) -> None:
        # 1. la commande, vue par la base
        async with self._bd() as session:
            commande = await bd.lire_commande(session, conv.commande_id)
            exclus = await bd.livreurs_exclus(session, conv.commande_id) if commande else set()
        if commande is None:
            return self._echec(conv, ECHEC_COMMANDE, "commande introuvable")
        if commande.etat != EtatCommande.RECHERCHE_LIVREUR:
            return self._echec(conv, ECHEC_COMMANDE, f"état « {commande.etat} »")

        # 2. les candidats : Annuaire, puis base
        ids = await self._interroger_annuaire(conv, demande, commande, exclus)
        if ids is None:
            return
        async with self._bd() as session:
            candidats = await bd.livreurs_eligibles(session, ids)
        if not candidats:
            return self._echec(conv, ECHEC_SANS_OFFRE, "aucun livreur éligible dans le rayon")

        # 3. appel d'offres
        propositions, refus, silencieux = await self._lancer_appel(conv, demande, commande, candidats)

        # 4. les offres sont écrites avant toute décision
        async with self._bd() as session:
            await bd.enregistrer_reponses(
                session,
                conv.commande_id,
                conv.tour,
                propositions={i: (p.cout, round(p.eta_s)) for i, p in propositions.items()},
                refus=refus,
                silencieux=silencieux,
            )
            await self._ecrire_journal(conv, session)
            await session.commit()
        if not propositions:
            self._rejeter(conv, silencieux)
            return self._echec(conv, ECHEC_SANS_OFFRE, f"{len(refus)} refus, {len(silencieux)} silences")

        # 5. la décision
        gagnant = await self._decider(conv, propositions)
        if gagnant is not None:  # le gagnant d'abord, puis les autres (docs 03, S2)
            self._vers(
                conv,
                jid_livreur(gagnant),
                ACCEPT_PROPOSAL,
                ONTO_CONTRACT_NET,
                Decision(conv.commande_id, conv.tour, parametres.attribution_confirmation_s),
            )
        self._rejeter(conv, [i for i in (*propositions, *silencieux) if i != gagnant])
        if gagnant is None:
            return self._echec(conv, ECHEC_SANS_OFFRE, "toutes les offres ont été écartées par la base")

        # 6. la confirmation du livreur humain
        await self._conclure(conv, gagnant)

    async def _interroger_annuaire(
        self, conv: Conversation, demande: DemandeAttribution, commande: bd.CommandeAAttribuer, exclus: set[str]
    ) -> list[str] | None:
        recherche = Recherche(
            latitude=commande.enlevement[0],
            longitude=commande.enlevement[1],
            rayon_m=demande.rayon_m,
            poids_kg=None if commande.poids_kg is None else math.ceil(commande.poids_kg),
            exclure=tuple(sorted(exclus)),
        )
        self._vers(conv, self.annuaire_jid, QUERY_REF, ONTO_RECHERCHE, recherche)
        reponse = await self._attendre(
            conv,
            parametres.attribution_annuaire_attente_s,
            ontologie=ONTO_RECHERCHE,
            accepte=lambda m: str(m.sender.bare) == self.annuaire_jid,
        )
        if reponse is None:
            self._echec(conv, ECHEC_ANNUAIRE, "l'Annuaire n'a pas répondu")
            return None
        if reponse.get_metadata("performative") != INFORM:
            self._echec(conv, ECHEC_ANNUAIRE, f"réponse {reponse.get_metadata('performative')}")
            return None
        try:
            brut = lire_contenu(reponse).get("candidats") or []
            ids = [str(c["livreur_id"]) for c in brut]
        except (MessageInvalide, KeyError, TypeError) as e:
            self._echec(conv, ECHEC_ANNUAIRE, f"réponse illisible : {e}")
            return None
        return regles.candidats_a_consulter(ids, exclus)

    async def _lancer_appel(
        self,
        conv: Conversation,
        demande: DemandeAttribution,
        commande: bd.CommandeAAttribuer,
        candidats: list[str],
    ) -> tuple[dict[str, Proposition], dict[str, str], list[str]]:
        """Envoie le CFP et collecte les réponses : (propositions, refus, livreurs silencieux)."""
        appel = AppelOffres(
            commande_id=conv.commande_id,
            tour=conv.tour,
            enlevement=commande.enlevement,
            livraison=commande.livraison,
            taille=commande.taille,
            priorite=commande.priorite,
            rayon_m=demande.rayon_m,
            delai_s=parametres.attribution_collecte_s,
            poids_kg=commande.poids_kg,
        )
        for livreur_id in candidats:
            self._vers(conv, jid_livreur(livreur_id), CFP, ONTO_CONTRACT_NET, appel)

        en_attente = set(candidats)
        propositions: dict[str, Proposition] = {}  # dans l'ordre d'arrivée
        refus: dict[str, str] = {}
        loop = asyncio.get_running_loop()
        fin = loop.time() + parametres.attribution_collecte_s
        while en_attente:
            message = await self._attendre(
                conv,
                fin - loop.time(),
                ontologie=ONTO_CONTRACT_NET,
                accepte=lambda m: livreur_id_depuis_jid(str(m.sender.bare)) in en_attente,
            )
            if message is None:
                break
            livreur_id = livreur_id_depuis_jid(str(message.sender.bare))
            performative = message.get_metadata("performative")
            try:
                contenu = lire_contenu(message)
                if performative == PROPOSE:
                    offre = Proposition.depuis_contenu(contenu)
                    self._verifier_tour(conv, offre.commande_id, offre.tour)
                    propositions[livreur_id] = offre
                elif performative == REFUSE:
                    non = Refus.depuis_contenu(contenu)
                    self._verifier_tour(conv, non.commande_id, non.tour)
                    refus[livreur_id] = non.motif
                else:
                    raise MessageInvalide(f"{performative} inattendu pendant la collecte")
            except MessageInvalide as e:
                journal.warning("Dispatcher : réponse de %s ignorée (%s)", livreur_id, e)
                continue
            en_attente.discard(livreur_id)
        return propositions, refus, [i for i in candidats if i in en_attente]

    @staticmethod
    def _verifier_tour(conv: Conversation, commande_id: str, tour: int) -> None:
        if (commande_id, tour) != (conv.commande_id, conv.tour):
            raise MessageInvalide("réponse à un autre appel d'offres")

    async def _decider(self, conv: Conversation, propositions: dict[str, Proposition]) -> str | None:
        """Retient la meilleure offre que la base accepte (D-06, F-32). Renvoie le gagnant, ou None."""
        classement = regles.ordre_de_preference([Offre(i, p.cout) for i, p in propositions.items()])
        async with self._verrou_decision:
            async with self._bd() as session:
                gagnant = None
                for offre in classement:
                    if await bd.retenir_offre(session, conv.commande_id, conv.tour, offre.livreur):
                        gagnant = offre.livreur
                        break
                    journal.info(
                        "Dispatcher : offre de %s écartée par la base (saturé ou commande modifiée)",
                        offre.livreur,
                    )
                await bd.rejeter_les_autres(session, conv.commande_id, conv.tour, gagnant)
                await self._ecrire_journal(conv, session)
                await session.commit()
        return gagnant

    def _rejeter(self, conv: Conversation, livreurs: list[str]) -> None:
        for livreur_id in livreurs:
            self._vers(
                conv,
                jid_livreur(livreur_id),
                REJECT_PROPOSAL,
                ONTO_CONTRACT_NET,
                Decision(conv.commande_id, conv.tour),
            )

    async def _conclure(self, conv: Conversation, gagnant: str) -> None:
        """Attend la confirmation du livreur humain, puis attribue la commande ou demande un nouveau tour."""
        jid_gagnant = jid_livreur(gagnant)
        reponse = await self._attendre(
            conv,
            parametres.attribution_confirmation_s + parametres.attribution_marge_s,
            ontologie=ONTO_CONTRACT_NET,
            accepte=lambda m: str(m.sender.bare) == jid_gagnant,
        )
        performative = None if reponse is None else reponse.get_metadata("performative")
        if performative == INFORM:
            try:
                Confirmation.depuis_contenu(lire_contenu(reponse))
            except MessageInvalide as e:
                journal.warning("Dispatcher : confirmation illisible de %s (%s)", gagnant, e)
                performative = FAILURE
        if performative == INFORM:
            return await self._attribuer(conv, gagnant)

        # Décliné, expiré, saturé, silence ou erreur de l'agent : la commande repart pour un tour.
        motif = "expiree"
        if performative == CANCEL:
            try:
                motif = Annulation.depuis_contenu(lire_contenu(reponse)).motif
            except MessageInvalide:
                motif = "declinee"
        elif performative == FAILURE:
            motif = "proposition_inconnue"  # l'agent ne reconnaît pas l'offre : pas d'exclusion
        async with self._bd() as session:
            await bd.declarer_non_prise(session, conv.commande_id, conv.tour, gagnant, motif)
            await self._ecrire_journal(conv, session)
            await session.commit()
        if reponse is None:  # l'agent ne dit rien : on libère sa réservation s'il vit encore
            self._rejeter(conv, [gagnant])
        self._vers(
            conv,
            conv.commande_jid,
            INFORM,
            ONTO_RESULTAT,
            ResultatAttribution(conv.commande_id, conv.tour, RESULTAT_REATTRIBUTION, gagnant, motif),
        )

    async def _attribuer(self, conv: Conversation, gagnant: str) -> None:
        async with self._bd() as session:
            ok = await bd.confirmer_attribution(session, conv.commande_id, conv.tour, gagnant)
            await self._ecrire_journal(conv, session)
            await session.commit()
        if not ok:  # la commande a été annulée pendant la confirmation
            self._rejeter(conv, [gagnant])  # le livreur récupère sa place
            return self._echec(conv, ECHEC_COMMANDE, "commande plus attribuable après confirmation")
        self._vers(
            conv,
            conv.commande_jid,
            INFORM,
            ONTO_RESULTAT,
            ResultatAttribution(conv.commande_id, conv.tour, RESULTAT_ATTRIBUEE, gagnant),
        )
        redis = self._redis_fn()
        try:
            await evenements.publier(
                redis, evenements.COMMANDE_ATTRIBUEE, commande_id=conv.commande_id, livreur_id=gagnant
            )
        finally:
            await redis.aclose()
