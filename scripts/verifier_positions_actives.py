import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from livraison.agents import boucle
from livraison.agents.registre import distance_vol_d_oiseau_m
from livraison.services.itineraires import Point
from livraison.services.positions import lire_positions
from livraison.services.redis_client import creer_redis

CENTRE_COMMANDE = Point(5.3236, -4.0197)  # Plateau, Abidjan


async def main():
    boucle.utiliser_boucle_compatible_xmpp()
    redis = creer_redis()
    try:
        positions = await lire_positions(redis)
        print(f"Nombre de livreurs avec une position dans Redis : {len(positions)}")
        for p in positions:
            pt = Point(p["latitude"], p["longitude"])
            dist = distance_vol_d_oiseau_m(CENTRE_COMMANDE, pt)
            print(
                f"  Livreur {p['livreur_id']} : ({p['latitude']:.4f}, {p['longitude']:.4f}), "
                f"age={p['age_s']:.1f}s, périmée={p['perimee']}, "
                f"distance Plateau={dist:.0f}m"
            )
    finally:
        await redis.aclose()


if __name__ == "__main__":
    asyncio.run(main())
