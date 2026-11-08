"""Boucle asyncio des programmes qui parlent XMPP.

Sous Windows, la boucle asyncio par défaut (« Proactor ») fait parfois échouer la connexion d'un agent SPADE :
la session XMPP ne démarre jamais (`TimeoutError`), alors que la boucle « Selector » se connecte sans problème
au même serveur (constaté avec `scripts/diagnostic_xmpp.py`, Python 3.13). On impose donc « Selector » sous
Windows ; sous Linux et macOS, c'est déjà la boucle par défaut et rien ne change.

À appeler **avant** de créer la boucle : au lancement de `python -m livraison.agents`, et en tête de
`tests/conftest.py`.
"""

import asyncio
import sys
import warnings


def utiliser_boucle_compatible_xmpp() -> None:
    if sys.platform != "win32":
        return
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)  # la notion de politique disparaît avec Python 3.16
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
