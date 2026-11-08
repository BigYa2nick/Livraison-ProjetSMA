# Serveur XMPP : ce qui s'est passé et comment c'est réglé

Les agents (Annuaire, Dispatcher, Commande, Livreur) s'échangent leurs messages par XMPP. Il leur faut donc un
serveur XMPP. Sous Windows, le serveur de développement de SPADE (`spade run`) a bloqué les connexions d'une
partie des agents : les tests d'agents réels échouaient. **Règle finale : le serveur XMPP tourne dans Docker
(Linux), pas avec `spade run` sous Windows.** Ce document garde la trace du diagnostic, pour ne pas le refaire.

## 1. Utiliser le serveur XMPP au quotidien

Dossier : **racine du projet**, Docker Desktop démarré.

```powershell
docker compose -f docker-compose.xmpp.yml up -d --build     # démarrer (1re fois : 1 à 2 minutes)
docker compose -f docker-compose.xmpp.yml logs --tail 5     # doit contenir « Server started... »
docker compose -f docker-compose.xmpp.yml down              # arrêter
```

Réglages dans `.env` : `XMPP_HOST=127.0.0.1` et `XMPP_DOMAINE=127.0.0.1`. Le domaine du serveur et `XMPP_DOMAINE` doivent
être **identiques** : les agents se connectent par ce nom. Un seul serveur XMPP à la fois sur le port 5222 : si
un ancien `spade run` tourne encore, l'arrêter (`Ctrl + C`).

Fichiers : `docker-compose.xmpp.yml` (racine) et `docker/xmpp/Dockerfile`. L'image contient les mêmes versions que le
projet (spade 4.1.4, pyjabber 0.4.5), plus `greenlet`.

Vérifier que tout marche : `pytest tests/test_agents_xmpp.py tests/test_contract_net_xmpp.py` donne **16 passed**
(résultat obtenu sur ta machine le 6 novembre 2026). Sans serveur, ces 16 tests sont **ignorés**, pas en échec.

## 2. Chronologie des problèmes

| # | Ce qu'on voyait | Cause | Réglage |
|---|---|---|---|
| 1 | 9 tests `test_agents_xmpp.py` ignorés (« skipped ») | Le serveur XMPP n'était pas lancé : le test s'ignore sans lui | Normal. Lancer le serveur pour les exécuter |
| 2 | `DisconnectedException` dès la connexion | `localhost` désigne `::1` (IPv6) **avant** `127.0.0.1` sous Windows ; le serveur n'écoute qu'en IPv4 | `127.0.0.1` partout : `.env` et le domaine du serveur |
| 3 | Suite de tests de plus de 40 minutes | Chaque test attendait la fin des tentatives de connexion d'un serveur inutilisable | La fixture `xmpp` (`tests/aides_xmpp.py`) fait **un seul** essai réel, borné à 15 s, puis ignore ou fait échouer toute la suite avec un diagnostic |
| 4 | La sonde disait « serveur muet » à tort | Elle ne lisait qu'un morceau de la réponse ; `stream:features` arrive parfois dans le suivant | Lecture jusqu'à voir `features`, 5 s au plus |
| 5 | `TimeoutError` sur la plupart des connexions, une sur deux qui passe | **Le serveur `spade run` sous Windows reste bloqué juste après le passage en TLS** (voir § 3) | Serveur dans Docker (Linux) |
| 6 | `docker compose` : « file not found » | `docker-compose.xmpp.yml` n'avait pas été copié à la racine | Le créer (commandes PowerShell dans `LANCEMENT.md`, § 2.10) |
| 7 | Construction de l'image qui échoue : « No matching distribution found for aiohttp » (après 140 s) | Le réseau du conteneur a lâché pendant `pip install` | Délais et nouvelles tentatives dans le Dockerfile (`--default-timeout=120 --retries 10`) et relancer |
| 8 | Conteneur démarré mais serveur planté : « requires that the Python 'greenlet' library is installed » | SQLAlchemy 2.1 (utilisé par pyjabber) n'installe plus `greenlet` tout seul | `greenlet` ajouté au Dockerfile |

## 3. La cause du blocage sous Windows

Du côté de l'agent, le journal de la connexion (`xmpp_sonde.log`) montre tout se dérouler jusqu'au bout :
`starttls` envoyé, `proceed` reçu, TLS établi (`tls_success`), nouvel en-tête de flux envoyé, puis **plus aucune
réponse du serveur**. Dans le journal du serveur : « SSL handshake is taking longer than 60.0 seconds: aborting the
connection ». Le serveur s'est donc arrêté après le passage en TLS.

Ce qui a été essayé, avec le résultat :
- **Changer la boucle asyncio des agents** (Proactor ou Selector) : sans effet sur le défaut. SPADE force sa propre
  boucle (`winloop` sous Windows, `uvloop` sous Linux et macOS) dès la création du premier agent.
- **Le serveur sur la boucle standard d'asyncio** : en le reproduisant sous Linux (Python 3.12 et 3.13), les mêmes
  blocages apparaissent par moments. Avec `uvloop`, qui est le choix de SPADE, **6 connexions sur 6 réussissent**.
  `winloop` est bien installé chez toi (version 0.7.2) et SPADE le choisit, et pourtant le serveur bloque : le défaut
  est propre à ce serveur sous Windows.
- **`--memory` (base du serveur en mémoire)** : cassé avec ces versions (`no such table: credentials`). À ne pas utiliser.
- **`--purge`** (base de comptes vidée au démarrage) : fonctionne, utile, mais ne règle pas le blocage.

Le serveur sous Linux ne bloque pas : d'où Docker. **Non établi** : la cause exacte dans `winloop` ou dans le serveur
sous Windows (non creusée, le contournement suffit).

## 4. Outils de diagnostic

```powershell
python scripts/diagnostic_xmpp.py              # 5 étapes : boucle choisie par SPADE, réseau brut, agent, etc.
python scripts/diagnostic_xmpp.py --repeter 8  # 8 connexions par boucle asyncio : mesure la fiabilité
docker compose -f docker-compose.xmpp.yml logs --tail 30
```

- Un test XMPP en échec écrit les échanges de sa tentative dans **`xmpp_sonde.log`** (racine du projet) : le joindre à
  toute demande d'aide.
- Dans PowerShell, les messages d'information d'un programme lancé à la main s'affichent **en rouge** : c'est normal,
  ils sortent sur la sortie d'erreur.
- Pour garder le journal d'un `spade run` lancé à la main (déconseillé sous Windows) :
  `spade run --host 127.0.0.1 --purge *> serveur_xmpp.log`.

## 5. Si le serveur XMPP pose de nouveau problème

1. `docker compose -f docker-compose.xmpp.yml logs --tail 30` : le serveur tourne-t-il (`Server started...`) ? S'il
   redémarre en boucle, l'erreur est dans ces lignes.
2. Après une modification du `Dockerfile` : `docker compose -f docker-compose.xmpp.yml up -d --build`.
3. `python scripts/diagnostic_xmpp.py` : les étapes 1 et 2 doivent dire `OK`.
4. Les fichiers `xmpp_sonde.log`, `diagnostic_xmpp.log` et `serveur_xmpp.log` sont des journaux locaux : les ajouter à
   `.gitignore`.
