"""Vérification du serveur XMPP avant les tests d'agents réels.

Les agents SPADE se connectent à l'adresse du **domaine** de leur JID (`XMPP_DOMAINE`). La vérification
se fait donc à cette adresse, et en deux temps :

1. un début de session XMPP écrit à la main (l'ancien test ouvrait une connexion TCP et la fermait
   aussitôt, sans rien dire au serveur) ;
2. une vraie connexion d'agent, bornée à 15 secondes, faite une seule fois par exécution.

Sans cela, chaque test attendait la fin de ses tentatives de connexion : plus de 40 minutes pour la suite.
"""

import asyncio
import logging
import socket
import sys
import time
import uuid
from pathlib import Path

import pytest

from livraison.agents.annuaire import AgentAnnuaire
from livraison.config import parametres

DELAI_SONDE_S = 15.0
FICHIER_JOURNAL = Path("xmpp_sonde.log")  # échanges de la connexion d'essai, gardés seulement en cas d'échec
_resultat: str | None = None  # None : pas encore vérifié ; "" : serveur utilisable ; sinon : message d'échec
_serveur_absent = False  # sous Windows, chaque connexion refusée coûte ~2 s : on ne la refait pas par test


def sonde_reseau() -> str:
    """« absent » (rien n'écoute), « muet » (écoute mais ne parle pas XMPP) ou « ok »."""
    hote, port = parametres.xmpp_domaine, parametres.xmpp_port
    try:
        connexion = socket.create_connection((hote, port), timeout=2)
    except OSError:
        return "absent"
    with connexion:
        try:
            connexion.sendall(
                (
                    f"<?xml version='1.0'?><stream:stream to='{hote}' xmlns='jabber:client' "
                    "xmlns:stream='http://etherx.jabber.org/streams' version='1.0'>"
                ).encode()
            )
            # La réponse arrive en plusieurs morceaux (l'en-tête du flux, puis ses fonctions) : on lit
            # jusqu'à voir « features », au plus 5 secondes.
            fin = time.monotonic() + 5
            recu = b""
            while b"features" not in recu:
                reste = fin - time.monotonic()
                if reste <= 0:
                    return "muet"
                connexion.settimeout(reste)
                morceau = connexion.recv(4000)
                if not morceau:  # le serveur a fermé la connexion
                    return "muet"
                recu += morceau
        except (OSError, TimeoutError):
            return "muet"
    return "ok"


def _message(cause: str, detail: str = "") -> str:
    return (
        f"Le serveur XMPP écoute sur {parametres.xmpp_domaine}:{parametres.xmpp_port}, mais {cause}.\n"
        f"{detail}"
        "Les agents se connectent à l'adresse du domaine de leur JID (XMPP_DOMAINE) : elle doit être "
        "identique dans .env et pour le serveur (127.0.0.1 sous Windows).\n"
        "Le serveur doit être celui de Docker, pas un « spade run » lancé sous Windows (il bloque une partie des "
        "connexions) : docker compose -f docker-compose.xmpp.yml up -d --build.\n"
        "Diagnostic : python scripts/diagnostic_xmpp.py ; historique : docs/xmpp-depannage.md."
    )


async def _essayer_un_agent() -> str:
    """Une vraie connexion d'agent, avec le détail des échanges écrit dans `xmpp_sonde.log`.

    Renvoie "" si l'agent s'est connecté (le fichier est alors supprimé), sinon le message d'échec.
    """
    sonde = AgentAnnuaire(
        parametres.jid(f"sonde-{uuid.uuid4().hex[:8]}"), parametres.xmpp_mot_de_passe_agents
    )
    racine = logging.getLogger()
    niveau_initial = racine.level
    journal = logging.FileHandler(FICHIER_JOURNAL, mode="w", encoding="utf-8")
    journal.setLevel(logging.DEBUG)
    journal.setFormatter(logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s"))
    racine.addHandler(journal)
    racine.setLevel(logging.DEBUG)
    boucle = type(asyncio.get_running_loop()).__name__
    debut = time.monotonic()
    erreur = None
    try:
        await asyncio.wait_for(sonde.start(auto_register=True), DELAI_SONDE_S)
    except Exception as e:  # noqa: BLE001 - connexion refusée, délai dépassé, authentification...
        erreur = e
    finally:
        racine.removeHandler(journal)
        racine.setLevel(niveau_initial)
        journal.close()
        try:
            await asyncio.wait_for(sonde.stop(), 3)
        except Exception:  # noqa: BLE001 - la sonde est jetable
            pass
    if erreur is None:
        FICHIER_JOURNAL.unlink(missing_ok=True)
        return ""
    detail = (
        f"Essai : {type(erreur).__name__} après {time.monotonic() - debut:.1f} s ; boucle asyncio : {boucle} ; "
        f"Python {sys.version.split()[0]}. Échanges enregistrés dans {FICHIER_JOURNAL.resolve()}\n"
    )
    return _message(f"un agent n'arrive pas à s'y connecter ({type(erreur).__name__})", detail)


async def verifier_xmpp() -> None:
    """À appeler en tête de la fixture `xmpp` : ignore le test si le serveur est éteint, le fait échouer
    avec un diagnostic s'il tourne mais n'est pas utilisable par les agents."""
    global _resultat, _serveur_absent
    message_absent = (
        "Serveur XMPP injoignable : le démarrer avec "
        "« docker compose -f docker-compose.xmpp.yml up -d » (docs/xmpp-depannage.md)"
    )
    if _serveur_absent:
        pytest.skip(message_absent)
    if _resultat is None:
        etat = sonde_reseau()
        if etat == "absent":
            _serveur_absent = True
            pytest.skip(message_absent)
        if etat == "muet":
            _resultat = _message("il ne répond pas à un début de session XMPP")
        else:
            _resultat = await _essayer_un_agent()
    if _resultat:
        pytest.fail(_resultat, pytrace=False)
