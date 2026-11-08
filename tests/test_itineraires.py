"""Service d'itinéraires (F-42). Aucun test n'a besoin d'un vrai OSRM, sauf le dernier (ignoré sinon)."""

import logging

import httpx
import pytest
import pytest_asyncio

from livraison.config import parametres
from livraison.persistance.enums import TypeVehicule
from livraison.services import itineraires
from livraison.services.itineraires import (
    AucunItineraire,
    ErreurItineraire,
    MoteurIndisponible,
    Point,
    PointHorsReseau,
)

pytestmark = pytest.mark.asyncio

PLATEAU = Point(5.3236, -4.0197)
COCODY = Point(5.3480, -3.9870)


class FauxOsrm:
    """Faux serveur OSRM : mémorise les requêtes reçues et répond ce qu'on lui demande."""

    def __init__(self):
        self.requetes: list[httpx.Request] = []
        self.reponse: httpx.Response | Exception = httpx.Response(200, json=self.route_ok())

    @staticmethod
    def route_ok(distance=5400.5, duree=780.25):
        return {
            "code": "Ok",
            "routes": [
                {
                    "distance": distance,
                    "duration": duree,
                    "geometry": {"coordinates": [[-4.0197, 5.3236], [-4.0, 5.33], [-3.987, 5.348]]},
                }
            ],
        }

    def __call__(self, requete: httpx.Request) -> httpx.Response:
        self.requetes.append(requete)
        if isinstance(self.reponse, Exception):
            raise self.reponse
        return self.reponse


@pytest_asyncio.fixture
async def osrm():
    faux = FauxOsrm()
    itineraires.utiliser_client(httpx.AsyncClient(transport=httpx.MockTransport(faux)))
    yield faux
    await itineraires.fermer_client()


# ============================================================ calcul d'un itinéraire


async def test_itineraire_lit_distance_et_duree(osrm):
    resultat = await itineraires.itineraire(PLATEAU, COCODY, TypeVehicule.MOTO)
    assert resultat.distance_m == 5400.5
    assert resultat.duree_s == 780.25
    assert resultat.estime is False
    assert resultat.trace is None  # pas demandé


async def test_osrm_recoit_longitude_puis_latitude(osrm):
    await itineraires.itineraire(PLATEAU, COCODY, TypeVehicule.MOTO)
    chemin = osrm.requetes[0].url.path
    assert chemin == "/route/v1/driving/-4.019700,5.323600;-3.987000,5.348000"


async def test_trace_renvoyee_en_latitude_longitude(osrm):
    resultat = await itineraires.itineraire(PLATEAU, COCODY, TypeVehicule.VOITURE, avec_trace=True)
    assert resultat.trace[0] == Point(5.3236, -4.0197)
    assert resultat.trace[-1] == Point(5.348, -3.987)
    assert osrm.requetes[0].url.params["overview"] == "full"
    assert osrm.requetes[0].url.params["geometries"] == "geojson"


async def test_moto_et_voiture_partagent_le_serveur_voiture_le_velo_a_le_sien(osrm):
    for vehicule in (TypeVehicule.MOTO, TypeVehicule.VOITURE, TypeVehicule.VELO):
        await itineraires.itineraire(PLATEAU, COCODY, vehicule)
    ports = [r.url.port for r in osrm.requetes]
    assert ports == [5000, 5000, 5001]
    assert osrm.requetes[2].url.path.startswith("/route/v1/cycling/")


# ============================================================ réponses d'erreur d'OSRM


@pytest.mark.parametrize(
    ("code", "erreur"),
    [
        ("NoRoute", AucunItineraire),
        ("NoSegment", PointHorsReseau),
        ("InvalidQuery", ErreurItineraire),
    ],
)
async def test_les_erreurs_metier_ne_sont_jamais_estimees(osrm, code, erreur):
    osrm.reponse = httpx.Response(400, json={"code": code, "message": "détail"})
    with pytest.raises(erreur):
        await itineraires.itineraire(PLATEAU, COCODY, TypeVehicule.MOTO)  # repli=True par défaut


async def test_reponse_sans_route(osrm):
    osrm.reponse = httpx.Response(200, json={"code": "Ok", "routes": []})
    with pytest.raises(AucunItineraire):
        await itineraires.itineraire(PLATEAU, COCODY, TypeVehicule.MOTO)


# ============================================================ OSRM en panne : estimation signalée


@pytest.mark.parametrize(
    "panne",
    [
        httpx.ConnectError("refusé"),
        httpx.ReadTimeout("trop lent"),
        httpx.Response(503, text="en maintenance"),
        httpx.Response(200, text="<html>pas du JSON</html>"),
    ],
)
async def test_panne_d_osrm_donne_une_estimation_signalee(osrm, caplog, panne):
    osrm.reponse = panne
    with caplog.at_level(logging.WARNING, logger="livraison"):
        resultat = await itineraires.itineraire(PLATEAU, COCODY, TypeVehicule.MOTO)
    assert resultat.estime is True
    assert resultat.distance_m > 0 and resultat.duree_s > 0
    assert "estimé à vol d'oiseau" in caplog.text  # jamais en silence


