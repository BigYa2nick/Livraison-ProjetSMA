"""Prépare la base de données des tests.

Les tests tournent sur une base à part (`livraison_test`) pour ne jamais se heurter aux comptes et aux
données créés pendant le développement (iPhone, app Flet, Swagger...).

À lancer à la racine du dépôt, venv activé, Docker démarré :

    python scripts/preparer_base_test.py            crée la base si besoin, active PostGIS, migre
    python scripts/preparer_base_test.py --reset    supprime la base de test puis la recrée vide

À relancer après chaque nouvelle migration Alembic : la base de test ne suit pas toute seule.
Pour une autre base de test : variable d'environnement POSTGRES_DB_TEST (le nom doit finir par _test).
"""

import argparse
import asyncio
import os
import re
import subprocess
import sys
from pathlib import Path

import asyncpg

from livraison.config import parametres

RACINE = Path(__file__).resolve().parent.parent
NOM_BASE_TEST = os.environ.get("POSTGRES_DB_TEST", "livraison_test")
TABLES_ATTENDUES = ("utilisateur", "livreur", "commande")


def verifier_nom() -> None:
    # Le nom est inséré tel quel dans CREATE / DROP DATABASE : on le contrôle strictement.
    if not re.fullmatch(r"[a-z][a-z0-9_]*_test", NOM_BASE_TEST):
        sys.exit(
            f"Nom de base de test invalide : {NOM_BASE_TEST!r} "
            "(minuscules, chiffres et _, et il doit finir par _test)."
        )
    if NOM_BASE_TEST == parametres.postgres_db:
        sys.exit("La base de test ne peut pas être la base de développement.")


async def _connecter(base: str) -> asyncpg.Connection:
    try:
        return await asyncpg.connect(
            user=parametres.postgres_user,
            password=parametres.postgres_password,
            host=parametres.postgres_host,
            port=parametres.postgres_port,
            database=base,
        )
    except (OSError, asyncpg.PostgresError) as e:
        sys.exit(
            f"Connexion impossible à la base « {base} » ({type(e).__name__}). "
            "Docker Desktop est-il démarré (docker compose up -d) ?"
        )


async def creer_base(reset: bool) -> None:
    admin = await _connecter(parametres.postgres_db)  # la base de développement existe forcément
    try:
        existe = await admin.fetchval(
            "SELECT 1 FROM pg_database WHERE datname = $1", NOM_BASE_TEST
        )
        if existe and reset:
            await admin.execute(f'DROP DATABASE "{NOM_BASE_TEST}" WITH (FORCE)')
            print(f"Base {NOM_BASE_TEST} supprimée.")
            existe = False
        if existe:
            print(f"Base {NOM_BASE_TEST} déjà présente.")
        else:
            await admin.execute(f'CREATE DATABASE "{NOM_BASE_TEST}"')
            print(f"Base {NOM_BASE_TEST} créée.")
    finally:
        await admin.close()
    cx = await _connecter(NOM_BASE_TEST)
    try:
        await cx.execute("CREATE EXTENSION IF NOT EXISTS postgis")
    finally:
        await cx.close()


def migrer() -> None:
    """Applique les migrations Alembic à la base de test (POSTGRES_DB prime sur .env)."""
    print("Migrations Alembic...")
    env = {**os.environ, "POSTGRES_DB": NOM_BASE_TEST}
    resultat = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"], cwd=RACINE, env=env
    )
    if resultat.returncode != 0:
        sys.exit("La migration a échoué : lis le message d'Alembic ci-dessus.")


async def verifier() -> None:
    cx = await _connecter(NOM_BASE_TEST)
    try:
        presentes = await cx.fetch(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = ANY($1::text[])",
            list(TABLES_ATTENDUES),
        )
        revision = None
        if await cx.fetchval("SELECT to_regclass('public.alembic_version')"):
            revision = await cx.fetchval("SELECT version_num FROM alembic_version")
        nb_tables = await cx.fetchval(
            "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public'"
        )
    finally:
        await cx.close()
    if len(presentes) != len(TABLES_ATTENDUES):
        sys.exit(
            f"La base {NOM_BASE_TEST} n'a pas ses tables après la migration : migrations/env.py "
            "ne lit sans doute pas POSTGRES_DB (la migration est partie vers une autre base). "
            "Envoie migrations/env.py pour qu'on l'adapte."
        )
    print(f"Base de test prête : {NOM_BASE_TEST}, révision {revision}, {nb_tables} tables.")


def main() -> None:
    lecteur = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    lecteur.add_argument(
        "--reset", action="store_true", help="supprime la base de test puis la recrée vide"
    )
    arguments = lecteur.parse_args()
    verifier_nom()
    asyncio.run(creer_base(arguments.reset))
    migrer()
    asyncio.run(verifier())


if __name__ == "__main__":
    main()
