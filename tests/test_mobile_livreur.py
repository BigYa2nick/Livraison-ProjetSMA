"""Tests de la logique de l'application mobile du livreur (sans écran, sans GPS)."""

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import pytest_asyncio

from aides import bearer  # noqa: F401  (garde l'import des aides disponible pour les tests)

sys.path.insert(0, str(Path(__file__).parents[1] / "mobile" / "livreur" / "src"))

from client_api import ClientApi, ErreurApi, ErreurReseau
from emetteur import Emetteur, SuiviCadence
from simulateur import TRAJET_PLATEAU_COCODY, Simulateur, distance_m

# ------------------------------------------------------------------------- simulateur


def test_distance_plateau_cocody():
    assert 3_000 < distance_m(TRAJET_PLATEAU_COCODY[0], TRAJET_PLATEAU_COCODY[-1]) < 6_000


def test_le_simulateur_avance_a_la_vitesse_demandee():
    sim = Simulateur(vitesse_m_s=8.0, bruit_m=0.0, graine=1)
    p1, p2 = sim.avancer(5), sim.avancer(5)
    a, b = (p1["latitude"], p1["longitude"]), (p2["latitude"], p2["longitude"])
    assert distance_m(a, b) == pytest.approx(40, rel=0.05)  # 8 m/s pendant 5 s


def test_le_simulateur_fait_demi_tour_au_bout_du_trajet():
    sim = Simulateur(vitesse_m_s=100.0, bruit_m=0.0, graine=1)
    points = [sim.avancer(10) for _ in range(40)]  # 40 km parcourus : plusieurs allers-retours
    lats = [p["latitude"] for p in points]
    assert min(lats) >= 5.3236 - 1e-4 and max(lats) <= 5.3480 + 1e-4
    assert max(lats) - min(lats) > 0.015  # le trajet est bien parcouru en entier


def test_simulateur_reproductible_avec_une_graine():
    a = [Simulateur(graine=7).avancer(5) for _ in range(3)]
    b = [Simulateur(graine=7).avancer(5) for _ in range(3)]
    assert a == b


# ---------------------------------------------------------------------------- émetteur


def _pos(n: int) -> dict:
    return {"n": n, "mesure_le": datetime.now(UTC)}


class ServeurFactice:
    """Remplace le serveur : enregistre ce qui arrive, et peut simuler des pannes."""

    def __init__(self):
        self.recues: list[int] = []
        self.panne_reseau = False
        self.refus: set[int] = set()

    async def __call__(self, position: dict) -> dict:
        if self.panne_reseau:
            raise ErreurReseau("coupure")
        if position["n"] in self.refus:
            raise ErreurApi(409, "Passez en ligne avant d'envoyer votre position.")
        self.recues.append(position["n"])
        return {"retard_s": 0.4}


async def test_envoi_normal():
    serveur = ServeurFactice()
    emetteur = Emetteur(serveur)
    await emetteur.pousser(_pos(1))
    assert serveur.recues == [1] and emetteur.envoyees == 1 and not emetteur.attente
    assert emetteur.dernier_retard_s == 0.4 and emetteur.derniere_latence_ms is not None


async def test_les_positions_sont_gardees_pendant_une_coupure_puis_renvoyees_dans_l_ordre():
    serveur = ServeurFactice()
    emetteur = Emetteur(serveur)
    serveur.panne_reseau = True
    for n in (1, 2, 3):
        await emetteur.pousser(_pos(n))
    assert serveur.recues == [] and len(emetteur.attente) == 3 and emetteur.echecs_reseau == 3
    serveur.panne_reseau = False
    await emetteur.pousser(_pos(4))
    assert serveur.recues == [1, 2, 3, 4] and not emetteur.attente


async def test_une_position_refusee_est_abandonnee_sans_bloquer_les_suivantes():
    serveur = ServeurFactice()
    serveur.refus = {2}
    emetteur = Emetteur(serveur)
    for n in (1, 2, 3):
        await emetteur.pousser(_pos(n))
    assert serveur.recues == [1, 3] and emetteur.abandonnees == 1 and not emetteur.attente


async def test_la_file_est_limitee():
    serveur = ServeurFactice()
    serveur.panne_reseau = True
    emetteur = Emetteur(serveur, taille_max=3)
    for n in range(1, 7):
        await emetteur.pousser(_pos(n))
    assert [p["n"] for p in emetteur.attente] == [4, 5, 6] and emetteur.perdues == 3


# ------------------------------------------------------------------- cadence du GPS


def test_detection_des_trous_de_reception():
    cadence = SuiviCadence(seuil_s=15)
    t0 = datetime(2026, 10, 15, 12, 0, 0, tzinfo=UTC)
    assert cadence.enregistrer(t0) is None
    assert cadence.enregistrer(t0 + timedelta(seconds=5)) is None
    assert cadence.enregistrer(t0 + timedelta(seconds=65)) == pytest.approx(60)
    assert (cadence.recues, cadence.nb_trous, cadence.trou_max_s) == (3, 1, 60)


# ------------------------------------------------------ client contre le vrai serveur


@pytest_asyncio.fixture
async def api(client):
    return ClientApi("http://test", http=client)


async def _ouvrir_session(api, sms, telephone="0722222222") -> ClientApi:
    await api.demander_code(telephone)
    assert await api.verifier_code(telephone, sms.dernier_code) is True
    return api


async def test_parcours_complet_du_client(api, sms, redis_test):
    await _ouvrir_session(api, sms)
    profil = await api.definir_profil("moto", 25)
    assert profil["en_ligne"] is False
    assert await api.passer_en_ligne() == {"en_ligne": True}
    reponse = await api.envoyer_position(5.3236, -4.0197, 8.0, datetime.now(UTC))
    assert 0 <= reponse["retard_s"] < 5
    assert await api.passer_hors_ligne() == {"en_ligne": False}


async def test_le_client_renouvelle_son_jeton_expire(api, sms, redis_test):
    await _ouvrir_session(api, sms)
    await api.definir_profil()
    await api.passer_en_ligne()
    api.acces = "jeton-expire-ou-faux"  # le serveur répond 401, le client doit rafraîchir seul
    reponse = await api.envoyer_position(5.33, -4.01, None, datetime.now(UTC))
    assert "recu_le" in reponse and api.acces != "jeton-expire-ou-faux"


async def test_les_erreurs_du_serveur_sont_remontees(api, sms, redis_test):
    await _ouvrir_session(api, sms)
    with pytest.raises(ErreurApi) as erreur:
        await api.passer_en_ligne()  # pas de profil
    assert erreur.value.statut == 409


async def test_serveur_injoignable():
    import httpx

    api = ClientApi("http://127.0.0.1:9", delai_s=1)  # aucun serveur sur ce port
    with pytest.raises(ErreurReseau):
        await api.demander_code("0722222222")
    await api.fermer()
    assert isinstance(httpx.HTTPError("x"), Exception)
