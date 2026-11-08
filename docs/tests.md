# Tests : base de données dédiée

Les tests tournent sur une base PostgreSQL **à part**, `livraison_test`. Ils ne touchent plus à la base de
développement (`livraison`), celle qui contient les comptes créés avec l'iPhone, l'app Flet ou Swagger.

**Pourquoi :** les tests créent des utilisateurs avec des numéros fixes (`+2250700000001`...). Quand ce
numéro existait déjà dans la base de développement, le test échouait avec
`duplicate key value violates unique constraint "uq_utilisateur_telephone"`. Chaque test annule déjà ses
écritures à la fin, mais il ne peut pas ignorer ce qui existait avant lui.

Tous les chemins partent de la **racine du dépôt**, le dossier `livraison-sma/`.

## 1. Fichiers livrés et où les placer

```
livraison-sma/
├── scripts/
│   └── preparer_base_test.py     NOUVEAU
├── tests/
│   └── conftest.py               REMPLACE
└── docs/
    └── tests.md                  NOUVEAU (ce fichier)
```

| Fichier (chemin complet depuis la racine) | Action | Rôle |
|---|---|---|
| `scripts/preparer_base_test.py` | NOUVEAU | Crée la base `livraison_test`, y active PostGIS, applique les migrations Alembic, puis vérifie le résultat |
| `tests/conftest.py` | REMPLACE | Oriente tous les tests vers la base de test, refuse de tourner sur une autre base, ignore proprement les tests si la base n'est pas prête |
| `docs/tests.md` | NOUVEAU | Ce guide |

Ce `conftest.py` contient aussi les changements du dossier livreur (`docs/dossier-livreur.md`). Si tu as
modifié le tien depuis, reporte plutôt ces trois changements à la main :

| Où dans `tests/conftest.py` | Changement |
|---|---|
| Tout en haut, avant les autres imports | `import os`, puis `os.environ["POSTGRES_DB"] = os.environ.get("POSTGRES_DB_TEST", "livraison_test")`, et la ligne `# ruff: noqa: E402` en tête de fichier |
| Après `SECRET_JWT_TEST = ...` | Le garde-fou : `raise pytest.UsageError(...)` si `parametres.postgres_db` ne finit pas par `_test` |
| Dans la fixture `session` | Le message d'ignorance du test qui invite à lancer `python scripts/preparer_base_test.py` |

## 2. Mise en place (une seule fois)

Terminal à la racine du dépôt, venv activé, Docker démarré (`docker compose up -d`) :

```powershell
python scripts/preparer_base_test.py
```

Tu dois voir :

```
Base livraison_test créée.
Migrations Alembic...
Base de test prête : livraison_test, révision 0003, 18 tables.
```

Ensuite :

```powershell
pytest
```

## 3. Au quotidien

| Situation | Commande |
|---|---|
| Lancer les tests | `pytest` |
| Une nouvelle migration Alembic vient d'être ajoutée | `alembic upgrade head` (base de développement) **puis** `python scripts/preparer_base_test.py` (base de test) |
| Repartir d'une base de test vide | `python scripts/preparer_base_test.py --reset` |
| Docker a été réinitialisé (volume supprimé) | `python scripts/preparer_base_test.py` |

Le script peut être relancé autant de fois que nécessaire : il ne recrée pas ce qui existe déjà.

## 4. Ce qui te protège

- **Un seul réglage, posé avant tout import** : `conftest.py` définit la variable d'environnement
  `POSTGRES_DB`, qui prime sur `.env`. La configuration, le moteur SQLAlchemy et l'API visent donc tous
  la base de test.
- **Garde-fou** : si le nom de la base ne finit pas par `_test`, `pytest` s'arrête tout de suite avec un
  message, avant d'écrire quoi que ce soit.
- **Le script refuse** un nom invalide ou égal à la base de développement : `--reset` ne peut pas
  supprimer ta vraie base.
- **Redis** : les tests utilisent déjà la base n° 15, séparée de celle du développement.

Pour utiliser un autre nom de base de test : variable d'environnement `POSTGRES_DB_TEST` (le nom doit
finir par `_test`).

## 5. Si ça ne marche pas

| Ce que tu vois | Cause probable | Action |
|---|---|---|
| Tests ignorés (« skipped ») : « Base de test non prête » | La base n'existe pas ou n'est pas migrée | `python scripts/preparer_base_test.py` |
| `Connexion impossible à la base « livraison »` | Docker Desktop éteint ou conteneurs arrêtés | `docker compose up -d`, puis relancer |
| « n'a pas ses tables après la migration » | `migrations/env.py` ne lit pas `POSTGRES_DB` : la migration est partie vers la base de développement | Envoie-moi `migrations/env.py` pour l'adapter |
| `permission denied to create database` ou `to create extension` | L'utilisateur PostgreSQL n'est pas superutilisateur | Avec l'image Docker habituelle, `POSTGRES_USER` l'est. Sinon, créer la base et activer PostGIS à la main |
| `pytest` s'arrête avec « finit par _test » | `POSTGRES_DB_TEST` pointe vers une autre base | Supprimer cette variable, ou la renommer en `..._test` |
| Échec `UniqueViolationError` encore présent | Le test tourne encore sur la base de développement | Vérifier que `tests/conftest.py` est bien la nouvelle version (le début du fichier doit contenir `os.environ["POSTGRES_DB"]`) |

## 6. Limites

- Si un jour tu ajoutes un test qui lance un processus séparé (un serveur, un agent) qui ouvre sa propre
  connexion, il doit hériter de `POSTGRES_DB` : un sous-processus lancé depuis `pytest` le fait
  automatiquement, un processus déjà lancé à part non.
- Le script suppose que `migrations/env.py` construit son adresse de connexion à partir de la
  configuration du projet (`livraison.config`). Il le vérifie à la fin et le dit clairement si ce n'est pas
  le cas.
