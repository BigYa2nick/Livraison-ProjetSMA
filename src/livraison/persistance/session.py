"""Moteur et sessions asynchrones vers PostgreSQL."""

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from livraison.config import parametres


def creer_moteur() -> AsyncEngine:
    return create_async_engine(parametres.sqlalchemy_url, pool_pre_ping=True)


moteur = creer_moteur()
FabriqueSession = async_sessionmaker(moteur, expire_on_commit=False)


async def obtenir_session() -> AsyncIterator[AsyncSession]:
    """Dépendance FastAPI : une session par requête, validée ou annulée à la fin."""
    async with FabriqueSession() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise