"""Tests du suivi GPS : profil livreur, passage en ligne, positions, diffusion Redis."""

import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from aides import bearer, connecter, creer_personnel
from livraison.config import parametres
from livraison.persistance.enums import Role
from livraison.services.positions import CANAL_POSITIONS, cle_position

PROFIL = {"type_vehicule": "moto", "capacite_kg": 25, "immatriculation": "1234 AB 01"}
PLATEAU = {"latitude": 5.3236, "longitude": -4.0197, "precision_m": 8.5}


def _maintenant() -> str:
    return datetime.now(UTC).isoformat()


async def _livreur(client, sms, *, profil=True, en_ligne=True, tel="0711111111") -> dict:
    """Crée un livreur connecté ; renvoie ses en-têtes d'authentification."""
    jetons = (await connecter(client, sms, role="livreur", tel=tel)).json()
    entetes = bearer(jetons["access_token"])
    if profil:
        assert (
            await client.put("/livreurs/moi/profil", json=PROFIL, headers=entetes)
        ).status_code == 200
    if en_ligne:
        assert (await client.post("/livreurs/moi/en-ligne", headers=entetes)).status_code == 200
    return entetes


async def _identifiant(client, entetes) -> str:
    return (await client.get("/auth/moi", headers=entetes)).json()["id"]


async def _envoyer(client, entetes, mesure_le=None, **position):
    corps = {**PLATEAU, **position, "mesure_le": mesure_le or _maintenant()}
    return await client.post("/livreurs/moi/position", json=corps, headers=entetes)


async def _personnel(client, session) -> dict:
    await creer_personnel(session, Role.REPARTITEUR, "+2250102030405")
    reponse = await client.post(
        "/auth/connexion", json={"telephone": "0102030405", "mot_de_passe": "MotDePasse-Solide-1"}
    )
    return bearer(reponse.json()["access_token"])


# ----------------------------------------------------------------- profil et statut


async def test_profil_livreur(client, sms, redis_test):
    entetes = await _livreur(client, sms, profil=False, en_ligne=False)
    r = await client.put("/livreurs/moi/profil", json=PROFIL, headers=entetes)
    assert r.status_code == 200
    assert r.json() == {**PROFIL, "statut_validation": "en_attente", "en_ligne": False}
    assert (await client.get("/livreurs/moi/profil", headers=entetes)).json()["capacite_kg"] == 25


async def test_un_client_n_a_pas_acces_aux_routes_livreur(client, sms, redis_test):
    jetons = (await connecter(client, sms, role="client")).json()
    r = await client.put(
        "/livreurs/moi/profil", json=PROFIL, headers=bearer(jetons["access_token"])
    )
    assert r.status_code == 403


async def test_passer_en_ligne_sans_profil(client, sms, redis_test):
    entetes = await _livreur(client, sms, profil=False, en_ligne=False)
    r = await client.post("/livreurs/moi/en-ligne", headers=entetes)
    assert r.status_code == 409 and "profil" in r.json()["detail"]


async def test_validation_requise_bloque_le_passage_en_ligne(client, sms, redis_test, monkeypatch):
    monkeypatch.setattr(parametres, "livreur_validation_requise", True)
    entetes = await _livreur(client, sms, en_ligne=False)
    r = await client.post("/livreurs/moi/en-ligne", headers=entetes)
    assert r.status_code == 403


# --------------------------------------------------------------------- positions


async def test_position_refusee_hors_ligne(client, sms, redis_test):
    entetes = await _livreur(client, sms, en_ligne=False)
    r = await _envoyer(client, entetes)
    assert r.status_code == 409


async def test_position_enregistree_et_visible_pour_le_repartiteur(
    client, sms, session, redis_test
):
    entetes = await _livreur(client, sms)
    r = await _envoyer(client, entetes)
    assert r.status_code == 200 and 0 <= r.json()["retard_s"] < 5

    # Historique en base : l'ordre longitude, latitude est respecté.
    ligne = (
        await session.execute(
            text(
                "SELECT ST_X(localisation::geometry), ST_Y(localisation::geometry), precision_m "
                "FROM position WHERE livreur_id = :i"
            ),
            {"i": await _identifiant(client, entetes)},
        )
    ).one()
    assert ligne == pytest.approx((-4.0197, 5.3236, 8.5))

    # Dernière position dans Redis, avec une durée de vie limitée.
    repartiteur = await _personnel(client, session)
    positions = await client.get("/suivi/positions", headers=repartiteur)
    assert positions.status_code == 200
    [p] = positions.json()
    assert (p["latitude"], p["longitude"], p["perimee"]) == (5.3236, -4.0197, False)
    assert p["age_s"] < 5


