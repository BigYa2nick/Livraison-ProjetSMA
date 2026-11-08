# Service d'itinéraires (F-42)

Distance, durée et tracé d'un trajet entre deux points, selon le véhicule. Les agents du lot 2 s'en servent
pour calculer le coût d'une offre (`durée jusqu'à l'enlèvement + détour`). Il interroge un serveur **OSRM**
auto-hébergé sur la carte OpenStreetMap de la Côte d'Ivoire (décision D-05,
`docs/conception/06-decisions.md`).

Tous les chemins partent de la **racine du dépôt**, le dossier `livraison-sma/`.

## 1. Fichiers livrés et où les placer

Légende : **NOUVEAU** = le fichier n'existe pas chez toi ; **REMPLACE** = il existe, on écrase l'ancien.

```
livraison-sma/
├── docker-compose.osrm.yml                    NOUVEAU
├── docker-compose.yml                         REMPLACE (conseillé : service OSRM en double retiré)
├── scripts/
│   ├── preparer_osrm.py                       NOUVEAU
│   └── verifier_osrm.py                       NOUVEAU
├── src/
│   └── livraison/
│       ├── config.py                          REMPLACE
│       ├── api/
│       │   └── routes_admin.py                REMPLACE (facultatif : commentaire corrigé)
│       └── services/
│           ├── dossier.py                     REMPLACE (facultatif : commentaire corrigé)
│           └── itineraires.py                 NOUVEAU
├── tests/
│   └── test_itineraires.py                    NOUVEAU
└── docs/
    ├── itineraires.md                         NOUVEAU (ce fichier)
    └── conception/
        └── 06-decisions.md                    REMPLACE (décision D-05 précisée)
```

