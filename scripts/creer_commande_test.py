"""Crée une commande de test « validée » et annonce `commande_validee`, pour essayer le Contract Net.

Les commandes arriveront par l'API au lot 4 ; en attendant, ce script joue ce rôle :

    python scripts/creer_commande_test.py
    python scripts/creer_commande_test.py --enlevement 5.3236,-4.0197 --livraison 5.36,-3.99 --poids 3
    python scripts/creer_commande_test.py --suivre      # affiche ensuite l'attribution, 90 s au plus

Prérequis : PostgreSQL et Redis (docker compose up -d), la base migrée, et `python -m livraison.agents`
lancé dans un autre terminal avec au moins un livreur en ligne, validé, dont l'application envoie sa
position. La base utilisée est celle de .env (POSTGRES_DB).
"""

import argparse
import asyncio
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sqlalchemy import func, select  # noqa: E402

from livraison.persistance.enums import (  # noqa: E402
    EtatCommande,
    Priorite,
    Role,
    TailleColis,
)
from livraison.persistance.modeles import Commande, Utilisateur  # noqa: E402
from livraison.persistance.session import FabriqueSession, moteur  # noqa: E402
from livraison.services import evenements  # noqa: E402
from livraison.services.redis_client import creer_redis  # noqa: E402

TELEPHONE_CLIENT_TEST = "+2250700000001"


def lire_point(texte: str) -> tuple[float, float]:
    try:
        latitude, longitude = (float(x) for x in texte.split(","))
    except ValueError as e:
        raise argparse.ArgumentTypeError("attendu : latitude,longitude (ex. 5.3236,-4.0197)") from e
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        raise argparse.ArgumentTypeError("coordonnées hors limites")
    return latitude, longitude


def point_wkt(point: tuple[float, float]):
    return func.ST_GeogFromText(f"SRID=4326;POINT({point[1]} {point[0]})")  # longitude puis latitude


async def client_de_test(session) -> Utilisateur:
    client = (
        await session.execute(select(Utilisateur).where(Utilisateur.telephone == TELEPHONE_CLIENT_TEST))
    ).scalar_one_or_none()
    if client is None:
        client = Utilisateur(
            telephone=TELEPHONE_CLIENT_TEST, role=Role.CLIENT, nom="Client", prenom="Test"
        )
        session.add(client)
        await session.flush()
    return client


async def creer(args) -> str:
    async with FabriqueSession() as session:
        client = await client_de_test(session)
        commande = Commande(
            client_id=client.id,
            etat=EtatCommande.VALIDEE,
            priorite=Priorite.URGENTE if args.urgente else Priorite.NORMALE,
            adresse_enlevement=f"Point de test {args.enlevement[0]:.4f},{args.enlevement[1]:.4f}",
            enlevement=point_wkt(args.enlevement),
            adresse_livraison=f"Point de test {args.livraison[0]:.4f},{args.livraison[1]:.4f}",
            livraison=point_wkt(args.livraison),
            destinataire_nom="Destinataire test",
            destinataire_telephone="+2250700000002",
            taille_colis=TailleColis.PETIT,
            poids_kg=args.poids,
            prix_xof=args.prix,
            validee_le=func.now(),
        )
        session.add(commande)
        await session.flush()
        commande_id = str(commande.id)
        await session.commit()  # d'abord la base (D-04), ensuite l'annonce
    redis = creer_redis()
    try:
        if not await evenements.publier(redis, evenements.COMMANDE_VALIDEE, commande_id=commande_id):
            print("Redis ne répond pas : l'annonce n'est pas partie. Les agents trouveront la "
                  "commande à leur prochaine réconciliation (toutes les 30 s).")
    finally:
        await redis.aclose()
    return commande_id


async def suivre(commande_id: str, duree_s: float) -> None:
    fin = time.monotonic() + duree_s
    dernier = None
    while time.monotonic() < fin:
        async with FabriqueSession() as session:
            commande = await session.get(Commande, uuid.UUID(commande_id))
            await session.refresh(commande)
            vue = (commande.etat.value, commande.tentatives_attribution, commande.rayon_recherche_m,
                   None if commande.livreur_id is None else str(commande.livreur_id))
        if vue != dernier:
            etat, tour, rayon, livreur = vue
            print(f"  état={etat}  tour={tour}  rayon={rayon} m  livreur={livreur}")
            dernier = vue
        if vue[0] == EtatCommande.ATTRIBUEE.value:
            print("Commande attribuée.")
            return
        await asyncio.sleep(1)
    print("Pas encore attribuée (livreur en ligne, validé, avec une position récente ? Il a 30 s pour confirmer).")


async def main() -> int:
    analyseur = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    analyseur.add_argument("--enlevement", type=lire_point, default=(5.3236, -4.0197), help="latitude,longitude (Plateau par défaut)")
    analyseur.add_argument("--livraison", type=lire_point, default=(5.3600, -3.9900), help="latitude,longitude (Cocody par défaut)")
    analyseur.add_argument("--poids", type=float, default=2.0, help="poids du colis en kg")
    analyseur.add_argument("--prix", type=int, default=2000, help="prix en XOF")
    analyseur.add_argument("--urgente", action="store_true")
    analyseur.add_argument("--suivre", action="store_true", help="afficher l'attribution pendant 90 s")
    args = analyseur.parse_args()
    try:
        commande_id = await creer(args)
        print(f"Commande {commande_id} créée (état validee) et annoncée.")
        if args.suivre:
            await suivre(commande_id, 90.0)
    finally:
        await moteur.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
