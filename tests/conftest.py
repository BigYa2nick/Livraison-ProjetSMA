"""Fixtures partagées par les tests.

Les tests s'exécutent sur une base dédiée (`livraison_test` par défaut), jamais sur la base de
développement : voir docs/tests.md et scripts/preparer_base_test.py.
"""
# ruff: noqa: E402  (la variable d'environnement ci-dessous doit précéder les imports du projet)

import os

# Posée AVANT l'import de livraison.config : une variable d'environnement prime sur le fichier .env,
# donc la configuration, le moteur SQLAlchemy et l'API visent tous la base de test.
os.environ["POSTGRES_DB"] = os.environ.get("POSTGRES_DB_TEST", "livraison_test")

import re

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from aides import SmsEspion
from livraison.api.main import app
from livraison.config import parametres
from livraison.persistance.session import obtenir_session
from livraison.services.sms import obtenir_envoi_sms

SECRET_JWT_TEST = "secret-de-test-" + "x" * 40

# Garde-fou : on refuse de lancer les tests sur une base qui n'est pas une base de test.
if not parametres.postgres_db.endswith("_test"):
    raise pytest.UsageError(
        f"Les tests doivent utiliser une base dont le nom finit par _test (reçu : "
        f"{parametres.postgres_db!r}). Vérifie la variable POSTGRES_DB_TEST."
    )


@pytest.fixture(autouse=True)
def configuration_de_test(monkeypatch):
    """Les tests ne dépendent pas du contenu de .env pour la sécurité."""
    monkeypatch.setattr(parametres, "jwt_secret", SECRET_JWT_TEST)
    monkeypatch.setattr(parametres, "environnement", "dev")
    monkeypatch.setattr(parametres, "sms_mode", "console")
    # Indépendant de .env : les tests qui exigent la validation du dossier l'activent eux-mêmes.
    monkeypatch.setattr(parametres, "livreur_validation_requise", False)


@pytest_asyncio.fixture
async def session():
    """Session PostgreSQL dont tout le contenu est annulé à la fin du test.

    Si la base n'est pas joignable ou pas migrée, le test est ignoré (et non en échec).
    """
    moteur = create_async_engine(parametres.sqlalchemy_url)
    try:
        async with moteur.connect() as cx:
            await cx.execute(text("SELECT echecs_connexion FROM utilisateur LIMIT 1"))
            await cx.execute(text("SELECT motif_rejet, valide_le FROM livreur LIMIT 1"))  # 0003
            await cx.execute(text("SELECT type_mime, examine_le FROM document_livreur LIMIT 1"))
    except Exception as e:  # noqa: BLE001
        await moteur.dispose()
        pytest.skip(
            f"Base de test non prête ({parametres.postgres_db}) : lance "
            f"« python scripts/preparer_base_test.py » ({type(e).__name__})"
        )
    async with moteur.connect() as cx:
        transaction = await cx.begin()
        async with AsyncSession(
            bind=cx, expire_on_commit=False, join_transaction_mode="create_savepoint"
        ) as s:
            yield s
        await transaction.rollback()
    await moteur.dispose()


@pytest.fixture
def sms():
    return SmsEspion()


@pytest_asyncio.fixture
async def client(session, sms):
    """Client HTTP branché sur l'API, avec la session de test et un faux envoi de SMS."""

    async def _session():
        return session

    app.dependency_overrides[obtenir_session] = _session
    app.dependency_overrides[obtenir_envoi_sms] = lambda: sms
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def redis_test(monkeypatch):
    """Redis de test (base n° 15, vidée avant et après). Test ignoré si Redis est injoignable."""
    url = re.sub(r"/\d+$", "", parametres.redis_url) + "/15"
    monkeypatch.setattr(parametres, "redis_url", url)
    redis = Redis.from_url(url, decode_responses=True)
    try:
        await redis.ping()
    except Exception as e:  # noqa: BLE001
        await redis.aclose()
        pytest.skip(f"Redis injoignable (docker compose up -d) : {type(e).__name__}")
    await redis.flushdb()
    yield redis
    await redis.flushdb()
    await redis.aclose()
