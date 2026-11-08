"""Diagnostic de la connexion XMPP : où la connexion d'un agent s'arrête-t-elle ?

Démarre d'abord le serveur XMPP (Docker), avec la même adresse que XMPP_DOMAINE :

    docker compose -f docker-compose.xmpp.yml up -d

puis, dans le terminal du projet :

    python scripts/diagnostic_xmpp.py
    python scripts/diagnostic_xmpp.py --repeter 8     # 8 essais par boucle asyncio : mesure la fiabilité

Cinq étapes, chacune affichée sur une ligne :
  0. quelle boucle asyncio SPADE choisit-il (le serveur `spade run` choisit de la même façon) ?
  1. le serveur répond-il à un début de session XMPP écrit à la main (sans SPADE) ?
  2. un agent SPADE arrive-t-il à se connecter avec la boucle asyncio par défaut ?
  3. et avec l'autre boucle asyncio de Windows (« Selector ») ?
  4. un agent se connecte-t-il encore après une connexion TCP ouverte puis fermée aussitôt (ce que faisait
     l'ancien test de présence du serveur) ?

Le détail des échanges est écrit dans `diagnostic_xmpp.log`.
"""

import argparse
import asyncio
import logging
import socket
import sys
import time
import uuid
import warnings
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from livraison.agents.annuaire import AgentAnnuaire  # noqa: E402
from livraison.config import parametres  # noqa: E402

DELAI_AGENT_S = 12.0
FICHIER_LOG = Path("diagnostic_xmpp.log")


def etape_boucle_spade() -> None:
    """Le serveur de SPADE n'est fiable qu'avec la boucle alternative `winloop` (Windows) ou `uvloop`."""
    print("0. Boucle asyncio choisie par SPADE")
    nom = "winloop" if sys.platform == "win32" else "uvloop"
    try:
        module = __import__(nom)
        print(f"   {nom} : installé (version {getattr(module, '__version__', 'inconnue')})")
    except ImportError as e:
        print(f"   {nom} : ABSENT ({e}) : SPADE se rabat sur la boucle standard, peu fiable pour le serveur")
    import spade.container as conteneur

    boucle = conteneur.get_or_create_eventloop()
    print(f"   boucle choisie : {type(boucle).__module__}.{type(boucle).__name__}")
    boucle.close()
    asyncio.set_event_loop(None)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        asyncio.set_event_loop_policy(None)  # on repart de la politique par défaut pour les étapes suivantes


def etape_reseau() -> None:
    """Début de session XMPP écrit à la main : la réponse du serveur dit s'il « parle » XMPP."""
    hote, port = parametres.xmpp_domaine, parametres.xmpp_port
    print(f"1. Réseau brut vers {hote}:{port}")
    try:
        adresses = socket.getaddrinfo(hote, port, type=socket.SOCK_STREAM)
        print("   adresses essayées par Python :", [a[4][0] for a in adresses])
        with socket.create_connection((hote, port), timeout=3) as s:
            s.sendall(
                (
                    f"<?xml version='1.0'?><stream:stream to='{hote}' xmlns='jabber:client' "
                    "xmlns:stream='http://etherx.jabber.org/streams' version='1.0'>"
                ).encode()
            )
            fin = time.monotonic() + 5
            recu = b""
            while b"features" not in recu and time.monotonic() < fin:  # réponse en plusieurs morceaux
                s.settimeout(max(0.1, fin - time.monotonic()))
                morceau = s.recv(4000)
                if not morceau:
                    break
                recu += morceau
            reponse = recu.decode(errors="replace")
    except (OSError, TimeoutError) as e:
        print(f"   ECHEC : {type(e).__name__} : {e}")
        return
    if "stream:features" in reponse or "<features" in reponse:
        print("   OK : le serveur répond et annonce ses fonctions (" + (
            "STARTTLS proposé" if "starttls" in reponse else "sans STARTTLS") + ")")
    else:
        print(f"   ETRANGE : réponse inattendue : {reponse[:300]!r}")


