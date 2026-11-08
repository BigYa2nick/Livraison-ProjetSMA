"""Simulateur de GPS : parcourt un trajet à Abidjan, sans téléphone ni satellite.

Sert à tester le serveur et la carte depuis un ordinateur. Le trajet est une ligne brisée
approximative (Plateau vers Cocody), pas un itinéraire routier réel.
"""

import math
import random

# (latitude, longitude)
TRAJET_PLATEAU_COCODY = [
    (5.3236, -4.0197),
    (5.3290, -4.0100),
    (5.3350, -4.0020),
    (5.3420, -3.9930),
    (5.3480, -3.9870),
]

_METRES_PAR_DEGRE = 111_320.0


def distance_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Distance en mètres entre deux points (latitude, longitude), formule de haversine."""
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = (
        math.sin((lat2 - lat1) / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    )
    return 2 * 6_371_000 * math.asin(math.sqrt(h))


class Simulateur:
    """Avance le long du trajet (aller puis retour, en boucle) à vitesse constante."""

    def __init__(
        self,
        trajet: list[tuple[float, float]] | None = None,
        vitesse_m_s: float = 8.0,  # environ 29 km/h
        bruit_m: float = 4.0,
        graine: int | None = None,
        decalage_m: float = 0.0,  # permet d'espacer plusieurs livreurs simulés
    ):
        self.trajet = trajet or TRAJET_PLATEAU_COCODY
        self.vitesse = vitesse_m_s
        self.bruit_m = bruit_m
        self._rng = random.Random(graine)
        self._parcouru = decalage_m
        self._segments = [
            distance_m(self.trajet[i], self.trajet[i + 1]) for i in range(len(self.trajet) - 1)
        ]
        self.longueur = sum(self._segments)

    def _point_a(self, distance: float) -> tuple[float, float]:
        d = distance % (2 * self.longueur)
        if d > self.longueur:  # trajet retour
            d = 2 * self.longueur - d
        for i, longueur in enumerate(self._segments):
            if d <= longueur or i == len(self._segments) - 1:
                f = min(d / longueur, 1.0)
                (lat1, lon1), (lat2, lon2) = self.trajet[i], self.trajet[i + 1]
                return lat1 + (lat2 - lat1) * f, lon1 + (lon2 - lon1) * f
            d -= longueur
        return self.trajet[-1]

    def avancer(self, dt_s: float) -> dict:
        """Fait avancer le livreur de dt_s secondes et renvoie sa position (avec un peu de bruit)."""
        self._parcouru += self.vitesse * dt_s
        lat, lon = self._point_a(self._parcouru)
        dlat = self._rng.gauss(0, self.bruit_m) / _METRES_PAR_DEGRE
        dlon = self._rng.gauss(0, self.bruit_m) / (_METRES_PAR_DEGRE * math.cos(math.radians(lat)))
        return {
            "latitude": round(lat + dlat, 6),
            "longitude": round(lon + dlon, 6),
            "precision_m": round(max(3.0, self.bruit_m * 1.5), 1),
        }
