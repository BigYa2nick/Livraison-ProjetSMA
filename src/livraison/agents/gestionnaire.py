"""Gestionnaire d'agents : crée l'agent d'un livreur au passage en ligne, le retire quand il part.

C'est le seul programme qui écoute Redis (décision D-03). Il reçoit :
- les événements de l'API (`livreur_en_ligne`, `livreur_hors_ligne`) : création et retrait
  immédiats ;
- les positions GPS (canal `positions`) : transmises à l'agent concerné ;
- la réponse du livreur à une course proposée (`course_confirmee`, `course_declinee`) : transmise à
  son agent, qui l'attend pendant 30 secondes (F-34) ;
- `commande_validee` : transmis au gestionnaire des commandes (`gestionnaire_commandes.py`).

Un événement n'est pas durable (D-04) : le gestionnaire compare donc régulièrement la **base de
données** (la vérité) et ses agents, et corrige l'écart. C'est aussi ce qui reconstruit tout après
un redémarrage. Pour éviter de défaire ce qu'un événement vient de faire alors que la base n'est
pas encore à jour, un agent créé ou retiré récemment n'est pas retouché par la réconciliation
(délai de grâce).
"""

import asyncio
import contextlib
import json
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from livraison.agents.gestionnaire_commandes import GestionnaireCommandes
from livraison.agents.livreur import creer_agent_livreur
from livraison.config import parametres
from livraison.persistance.modeles import Livreur
from livraison.persistance.session import FabriqueSession
from livraison.services.evenements import (
    CANAL_EVENEMENTS,
    COMMANDE_VALIDEE,
    COURSE_CONFIRMEE,
    COURSE_DECLINEE,
    EVENEMENTS_DES_AGENTS,
    LIVREUR_EN_LIGNE,
    LIVREUR_HORS_LIGNE,
)
from livraison.services.positions import CANAL_POSITIONS, cle_position
from livraison.services.redis_client import creer_redis

journal = logging.getLogger("livraison.agents.gestionnaire")

DELAI_REESSAI_REDIS_S = 2.0


class AgentGere(Protocol):
    """Ce que le gestionnaire demande à un agent Livreur."""

    def position_recue(self, latitude: float, longitude: float, mesure_le: datetime) -> None: ...

    def confirmation_recue(self, commande_id: str, accepte: bool) -> bool: ...

    async def arreter(self) -> None: ...


@dataclass(frozen=True)
class LivreurEnLigne:
    livreur_id: str
    type_vehicule: str
    capacite_kg: int


FabriqueAgent = Callable[..., Awaitable[AgentGere]]
LectureEnLigne = Callable[[], Awaitable[list[LivreurEnLigne]]]


async def lire_livreurs_en_ligne(session: AsyncSession) -> list[LivreurEnLigne]:
    """Les livreurs que la base déclare en ligne (la vérité, D-04)."""
    lignes = await session.execute(
        select(Livreur.utilisateur_id, Livreur.type_vehicule, Livreur.capacite_kg).where(
            Livreur.en_ligne.is_(True)
        )
    )
    return [
        LivreurEnLigne(str(i), getattr(v, "value", v), capacite) for i, v, capacite in lignes.all()
    ]


async def _lire_en_ligne_par_defaut() -> list[LivreurEnLigne]:
    async with FabriqueSession() as session:
        return await lire_livreurs_en_ligne(session)


