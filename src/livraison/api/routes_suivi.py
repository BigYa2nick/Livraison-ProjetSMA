"""Suivi en direct des livreurs, pour le répartiteur et l'administrateur (F-60)."""

import asyncio

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect
from redis.asyncio import Redis

from livraison.api.dependances import exiger_roles
from livraison.api.schemas_gps import PositionSuivi
from livraison.persistance.enums import Role
from livraison.persistance.modeles import Utilisateur
from livraison.persistance.session import FabriqueSession
from livraison.services import auth, positions
from livraison.services.auth import ErreurAuthentification
from livraison.services.redis_client import creer_redis, obtenir_redis

routeur = APIRouter(tags=["Suivi"])

ROLES_SUIVI = (Role.REPARTITEUR, Role.ADMINISTRATEUR)
personnel_connecte = exiger_roles(*ROLES_SUIVI)


@routeur.get("/suivi/positions", response_model=list[PositionSuivi])
async def positions_actuelles(
    _: Utilisateur = Depends(personnel_connecte),
    redis: Redis = Depends(obtenir_redis),
):
    """Instantané : dernière position de chaque livreur en ligne."""
    return await positions.lire_positions(redis)


@routeur.websocket("/ws/suivi")
async def suivi_en_direct(websocket: WebSocket, token: str = Query(...)):
    """Flux en direct des positions. Le jeton d'accès est passé dans l'adresse (?token=...).

    Messages envoyés : un « instantane » à la connexion, puis des « position » et des « hors_ligne ».
    """
    async with FabriqueSession() as session:
        try:
            utilisateur = await auth.utilisateur_depuis_jeton(session, token, "acces")
        except ErreurAuthentification:
            await websocket.close(code=4401)
            return
    if utilisateur.role not in ROLES_SUIVI:
        await websocket.close(code=4403)
        return

    await websocket.accept()
    redis = creer_redis()
    pubsub = redis.pubsub()
    try:
        await pubsub.subscribe(positions.CANAL_POSITIONS)
        await websocket.send_json(
            {"type": "instantane", "positions": await positions.lire_positions(redis)}
        )

        async def relayer() -> None:
            async for message in pubsub.listen():
                if message["type"] == "message":
                    await websocket.send_text(message["data"])

        async def surveiller_deconnexion() -> None:
            while True:
                await websocket.receive_text()

        taches = {asyncio.create_task(relayer()), asyncio.create_task(surveiller_deconnexion())}
        _, en_attente = await asyncio.wait(taches, return_when=asyncio.FIRST_COMPLETED)
        for tache in en_attente:
            tache.cancel()
    except WebSocketDisconnect:
        pass
    finally:
        await pubsub.unsubscribe()
        await pubsub.aclose()
        await redis.aclose()