async def test_la_position_redis_expire(client, sms, redis_test):
    entetes = await _livreur(client, sms)
    await _envoyer(client, entetes)
    [cle] = [c async for c in redis_test.scan_iter(match="position:*")]
    ttl = await redis_test.ttl(cle)
    assert 0 < ttl <= parametres.position_ttl_secondes
    assert cle == cle_position(cle.split(":", 1)[1])


@pytest.mark.parametrize(
    "corps",
    [{"latitude": 95}, {"latitude": -91}, {"longitude": 181}, {"precision_m": -1}],
)
async def test_coordonnees_invalides(client, sms, redis_test, corps):
    entetes = await _livreur(client, sms)
    assert (await _envoyer(client, entetes, **corps)).status_code == 422


async def test_horodatage_sans_fuseau_ou_dans_le_futur(client, sms, redis_test):
    entetes = await _livreur(client, sms)
    sans_fuseau = datetime.now().replace(tzinfo=None).isoformat()
    assert (await _envoyer(client, entetes, mesure_le=sans_fuseau)).status_code == 422
    futur = (datetime.now(UTC) + timedelta(minutes=10)).isoformat()
    assert (await _envoyer(client, entetes, mesure_le=futur)).status_code == 422


async def test_une_position_en_retard_ne_remplace_pas_une_plus_recente(
    client, sms, session, redis_test
):
    entetes = await _livreur(client, sms)
    repartiteur = await _personnel(client, session)
    maintenant = datetime.now(UTC)
    await _envoyer(client, entetes, mesure_le=maintenant.isoformat(), latitude=5.40)
    # Position mise en attente par le téléphone pendant une perte de réseau, arrivée après.
    r = await _envoyer(
        client, entetes, mesure_le=(maintenant - timedelta(minutes=5)).isoformat(), latitude=5.30
    )
    assert r.status_code == 200 and r.json()["retard_s"] == pytest.approx(300, abs=5)
    [p] = (await client.get("/suivi/positions", headers=repartiteur)).json()
    assert p["latitude"] == 5.40
    nb = (
        await session.execute(
            text("SELECT count(*) FROM position WHERE livreur_id = :i"),
            {"i": await _identifiant(client, entetes)},
        )
    ).scalar_one()
    assert nb == 2  # les deux positions sont gardées dans l'historique


async def test_position_perimee_signalee(client, sms, session, redis_test, monkeypatch):
    monkeypatch.setattr(parametres, "position_age_max_secondes", 30)
    entetes = await _livreur(client, sms)
    repartiteur = await _personnel(client, session)
    ancienne = (datetime.now(UTC) - timedelta(seconds=45)).isoformat()
    await _envoyer(client, entetes, mesure_le=ancienne)
    [p] = (await client.get("/suivi/positions", headers=repartiteur)).json()
    assert p["perimee"] is True and p["age_s"] >= 45


async def test_passage_hors_ligne_retire_la_position(client, sms, session, redis_test):
    entetes = await _livreur(client, sms)
    repartiteur = await _personnel(client, session)
    await _envoyer(client, entetes)
    assert len((await client.get("/suivi/positions", headers=repartiteur)).json()) == 1

    assert (await client.post("/livreurs/moi/hors-ligne", headers=entetes)).json() == {
        "en_ligne": False
    }
    assert (await client.get("/suivi/positions", headers=repartiteur)).json() == []
    assert (await _envoyer(client, entetes)).status_code == 409  # plus de collecte (NF-07)


# -------------------------------------------------------- diffusion et contrôle d'accès


async def test_la_position_est_diffusee_sur_redis(client, sms, redis_test):
    entetes = await _livreur(client, sms)
    pubsub = redis_test.pubsub()
    await pubsub.subscribe(CANAL_POSITIONS)
    await pubsub.get_message(timeout=1)  # confirmation d'abonnement
    await _envoyer(client, entetes)
    message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=2)
    await pubsub.aclose()
    assert message is not None
    contenu = json.loads(message["data"])
    assert contenu["type"] == "position" and contenu["latitude"] == 5.3236


async def test_le_hors_ligne_est_diffuse(client, sms, redis_test):
    entetes = await _livreur(client, sms)
    pubsub = redis_test.pubsub()
    await pubsub.subscribe(CANAL_POSITIONS)
    await pubsub.get_message(timeout=1)
    await client.post("/livreurs/moi/hors-ligne", headers=entetes)
    message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=2)
    await pubsub.aclose()
    assert json.loads(message["data"])["type"] == "hors_ligne"


async def test_le_suivi_est_reserve_au_personnel(client, sms, redis_test):
    assert (await client.get("/suivi/positions")).status_code == 401
    entetes = await _livreur(client, sms)
    assert (await client.get("/suivi/positions", headers=entetes)).status_code == 403
