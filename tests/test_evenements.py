"""Événements de l'API vers les agents : publication, et surtout résistance à une panne de Redis."""

import json
import logging

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from livraison.api.dependances import utilisateur_courant
from livraison.api.main import app
from livraison.persistance.enums import Role, TypeVehicule
from livraison.persistance.modeles import Utilisateur
from livraison.services import evenements, livreurs
from livraison.services.redis_client import obtenir_redis

pytestmark = pytest.mark.asyncio


class RedisEnPanne:
    async def publish(self, *args, **kwargs):
        raise RedisConnectionError("Redis est arrêté")

    async def delete(self, *args, **kwargs):
        raise RedisConnectionError("Redis est arrêté")


async def abonner(redis, canal):
    pubsub = redis.pubsub()
    await pubsub.subscribe(canal)
    await pubsub.get_message(timeout=1)  # confirmation de l'abonnement : on est prêt à recevoir
    return pubsub


async def recevoir(pubsub) -> dict | None:
    message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=2)
    return None if message is None else json.loads(message["data"])


async def creer_livreur(session, numero, vehicule=TypeVehicule.MOTO, capacite=25):
    utilisateur = Utilisateur(
        telephone=f"+22597{numero:06d}", role=Role.LIVREUR, nom="K", prenom="A"
    )
    session.add(utilisateur)
    await session.flush()
    await livreurs.definir_profil(session, utilisateur.id, vehicule, capacite, None)
    return utilisateur


async def test_publication_d_un_evenement(redis_test):
    pubsub = await abonner(redis_test, evenements.CANAL_EVENEMENTS)
    assert await evenements.publier(redis_test, "test", livreur_id="abc", valeur=3) is True
    message = await recevoir(pubsub)
    assert message["type"] == "test" and message["livreur_id"] == "abc" and message["valeur"] == 3
    assert "emis_le" in message


async def test_sans_redis_rien_n_est_publie_et_rien_ne_plante():
    assert await evenements.publier(None, "test") is False


async def test_redis_en_panne_est_signale_sans_faire_echouer(caplog):
    with caplog.at_level(logging.WARNING, logger="livraison"):
        assert await evenements.publier(RedisEnPanne(), "livreur_en_ligne", livreur_id="x") is False
    assert "non publié" in caplog.text and "réconciliation" in caplog.text


async def test_passer_en_ligne_annonce_l_evenement(session, redis_test):
    utilisateur = await creer_livreur(session, 1, TypeVehicule.VELO, 12)
    pubsub = await abonner(redis_test, evenements.CANAL_EVENEMENTS)
    livreur = await livreurs.passer_en_ligne(session, utilisateur.id, redis_test)
    assert livreur.en_ligne is True
    message = await recevoir(pubsub)
    assert message["type"] == evenements.LIVREUR_EN_LIGNE
    assert message["livreur_id"] == str(utilisateur.id)
    assert message["type_vehicule"] == "velo" and message["capacite_kg"] == 12


async def test_passer_en_ligne_marche_meme_si_redis_est_en_panne(session, caplog):
    utilisateur = await creer_livreur(session, 2)
    with caplog.at_level(logging.WARNING, logger="livraison"):
        livreur = await livreurs.passer_en_ligne(session, utilisateur.id, RedisEnPanne())
    assert livreur.en_ligne is True  # la base est la vérité : l'annonce perdue sera rattrapée
    assert "non publié" in caplog.text


async def test_passer_en_ligne_sans_redis_reste_possible(session):
    utilisateur = await creer_livreur(session, 3)
    assert (await livreurs.passer_en_ligne(session, utilisateur.id)).en_ligne is True


async def test_un_livreur_refuse_n_annonce_rien(session, redis_test):
    from livraison.persistance.enums import StatutValidation
    from livraison.services.erreurs import ErreurMetier

    utilisateur = await creer_livreur(session, 4)
    (
        await livreurs.livreur_ou_erreur(session, utilisateur.id)
    ).statut_validation = StatutValidation.SUSPENDU
    pubsub = await abonner(redis_test, evenements.CANAL_EVENEMENTS)
    with pytest.raises(ErreurMetier):
        await livreurs.passer_en_ligne(session, utilisateur.id, redis_test)
    assert await recevoir(pubsub) is None


async def test_passer_hors_ligne_annonce_l_evenement(session, redis_test):
    utilisateur = await creer_livreur(session, 5)
    await livreurs.passer_en_ligne(session, utilisateur.id)
    evenement = await abonner(redis_test, evenements.CANAL_EVENEMENTS)
    carte = await abonner(redis_test, "positions")
    await livreurs.passer_hors_ligne(session, redis_test, utilisateur.id)
    message = await recevoir(evenement)
    assert message["type"] == evenements.LIVREUR_HORS_LIGNE and message["livreur_id"] == str(
        utilisateur.id
    )
    assert (await recevoir(carte))[
        "type"
    ] == "hors_ligne"  # la carte du répartiteur est toujours prévenue


async def test_la_route_en_ligne_annonce_l_evenement(client, session, redis_test):
    utilisateur = await creer_livreur(session, 6)
    app.dependency_overrides[utilisateur_courant] = lambda: utilisateur
    app.dependency_overrides[obtenir_redis] = lambda: redis_test
    pubsub = await abonner(redis_test, evenements.CANAL_EVENEMENTS)
    reponse = await client.post("/livreurs/moi/en-ligne")
    assert reponse.status_code == 200 and reponse.json() == {"en_ligne": True}
    message = await recevoir(pubsub)
    assert message["type"] == evenements.LIVREUR_EN_LIGNE and message["livreur_id"] == str(
        utilisateur.id
    )
