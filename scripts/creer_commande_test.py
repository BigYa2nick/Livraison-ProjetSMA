"""Crée une commande de test « validée » et annonce `commande_validee`, pour essayer le Contract Net.

Les commandes arriveront par l'API au lot 4 ; en attendant, ce script joue ce rôle :

    python scripts/creer_commande_test.py
    python scripts/creer_commande_test.py --enlevement 5.3236,-4.0197 --livraison 5.36,-3.99 --poids 3
    python scripts/creer_commande_test.py --suivre      # suit la course jusqu'à sa fin
    python scripts/creer_commande_test.py --annuler-tests  # annule les commandes de démonstration inachevées

Prérequis : PostgreSQL et Redis (docker compose up -d), la base migrée, et `python -m livraison.agents`
lancé dans un autre terminal avec au moins un livreur en ligne, validé, dont l'application envoie sa
position. La base utilisée est celle de .env (POSTGRES_DB).
"""

import argparse
import asyncio
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sqlalchemy import func, select  # noqa: E402

from livraison.persistance.enums import (  # noqa: E402
    EtatCommande,
    Priorite,
    Role,
    TailleColis,
    TypeEvenement,
)
from livraison.persistance.modeles import Commande, Evenement, Utilisateur  # noqa: E402
from livraison.persistance.session import FabriqueSession, moteur  # noqa: E402
from livraison.services import evenements  # noqa: E402
from livraison.services.redis_client import creer_redis  # noqa: E402
from livraison.services.securite import generer_code, hacher_code  # noqa: E402

TELEPHONE_CLIENT_TEST = "+2250700000001"
TELEPHONE_DESTINATAIRE_TEST = "+2250700000002"
ETATS_TEST_EN_COURS = (
    EtatCommande.CREEE,
    EtatCommande.VALIDEE,
    EtatCommande.RECHERCHE_LIVREUR,
    EtatCommande.ATTRIBUEE,
    EtatCommande.ENLEVEE,
    EtatCommande.EN_LIVRAISON,
)


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


async def creer(args) -> tuple[str, str]:
    code_secret = generer_code()
    destinataire_tel = TELEPHONE_DESTINATAIRE_TEST
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
            destinataire_telephone=destinataire_tel,
            taille_colis=TailleColis.PETIT,
            poids_kg=args.poids,
            distance_m=4500,
            prix_xof=args.prix,
            code_confirmation_hash=hacher_code(destinataire_tel, code_secret),
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
    return commande_id, code_secret


async def annuler_commandes_test(session) -> list[str]:
    """Annule uniquement les commandes inachevées créées par ce script de démonstration."""
    resultat = await session.scalars(
        select(Commande)
        .join(Utilisateur, Utilisateur.id == Commande.client_id)
        .where(
            Utilisateur.telephone == TELEPHONE_CLIENT_TEST,
            Commande.destinataire_nom == "Destinataire test",
            Commande.destinataire_telephone == TELEPHONE_DESTINATAIRE_TEST,
            Commande.adresse_enlevement.like("Point de test %"),
            Commande.adresse_livraison.like("Point de test %"),
            Commande.etat.in_(ETATS_TEST_EN_COURS),
        )
        .with_for_update()
    )
    commandes = list(resultat)
    maintenant = datetime.now(UTC)
    for commande in commandes:
        ancien_etat = commande.etat
        commande.etat = EtatCommande.ANNULEE
        commande.terminee_le = maintenant
        session.add(
            Evenement(
                commande_id=commande.id,
                type=TypeEvenement.CHANGEMENT_ETAT,
                auteur="outil:creer_commande_test",
                detail={"de": ancien_etat.value, "vers": EtatCommande.ANNULEE.value},
            )
        )
    await session.flush()
    return [str(commande.id) for commande in commandes]


async def annuler_commandes_test_en_base() -> list[str]:
    async with FabriqueSession() as session:
        commande_ids = await annuler_commandes_test(session)
        await session.commit()
    return commande_ids


async def suivre(commande_id: str) -> None:
    dernier = None
    while True:
        async with FabriqueSession() as session:
            commande = await session.get(Commande, uuid.UUID(commande_id))
            if commande is None:
                raise RuntimeError(f"Commande de test introuvable : {commande_id}")
            await session.refresh(commande)
            vue = (commande.etat.value, commande.tentatives_attribution, commande.rayon_recherche_m,
                   None if commande.livreur_id is None else str(commande.livreur_id))
        if vue != dernier:
            etat, tour, rayon, livreur = vue
            print(f"  état={etat}  tour={tour}  rayon={rayon} m  livreur={livreur}")
            dernier = vue
            if vue[0] == EtatCommande.ATTRIBUEE.value:
                print("Commande attribuée. Ctrl+C annule les commandes de test inachevées.")
            elif vue[0] in (
                EtatCommande.LIVREE.value,
                EtatCommande.CLOTUREE.value,
                EtatCommande.ANNULEE.value,
                EtatCommande.ECHEC_LIVRAISON.value,
            ):
                print(f"Commande terminée ({vue[0]}).")
                return
        await asyncio.sleep(1)


async def main() -> int:
    analyseur = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    analyseur.add_argument("--enlevement", type=lire_point, default=(5.3236, -4.0197), help="latitude,longitude (Plateau par défaut)")
    analyseur.add_argument("--livraison", type=lire_point, default=(5.3600, -3.9900), help="latitude,longitude (Cocody par défaut)")
    analyseur.add_argument("--poids", type=float, default=2.0, help="poids du colis en kg")
    analyseur.add_argument("--prix", type=int, default=2000, help="prix en XOF")
    analyseur.add_argument("--urgente", action="store_true")
    analyseur.add_argument(
        "--suivre",
        action="store_true",
        help="suivre la commande jusqu'à sa fin (Ctrl+C annule les commandes de test inachevées)",
    )
    analyseur.add_argument(
        "--annuler-tests",
        action="store_true",
        help="annuler les commandes de démonstration inachevées créées par ce script",
    )
    args = analyseur.parse_args()
    try:
        if args.annuler_tests:
            commande_ids = await annuler_commandes_test_en_base()
            print(f"{len(commande_ids)} commande(s) de test inachevée(s) annulée(s).")
            return 0

        commande_id, code_secret = await creer(args)
        print(f"Commande {commande_id} créée (état validee) et annoncée.")
        print(f"Référence de commande : {commande_id}")
        print(f"Code secret de livraison (F-23) : {code_secret}")
        print("Saisis ces 6 chiffres dans l'app livreur pour cette commande précise (zéro initial inclus).")
        if args.suivre:
            try:
                await suivre(commande_id)
            except asyncio.CancelledError:
                print("\nCtrl+C reçu : annulation des commandes de test inachevées...")
                commande_ids = await annuler_commandes_test_en_base()
                print(f"{len(commande_ids)} commande(s) de test annulée(s).")
                return 130
    finally:
        await moteur.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
