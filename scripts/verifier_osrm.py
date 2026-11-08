"""Vérifie qu'OSRM répond et que quelques trajets d'essai à Abidjan sont plausibles.

À lancer à la racine du dépôt, venv activé, une fois les serveurs démarrés
(docker compose -f docker-compose.osrm.yml up -d) :

    python scripts/verifier_osrm.py

Code de retour 0 : OSRM répond partout. Les trajets à examiner sont signalés par « A EXAMINER » sans
faire échouer le script : seul un humain qui connaît Abidjan peut juger d'un détour.
"""

import asyncio
import sys

from livraison.persistance.enums import TypeVehicule
from livraison.services import itineraires
from livraison.services.itineraires import ErreurItineraire, Point

# Positions approximatives, pour un contrôle de plausibilité (pas pour la navigation).
LIEUX = {
    "Plateau": Point(5.3236, -4.0197),
    "Cocody (Riviera)": Point(5.3480, -3.9870),
    "Koumassi": Point(5.3027, -3.9478),
    "Yopougon": Point(5.3400, -4.0800),
}
TRAJETS = [
    ("Plateau", "Cocody (Riviera)"),
    ("Plateau", "Koumassi"),
    ("Yopougon", "Plateau"),
    ("Koumassi", "Cocody (Riviera)"),
]
VEHICULES = [("voiture / moto", TypeVehicule.VOITURE), ("vélo", TypeVehicule.VELO)]


def jugement(droit_m: float, distance_m: float, duree_s: float) -> str:
    vitesse = distance_m / duree_s * 3.6 if duree_s else 0
    if distance_m < droit_m * 0.98:
        return "A EXAMINER (route plus courte que la ligne droite)"
    if distance_m > droit_m * 3:
        return "A EXAMINER (détour supérieur à x3)"
    if not 3 <= vitesse <= 90:
        return f"A EXAMINER (vitesse moyenne de {vitesse:.0f} km/h)"
    return "plausible"


async def main() -> int:
    try:
        etat = await itineraires.sante()
        print("Serveurs OSRM :", {k: ("répond" if v else "NE RÉPOND PAS") for k, v in etat.items()})
        if not all(etat.values()):
            print("\nDémarrer les serveurs : docker compose -f docker-compose.osrm.yml up -d")
            print("Les données manquent ? python scripts/preparer_osrm.py")
            return 1
        erreurs = 0
        colonnes = f"{'Trajet':40} {'Véhicule':15} {'Distance':>9} {'Durée':>8} {'x droite':>9}"
        print(f"\n{colonnes}  Jugement")
        for depart, arrivee in TRAJETS:
            a, b = LIEUX[depart], LIEUX[arrivee]
            droit = itineraires.distance_vol_d_oiseau_m(a, b)
            for nom, vehicule in VEHICULES:
                trajet = f"{depart} vers {arrivee}"
                try:
                    r = await itineraires.itineraire(a, b, vehicule, repli=False)
                except ErreurItineraire as e:
                    erreurs += 1
                    print(f"{trajet:40} {nom:15} ERREUR : {type(e).__name__} {e}")
                    continue
                print(
                    f"{trajet:40} {nom:15} {r.distance_m / 1000:7.1f} km {r.duree_s / 60:5.0f} min "
                    f"{r.distance_m / droit:8.2f}  {jugement(droit, r.distance_m, r.duree_s)}"
                )
        return 1 if erreurs else 0
    finally:
        await itineraires.fermer_client()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
