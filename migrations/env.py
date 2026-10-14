"""Environnement Alembic : connexion asynchrone et détection des modèles."""

import asyncio
from copy import copy
from logging.config import fileConfig

import sqlalchemy as sa
from alembic import context
from alembic.operations.ops import CreateTableOp
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from livraison.config import parametres
from livraison.persistance import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# Tables créées par l'extension PostGIS elle-même : Alembic ne doit pas y toucher.
TABLES_POSTGIS = {"spatial_ref_sys", "geography_columns", "geometry_columns"}


def inclure_objet(objet, nom, type_, reflected, compare_to) -> bool:
    return not (type_ == "table" and nom in TABLES_POSTGIS)


def eviter_check_en_double(contexte, revision, directives) -> None:
    """Évite que la contrainte CHECK d'une colonne énumérée soit écrite deux fois.

    Alembic écrit la contrainte à côté de la table, puis le type Enum la recrée à l'exécution.
    On désactive donc la seconde pour les colonnes des tables créées par la migration.
    """
    if not directives:
        return
    for operation in directives[0].upgrade_ops.ops:
        if isinstance(operation, CreateTableOp):
            for element in operation.columns:
                if isinstance(element, sa.Column) and isinstance(element.type, sa.Enum):
                    type_sans_check = copy(element.type)
                    type_sans_check.create_constraint = False
                    element.type = type_sans_check


def _options() -> dict:
    return {
        "target_metadata": target_metadata,
        "include_object": inclure_objet,
        "process_revision_directives": eviter_check_en_double,
        "compare_type": True,
        "compare_server_default": True,
    }


def migrer_hors_ligne() -> None:
    """Mode « --sql » : produit le script SQL sans se connecter à la base."""
    context.configure(
        url=parametres.sqlalchemy_url.render_as_string(hide_password=False),
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        **_options(),
    )
    with context.begin_transaction():
        context.run_migrations()


def _executer(connexion: Connection) -> None:
    context.configure(connection=connexion, **_options())
    with context.begin_transaction():
        context.run_migrations()


async def migrer_en_ligne() -> None:
    moteur = create_async_engine(parametres.sqlalchemy_url)
    async with moteur.connect() as connexion:
        await connexion.run_sync(_executer)
    await moteur.dispose()


if context.is_offline_mode():
    migrer_hors_ligne()
else:
    asyncio.run(migrer_en_ligne())