async def test_sans_repli_la_panne_est_une_erreur(osrm):
    osrm.reponse = httpx.ConnectError("refusé")
    with pytest.raises(MoteurIndisponible):
        await itineraires.itineraire(PLATEAU, COCODY, TypeVehicule.MOTO, repli=False)


async def test_estimation_coherente_avec_les_parametres(osrm):
    osrm.reponse = httpx.ConnectError("refusé")
    droit = itineraires.distance_vol_d_oiseau_m(PLATEAU, COCODY)
    moto = await itineraires.itineraire(PLATEAU, COCODY, TypeVehicule.MOTO)
    velo = await itineraires.itineraire(PLATEAU, COCODY, TypeVehicule.VELO)
    assert moto.distance_m == pytest.approx(droit * parametres.itineraire_facteur_detour)
    assert moto.duree_s == pytest.approx(
        moto.distance_m / (parametres.vitesse_estimee_moto_kmh / 3.6)
    )
    assert velo.duree_s > moto.duree_s  # le vélo est plus lent


# ============================================================ outils


async def test_distance_vol_d_oiseau():
    # 0,01 degré de latitude = environ 1 112 m, quelle que soit la longitude
    d = itineraires.distance_vol_d_oiseau_m(Point(5.30, -4.0), Point(5.31, -4.0))
    assert d == pytest.approx(1111.95, abs=1.0)
    assert itineraires.distance_vol_d_oiseau_m(PLATEAU, PLATEAU) == 0


@pytest.mark.parametrize("point", [Point(91, 0), Point(-91, 0), Point(0, 181), Point(0, -181)])
async def test_coordonnees_invalides_refusees(osrm, point):
    with pytest.raises(ValueError):
        await itineraires.itineraire(point, COCODY, TypeVehicule.MOTO)
    assert osrm.requetes == []  # rien n'est envoyé à OSRM


# ============================================================ matrice de durées


async def test_matrice_durees(osrm):
    osrm.reponse = httpx.Response(
        200, json={"code": "Ok", "durations": [[0.0, 120.5], [300, None]]}
    )
    matrice = await itineraires.matrice_durees(
        [PLATEAU, COCODY], [COCODY, PLATEAU], TypeVehicule.MOTO
    )
    assert matrice == [[0.0, 120.5], [300.0, None]]  # None : pas de route
    requete = osrm.requetes[0]
    assert requete.url.path.startswith("/table/v1/driving/")
    assert requete.url.params["sources"] == "0;1"
    assert requete.url.params["destinations"] == "2;3"


async def test_matrice_trop_grande_ou_vide(osrm):
    with pytest.raises(ValueError):
        await itineraires.matrice_durees([PLATEAU] * 60, [COCODY] * 60, TypeVehicule.MOTO)
    with pytest.raises(ValueError):
        await itineraires.matrice_durees([], [COCODY], TypeVehicule.MOTO)


async def test_matrice_estimee_quand_osrm_est_en_panne(osrm, caplog):
    osrm.reponse = httpx.ConnectError("refusé")
    with caplog.at_level(logging.WARNING, logger="livraison"):
        matrice = await itineraires.matrice_durees([PLATEAU], [COCODY, PLATEAU], TypeVehicule.MOTO)
    assert len(matrice) == 1 and len(matrice[0]) == 2
    assert matrice[0][0] > 0 and matrice[0][1] == 0
    assert "estimée à vol d'oiseau" in caplog.text


# ============================================================ santé


async def test_sante(osrm):
    osrm.reponse = httpx.Response(200, json={"code": "Ok", "waypoints": []})
    assert await itineraires.sante() == {"voiture": True, "velo": True}
    osrm.reponse = httpx.ConnectError("refusé")
    assert await itineraires.sante() == {"voiture": False, "velo": False}


# ============================================================ vrai OSRM (ignoré s'il ne tourne pas)


async def test_vrai_osrm_trajet_plateau_cocody():
    itineraires.utiliser_client(None)
    try:
        etat = await itineraires.sante()
        if not all(etat.values()):
            pytest.skip(f"OSRM ne répond pas ({etat}) : voir docs/itineraires.md")
        resultat = await itineraires.itineraire(PLATEAU, COCODY, TypeVehicule.VOITURE, repli=False)
    finally:
        await itineraires.fermer_client()
    droit = itineraires.distance_vol_d_oiseau_m(PLATEAU, COCODY)
    assert resultat.estime is False
    assert (
        droit <= resultat.distance_m <= droit * 3
    )  # une route n'est jamais plus courte que la droite
    assert 60 < resultat.duree_s < 3 * 3600
