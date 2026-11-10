"""Course proposée au livreur : lecture de la proposition et réponse dans les 30 secondes (F-34)."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy import select

from aides_commandes import FauxRedis, creer_commande, creer_livreur
from livraison.api import routes_livreur
from livraison.config import parametres
from livraison.persistance.enums import EtatCommande, MotifIncident, TypeEvenement
from livraison.persistance.modeles import Commande, Evenement, Utilisateur
from livraison.persistance.session import obtenir_session
from livraison.services import attribution as attr
from livraison.services import courses, evenements
from livraison.services.erreurs import ErreurMetier
from livraison.services.redis_client import obtenir_redis
from livraison.services.securite import hacher_code

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


# ==================================================================== course en cours (F-22, F-23)


async def course_attribuee(session, livreur_id, code_otp="123456", **options):
    """Crée une commande déjà attribuée au livreur avec son code de confirmation."""
    cid = await course_retenue(session, livreur_id, **options)
    cmd = await session.get(Commande, uuid.UUID(cid))
    cmd.etat = EtatCommande.ATTRIBUEE
    cmd.livreur_id = uuid.UUID(livreur_id)
    cmd.attribuee_le = datetime.now(UTC)
    cmd.code_confirmation_hash = hacher_code(cmd.destinataire_telephone, code_otp)
    await session.flush()
    return cid


async def test_route_course_en_cours_vide(api, session):
    await api.se_connecter(await creer_livreur(session))
    reponse = await api.get("/livreurs/moi/course-en-cours")
    assert reponse.status_code == 200 and reponse.json() is None


async def test_cycle_complet_course_enlever_et_livrer(api, session):
    livreur = await creer_livreur(session)
    cid = await course_attribuee(session, livreur, code_otp="654321")
    await api.se_connecter(livreur)

    # 1. Course active visible
    rep_active = await api.get("/livreurs/moi/course-en-cours")
    assert rep_active.status_code == 200
    details = rep_active.json()
    assert details["commande_id"] == cid
    assert details["etat"] == "attribuee"
    assert details["destinataire_nom"] == "Destinataire"
    assert "enlevement" in details and "livraison" in details

    # 2. Validation de livraison refusée avant enlèvement
    rep_livrer_trop_tot = await api.post(
        f"/livreurs/moi/course-en-cours/{cid}/livrer",
        json={"code_confirmation": "654321"},
    )
    assert rep_livrer_trop_tot.status_code == 409

    # 3. Enlèvement du colis
    rep_enleve = await api.post(f"/livreurs/moi/course-en-cours/{cid}/enlever")
    assert rep_enleve.status_code == 200
    assert rep_enleve.json()["nouvel_etat"] == "enlevee"
    assert any(m["type"] == evenements.COMMANDE_ENLEVEE for m in api.redis.publies)

    # 4. Échec livraison avec code incorrect
    rep_faux_code = await api.post(
        f"/livreurs/moi/course-en-cours/{cid}/livrer",
        json={"code_confirmation": "000000"},
    )
    assert rep_faux_code.status_code == 400
    assert "erroné" in rep_faux_code.json()["detail"]

    # 5. Réussite livraison avec bon code OTP
    rep_livre = await api.post(
        f"/livreurs/moi/course-en-cours/{cid}/livrer",
        json={"code_confirmation": "654321"},
    )
    assert rep_livre.status_code == 200
    assert rep_livre.json()["nouvel_etat"] == "livree"
    assert any(m["type"] == evenements.COMMANDE_LIVREE for m in api.redis.publies)

    # 6. Plus de course active
    rep_apres = await api.get("/livreurs/moi/course-en-cours")
    assert rep_apres.status_code == 200 and rep_apres.json() is None


async def test_livraison_refuse_si_aucun_code_n_est_associe(api, session):
    livreur = await creer_livreur(session)
    cid = await course_attribuee(session, livreur)
    commande = await session.get(Commande, uuid.UUID(cid))
    commande.code_confirmation_hash = None
    await api.se_connecter(livreur)
    await api.post(f"/livreurs/moi/course-en-cours/{cid}/enlever")

    reponse = await api.post(
        f"/livreurs/moi/course-en-cours/{cid}/livrer",
        json={"code_confirmation": "123456"},
    )

    assert reponse.status_code == 409
    assert "Aucun code de livraison" in reponse.json()["detail"]


@pytest.mark.parametrize("code", ["12345", "1234567", "12a456"])
async def test_route_livrer_refuse_code_qui_n_a_pas_six_chiffres(api, session, code):
    livreur = await creer_livreur(session)
    cid = await course_attribuee(session, livreur)
    await api.se_connecter(livreur)

    reponse = await api.post(
        f"/livreurs/moi/course-en-cours/{cid}/livrer",
        json={"code_confirmation": code},
    )

    assert reponse.status_code == 422


@pytest.mark.parametrize("motif", list(MotifIncident))
async def test_signaler_incident_termine_la_course_et_journalise_le_motif(api, session, motif):
    livreur = await creer_livreur(session)
    cid = await course_attribuee(session, livreur)
    await api.se_connecter(livreur)

    reponse = await api.post(
        f"/livreurs/moi/course-en-cours/{cid}/incident",
        json={"motif": motif.value},
    )

    assert reponse.status_code == 200
    assert reponse.json()["motif"] == motif.value
    assert reponse.json()["nouvel_etat"] == EtatCommande.ECHEC_LIVRAISON.value
    commande = await session.get(Commande, uuid.UUID(cid))
    assert commande.etat == EtatCommande.ECHEC_LIVRAISON
    assert commande.terminee_le is not None
    incident = await session.scalar(
        select(Evenement).where(
            Evenement.commande_id == uuid.UUID(cid),
            Evenement.type == TypeEvenement.INCIDENT,
        )
    )
    assert incident.auteur == f"livreur:{livreur}"
    assert incident.detail == {
        "motif": motif.value,
        "etat_avant": EtatCommande.ATTRIBUEE.value,
        "etat_apres": EtatCommande.ECHEC_LIVRAISON.value,
    }
    active = await api.get("/livreurs/moi/course-en-cours")
    assert active.status_code == 200 and active.json() is None


async def test_signaler_incident_refuse_un_motif_inconnu(api, session):
    livreur = await creer_livreur(session)
    cid = await course_attribuee(session, livreur)
    await api.se_connecter(livreur)

    reponse = await api.post(
        f"/livreurs/moi/course-en-cours/{cid}/incident",
        json={"motif": "autre"},
    )

    assert reponse.status_code == 422
    commande = await session.get(Commande, uuid.UUID(cid))
    assert commande.etat == EtatCommande.ATTRIBUEE


async def test_signaler_incident_refuse_une_commande_d_un_autre_livreur(api, session):
    livreur = await creer_livreur(session)
    autre_livreur = await creer_livreur(session)
    cid = await course_attribuee(session, autre_livreur)
    await api.se_connecter(livreur)

    reponse = await api.post(
        f"/livreurs/moi/course-en-cours/{cid}/incident",
        json={"motif": MotifIncident.PANNE.value},
    )

    assert reponse.status_code == 404
    commande = await session.get(Commande, uuid.UUID(cid))
    assert commande.etat == EtatCommande.ATTRIBUEE
