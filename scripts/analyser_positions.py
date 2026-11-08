"""Analyse les positions GPS reçues d'un livreur : trous, retards, précision.

À lancer après un test sur téléphone pour savoir si le suivi tient les objectifs
(trous de moins de 30 s, 95 % des positions reçues en moins de 10 s, NF-01) :

    python scripts/analyser_positions.py --telephone 0700000001 --minutes 60
"""

import argparse
import asyncio
import statistics
import sys
from datetime import UTC, datetime, timedelta
from itertools import pairwise

from sqlalchemy import select

from livraison.config import parametres
from livraison.modele.telephone import TelephoneInvalide, normaliser_telephone
from livraison.persistance.modeles import Position, Utilisateur
from livraison.persistance.session import FabriqueSession, moteur


def percentile(valeurs: list[float], p: float) -> float:
    ordonnees = sorted(valeurs)
    return ordonnees[min(len(ordonnees) - 1, int(p / 100 * len(ordonnees)))]


def formater(secondes: float) -> str:
    return f"{secondes:.1f} s" if secondes < 120 else f"{secondes / 60:.1f} min"


async def lire(telephone: str, minutes: int):
    depuis = datetime.now(UTC) - timedelta(minutes=minutes)
    async with FabriqueSession() as session:
        requete = (
            select(Position.enregistre_le, Position.recu_le, Position.precision_m)
            .join(Utilisateur, Utilisateur.id == Position.livreur_id)
            .where(Utilisateur.telephone == telephone, Position.recu_le >= depuis)
            .order_by(Position.enregistre_le)
        )
        return (await session.execute(requete)).all()


def analyser(lignes, seuil_trou: float, seuil_retard: float) -> bool:
    if len(lignes) < 2:
        print(f"Seulement {len(lignes)} position(s) : pas assez pour analyser.")
        return False
    mesures = [ligne.enregistre_le for ligne in lignes]
    ecarts = [(b - a).total_seconds() for a, b in pairwise(mesures)]
    retards = [max(0.0, (ligne.recu_le - ligne.enregistre_le).total_seconds()) for ligne in lignes]
    precisions = [ligne.precision_m for ligne in lignes if ligne.precision_m is not None]
    trous = [(mesures[i], ecart) for i, ecart in enumerate(ecarts) if ecart > seuil_trou]

    duree = (mesures[-1] - mesures[0]).total_seconds()
    print(f"Positions : {len(lignes)} sur {formater(duree)}")
    print(f"Du {mesures[0]:%H:%M:%S} au {mesures[-1]:%H:%M:%S} (UTC)\n")

    print("Intervalle entre deux positions mesurées")
    print(
        f"  médian {formater(statistics.median(ecarts))}   95e centile {formater(percentile(ecarts, 95))}"
        f"   max {formater(max(ecarts))}"
    )
    print("Retard entre la mesure et la réception par le serveur")
    print(
        f"  médian {formater(statistics.median(retards))}   95e centile {formater(percentile(retards, 95))}"
        f"   max {formater(max(retards))}"
    )
    if precisions:
        print(
            f"Précision GPS : médiane {statistics.median(precisions):.0f} m, pire {max(precisions):.0f} m"
        )

    rapides = sum(1 for r in retards if r <= seuil_retard) / len(retards) * 100
    print(f"\nTrous de plus de {seuil_trou:.0f} s : {len(trous)}")
    for debut, duree_trou in trous[:15]:
        print(f"  à {debut:%H:%M:%S} UTC : {formater(duree_trou)} sans position")
    if len(trous) > 15:
        print(f"  ... et {len(trous) - 15} autres")

    ok_trous, ok_retard = not trous, rapides >= 95
    print(f"Positions reçues en moins de {seuil_retard:.0f} s : {rapides:.0f} %")
    print("\nVerdict")
    print(f"  [{'OK' if ok_trous else 'KO'}] aucun trou de plus de {seuil_trou:.0f} s")
    print(
        f"  [{'OK' if ok_retard else 'KO'}] au moins 95 % des positions reçues en moins de {seuil_retard:.0f} s"
    )
    return ok_trous and ok_retard


async def principal(args) -> bool:
    try:
        telephone = normaliser_telephone(args.telephone, parametres.indicatif_pays_defaut)
    except TelephoneInvalide as e:
        sys.exit(str(e))
    lignes = await lire(telephone, args.minutes)
    await moteur.dispose()
    print(f"Livreur {telephone}, {args.minutes} dernières minutes\n")
    return analyser(lignes, args.seuil_trou, args.seuil_retard)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--telephone", required=True)
    parser.add_argument("--minutes", type=int, default=60)
    parser.add_argument("--seuil-trou", type=float, default=30.0, help="secondes")
    parser.add_argument("--seuil-retard", type=float, default=10.0, help="secondes")
    args = parser.parse_args()
    sys.exit(0 if asyncio.run(principal(args)) else 1)


if __name__ == "__main__":
    main()
