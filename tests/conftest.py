"""Fixtures partagées par les tests."""

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from livraison.config import parametres

SECRET_JWT_TEST = "secret-de-test-" + "x" * 40


@pytest.fixture(autouse=True)
def configuration_de_test(monkeypatch):
    """Les tests ne dépendent pas du contenu de .env pour la sécurité."""
    monkeypatch.setattr(parametres, "jwt_secret", SECRET_JWT_TEST)
    monkeypatch.setattr(parametres, "environnement", "dev")
    monkeypatch.setattr(parametres, "sms_mode", "console")


@pytest_asyncio.fixture
async def session():
    """Session PostgreSQL dont tout le contenu est annulé à la fin du test.

    Si la base n'est pas joignable ou pas migrée, le test est ignoré (et non en échec).
    """
    moteur = create_async_engine(parametres.sqlalchemy_url)
    try:
        async with moteur.connect() as cx:
            await cx.execute(text("SELECT echecs_connexion FROM utilisateur LIMIT 1"))
    except Exception as e:  # noqa: BLE001
        await moteur.dispose()
        pytest.skip(
            f"Base non prête (docker compose up -d, puis alembic upgrade head) : {type(e).__name__}"
        )
    async with moteur.connect() as cx:
        transaction = await cx.begin()
        async with AsyncSession(
            bind=cx, expire_on_commit=False, join_transaction_mode="create_savepoint"
        ) as s:
            yield s
        await transaction.rollback()
    await moteur.dispose()
