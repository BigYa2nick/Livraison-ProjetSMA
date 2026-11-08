"""Gestionnaire des agents Commande : un agent par commande qui attend un livreur.

Même principe que le gestionnaire des livreurs (D-03, D-04) : l'événement `commande_validee` de l'API
crée l'agent tout de suite ; la **base** reste la vérité, et la réconciliation (au démarrage, puis
régulièrement) crée l'agent des commandes qui attendent un livreur sans en avoir. C'est ce qui
reconstruit tout après un redémarrage des agents.

Un agent Commande s'arrête de lui-même quand il a fini son travail (commande attribuée, tours
épuisés, commande annulée) : le gestionnaire le voit et le retire. Ce gestionnaire n'écoute pas Redis
lui-même : le gestionnaire des livreurs lui transmet les événements (un seul abonné Redis).
"""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from livraison.agents.commande import creer_agent_commande
from livraison.config import parametres
from livraison.persistance.session import FabriqueSession
from livraison.services import attribution as bd
from livraison.services.evenements import COMMANDE_VALIDEE

journal = logging.getLogger("livraison.agents.gestionnaire_commandes")


class AgentCommandeGere(Protocol):
    """Ce que le gestionnaire demande à un agent Commande."""

    fini: asyncio.Event
    issue: str | None

    async def arreter(self) -> None: ...


FabriqueAgentCommande = Callable[[str], Awaitable[AgentCommandeGere]]
LectureCommandes = Callable[[], Awaitable[list[str]]]


async def _lire_a_attribuer_par_defaut() -> list[str]:
    async with FabriqueSession() as session:
        return await bd.lire_commandes_a_attribuer(session)


class GestionnaireCommandes:
    def __init__(
        self,
        *,
        fabrique: FabriqueAgentCommande = creer_agent_commande,
        lire_a_attribuer: LectureCommandes = _lire_a_attribuer_par_defaut,
        horloge: Callable[[], float] = time.monotonic,
        delai_grace_s: float | None = None,
    ):
        self.agents: dict[str, AgentCommandeGere] = {}
        self._fabrique = fabrique
        self._lire_a_attribuer = lire_a_attribuer
        self._horloge = horloge
        self._grace = parametres.agents_reconciliation_s if delai_grace_s is None else delai_grace_s
        self._modifie_le: dict[str, float] = {}
        self._surveillants: set[asyncio.Task] = set()

    # -- création et retrait ---------------------------------------------------------------------

    async def creer(self, commande_id: str) -> bool:
        if commande_id in self.agents:
            return False
        try:
            agent = await self._fabrique(commande_id)
        except Exception:  # noqa: BLE001 - une commande qui ne démarre pas ne bloque pas les autres
            journal.exception("Agent de la commande %s : démarrage impossible", commande_id)
            return False
        self.agents[commande_id] = agent
        self._modifie_le[commande_id] = self._horloge()
        surveillant = asyncio.get_running_loop().create_task(self._surveiller(commande_id, agent))
        self._surveillants.add(surveillant)
        surveillant.add_done_callback(self._surveillants.discard)
        journal.info("Agent créé pour la commande %s (%d agents)", commande_id, len(self.agents))
        return True

    async def _surveiller(self, commande_id: str, agent: AgentCommandeGere) -> None:
        """Retire l'agent dès qu'il a terminé son travail."""
        await agent.fini.wait()
        journal.info("Commande %s : agent terminé (%s)", commande_id, agent.issue)
        if self.agents.get(commande_id) is agent:  # pas déjà retiré, ni remplacé
            await self.retirer(commande_id)

    async def retirer(self, commande_id: str) -> bool:
        agent = self.agents.pop(commande_id, None)
        if agent is None:
            return False
        self._modifie_le[commande_id] = self._horloge()
        try:
            await agent.arreter()
        except Exception:  # noqa: BLE001 - l'agent est de toute façon retiré de notre liste
            journal.exception("Agent de la commande %s : arrêt incomplet", commande_id)
        journal.info("Agent retiré pour la commande %s (%d agents)", commande_id, len(self.agents))
        return True

    # -- événements ------------------------------------------------------------------------------

    async def traiter_evenement(self, evenement: dict[str, Any]) -> None:
        if evenement.get("type") != COMMANDE_VALIDEE:
            return
        commande_id = evenement.get("commande_id")
        if not isinstance(commande_id, str) or not commande_id:
            journal.warning("Événement %s sans commande_id ignoré : %s", COMMANDE_VALIDEE, evenement)
            return
        await self.creer(commande_id)

    # -- réconciliation avec la base ---------------------------------------------------------------

    def _recent(self, commande_id: str) -> bool:
        modifie = self._modifie_le.get(commande_id)
        return modifie is not None and self._horloge() - modifie < self._grace

    async def reconcilier(self) -> tuple[list[str], list[str]]:
        """Aligne les agents sur la base. Renvoie (créés, retirés)."""
        attendues = await self._lire_a_attribuer()
        crees, retires = [], []
        for commande_id in attendues:
            if commande_id not in self.agents and not self._recent(commande_id):
                if await self.creer(commande_id):
                    crees.append(commande_id)
        for commande_id in list(self.agents):
            if commande_id not in attendues and not self._recent(commande_id):
                if await self.retirer(commande_id):
                    retires.append(commande_id)
        if crees or retires:
            journal.info("Réconciliation des commandes : %d créée(s), %d retirée(s)", len(crees), len(retires))
        return crees, retires

    async def arreter(self) -> None:
        for surveillant in list(self._surveillants):
            surveillant.cancel()
        await asyncio.gather(*self._surveillants, return_exceptions=True)
        for commande_id in list(self.agents):
            await self.retirer(commande_id)
