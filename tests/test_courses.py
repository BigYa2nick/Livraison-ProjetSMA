"""Course proposée au livreur : lecture de la proposition et réponse dans les 30 secondes (F-34)."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient
from redis.exceptions import ConnectionError as RedisConnectionError

from aides_commandes import FauxRedis, creer_commande, creer_livreur
from livraison.api import routes_livreur
from livraison.config import parametres
from livraison.persistance.enums import EtatCommande
from livraison.persistance.modeles import Commande, Utilisateur
from livraison.persistance.session import obtenir_session
from livraison.services import attribution as attr
from livraison.services import courses, evenements
from livraison.services.erreurs import ErreurMetier
from livraison.services.redis_client import obtenir_redis

pytestmark = pytest.mark.asyncio


class RedisEnPanne:
    async def publish(self, canal, message):
        raise RedisConnectionError("Redis est tombé")


async def course_retenue(session, livreur_id, **options):
    """Une course que le Dispatcher vient de retenir pour ce livreur (offre « retenue »)."""
    cid = await creer_commande(session, **options)
    await attr.preparer_tour(session, cid)
    await attr.enregistrer_reponses(session, cid, 1, propositions={livreur_id: (100.0, 90)}, refus={})
    assert await attr.retenir_offre(session, cid, 1, livreur_id)
    return cid


# ================================================================================ service


async def test_pas_de_proposition(session):
    livreur = await creer_livreur(session)
    assert await courses.proposition_en_cours(session, uuid.UUID(livreur)) is None


async def test_la_proposition_du_livreur_retenu(session):
    livreur, autre = await creer_livreur(session), await creer_livreur(session)
    cid = await course_retenue(session, livreur, poids_kg=3.5, description_colis="Un livre")
    p = await courses.proposition_en_cours(session, uuid.UUID(livreur))
    assert str(p.commande_id) == cid and p.tour == 1
    assert (p.adresse_enlevement, p.adresse_livraison) == ("Plateau", "Cocody")
    assert (p.taille_colis, p.poids_kg, p.priorite, p.description_colis) == ("petit", 3.5, "normale", "Un livre")
    assert 0 < p.secondes_restantes <= parametres.attribution_confirmation_s
    assert not hasattr(p, "destinataire_nom") and not hasattr(p, "destinataire_telephone")
    assert await courses.proposition_en_cours(session, uuid.UUID(autre)) is None


async def test_la_proposition_disparait_apres_le_delai(session):
    livreur = await creer_livreur(session)
    await course_retenue(session, livreur)
    plus_tard = datetime.now(UTC) + timedelta(seconds=parametres.attribution_confirmation_s + 1)
    assert await courses.proposition_en_cours(session, uuid.UUID(livreur), maintenant=plus_tard) is None


async def test_la_proposition_disparait_quand_la_commande_n_est_plus_en_recherche(session):
    livreur = await creer_livreur(session)
    cid = await course_retenue(session, livreur)
    (await session.get(Commande, uuid.UUID(cid))).etat = EtatCommande.ANNULEE
    await session.flush()
    assert await courses.proposition_en_cours(session, uuid.UUID(livreur)) is None


async def test_confirmer_annonce_l_evenement(session):
    livreur = await creer_livreur(session)
    cid = await course_retenue(session, livreur)
    redis = FauxRedis()
    await courses.repondre_a_la_proposition(session, redis, uuid.UUID(livreur), uuid.UUID(cid), accepte=True)
    (annonce,) = redis.publies
    assert annonce["type"] == evenements.COURSE_CONFIRMEE
    assert (annonce["livreur_id"], annonce["commande_id"]) == (livreur, cid)


async def test_decliner_annonce_l_evenement(session):
    livreur = await creer_livreur(session)
    cid = await course_retenue(session, livreur)
    redis = FauxRedis()
    await courses.repondre_a_la_proposition(session, redis, uuid.UUID(livreur), uuid.UUID(cid), accepte=False)
    assert redis.publies[0]["type"] == evenements.COURSE_DECLINEE


async def test_reponse_sans_proposition_pour_cette_commande(session):
    livreur, autre = await creer_livreur(session), await creer_livreur(session)
    cid = await course_retenue(session, livreur)
    redis = FauxRedis()
    for qui, quoi in ((autre, cid), (livreur, str(uuid.uuid4()))):  # pas son offre, ou commande inconnue
        with pytest.raises(ErreurMetier) as e:
            await courses.repondre_a_la_proposition(session, redis, uuid.UUID(qui), uuid.UUID(quoi), accepte=True)
        assert e.value.statut == 404
    assert redis.publies == []


async def test_reponse_apres_le_delai(session):
    livreur = await creer_livreur(session)
    cid = await course_retenue(session, livreur)
    redis = FauxRedis()
    plus_tard = datetime.now(UTC) + timedelta(seconds=parametres.attribution_confirmation_s + 1)
    with pytest.raises(ErreurMetier) as e:
        await courses.repondre_a_la_proposition(
            session, redis, uuid.UUID(livreur), uuid.UUID(cid), accepte=True, maintenant=plus_tard
        )
    assert e.value.statut == 410 and redis.publies == []


async def test_redis_en_panne_la_reponse_n_est_pas_perdue_en_silence(session):
    livreur = await creer_livreur(session)
    cid = await course_retenue(session, livreur)
    with pytest.raises(ErreurMetier) as e:
        await courses.repondre_a_la_proposition(
            session, RedisEnPanne(), uuid.UUID(livreur), uuid.UUID(cid), accepte=True
        )
    assert e.value.statut == 503  # le livreur est prévenu et peut réessayer


# ================================================================================ routes HTTP


@pytest.fixture
async def api(session):
    """L'application réduite aux routes du livreur ; l'identité est imposée par le test."""
    app = FastAPI()
    app.include_router(routes_livreur.routeur)

    @app.exception_handler(ErreurMetier)  # comme l'application complète : erreur métier -> HTTP
    async def _erreur_metier(requete, erreur):
        return JSONResponse({"detail": erreur.detail}, status_code=erreur.statut)

    redis = FauxRedis()
    etat = {"livreur": None}

    async def _session():
        return session

    app.dependency_overrides[obtenir_session] = _session
    app.dependency_overrides[obtenir_redis] = lambda: redis
    app.dependency_overrides[routes_livreur.livreur_connecte] = lambda: etat["livreur"]

    async def se_connecter(livreur_id):
        etat["livreur"] = await session.get(Utilisateur, uuid.UUID(livreur_id))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        client.redis, client.se_connecter = redis, se_connecter
        yield client
    app.dependency_overrides.clear()


async def test_route_proposition_vide(api, session):
    await api.se_connecter(await creer_livreur(session))
    reponse = await api.get("/livreurs/moi/proposition")
    assert reponse.status_code == 200 and reponse.json() is None


async def test_route_proposition_sans_donnee_personnelle(api, session):
    livreur = await creer_livreur(session)
    cid = await course_retenue(session, livreur)
    await api.se_connecter(livreur)
    reponse = await api.get("/livreurs/moi/proposition")
    corps = reponse.json()
    assert reponse.status_code == 200 and corps["commande_id"] == cid
    assert corps["adresse_enlevement"] == "Plateau" and corps["secondes_restantes"] > 0
    assert "destinataire" not in reponse.text and "+2250700000000" not in reponse.text


async def test_route_confirmer(api, session):
    livreur = await creer_livreur(session)
    cid = await course_retenue(session, livreur)
    await api.se_connecter(livreur)
    reponse = await api.post(f"/livreurs/moi/proposition/{cid}/confirmer")
    assert reponse.status_code == 202 and reponse.json() == {"commande_id": cid, "reponse": "confirmee"}
    assert api.redis.publies[0]["type"] == evenements.COURSE_CONFIRMEE


async def test_route_decliner(api, session):
    livreur = await creer_livreur(session)
    cid = await course_retenue(session, livreur)
    await api.se_connecter(livreur)
    reponse = await api.post(f"/livreurs/moi/proposition/{cid}/decliner")
    assert reponse.status_code == 202 and reponse.json()["reponse"] == "declinee"
    assert api.redis.publies[0]["type"] == evenements.COURSE_DECLINEE


async def test_routes_erreurs(api, session):
    livreur = await creer_livreur(session)
    await api.se_connecter(livreur)
    inconnue = await api.post(f"/livreurs/moi/proposition/{uuid.uuid4()}/confirmer")
    assert inconnue.status_code == 404 and "proposée" in inconnue.json()["detail"]
    assert (await api.post("/livreurs/moi/proposition/pas-un-uuid/confirmer")).status_code == 422
    assert api.redis.publies == []