async def _agent() -> None:
    agent = AgentAnnuaire(
        parametres.jid(f"diagnostic-{uuid.uuid4().hex[:8]}"), parametres.xmpp_mot_de_passe_agents
    )
    try:
        await asyncio.wait_for(agent.start(auto_register=True), DELAI_AGENT_S)
    finally:
        try:
            await asyncio.wait_for(agent.stop(), 3)
        except Exception:  # noqa: BLE001 - l'agent est jetable
            pass


def etape_agent(numero: int, nom: str, politique) -> None:
    if numero != 4:
        print(f"{numero}. Agent SPADE, boucle asyncio « {nom} »")
    if politique is None:
        print("   (non disponible sur ce système)")
        return
    asyncio.set_event_loop_policy(politique())
    debut = time.monotonic()
    try:
        asyncio.run(_agent())
    except Exception as e:  # noqa: BLE001 - on veut le type exact de l'échec
        print(f"   ECHEC après {time.monotonic() - debut:.1f} s : {type(e).__name__}")
    else:
        print(f"   OK en {time.monotonic() - debut:.1f} s")


def etape_connexion_coupee() -> None:
    """Ouvre une connexion TCP puis la ferme sans rien envoyer, comme l'ancien test de présence."""
    hote, port = parametres.xmpp_domaine, parametres.xmpp_port
    print(f"4. Connexion TCP coupée aussitôt vers {hote}:{port}, puis agent SPADE")
    try:
        socket.create_connection((hote, port), timeout=3).close()
    except OSError as e:
        print(f"   ECHEC de la connexion TCP : {type(e).__name__} : {e}")
        return
    time.sleep(0.5)
    etape_agent(4, "par défaut, après la connexion coupée", asyncio.DefaultEventLoopPolicy)


def statistiques(nombre: int) -> None:
    """Plusieurs connexions d'agent de suite avec chaque boucle : combien réussissent ?"""
    print(f"\nFiabilité : {nombre} connexions d'agent par boucle asyncio")
    boucles = [("par défaut", asyncio.DefaultEventLoopPolicy)]
    if sys.platform == "win32":
        boucles = [
            ("Proactor", asyncio.WindowsProactorEventLoopPolicy),
            ("Selector", asyncio.WindowsSelectorEventLoopPolicy),
        ]
    for nom, politique in boucles:
        asyncio.set_event_loop_policy(politique())
        reussites, erreurs = 0, {}
        for _ in range(nombre):
            try:
                asyncio.run(_agent())
                reussites += 1
            except Exception as e:  # noqa: BLE001
                erreurs[type(e).__name__] = erreurs.get(type(e).__name__, 0) + 1
        print(f"   {nom} : {reussites}/{nombre} réussies" + (f"  (échecs : {erreurs})" if erreurs else ""))


def main() -> int:
    analyseur = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    analyseur.add_argument("--repeter", type=int, default=0, help="nombre d'essais par boucle (mesure)")
    args = analyseur.parse_args()
    logging.basicConfig(
        filename=FICHIER_LOG, filemode="w", level=logging.DEBUG,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    print(f"Python {sys.version.split()[0]} sur {sys.platform} ; domaine XMPP : {parametres.xmpp_domaine}")
    etape_boucle_spade()
    etape_reseau()
    if sys.platform == "win32":
        etape_agent(2, "Proactor (par défaut sous Windows)", asyncio.WindowsProactorEventLoopPolicy)
        etape_agent(3, "Selector", asyncio.WindowsSelectorEventLoopPolicy)
    else:
        etape_agent(2, "par défaut", asyncio.DefaultEventLoopPolicy)
        etape_agent(3, "Selector", None)
    etape_connexion_coupee()
    if args.repeter > 0:
        statistiques(args.repeter)
    print(f"\nDétails des échanges : {FICHIER_LOG.resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
