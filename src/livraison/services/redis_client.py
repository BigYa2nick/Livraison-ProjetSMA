"""Connexion à Redis (dernières positions GPS, diffusion en temps réel)."""

from collections.abc import AsyncIterator

from redis.asyncio import Redis

from livraison.config import parametres


def creer_redis() -> Redis:
    return Redis.from_url(parametres.redis_url, decode_responses=True)


async def obtenir_redis() -> AsyncIterator[Redis]:
    """Dépendance FastAPI : une connexion par requête, fermée à la fin."""
    client = creer_redis()
    try:
        yield client
    finally:
        await client.aclose()
