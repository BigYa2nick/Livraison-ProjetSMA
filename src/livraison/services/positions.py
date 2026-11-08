"""Réception et diffusion des positions GPS des livreurs (F-12, F-20, F-60, NF-01, NF-07).

Chemin d'une position :
    téléphone -> POST /livreurs/moi/position -> PostgreSQL (historique) + Redis (dernière
    position, durée de vie limitée) + canal Redis "positions" -> WebSocket /ws/suivi -> carte.
"""

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from redis.asyncio import Redis
from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession

from livraison.config import parametres
from livraison.persistance.modeles import Position
from livraison.services.erreurs import ErreurMetier

CANAL_POSITIONS = "positions"
_PREFIXE_CLE = "position:"
_TOLERANCE_HORLOGE = timedelta(seconds=60)  # écart d'horloge admis entre téléphone et serveur


def cle_position(livreur_id: uuid.UUID | str) -> str:
    return f"{_PREFIXE_CLE}{livreur_id}"


@dataclass
class PositionAcceptee:
    recu_le: datetime
    retard_s: float  # délai entre la mesure sur le téléphone et la réception par le serveur


def _point_wkt(latitude: float, longitude: float):
    # PostGIS attend longitude puis latitude.
    return func.ST_GeogFromText(f"SRID=4326;POINT({longitude} {latitude})")


async def enregistrer_position(
    session: AsyncSession,
    redis: Redis,
    livreur,
    latitude: float,
    longitude: float,
    precision_m: float | None,
    mesure_le: datetime,
) -> PositionAcceptee:
    """Enregistre une position envoyée par un livreur qui est en ligne."""
    if not livreur.en_ligne:
        raise ErreurMetier(409, "Passez en ligne avant d'envoyer votre position.")
    maintenant = datetime.now(UTC)
    if mesure_le > maintenant + _TOLERANCE_HORLOGE:
        raise ErreurMetier(422, "Horodatage de la position dans le futur.")

    # Historique complet (analyse des trous et des retards)
    session.add(
        Position(
            livreur_id=livreur.utilisateur_id,
            localisation=_point_wkt(latitude, longitude),
            precision_m=precision_m,
            enregistre_le=mesure_le,
            recu_le=maintenant,
        )
    )

    # Dernière position connue : seulement si elle est plus récente que la précédente
    # (des positions mises en attente par le téléphone peuvent arriver en retard).
    plus_recente = livreur.position_maj_le is None or mesure_le >= livreur.position_maj_le
    if plus_recente:
        livreur.derniere_position = _point_wkt(latitude, longitude)
        livreur.position_maj_le = mesure_le
    await session.flush()

    if plus_recente:
        message = {
            "type": "position",
            "livreur_id": str(livreur.utilisateur_id),
            "latitude": latitude,
            "longitude": longitude,
            "precision_m": precision_m,
            "mesure_le": mesure_le.isoformat(),
            "recu_le": maintenant.isoformat(),
        }
        cle = cle_position(livreur.utilisateur_id)
        await redis.hset(cle, mapping={k: "" if v is None else str(v) for k, v in message.items()})
        await redis.expire(cle, parametres.position_ttl_secondes)
        await redis.publish(CANAL_POSITIONS, json.dumps(message))

    return PositionAcceptee(recu_le=maintenant, retard_s=(maintenant - mesure_le).total_seconds())


async def lire_positions(redis: Redis) -> list[dict]:
    """Dernières positions de tous les livreurs en ligne, avec l'âge de chaque position."""
    maintenant = datetime.now(UTC)
    resultat = []
    async for cle in redis.scan_iter(match=f"{_PREFIXE_CLE}*"):
        donnees = await redis.hgetall(cle)
        if not donnees:
            continue
        age = (maintenant - datetime.fromisoformat(donnees["mesure_le"])).total_seconds()
        resultat.append(
            {
                "livreur_id": donnees["livreur_id"],
                "latitude": float(donnees["latitude"]),
                "longitude": float(donnees["longitude"]),
                "precision_m": float(donnees["precision_m"]) if donnees["precision_m"] else None,
                "mesure_le": donnees["mesure_le"],
                "recu_le": donnees["recu_le"],
                "age_s": round(age, 1),
                "perimee": age > parametres.position_age_max_secondes,
            }
        )
    return resultat
