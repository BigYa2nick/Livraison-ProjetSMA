# Prototype du suivi GPS

Le cahier des charges classe ce point en risque **élevé** : le suivi GPS en arrière-plan est
difficile avec une application mobile écrite en Python. Ce prototype sert à le **mesurer** dès le
lot 1, pour décider avec des chiffres s'il faut garder Flet ou prévoir un module natif.

## Ce qui est construit

```
Téléphone (Flet)                    Serveur                       Répartiteur (Flet)
mobile/livreur/                     FastAPI                       console_suivi.py
 GPS (flux, sondage, simulé)         POST /livreurs/moi/position   carte + liste des livreurs
 file d'envoi (coupures réseau)  ->  PostgreSQL : historique   ->  WebSocket /ws/suivi
 mesure des trous du GPS             Redis : dernière position     délai mesure -> carte
                                     Redis : canal "positions"
```

| Élément | Où |
|---|---|
| Profil livreur, en ligne / hors ligne, réception des positions | `src/livraison/api/routes_livreur.py`, `services/livreurs.py`, `services/positions.py` |
| Suivi en direct (instantané + WebSocket) | `src/livraison/api/routes_suivi.py` |
| Application du livreur (3 modes de collecte) | `mobile/livreur/src/main.py` |
| Carte du répartiteur | `src/livraison/interfaces/console_suivi.py` |
| Mesure des résultats d'un test | `scripts/analyser_positions.py` |

Règles appliquées : la position n'est acceptée que si le livreur est **en ligne** (NF-07) ; une
position plus ancienne que la dernière connue ne la remplace pas (positions renvoyées après une
coupure) ; la dernière position vit 2 minutes dans Redis ; une position de plus de 30 s est signalée
« périmée ».

## Ce qui est vérifié, et ce qui ne l'est pas

Vérifié automatiquement (98 tests) et à la main (serveur réel, 2 livreurs simulés, carte) : tout le
chemin serveur, la file d'envoi, le renouvellement de jeton, la diffusion en direct, l'arrêt de la
collecte hors ligne. Avec la simulation : 0,0 s de délai entre la mesure et la carte, en local.

**Non vérifiable sans téléphone** : le comportement réel du GPS Android, écran éteint, avec Flet.
C'est le seul vrai risque, et c'est ce que les tests ci-dessous mesurent.

## Étape A : tester sur l'ordinateur (10 minutes)

Installer ce qui manque dans le venv du projet, puis lancer trois terminaux (venv activé) :

```powershell
pip install flet-map flet-geolocator==1.0.4
# Terminal 1 : le serveur
uvicorn livraison.api.main:app --host 0.0.0.0 --reload
uvicorn livraison.api.main:app --host 0.0.0.0 --reload --reload-dir src
# Terminal 2 : la carte (il faut un compte répartiteur, voir docs/authentification.md)
flet run src/livraison/interfaces/console_suivi.py
# Terminal 3 : l'application du livreur, en mode « Simulation »
cd mobile/livreur
pip install httpx
flet run src/main.py
```

