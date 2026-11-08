"""Lancement des agents de la plateforme : python -m livraison.agents

Prérequis : le serveur XMPP tourne dans un autre terminal (spade run --host localhost), ainsi que
PostgreSQL et Redis (docker compose up -d). Arrêt : Ctrl + C.
"""

import asyncio
import logging
import sys

import spade
from spade.agent import AuthenticationFailure, DisconnectedException

from livraison.agents.annuaire import AgentAnnuaire
from livraison.agents.dispatcher import AgentDispatcher
from livraison.agents.gestionnaire import GestionnaireAgents
from livraison.agents.gestionnaire_commandes import GestionnaireCommandes
from livraison.agents.protocole import jid_annuaire, jid_dispatcher
from livraison.config import parametres
from livraison.services import itineraires

journal = logging.getLogger("livraison.agents")

DELAI_CONNEXION_S = 10.0


class ErreurServeurXmpp(Exception):
    """Le serveur XMPP est éteint, ou injoignable à l'adresse du domaine."""


async def demarrer(agent) -> None:
    """Connecte un agent au serveur XMPP ; lève ErreurServeurXmpp avec un message clair en cas d'échec.

    Pas de connexion TCP « pour voir » avant : démarrer l'agent suffit à savoir si le serveur est utilisable,
    et l'erreur dit alors exactement ce qui manque.
    """
    try:
        await asyncio.wait_for(agent.start(auto_register=True), DELAI_CONNEXION_S)
    except (DisconnectedException, AuthenticationFailure, OSError, TimeoutError) as e:
        raise ErreurServeurXmpp(
            f"Connexion XMPP impossible vers {parametres.xmpp_domaine}:{parametres.xmpp_port} "
            f"({type(e).__name__}). Le serveur XMPP tourne-t-il (docker compose -f docker-compose.xmpp.yml up -d) ? "
            "XMPP_DOMAINE doit être la même adresse des deux côtés (127.0.0.1 sous Windows)."
        ) from e


async def main() -> int:
    annuaire = AgentAnnuaire(jid_annuaire(), parametres.xmpp_mot_de_passe_agents)
    dispatcher = AgentDispatcher(jid_dispatcher(), parametres.xmpp_mot_de_passe_agents)
    try:
        await demarrer(annuaire)
        await demarrer(dispatcher)
    except ErreurServeurXmpp as e:
        journal.error("%s", e)
        for agent in (dispatcher, annuaire):
            try:
                await asyncio.wait_for(agent.stop(), 3)
            except Exception:  # noqa: BLE001 - l'agent n'a peut-être jamais démarré
                pass
        return 1
    # Un seul abonné Redis : le gestionnaire des livreurs transmet `commande_validee` à celui des commandes.
    gestionnaire = GestionnaireAgents(commandes=GestionnaireCommandes())
    try:
        journal.info("Agents démarrés. Ctrl + C pour arrêter.")
        await gestionnaire.executer()
    finally:
        await gestionnaire.arreter()
        await dispatcher.arreter()
        await annuaire.stop()
        await itineraires.fermer_client()
        journal.info("Agents arrêtés.")
    return 0


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s : %(message)s"
    )
    resultat: list[int] = []

    async def lancer() -> None:
        resultat.append(await main())

    try:
        spade.run(lancer())
    except KeyboardInterrupt:
        pass
    sys.exit(resultat[0] if resultat else 0)
