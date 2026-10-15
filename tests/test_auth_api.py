"""Tests de l'authentification, de bout en bout (API + base PostgreSQL annulée après chaque test)."""

from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import update

from livraison.api.dependances import exiger_roles
from livraison.api.main import app
from livraison.config import parametres
from livraison.persistance.enums import EtatCompte, Role
from livraison.persistance.modeles import CodeVerification, Utilisateur
from livraison.persistance.session import obtenir_session
from livraison.services import securite
from livraison.services.auth import ErreurAuthentification
from livraison.services.sms import obtenir_envoi_sms

TEL = "07 08 09 10 11"
TEL_E164 = "+2250708091011"
MDP = "MotDePasse-Solide-1"


class SmsEspion:
    """Remplace l'envoi de SMS : garde les codes pour que le test puisse les relire."""

    def __init__(self):
        self.envoyes: list[tuple[str, str]] = []

    async def envoyer_code(self, telephone: str, code: str) -> None:
        self.envoyes.append((telephone, code))

    @property
    def dernier_code(self) -> str:
        return self.envoyes[-1][1]


@pytest.fixture
def sms():
    return SmsEspion()


@pytest_asyncio.fixture
async def client(session, sms):
    async def _session():
        return session

    app.dependency_overrides[obtenir_session] = _session
    app.dependency_overrides[obtenir_envoi_sms] = lambda: sms
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


async def _connecter(client, sms, role="client", tel=TEL):
    await client.post("/auth/code", json={"telephone": tel})
    return await client.post(
        "/auth/verifier", json={"telephone": tel, "code": sms.dernier_code, "role": role}
    )


def _bearer(jeton: str) -> dict:
    return {"Authorization": f"Bearer {jeton}"}


async def _creer_personnel(session, role=Role.ADMINISTRATEUR, tel="+2250102030405"):
    u = Utilisateur(telephone=tel, role=role, mot_de_passe_hash=securite.hacher_mot_de_passe(MDP))
    session.add(u)
    await session.flush()
    return u


# ------------------------------------------------------------------ inscription (F-01)


async def test_inscription_client_par_code_sms(client, sms):
    r = await client.post("/auth/code", json={"telephone": TEL})
    assert r.status_code == 202
    assert sms.envoyes[0][0] == TEL_E164

    r = await client.post("/auth/verifier", json={"telephone": TEL, "code": sms.dernier_code})
    assert r.status_code == 200
    corps = r.json()
    assert corps["nouveau_compte"] is True and corps["token_type"] == "bearer"

    moi = await client.get("/auth/moi", headers=_bearer(corps["access_token"]))
    assert moi.status_code == 200
    assert moi.json()["telephone"] == TEL_E164 and moi.json()["role"] == "client"


async def test_deuxieme_connexion_ne_recree_pas_le_compte(client, sms):
    premiere = (await _connecter(client, sms)).json()
    seconde = (await _connecter(client, sms)).json()
    assert premiere["nouveau_compte"] is True and seconde["nouveau_compte"] is False


async def test_inscription_livreur(client, sms):
    r = await _connecter(client, sms, role="livreur")
    moi = await client.get("/auth/moi", headers=_bearer(r.json()["access_token"]))
    assert moi.json()["role"] == "livreur"


@pytest.mark.parametrize("role", ["administrateur", "repartiteur"])
async def test_on_ne_s_inscrit_pas_comme_personnel(client, sms, role):
    await client.post("/auth/code", json={"telephone": TEL})
    r = await client.post(
        "/auth/verifier", json={"telephone": TEL, "code": sms.dernier_code, "role": role}
    )
    assert r.status_code == 422


async def test_numero_invalide(client):
    r = await client.post("/auth/code", json={"telephone": "abc"})
    assert r.status_code == 422


# ------------------------------------------------------- limitation des essais (NF-05)


async def test_code_faux_puis_verrouillage(client, sms):
    await client.post("/auth/code", json={"telephone": TEL})
    bon_code = sms.dernier_code
    faux = "000000" if bon_code != "000000" else "111111"
    for _ in range(parametres.code_tentatives_max):
        r = await client.post("/auth/verifier", json={"telephone": TEL, "code": faux})
        assert r.status_code == 401
    # Même avec le bon code, il faut désormais en redemander un.
    r = await client.post("/auth/verifier", json={"telephone": TEL, "code": bon_code})
    assert r.status_code == 429


async def test_code_expire(client, sms, session):
    await client.post("/auth/code", json={"telephone": TEL})
    await session.execute(
        update(CodeVerification).values(expire_le=datetime.now(UTC) - timedelta(minutes=1))
    )
    r = await client.post("/auth/verifier", json={"telephone": TEL, "code": sms.dernier_code})
    assert r.status_code == 401


async def test_code_a_usage_unique(client, sms):
    await client.post("/auth/code", json={"telephone": TEL})
    code = sms.dernier_code
    assert (
        await client.post("/auth/verifier", json={"telephone": TEL, "code": code})
    ).status_code == 200
    assert (
        await client.post("/auth/verifier", json={"telephone": TEL, "code": code})
    ).status_code == 401