Dans l'application du livreur : adresse `http://127.0.0.1:8000`, un numéro, **Demander le code**
(le code s'affiche dans le terminal 1, ligne `[SMS SIMULÉ]`), **Valider**, mode **Simulation**,
**Passer en ligne**. Dans la carte : se connecter avec le compte répartiteur ; un marqueur doit
bouger toutes les 5 secondes.

## Étape B : tester sur un téléphone Android

Il faut un **APK** : seul un APK peut déclarer le service de localisation au premier plan et les
autorisations d'arrière-plan. L'application Flet du Play Store ne permet pas de tester ce point.
Prérequis du build (Flutter, JDK, SDK Android) : README, section 11.

```powershell
cd mobile\livreur
flet build apk
```

Le fichier `pyproject.toml` de ce dossier déclare déjà les autorisations (position, arrière-plan,
service au premier plan, notifications). Installer l'APK sur le téléphone (câble ou envoi du fichier).

**Réseau** : le téléphone et l'ordinateur sur le même Wi-Fi. Dans l'application, utiliser
`http://<adresse IP de l'ordinateur>:8000` (commande `ipconfig`, ligne « Adresse IPv4 »). Autoriser
le port 8000 dans le pare-feu Windows (réseau privé). Si Android refuse la connexion en `http://`
(message « Cleartext HTTP traffic not permitted »), passer par un tunnel HTTPS (par exemple
`cloudflared tunnel --url http://localhost:8000`) et utiliser l'adresse `https://...` obtenue.

**Autorisations sur le téléphone**, avant le premier test :
1. Bouton **Autoriser la position**, accepter.
2. Réglages Android > Applications > Livreur GPS > Autorisations > Position > **Toujours autoriser**.
3. Autoriser les **notifications** de l'application (la notification permanente garde le suivi actif).
4. Batterie de l'application : **Sans restriction** (sur Tecno, Infinix, Xiaomi, Samsung, désactiver
   aussi le « démarrage automatique » et l'économiseur d'énergie : ils tuent les applications en
   arrière-plan, et c'est la cause la plus fréquente d'échec).

## Protocole de mesure

Mode **GPS en flux** pour tous les scénarios sauf le dernier. Chaque scénario dure 10 minutes, avec le
téléphone qui se déplace (à pied ou en voiture) si possible. Après chacun, **Passer hors ligne**, puis
sur l'ordinateur :

```powershell
python scripts/analyser_positions.py --telephone 0700000001 --minutes 15
```

| N° | Scénario | Ce qu'il vérifie |
|---|---|---|
| 1 | Écran allumé, application au premier plan | référence |
| 2 | Écran allumé, en déplacement | précision et cadence en mouvement |
| 3 | **Écran verrouillé**, téléphone dans la poche | le test décisif |
| 4 | Application en arrière-plan (autre application ouverte) | arrière-plan réel |
| 5 | Wi-Fi coupé 2 minutes puis rétabli (écran allumé) | file d'envoi : « en attente » monte puis retombe à 0 |
| 6 | Mode **sondage**, écran verrouillé | comparaison : on s'attend à de mauvais résultats |

Noter pour chacun : positions reçues, trous de plus de 30 s, plus long trou, % reçues en moins de
10 s, batterie consommée (% au début et à la fin). L'application affiche aussi les trous et les
changements d'état (premier plan, arrière-plan) dans son journal.

## Critères de décision

| Résultat du scénario 3 (écran verrouillé) | Décision |
|---|---|
| Aucun trou de plus de 30 s et au moins 95 % des positions reçues en moins de 10 s | **Flet suffit** : on garde l'application en Python |
| Quelques trous, mais seulement avec l'économiseur de batterie | Flet suffit ; prévoir un écran d'aide pour régler la batterie à l'installation du livreur |
| Trous fréquents ou collecte arrêtée écran éteint | **Repli prévu au cahier des charges** : module natif Android (Kotlin) limité à la position, appelé par l'application |

Une PWA (application web) ne peut pas servir de repli : un navigateur arrête la localisation dès que
l'écran s'éteint.

## À vérifier sur le téléphone (inconnues)

- Le démarrage et l'arrêt du flux GPS en ajoutant et retirant le service à la page : prévu pour
  fonctionner, jamais essayé sur un appareil.
- L'écran d'autorisation « Toujours autoriser » : Android 11 et plus l'impose depuis les réglages.
- L'envoi en `http://` depuis l'APK (voir le tunnel HTTPS en cas de refus).
- Le comportement de Python (envoi des positions) quand l'application est en arrière-plan : si le
  GPS reçoit des positions mais que le serveur n'en reçoit pas, le journal de l'application montrera
  « en attente » qui grossit, ce qui prouve que la collecte tient mais pas l'envoi.
