"""Itinéraires routiers (F-42) : distance, durée et tracé entre deux points, selon le véhicule.

Interroge un serveur OSRM auto-hébergé (décision D-05, docs/conception/06-decisions.md).

- Un serveur par profil : voiture (aussi utilisé pour la moto, voir docs/itineraires.md) et vélo.
- Si OSRM est injoignable, on **estime** l'itinéraire à vol d'oiseau, et on le **signale** : le
  résultat porte `estime=True` et un avertissement est écrit dans le journal. Jamais en silence.
- Si OSRM répond qu'il n'y a pas de route, on ne masque pas cette information par une estimation :
  l'erreur est propagée.

Attention : les points sont des (latitude, longitude), alors qu'OSRM attend longitude puis latitude.
La conversion est faite ici, une seule fois.
"""

import logging
import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, NamedTuple

import httpx

from livraison.config import parametres
from livraison.persistance.enums import TypeVehicule

journal = logging.getLogger("livraison")

_RAYON_TERRE_M = 6_371_000.0
_PLATEAU = (5.3236, -4.0197)  # point de contrôle pour sante()


class Point(NamedTuple):
    latitude: float
    longitude: float


@dataclass(frozen=True)
class Itineraire:
    distance_m: float
    duree_s: float
    trace: tuple[Point, ...] | None = None  # présent seulement si demandé
    estime: bool = False  # True : estimation à vol d'oiseau, OSRM n'a pas répondu


class ErreurItineraire(Exception):
    """Erreur de calcul d'itinéraire."""


class AucunItineraire(ErreurItineraire):
    """OSRM ne trouve aucune route entre les deux points."""


class PointHorsReseau(ErreurItineraire):
    """Un point est trop loin de toute route connue."""


class MoteurIndisponible(ErreurItineraire):
    """OSRM est injoignable, trop lent ou en erreur : on peut réessayer ou estimer."""


# -- client HTTP ---------------------------------------------------------------------------------

_client: httpx.AsyncClient | None = None


def utiliser_client(client: httpx.AsyncClient | None) -> None:
    """Remplace le client HTTP (tests). `None` revient au client par défaut."""
    global _client
    _client = client


def _obtenir_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=parametres.osrm_timeout_s)
    return _client


async def fermer_client() -> None:
    """À appeler à l'arrêt du programme qui a utilisé le service."""
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


# -- outils ----------------------------------------------------------------------------------------


def _verifier(point: Point) -> None:
    if not (-90 <= point.latitude <= 90 and -180 <= point.longitude <= 180):
        raise ValueError(f"Coordonnées invalides (latitude, longitude) : {tuple(point)}")


def _base_url(vehicule: TypeVehicule) -> str:
    url = parametres.osrm_url_velo if vehicule == TypeVehicule.VELO else parametres.osrm_url
    return url.rstrip("/")


def _profil_url(vehicule: TypeVehicule) -> str:
    # osrm-routed ignore ce segment (il utilise le profil chargé) ; on le garde cohérent.
    return "cycling" if vehicule == TypeVehicule.VELO else "driving"


def _lon_lat(points: Sequence[Point]) -> str:
    return ";".join(f"{p.longitude:.6f},{p.latitude:.6f}" for p in points)


def distance_vol_d_oiseau_m(a: Point, b: Point) -> float:
    """Distance en mètres entre deux points, en ligne droite (formule de haversine)."""
    lat1, lat2 = math.radians(a.latitude), math.radians(b.latitude)
    h = (
        math.sin((lat2 - lat1) / 2) ** 2
        + math.cos(lat1)
        * math.cos(lat2)
        * math.sin(math.radians(b.longitude - a.longitude) / 2) ** 2
    )
    return 2 * _RAYON_TERRE_M * math.asin(math.sqrt(h))


def _vitesse_estimee_ms(vehicule: TypeVehicule) -> float:
    kmh = {
        TypeVehicule.MOTO: parametres.vitesse_estimee_moto_kmh,
        TypeVehicule.VOITURE: parametres.vitesse_estimee_voiture_kmh,
        TypeVehicule.VELO: parametres.vitesse_estimee_velo_kmh,
    }[vehicule]
    return kmh / 3.6


def estimer(depart: Point, arrivee: Point, vehicule: TypeVehicule) -> Itineraire:
    """Itinéraire de repli : vol d'oiseau x facteur de détour, à la vitesse moyenne du véhicule."""
    distance = distance_vol_d_oiseau_m(depart, arrivee) * parametres.itineraire_facteur_detour
    return Itineraire(distance, distance / _vitesse_estimee_ms(vehicule), estime=True)


