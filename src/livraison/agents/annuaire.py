"""Agent Annuaire : tient la liste des livreurs disponibles (service « livreur »).

Il répond à quatre sortes de messages (voir docs/agents.md) :

| Ontologie                  | Performative | Réponse                                             |
|----------------------------|--------------|-----------------------------------------------------|
| annuaire.inscription       | request      | inform {statut: inscrit}                            |
| annuaire.desinscription    | request      | inform {statut: desinscrit}                         |
| annuaire.maj               | inform       | rien, ou failure {motif: inconnu} si pas inscrit    |
| annuaire.recherche         | query-ref    | inform {total, candidats: [...]}                    |

Un message incompréhensible reçoit `not-understood`, jamais un plantage de l'agent. Un agent ne peut
inscrire, mettre à jour ou retirer que **lui-même** : l'identité est lue dans l'adresse XMPP de
l'émetteur, pas dans le contenu du message.
"""

import logging
from datetime import datetime, timedelta
from functools import reduce

from spade.agent import Agent
from spade.behaviour import CyclicBehaviour, PeriodicBehaviour
from spade.message import Message
from spade.template import Template

from livraison.agents.protocole import (
    FAILURE,
    INFORM,
    NOT_UNDERSTOOD,
    ONTO_DESINSCRIPTION,
    ONTO_INSCRIPTION,
    ONTO_MAJ,
    ONTO_RECHERCHE,
    ONTOLOGIES_ANNUAIRE,
    QUERY_REF,
    REQUEST,
    Inscription,
    MessageInvalide,
    MiseAJour,
    Recherche,
    livreur_id_depuis_jid,
    lire_contenu,
    repondre,
)
from livraison.agents.registre import EntreeLivreur, RegistreLivreurs
from livraison.config import parametres
from livraison.services.itineraires import Point

journal = logging.getLogger("livraison.agents.annuaire")


class IdentiteInvalide(MessageInvalide):
    """L'émetteur prétend agir pour un autre livreur que lui-même."""


class AgentAnnuaire(Agent):
    def __init__(
        self,
        jid: str,
        password: str,
        *,
        port: int | None = None,
        registre: RegistreLivreurs | None = None,
    ):
        super().__init__(jid, password, port=port or parametres.xmpp_port)
        self.registre = registre or RegistreLivreurs(parametres.position_age_max_secondes)

    # -- comportements -----------------------------------------------------------------------

    class Traiter(CyclicBehaviour):
        async def run(self):
            message = await self.receive(timeout=1)
            if message is not None:
                for reponse in self.agent.traiter(message):
                    await self.send(reponse)

    class Purger(PeriodicBehaviour):
        async def run(self):
            for livreur_id in self.agent.registre.purger(parametres.annuaire_ttl_s):
                journal.warning("Livreur %s retiré de l'Annuaire : agent silencieux", livreur_id)

    async def setup(self):
        modele = reduce(
            lambda a, b: a | b, (Template(metadata={"ontology": o}) for o in ONTOLOGIES_ANNUAIRE)
        )
        self.add_behaviour(self.Traiter(), modele)
        # SPADE compare `start_at` à datetime.now() sans fuseau :
        # une date « aware » ferait planter le comportement.
        premiere = datetime.now() + timedelta(seconds=parametres.agents_battement_s)
        self.add_behaviour(self.Purger(period=parametres.agents_battement_s, start_at=premiere))
        journal.info("Annuaire prêt (%s)", self.jid)

    # -- traitement d'un message (sans réseau : testable directement) ------------------------------

    def traiter(self, message: Message) -> list[Message]:
        ontologie = message.get_metadata("ontology")
        performative = message.get_metadata("performative")
        try:
            contenu = lire_contenu(message)
            if ontologie == ONTO_INSCRIPTION and performative == REQUEST:
                return [self._inscrire(message, contenu)]
            if ontologie == ONTO_DESINSCRIPTION and performative == REQUEST:
                return [self._desinscrire(message)]
            if ontologie == ONTO_MAJ and performative == INFORM:
                return self._mettre_a_jour(message, contenu)
            if ontologie == ONTO_RECHERCHE and performative == QUERY_REF:
                return [self._rechercher(message, contenu)]
            motif = f"performative « {performative} » inattendue pour « {ontologie} »"
            return [repondre(message, NOT_UNDERSTOOD, {"motif": motif})]
        except IdentiteInvalide as e:
            journal.warning("Message refusé de %s : %s", message.sender, e)
            return [repondre(message, FAILURE, {"motif": "identite", "detail": str(e)})]
        except MessageInvalide as e:
            journal.warning("Message incompréhensible de %s : %s", message.sender, e)
            return [repondre(message, NOT_UNDERSTOOD, {"motif": str(e)})]

    @staticmethod
    def _emetteur_est(message: Message, livreur_id: str) -> None:
        if livreur_id_depuis_jid(str(message.sender.bare)) != livreur_id:
            raise IdentiteInvalide(f"{message.sender.bare} ne peut pas agir pour {livreur_id}")

    def _inscrire(self, message: Message, contenu: dict) -> Message:
        demande = Inscription.depuis_contenu(contenu)
        self._emetteur_est(message, demande.livreur_id)
        nouveau = self.registre.inscrire(
            EntreeLivreur(
                livreur_id=demande.livreur_id,
                type_vehicule=demande.type_vehicule,
                capacite_kg=demande.capacite_kg,
                charge=demande.charge,
                position=None if demande.position is None else Point(*demande.position),
                mesure_le=demande.mesure_le,
            )
        )
        if nouveau:
            journal.info(
                "Livreur %s inscrit (%d dans l'Annuaire)", demande.livreur_id, len(self.registre)
            )
        return repondre(message, INFORM, {"statut": "inscrit", "nouveau": nouveau})

    def _desinscrire(self, message: Message) -> Message:
        livreur_id = livreur_id_depuis_jid(str(message.sender.bare))
        if livreur_id is None:
            raise IdentiteInvalide(f"{message.sender.bare} n'est pas un agent livreur")
        existait = self.registre.desinscrire(livreur_id)
        if existait:
            journal.info("Livreur %s retiré (%d dans l'Annuaire)", livreur_id, len(self.registre))
        return repondre(message, INFORM, {"statut": "desinscrit", "existait": existait})

    def _mettre_a_jour(self, message: Message, contenu: dict) -> list[Message]:
        maj = MiseAJour.depuis_contenu(contenu)
        self._emetteur_est(message, maj.livreur_id)
        connu = self.registre.mettre_a_jour(
            maj.livreur_id,
            position=None if maj.position is None else Point(*maj.position),
            mesure_le=maj.mesure_le,
            charge=maj.charge,
        )
        if connu:
            return []  # une mise à jour réussie ne demande pas de réponse
        # L'Annuaire a redémarré ou a purgé ce livreur : on lui demande de se réinscrire.
        return [repondre(message, FAILURE, {"motif": "inconnu"})]

    def _rechercher(self, message: Message, contenu: dict) -> Message:
        demande = Recherche.depuis_contenu(contenu)
        candidats = self.registre.rechercher(
            Point(demande.latitude, demande.longitude),
            demande.rayon_m,
            type_vehicule=demande.type_vehicule,
            poids_kg=demande.poids_kg,
            exclure=demande.exclure,
        )
        retenus = candidats[: parametres.annuaire_max_resultats]
        return repondre(
            message,
            INFORM,
            {"total": len(candidats), "candidats": [c.vers_contenu() for c in retenus]},
        )