class GestionnaireAgents:
    def __init__(
        self,
        *,
        fabrique: FabriqueAgent = creer_agent_livreur,
        lire_en_ligne: LectureEnLigne = _lire_en_ligne_par_defaut,
        creer_redis_fn: Callable[[], Redis] = creer_redis,
        horloge: Callable[[], float] = time.monotonic,
        delai_grace_s: float | None = None,
        commandes: GestionnaireCommandes | None = None,
    ):
        self.agents: dict[str, AgentGere] = {}
        self.commandes = commandes  # agents Commande ; None : le gestionnaire ne s'en occupe pas
        self._fabrique = fabrique
        self._lire_en_ligne = lire_en_ligne
        self._creer_redis = creer_redis_fn
        self._horloge = horloge
        self._grace = parametres.agents_reconciliation_s if delai_grace_s is None else delai_grace_s
        self._modifie_le: dict[str, float] = {}  # dernière création ou dernier retrait, par livreur
        self._taches: list[asyncio.Task] = []
        self._redis_lecture: Redis | None = None

    # -- création et retrait ---------------------------------------------------------------------

    async def _position_initiale(self, livreur_id: str):
        """Dernière position connue dans Redis : un agent créé tard sait déjà où est son livreur."""
        try:
            if self._redis_lecture is None:
                self._redis_lecture = self._creer_redis()
            donnees = await self._redis_lecture.hgetall(cle_position(livreur_id))
            if not donnees:
                return None, None
            return (
                (float(donnees["latitude"]), float(donnees["longitude"])),
                datetime.fromisoformat(donnees["mesure_le"]),
            )
        except (RedisError, OSError, KeyError, ValueError) as e:
            journal.warning(
                "Position initiale de %s illisible (%s : %s)", livreur_id, type(e).__name__, e
            )
            return None, None

    async def creer(self, livreur: LivreurEnLigne) -> bool:
        if livreur.livreur_id in self.agents:
            return False
        position, mesure_le = await self._position_initiale(livreur.livreur_id)
        try:
            agent = await self._fabrique(
                livreur.livreur_id,
                livreur.type_vehicule,
                livreur.capacite_kg,
                position=position,
                mesure_le=mesure_le,
            )
        except Exception:  # noqa: BLE001 - un agent qui ne démarre pas ne doit pas arrêter les autres
            journal.exception("Agent du livreur %s : démarrage impossible", livreur.livreur_id)
            return False
        self.agents[livreur.livreur_id] = agent
        self._modifie_le[livreur.livreur_id] = self._horloge()
        journal.info(
            "Agent créé pour le livreur %s (%d agents)", livreur.livreur_id, len(self.agents)
        )
        return True

    async def retirer(self, livreur_id: str) -> bool:
        agent = self.agents.pop(livreur_id, None)
        if agent is None:
            return False
        self._modifie_le[livreur_id] = self._horloge()
        try:
            await agent.arreter()
        except Exception:  # noqa: BLE001 - l'agent est de toute façon retiré de notre liste
            journal.exception("Agent du livreur %s : arrêt incomplet", livreur_id)
        journal.info("Agent retiré pour le livreur %s (%d agents)", livreur_id, len(self.agents))
        return True

    # -- événements ------------------------------------------------------------------------------

    async def traiter_evenement(self, evenement: dict[str, Any]) -> None:
        genre = evenement.get("type")
        if genre == COMMANDE_VALIDEE:
            if self.commandes is not None:
                await self.commandes.traiter_evenement(evenement)
            return
        if genre in EVENEMENTS_DES_AGENTS:
            return  # annoncé par les agents eux-mêmes, pour l'API et la console : rien à faire ici
        livreur_id = evenement.get("livreur_id")
        if not isinstance(livreur_id, str) or not livreur_id:
            journal.warning("Événement sans livreur_id ignoré : %s", evenement)
            return
        if genre == LIVREUR_EN_LIGNE:
            vehicule, capacite = evenement.get("type_vehicule"), evenement.get("capacite_kg")
            if not isinstance(vehicule, str) or not isinstance(capacite, int) or capacite <= 0:
                journal.warning("Événement %s incomplet ignoré : %s", genre, evenement)
                return
            await self.creer(LivreurEnLigne(livreur_id, vehicule, capacite))
        elif genre == LIVREUR_HORS_LIGNE:
            await self.retirer(livreur_id)
        elif genre == "position":
            self._transmettre_position(livreur_id, evenement)
        elif genre in (COURSE_CONFIRMEE, COURSE_DECLINEE):
            self._transmettre_confirmation(livreur_id, evenement, accepte=genre == COURSE_CONFIRMEE)
        # tout autre type (par exemple « hors_ligne » du canal des positions) ne nous concerne pas

    def _transmettre_position(self, livreur_id: str, evenement: dict[str, Any]) -> None:
        agent = self.agents.get(livreur_id)
        if agent is None:
            return  # pas (encore) d'agent : la réconciliation le créera avec la dernière position
        try:
            agent.position_recue(
                float(evenement["latitude"]),
                float(evenement["longitude"]),
                datetime.fromisoformat(evenement["mesure_le"]),
            )
        except (KeyError, TypeError, ValueError) as e:
            journal.warning("Position illisible pour %s (%s : %s)", livreur_id, type(e).__name__, e)

    def _transmettre_confirmation(
        self, livreur_id: str, evenement: dict[str, Any], *, accepte: bool
    ) -> None:
        commande_id = evenement.get("commande_id")
        if not isinstance(commande_id, str) or not commande_id:
            journal.warning("Réponse à une course sans commande_id ignorée : %s", evenement)
            return
        agent = self.agents.get(livreur_id)
        if agent is None:
            journal.info("Réponse du livreur %s sans agent (hors ligne ?), ignorée", livreur_id)
            return
        agent.confirmation_recue(commande_id, accepte)

    async def traiter_message_brut(self, canal: str, donnees: str) -> None:
        try:
            evenement = json.loads(donnees)
        except json.JSONDecodeError:
            journal.warning("Message non JSON sur « %s » ignoré", canal)
            return
        if not isinstance(evenement, dict):
            journal.warning("Message sur « %s » ignoré : objet JSON attendu", canal)
            return
        await self.traiter_evenement(evenement)

    # -- réconciliation avec la base ---------------------------------------------------------------

    def _recent(self, livreur_id: str) -> bool:
        modifie = self._modifie_le.get(livreur_id)
        return modifie is not None and self._horloge() - modifie < self._grace

    async def reconcilier(self) -> tuple[list[str], list[str]]:
        """Aligne les agents sur la base. Renvoie (créés, retirés)."""
        attendus = {livreur.livreur_id: livreur for livreur in await self._lire_en_ligne()}
        crees, retires = [], []
        for livreur_id, livreur in attendus.items():
            if livreur_id not in self.agents and not self._recent(livreur_id):
                if await self.creer(livreur):
                    crees.append(livreur_id)
        for livreur_id in list(self.agents):
            if livreur_id not in attendus and not self._recent(livreur_id):
                if await self.retirer(livreur_id):
                    retires.append(livreur_id)
        if crees or retires:
            journal.info("Réconciliation : %d créé(s), %d retiré(s)", len(crees), len(retires))
        if self.commandes is not None:
            await self.commandes.reconcilier()
        return crees, retires

    # -- boucles ---------------------------------------------------------------------------------

    async def _ecouter(self) -> None:
        while True:
            redis = self._creer_redis()
            pubsub = redis.pubsub()
            try:
                await pubsub.subscribe(CANAL_EVENEMENTS, CANAL_POSITIONS)
                await self.reconcilier()  # rattrape ce qui a pu être manqué pendant une coupure
                async for message in pubsub.listen():
                    if message["type"] == "message":
                        await self.traiter_message_brut(message["channel"], message["data"])
            except (RedisError, OSError) as e:
                journal.warning(
                    "Redis indisponible (%s : %s), nouvelle tentative dans %.0f s",
                    type(e).__name__,
                    e,
                    DELAI_REESSAI_REDIS_S,
                )
                await asyncio.sleep(DELAI_REESSAI_REDIS_S)
            finally:
                with contextlib.suppress(RedisError, OSError):
                    await pubsub.aclose()
                    await redis.aclose()

    async def _boucle_reconciliation(self) -> None:
        while True:
            await asyncio.sleep(parametres.agents_reconciliation_s)
            try:
                await self.reconcilier()
            except Exception:  # noqa: BLE001 - une réconciliation ratée ne doit pas arrêter la boucle
                journal.exception("Réconciliation en échec, nouvelle tentative au prochain tour")

    async def executer(self) -> None:
        """Tourne jusqu'à l'annulation de la tâche (Ctrl+C)."""
        self._taches = [
            asyncio.create_task(self._ecouter(), name="ecoute-redis"),
            asyncio.create_task(self._boucle_reconciliation(), name="reconciliation"),
        ]
        await asyncio.gather(*self._taches)

    async def arreter(self) -> None:
        for tache in self._taches:
            tache.cancel()
        await asyncio.gather(*self._taches, return_exceptions=True)
        for livreur_id in list(self.agents):
            await self.retirer(livreur_id)
        if self.commandes is not None:
            await self.commandes.arreter()
        if self._redis_lecture is not None:
            with contextlib.suppress(RedisError, OSError):
                await self._redis_lecture.aclose()
            self._redis_lecture = None