| Fichier (chemin complet depuis la racine) | Action | Rôle |
|---|---|---|
| `docker-compose.yml` | REMPLACE (conseillé) | Retire le service `osrm` (profil `itineraires`), qui faisait double emploi avec `docker-compose.osrm.yml` : même port 5000, image `latest` non figée, autre dossier de données (`donnees/osrm`). PostgreSQL et Redis ne changent pas |
| `docker-compose.osrm.yml` | NOUVEAU | Lance deux serveurs OSRM : voiture (port 5000) et vélo (port 5001), accessibles depuis ce PC seulement |
| `scripts/preparer_osrm.py` | NOUVEAU | Télécharge la carte et prépare les données des deux profils dans Docker |
| `scripts/verifier_osrm.py` | NOUVEAU | Vérifie que les serveurs répondent et que des trajets d'essai à Abidjan sont plausibles |
| `src/livraison/services/itineraires.py` | NOUVEAU | Le service utilisé par le code : `itineraire`, `matrice_durees`, `sante` |
| `src/livraison/config.py` | REMPLACE | Nouveaux réglages d'itinéraires, voir ci-dessous |
| `src/livraison/services/dossier.py` | REMPLACE (facultatif) | Service du dossier livreur : seul un commentaire change (suspension = F-62 et non F-61) |
| `src/livraison/api/routes_admin.py` | REMPLACE (facultatif) | Routes de l'administrateur : seul un commentaire change (F-62) |
| `tests/test_itineraires.py` | NOUVEAU | 24 tests (23 sans OSRM, 1 qui interroge le vrai OSRM s'il tourne) |
| `docs/conception/06-decisions.md` | REMPLACE | D-05 précise que la moto utilise le profil voiture |

Les deux fichiers marqués « facultatif » ont reçu une correction de commentaire (la suspension d'un
livreur est F-62, pas F-61). **Rien ne change à l'exécution** : tu peux les remplacer ou les ignorer.

### Si tu as modifié `src/livraison/config.py` depuis ton dernier envoi

Reporte ces réglages à la main dans la classe `Parametres` (le premier remplace la valeur existante) :

| Réglage | Valeur par défaut | Rôle |
|---|---|---|
| `osrm_url` | `"http://127.0.0.1:5000"` (au lieu de `localhost`) | Serveur voiture, utilisé aussi pour la moto |
| `osrm_url_velo` | `"http://127.0.0.1:5001"` | Serveur vélo |
| `osrm_timeout_s` | `2.0` | Délai maximal d'une requête |
| `osrm_max_table` | `100` | Nombre maximal de points d'une matrice |
| `itineraire_facteur_detour` | `1.4` | Estimation de repli : route = ligne droite × 1,4 |
| `vitesse_estimee_moto_kmh` / `..._voiture_kmh` / `..._velo_kmh` | `25.0` / `20.0` / `12.0` | Vitesses du repli |

Les trois dernières lignes sont **mes hypothèses**, à calibrer avec des trajets réels au lot 2.

## 2. Mise en route, dans l'ordre

Terminal à la racine du dépôt, venv activé, **Docker Desktop démarré**.

**Étape 1.** Copier les fichiers du tableau ci-dessus à leur place.

**Étape 2.** Le service utilise la bibliothèque `httpx`. Si elle n'est pas déjà dans ton `requirements.txt`,
l'ajouter :

```powershell
pip install httpx
```

**Étape 3.** Ignorer les données de carte dans Git : ajouter la ligne `data/` dans `.gitignore`.

**Étape 4.** Vérifier `.env` et `.env.example` : si l'un contient `OSRM_URL=http://localhost:5000`,
le remplacer par `OSRM_URL=http://127.0.0.1:5000` et ajouter `OSRM_URL_VELO=http://127.0.0.1:5001`.
Sous Windows, `localhost` essaie d'abord IPv6 et ajoute environ 2 secondes à chaque requête : c'est la cause
du `requête : 2093 ms` vu au prototype GPS.

**Étape 5.** Préparer les données (la première fois, et pour mettre la carte à jour) :

```powershell
python scripts/preparer_osrm.py
```

Le script télécharge la carte, puis lance trois outils d'OSRM dans Docker pour chaque profil. Compte
plusieurs minutes. Si le téléchargement échoue, télécharger l'extrait de la Côte d'Ivoire sur
`geofabrik.de`, puis :

```powershell
python scripts/preparer_osrm.py --pbf C:\chemin\vers\le-fichier.osm.pbf
```

**Étape 6.** Démarrer les serveurs :

```powershell
docker compose -f docker-compose.osrm.yml up -d
```

**Étape 7.** Vérifier :

```powershell
python scripts/verifier_osrm.py
```

Tu dois voir `voiture: répond` et `velo: répond`, puis un tableau de quatre trajets avec la mention
`plausible`. Une ligne `A EXAMINER` signale un trajet suspect : route plus courte que la ligne droite,
détour de plus de ×3, ou vitesse moyenne hors de 3 à 90 km/h. C'est à toi de juger si le trajet est
réaliste, car tu connais Abidjan.

**Étape 8.** Les tests :

```powershell
pytest tests/test_itineraires.py
```

Le dernier test interroge le vrai OSRM : il est **ignoré** (« skipped ») tant que les serveurs ne
tournent pas, et s'exécute dès qu'ils répondent.

Commandes utiles : `docker compose -f docker-compose.osrm.yml ps` (état),
`docker compose -f docker-compose.osrm.yml logs osrm-voiture` (journal),
`docker compose -f docker-compose.osrm.yml down` (arrêt).

## 3. Utiliser le service dans le code

```python
from livraison.persistance.enums import TypeVehicule
from livraison.services.itineraires import Point, itineraire, matrice_durees

# Les points sont (latitude, longitude). La conversion pour OSRM est faite dans le service.
enlevement = Point(5.3236, -4.0197)
livraison = Point(5.3480, -3.9870)

trajet = await itineraire(enlevement, livraison, TypeVehicule.MOTO)
trajet.distance_m, trajet.duree_s, trajet.estime   # estime=True : OSRM n'a pas répondu

avec_trace = await itineraire(enlevement, livraison, TypeVehicule.VOITURE, avec_trace=True)
avec_trace.trace                                   # tuple de Point, pour dessiner sur une carte

# Durées de plusieurs livreurs vers un point d'enlèvement, en un seul appel
durees = await matrice_durees([pos_livreur_1, pos_livreur_2], [enlevement], TypeVehicule.MOTO)
durees[0][0]                                       # secondes, ou None si aucune route
```

Dans un programme qui s'arrête (agents, scripts), appeler `await fermer_client()` à la fin.

## 4. Quand OSRM ne répond pas

| Situation | Ce que fait le service |
|---|---|
| OSRM injoignable, trop lent (plus de 2 s) ou en erreur serveur | Renvoie une **estimation** : ligne droite × 1,4, à la vitesse moyenne du véhicule. Le résultat porte `estime=True` et un **avertissement est écrit dans le journal**. Jamais en silence |
| Même situation avec `repli=False` | Lève `MoteurIndisponible` |
| OSRM répond « aucune route » | Lève `AucunItineraire` : on ne masque pas cette information par une estimation |
| Un point est trop loin de toute route | Lève `PointHorsReseau` |
| Coordonnées invalides (latitude hors de −90 à 90, longitude hors de −180 à 180) | Lève `ValueError`, rien n'est envoyé à OSRM |

Un agent qui reçoit `estime=True` doit en tenir compte : son offre est moins fiable, et le journal des
messages (F-63) doit pouvoir le montrer.

## 5. Choix et limites

- **Deux serveurs, pas trois.** OSRM fournit des profils voiture, vélo et piéton ; il n'a pas de profil
  moto. La moto utilise le profil **voiture** : elle ne profite pas des passages réservés aux deux-roues
  et son temps en cas d'embouteillage est probablement sous-estimé. À mesurer au lot 2.
- **Qualité de la carte.** Les routes viennent d'OpenStreetMap : sens uniques, ponts et rues sans nom
  d'Abidjan peuvent être incomplets. `scripts/verifier_osrm.py` ne détecte que les anomalies grossières.
- **Pas de cache.** Deux demandes identiques font deux appels à OSRM. À ajouter si les mesures du lot 2
  montrent que c'est utile.
- **Version d'OSRM figée** (`v5.27.1`, variable `OSRM_IMAGE` pour la changer). Changer de version impose de
  refaire la préparation des données avec `--reset`.
- **Mémoire.** La préparation et OSRM ont besoin de mémoire : si Docker coupe le processus (code 137),
  augmenter la mémoire de Docker Desktop (Settings, Resources). Le besoin réel pour la Côte d'Ivoire est à
  mesurer.

## 6. Dépannage

| Ce que tu vois | Cause probable | Action |
|---|---|---|
| `Docker est introuvable` | Docker Desktop n'est pas installé ou pas démarré | Le démarrer, puis relancer |
| `Téléchargement impossible` | Pas de réseau, ou l'adresse de la carte a changé | Télécharger l'extrait sur `geofabrik.de`, puis `--pbf <fichier>` |
| `La commande a échoué (code 137)` | Docker manque de mémoire | Augmenter la mémoire de Docker Desktop |
| `verifier_osrm.py` : `NE RÉPOND PAS` | Serveurs arrêtés, ou données non préparées | `docker compose -f docker-compose.osrm.yml up -d` ; sinon `python scripts/preparer_osrm.py` |
| Le conteneur redémarre en boucle (`ps`) | Données absentes ou d'une autre version d'OSRM | `docker compose -f docker-compose.osrm.yml logs osrm-voiture`, puis `preparer_osrm.py --reset` |
| Les requêtes durent environ 2 secondes | `.env` utilise `localhost` | Utiliser `127.0.0.1` (étape 4) |
| Les itinéraires ont `estime=True` | OSRM injoignable depuis le code | Vérifier `OSRM_URL` dans `.env`, puis `verifier_osrm.py` |
| `PointHorsReseau` | Point en mer, dans la lagune ou hors du pays | Vérifier l'ordre latitude puis longitude du point |

## 7. État de la vérification

**Vérifié** :
- les 23 tests sans OSRM passent ; je les ai aussi mis à l'épreuve en introduisant volontairement quatre
  défauts (coordonnées inversées, repli silencieux, absence de route masquée, vélo sur le mauvais
  serveur) : les tests les détectent à chaque fois ;
- `scripts/preparer_osrm.py` fonctionne avec un faux Docker et un faux serveur de téléchargement :
  téléchargement, préparation des deux profils, relance sans rien refaire, `--reset`, échec de Docker,
  Docker absent, téléchargement impossible ;
- `scripts/verifier_osrm.py` fonctionne avec de faux serveurs OSRM : serveurs actifs, serveurs arrêtés
  (code de retour 1), trajets suspects signalés.

**Non vérifié** : le fonctionnement avec le **vrai OSRM**. Je n'ai pas pu lancer Docker ni télécharger la
carte. Restent donc à confirmer chez toi : l'adresse de téléchargement de la carte, l'image Docker
`v5.27.1` et les commandes d'OSRM, la durée et la mémoire de la préparation, et la plausibilité des
trajets réels. L'étape 7 sert exactement à ça.