async def test_seul_le_dernier_code_est_valable(client, sms):
    await client.post("/auth/code", json={"telephone": TEL})
    ancien = sms.dernier_code
    await client.post("/auth/code", json={"telephone": TEL})
    nouveau = sms.dernier_code
    if ancien != nouveau:  # (1 chance sur un million que les deux codes soient identiques)
        r = await client.post("/auth/verifier", json={"telephone": TEL, "code": ancien})
        assert r.status_code == 401
    r = await client.post("/auth/verifier", json={"telephone": TEL, "code": nouveau})
    assert r.status_code == 200


async def test_limite_de_demandes_de_code(client):
    for _ in range(parametres.codes_max_par_fenetre):
        assert (await client.post("/auth/code", json={"telephone": TEL})).status_code == 202
    assert (await client.post("/auth/code", json={"telephone": TEL})).status_code == 429


async def test_demande_de_code_ne_revele_pas_les_comptes(client, sms, session):
    await _creer_personnel(session)
    inconnu = await client.post("/auth/code", json={"telephone": "0505050505"})
    personnel = await client.post("/auth/code", json={"telephone": "0102030405"})
    assert inconnu.status_code == personnel.status_code == 202
    assert inconnu.json() == personnel.json()
    assert [t for t, _ in sms.envoyes] == ["+2250505050505"]  # aucun SMS pour le personnel


# ------------------------------------------------------ personnel : mot de passe (F-02)


async def test_connexion_du_personnel(client, session):
    await _creer_personnel(session)
    r = await client.post("/auth/connexion", json={"telephone": "0102030405", "mot_de_passe": MDP})
    assert r.status_code == 200
    moi = await client.get("/auth/moi", headers=_bearer(r.json()["access_token"]))
    assert moi.json()["role"] == "administrateur"


async def test_mauvais_mot_de_passe_puis_verrouillage(client, session):
    await _creer_personnel(session)
    faux = {"telephone": "0102030405", "mot_de_passe": "mauvais-mot-de-passe"}
    for _ in range(parametres.connexion_tentatives_max):
        assert (await client.post("/auth/connexion", json=faux)).status_code == 401
    bon = {"telephone": "0102030405", "mot_de_passe": MDP}
    assert (await client.post("/auth/connexion", json=bon)).status_code == 429


async def test_un_client_ne_peut_pas_utiliser_la_connexion_par_mot_de_passe(client, sms):
    await _connecter(client, sms)
    r = await client.post("/auth/connexion", json={"telephone": TEL, "mot_de_passe": MDP})
    assert r.status_code == 401
    assert (
        r.json()
        == (
            await client.post(
                "/auth/connexion", json={"telephone": "0909090909", "mot_de_passe": MDP}
            )
        ).json()
    )  # même message pour un numéro inconnu


# --------------------------------------------------------------------- jetons (NF-05)


async def test_rafraichissement(client, sms):
    jetons = (await _connecter(client, sms)).json()
    r = await client.post("/auth/rafraichir", json={"refresh_token": jetons["refresh_token"]})
    assert r.status_code == 200
    moi = await client.get("/auth/moi", headers=_bearer(r.json()["access_token"]))
    assert moi.status_code == 200


async def test_un_jeton_d_acces_ne_sert_pas_a_rafraichir(client, sms):
    jetons = (await _connecter(client, sms)).json()
    r = await client.post("/auth/rafraichir", json={"refresh_token": jetons["access_token"]})
    assert r.status_code == 401


async def test_un_jeton_de_rafraichissement_ne_donne_pas_acces(client, sms):
    jetons = (await _connecter(client, sms)).json()
    r = await client.get("/auth/moi", headers=_bearer(jetons["refresh_token"]))
    assert r.status_code == 401


async def test_acces_sans_jeton_ou_avec_jeton_falsifie(client, sms):
    r = await client.get("/auth/moi")
    assert r.status_code == 401 and r.headers["www-authenticate"] == "Bearer"
    jetons = (await _connecter(client, sms)).json()
    r = await client.get("/auth/moi", headers=_bearer(jetons["access_token"][:-3] + "abc"))
    assert r.status_code == 401


async def test_compte_suspendu_perd_l_acces_immediatement(client, sms, session):
    jetons = (await _connecter(client, sms)).json()
    await session.execute(update(Utilisateur).values(etat=EtatCompte.SUSPENDU))
    r = await client.get("/auth/moi", headers=_bearer(jetons["access_token"]))
    assert r.status_code == 401
    r = await client.post("/auth/rafraichir", json={"refresh_token": jetons["refresh_token"]})
    assert r.status_code == 401


async def test_completer_son_profil(client, sms):
    jetons = (await _connecter(client, sms)).json()
    r = await client.patch(
        "/auth/moi",
        json={"nom": "Kouassi", "prenom": "Awa"},
        headers=_bearer(jetons["access_token"]),
    )
    assert r.status_code == 200
    assert (r.json()["nom"], r.json()["prenom"]) == ("Kouassi", "Awa")


# ------------------------------------------------------------------ contrôle des rôles


async def test_controle_des_roles(session):
    verifier = exiger_roles(Role.ADMINISTRATEUR, Role.REPARTITEUR)
    client_ = Utilisateur(telephone="+2250700000010", role=Role.CLIENT)
    admin = Utilisateur(telephone="+2250700000011", role=Role.ADMINISTRATEUR)
    with pytest.raises(ErreurAuthentification) as erreur:
        await verifier(utilisateur=client_)
    assert erreur.value.statut == 403
    assert await verifier(utilisateur=admin) is admin