async def _appeler(
    vehicule: TypeVehicule, service: str, coordonnees: str, params: dict
) -> dict[str, Any]:
    url = f"{_base_url(vehicule)}/{service}/v1/{_profil_url(vehicule)}/{coordonnees}"
    try:
        reponse = await _obtenir_client().get(url, params=params)
    except httpx.HTTPError as e:  # connexion refusée, délai dépassé...
        raise MoteurIndisponible(f"{type(e).__name__}: {e}") from e
    if reponse.status_code >= 500:
        raise MoteurIndisponible(f"OSRM a répondu HTTP {reponse.status_code}")
    try:
        corps = reponse.json()
    except ValueError as e:
        raise MoteurIndisponible("réponse d'OSRM illisible") from e
    code = corps.get("code")
    if code == "Ok":
        return corps
    message = corps.get("message", "")
    if code == "NoRoute":
        raise AucunItineraire(message or "aucune route entre ces deux points")
    if code == "NoSegment":
        raise PointHorsReseau(message or "un point est trop loin de toute route")
    raise ErreurItineraire(f"OSRM a répondu {code} : {message}")


# -- API du service ------------------------------------------------------------------------------


async def itineraire(
    depart: Point,
    arrivee: Point,
    vehicule: TypeVehicule,
    *,
    avec_trace: bool = False,
    repli: bool = True,
) -> Itineraire:
    """Itinéraire routier entre deux points.

    `repli=True` (défaut) : si OSRM est indisponible, renvoie une estimation (`estime=True`).
    `repli=False` : lève `MoteurIndisponible`.
    """
    _verifier(depart)
    _verifier(arrivee)
    params = {
        "overview": "full" if avec_trace else "false",
        "geometries": "geojson",
        "steps": "false",
        "alternatives": "false",
    }
    try:
        corps = await _appeler(vehicule, "route", _lon_lat([depart, arrivee]), params)
    except MoteurIndisponible as e:
        if not repli:
            raise
        journal.warning("Itinéraire estimé à vol d'oiseau, OSRM indisponible (%s)", e)
        return estimer(depart, arrivee, vehicule)
    routes = corps.get("routes") or []
    if not routes:
        raise AucunItineraire("OSRM n'a renvoyé aucune route")
    route = routes[0]
    trace = None
    if avec_trace:
        trace = tuple(Point(lat, lon) for lon, lat in route["geometry"]["coordinates"])
    return Itineraire(float(route["distance"]), float(route["duration"]), trace)


async def matrice_durees(
    origines: Sequence[Point],
    destinations: Sequence[Point],
    vehicule: TypeVehicule,
    *,
    repli: bool = True,
) -> list[list[float | None]]:
    """Durées en secondes de chaque origine vers chaque destination, en un seul appel.

    Sert à présélectionner des livreurs autour d'un point d'enlèvement. Une case vaut `None` quand
    OSRM ne trouve pas de route. Avec `repli`, une panne d'OSRM donne des durées estimées pour toute
    la matrice (et un avertissement dans le journal).
    """
    if not origines or not destinations:
        raise ValueError("Il faut au moins une origine et une destination.")
    total = len(origines) + len(destinations)
    if total > parametres.osrm_max_table:
        raise ValueError(
            f"Matrice trop grande : {total} points, maximum {parametres.osrm_max_table}."
        )
    for point in (*origines, *destinations):
        _verifier(point)
    n = len(origines)
    params = {
        "sources": ";".join(str(i) for i in range(n)),
        "destinations": ";".join(str(i) for i in range(n, total)),
        "annotations": "duration",
    }
    try:
        corps = await _appeler(vehicule, "table", _lon_lat([*origines, *destinations]), params)
    except MoteurIndisponible as e:
        if not repli:
            raise
        journal.warning("Matrice estimée à vol d'oiseau, OSRM indisponible (%s)", e)
        return [[estimer(o, d, vehicule).duree_s for d in destinations] for o in origines]
    return [[None if x is None else float(x) for x in ligne] for ligne in corps["durations"]]


async def sante() -> dict[str, bool]:
    """Vérifie chaque serveur OSRM (voiture, vélo) : True s'il répond correctement."""
    resultat: dict[str, bool] = {}
    for nom, vehicule in (("voiture", TypeVehicule.VOITURE), ("velo", TypeVehicule.VELO)):
        coordonnees = _lon_lat([Point(*_PLATEAU)])
        url = f"{_base_url(vehicule)}/nearest/v1/{_profil_url(vehicule)}/{coordonnees}"
        try:
            reponse = await _obtenir_client().get(url)
            resultat[nom] = reponse.status_code == 200 and reponse.json().get("code") == "Ok"
        except (httpx.HTTPError, ValueError):
            resultat[nom] = False
    return resultat
