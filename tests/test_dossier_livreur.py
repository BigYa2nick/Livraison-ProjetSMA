"""Dossier livreur (F-03) : dépôt des pièces, examen et validation par l'administrateur."""

import itertools

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from livraison.api.dependances import utilisateur_courant
from livraison.api.main import app
from livraison.config import parametres
from livraison.persistance.enums import (
    Role,
    StatutDocument,
    StatutValidation,
    TypeDocument,
    TypeEvenement,
    TypeVehicule,
)
from livraison.persistance.modeles import DocumentLivreur, Evenement, Utilisateur
from livraison.services import dossier, livreurs
from livraison.services.erreurs import ErreurMetier
from livraison.services.redis_client import obtenir_redis

pytestmark = pytest.mark.asyncio

JPEG = b"\xff\xd8\xff\xe0" + b"jpeg" * 50
PNG = b"\x89PNG\r\n\x1a\n" + b"png" * 50
PDF = b"%PDF-1.4\n" + b"pdf" * 50

_numeros = itertools.count(1)


class FauxRedis:
    """Remplace Redis : on vérifie seulement que la position du livreur est retirée."""

    def __init__(self):
        self.supprimes: list[str] = []
        self.publies: list[tuple[str, str]] = []

    async def delete(self, *cles):
        self.supprimes.extend(cles)

    async def publish(self, canal, message):
        self.publies.append((canal, message))


@pytest.fixture
def stockage(tmp_path, monkeypatch):
    """Les pièces des tests vont dans un dossier temporaire, pas dans stockage/."""
    racine = tmp_path / "pieces"
    monkeypatch.setattr(parametres, "stockage_pieces_dir", str(racine))
    return racine


@pytest.fixture
def faux_redis():
    return FauxRedis()


@pytest.fixture
def agir_comme(client, faux_redis):
    """Fait agir l'API comme l'utilisateur donné (l'authentification est testée ailleurs)."""
    app.dependency_overrides[obtenir_redis] = lambda: faux_redis

    def _agir(utilisateur: Utilisateur):
        app.dependency_overrides[utilisateur_courant] = lambda: utilisateur

    return _agir


async def creer_utilisateur(session, role, nom="Koffi", prenom="Awa") -> Utilisateur:
    # Préfixe 99 : ne rencontre jamais les numéros de développement (07..., 01...).
    utilisateur = Utilisateur(
        telephone=f"+22599{next(_numeros):06d}", role=role, nom=nom, prenom=prenom
    )
    session.add(utilisateur)
    await session.flush()
    return utilisateur


async def creer_livreur(session, type_vehicule=TypeVehicule.MOTO, **identite):
    utilisateur = await creer_utilisateur(session, Role.LIVREUR, **identite)
    livreur = await livreurs.definir_profil(session, utilisateur.id, type_vehicule, 25, None)
    return utilisateur, livreur


async def creer_admin(session) -> Utilisateur:
    return await creer_utilisateur(session, Role.ADMINISTRATEUR, nom="Admin", prenom="Sys")


async def deposer_tout(session, utilisateur, types=None):
    """Dépose toutes les pièces exigées pour le véhicule du livreur."""
    livreur = await livreurs.livreur_ou_erreur(session, utilisateur.id)
    documents = []
    for type_piece in types or dossier.pieces_requises(livreur.type_vehicule):
        documents.append(await dossier.deposer_piece(session, utilisateur.id, type_piece, JPEG))
    return documents


async def accepter_tout(session, admin, documents):
    for document in documents:
        await dossier.examiner_piece(session, admin.id, document.id, True, None)


async def erreur_metier(coroutine) -> ErreurMetier:
    with pytest.raises(ErreurMetier) as info:
        await coroutine
    return info.value


# ============================================================ règles du dossier


async def test_pieces_requises_selon_le_vehicule():
    assert len(dossier.pieces_requises(TypeVehicule.MOTO)) == 4
    assert len(dossier.pieces_requises(TypeVehicule.VOITURE)) == 4
    assert set(dossier.pieces_requises(TypeVehicule.VELO)) == {
        TypeDocument.PIECE_IDENTITE,
        TypeDocument.PHOTO,
    }


async def test_format_detecte_sur_le_contenu():
    assert dossier.detecter_format(JPEG) == ("image/jpeg", ".jpg")
    assert dossier.detecter_format(PNG) == ("image/png", ".png")
    assert dossier.detecter_format(PDF) == ("application/pdf", ".pdf")
    assert dossier.detecter_format(b"<script>alert(1)</script>") is None
    assert dossier.detecter_format(b"MZ\x90\x00 programme.exe") is None


# ============================================================ dépôt des pièces


async def test_deposer_une_piece_ecrit_le_fichier(session, stockage):
    utilisateur, _ = await creer_livreur(session)
    document = await dossier.deposer_piece(
        session, utilisateur.id, TypeDocument.PIECE_IDENTITE, PNG
    )
    assert document.statut == StatutDocument.EN_ATTENTE
    assert document.type_mime == "image/png"
    assert document.taille_octets == len(PNG)
    fichier = stockage / document.chemin_fichier
    assert fichier.read_bytes() == PNG
    assert document.chemin_fichier.startswith(f"{utilisateur.id}/")


@pytest.mark.parametrize(
    ("contenu", "statut"),
    [(b"", 422), (b"juste du texte", 415), (b"MZ\x90\x00" + b"x" * 50, 415)],
)
async def test_fichier_invalide_refuse(session, stockage, contenu, statut):
    utilisateur, _ = await creer_livreur(session)
    erreur = await erreur_metier(
        dossier.deposer_piece(session, utilisateur.id, TypeDocument.PERMIS, contenu)
    )
    assert erreur.statut == statut
    assert not stockage.exists() or not any(stockage.rglob("*.*"))  # rien n'a été écrit


async def test_fichier_trop_gros_refuse(session, stockage, monkeypatch):
    monkeypatch.setattr(parametres, "piece_taille_max_octets", 100)
    utilisateur, _ = await creer_livreur(session)
    erreur = await erreur_metier(
        dossier.deposer_piece(session, utilisateur.id, TypeDocument.PERMIS, JPEG + b"x" * 100)
    )
    assert erreur.statut == 413


async def test_la_photo_n_accepte_pas_le_pdf(session, stockage):
    utilisateur, _ = await creer_livreur(session)
    erreur = await erreur_metier(
        dossier.deposer_piece(session, utilisateur.id, TypeDocument.PHOTO, PDF)
    )
    assert erreur.statut == 415
    autre = await dossier.deposer_piece(session, utilisateur.id, TypeDocument.PERMIS, PDF)
    assert autre.type_mime == "application/pdf"


async def test_deposer_sans_profil_livreur_refuse(session, stockage):
    utilisateur = await creer_utilisateur(session, Role.LIVREUR)
    erreur = await erreur_metier(
        dossier.deposer_piece(session, utilisateur.id, TypeDocument.PHOTO, JPEG)
    )
    assert erreur.statut == 409


async def test_redeposer_remplace_la_piece_et_supprime_l_ancien_fichier(session, stockage):
    utilisateur, _ = await creer_livreur(session)
    premier = await dossier.deposer_piece(session, utilisateur.id, TypeDocument.PERMIS, JPEG)
    ancien_chemin = stockage / premier.chemin_fichier
    deuxieme = await dossier.deposer_piece(session, utilisateur.id, TypeDocument.PERMIS, PNG)
    assert deuxieme.id == premier.id  # une seule pièce par type
    assert deuxieme.type_mime == "image/png"
    assert not ancien_chemin.exists()
    assert (stockage / deuxieme.chemin_fichier).read_bytes() == PNG
    nombre = await session.scalar(
        select(func.count())
        .select_from(DocumentLivreur)
        .where(DocumentLivreur.livreur_id == utilisateur.id)
    )
    assert nombre == 1


async def test_une_piece_refusee_peut_etre_redeposee(session, stockage):
    admin = await creer_admin(session)
    utilisateur, _ = await creer_livreur(session)
    document = await dossier.deposer_piece(session, utilisateur.id, TypeDocument.PHOTO, JPEG)
    await dossier.examiner_piece(session, admin.id, document.id, False, "Photo floue.")
    assert document.statut == StatutDocument.REFUSE
    nouveau = await dossier.deposer_piece(session, utilisateur.id, TypeDocument.PHOTO, PNG)
    assert nouveau.statut == StatutDocument.EN_ATTENTE
    assert nouveau.motif_refus is None
    assert nouveau.examine_par_id is None


async def test_une_piece_acceptee_ne_peut_plus_etre_remplacee(session, stockage):
    admin = await creer_admin(session)
    utilisateur, _ = await creer_livreur(session)
    document = await dossier.deposer_piece(session, utilisateur.id, TypeDocument.PHOTO, JPEG)
    await dossier.examiner_piece(session, admin.id, document.id, True, None)
    erreur = await erreur_metier(
        dossier.deposer_piece(session, utilisateur.id, TypeDocument.PHOTO, PNG)
    )
    assert erreur.statut == 409


# ============================================================ examen des pièces


async def test_refuser_une_piece_exige_un_motif(session, stockage):
    admin = await creer_admin(session)
    utilisateur, _ = await creer_livreur(session)
    document = await dossier.deposer_piece(session, utilisateur.id, TypeDocument.PHOTO, JPEG)
    for motif in (None, "", "   "):
        erreur = await erreur_metier(
            dossier.examiner_piece(session, admin.id, document.id, False, motif)
        )
        assert erreur.statut == 422
    assert document.statut == StatutDocument.EN_ATTENTE


async def test_examiner_enregistre_qui_et_quand(session, stockage):
    admin = await creer_admin(session)
    utilisateur, _ = await creer_livreur(session)
    document = await dossier.deposer_piece(session, utilisateur.id, TypeDocument.PHOTO, JPEG)
    await dossier.examiner_piece(session, admin.id, document.id, False, "  Photo floue.  ")
    assert document.statut == StatutDocument.REFUSE
    assert document.motif_refus == "Photo floue."
    assert document.examine_par_id == admin.id
    assert document.examine_le is not None


async def test_piece_inconnue(session, stockage):
    import uuid

    admin = await creer_admin(session)
    erreur = await erreur_metier(
        dossier.examiner_piece(session, admin.id, uuid.uuid4(), True, None)
    )
    assert erreur.statut == 404


# ============================================================ validation du dossier


async def test_valider_un_dossier_incomplet_refuse(session, stockage):
    admin = await creer_admin(session)
    utilisateur, _ = await creer_livreur(session)
    await deposer_tout(session, utilisateur, [TypeDocument.PIECE_IDENTITE])
    erreur = await erreur_metier(dossier.valider_dossier(session, admin.id, utilisateur.id))
    assert erreur.statut == 409
    assert "permis" in erreur.detail and "carte_grise" in erreur.detail and "photo" in erreur.detail
    assert "piece_identite" in erreur.detail  # déposée mais pas encore acceptée


async def test_valider_exige_toutes_les_pieces_acceptees(session, stockage):
    admin = await creer_admin(session)
    utilisateur, _ = await creer_livreur(session)
    documents = await deposer_tout(session, utilisateur)
    await accepter_tout(session, admin, documents[:-1])
    erreur = await erreur_metier(dossier.valider_dossier(session, admin.id, utilisateur.id))
    assert erreur.statut == 409
    assert "en_attente" in erreur.detail


async def test_valider_exige_nom_et_prenom(session, stockage):
    admin = await creer_admin(session)
    utilisateur, _ = await creer_livreur(session, nom=None, prenom=None)
    await accepter_tout(session, admin, await deposer_tout(session, utilisateur))
    erreur = await erreur_metier(dossier.valider_dossier(session, admin.id, utilisateur.id))
    assert "nom et prénom" in erreur.detail


async def test_un_velo_se_valide_avec_deux_pieces(session, stockage):
    admin = await creer_admin(session)
    utilisateur, _ = await creer_livreur(session, TypeVehicule.VELO)
    await accepter_tout(session, admin, await deposer_tout(session, utilisateur))
    resultat = await dossier.valider_dossier(session, admin.id, utilisateur.id)
    assert resultat.livreur.statut_validation == StatutValidation.VALIDE


async def test_valider_un_dossier_complet(session, stockage):
    admin = await creer_admin(session)
    utilisateur, livreur = await creer_livreur(session)
    await accepter_tout(session, admin, await deposer_tout(session, utilisateur))
    await dossier.valider_dossier(session, admin.id, utilisateur.id)
    assert livreur.statut_validation == StatutValidation.VALIDE
    assert livreur.valide_par_id == admin.id
    assert livreur.valide_le is not None
    assert dossier.peut_recevoir_des_courses(livreur)
    erreur = await erreur_metier(dossier.valider_dossier(session, admin.id, utilisateur.id))
    assert erreur.statut == 409  # déjà validé


async def test_un_dossier_valide_ne_se_modifie_plus(session, stockage):
    admin = await creer_admin(session)
    utilisateur, _ = await creer_livreur(session)
    documents = await deposer_tout(session, utilisateur)
    await accepter_tout(session, admin, documents)
    await dossier.valider_dossier(session, admin.id, utilisateur.id)
    erreur = await erreur_metier(
        dossier.deposer_piece(session, utilisateur.id, TypeDocument.PHOTO, PNG)
    )
    assert erreur.statut == 409
    erreur = await erreur_metier(
        dossier.examiner_piece(session, admin.id, documents[0].id, False, "Périmée.")
    )
    assert erreur.statut == 409


# ============================================================ rejet, suspension, réactivation


async def test_rejeter_exige_un_motif_puis_la_correction_relance_l_examen(session, stockage):
    admin = await creer_admin(session)
    utilisateur, livreur = await creer_livreur(session)
    document = await dossier.deposer_piece(session, utilisateur.id, TypeDocument.PHOTO, JPEG)
    erreur = await erreur_metier(dossier.rejeter_dossier(session, admin.id, utilisateur.id, " "))
    assert erreur.statut == 422
    await dossier.rejeter_dossier(session, admin.id, utilisateur.id, "Pièces illisibles.")
    assert livreur.statut_validation == StatutValidation.REJETE
    assert livreur.motif_rejet == "Pièces illisibles."
    erreur = await erreur_metier(
        dossier.rejeter_dossier(session, admin.id, utilisateur.id, "Encore.")
    )
    assert erreur.statut == 409  # seul un dossier en attente se rejette
    # le livreur corrige : le dossier repart à l'examen
    await dossier.deposer_piece(session, utilisateur.id, TypeDocument.PHOTO, PNG)
    assert livreur.statut_validation == StatutValidation.EN_ATTENTE
    assert livreur.motif_rejet is None
    assert document.type == TypeDocument.PHOTO


async def test_suspendre_met_hors_ligne_et_retire_de_la_carte(session, stockage, faux_redis):
    admin = await creer_admin(session)
    utilisateur, livreur = await creer_livreur(session)
    await accepter_tout(session, admin, await deposer_tout(session, utilisateur))
    await dossier.valider_dossier(session, admin.id, utilisateur.id)
    await livreurs.passer_en_ligne(session, utilisateur.id)
    assert livreur.en_ligne
    await dossier.suspendre_livreur(session, faux_redis, admin.id, utilisateur.id, "Plainte.")
    assert livreur.statut_validation == StatutValidation.SUSPENDU
    assert livreur.motif_rejet == "Plainte."
    assert livreur.en_ligne is False
    assert len(faux_redis.supprimes) == 1  # sa position quitte Redis
    # la carte du répartiteur ET les agents (événement livreur_hors_ligne) sont prévenus
    assert {canal for canal, _ in faux_redis.publies} == {"positions", "evenements"}
    assert not dossier.peut_recevoir_des_courses(livreur)
    erreur = await erreur_metier(livreurs.passer_en_ligne(session, utilisateur.id))
    assert erreur.statut == 403
    erreur = await erreur_metier(
        dossier.deposer_piece(session, utilisateur.id, TypeDocument.PHOTO, PNG)
    )
    assert erreur.statut == 403
    erreur = await erreur_metier(
        dossier.suspendre_livreur(session, faux_redis, admin.id, utilisateur.id, "Encore.")
    )
    assert erreur.statut == 409


async def test_suspendre_exige_un_motif(session, stockage, faux_redis):
    admin = await creer_admin(session)
    utilisateur, _ = await creer_livreur(session)
    erreur = await erreur_metier(
        dossier.suspendre_livreur(session, faux_redis, admin.id, utilisateur.id, None)
    )
    assert erreur.statut == 422


async def test_reactiver_seulement_un_livreur_suspendu(session, stockage, faux_redis):
    admin = await creer_admin(session)
    utilisateur, livreur = await creer_livreur(session)
    erreur = await erreur_metier(dossier.reactiver_livreur(session, admin.id, utilisateur.id))
    assert erreur.statut == 409
    await dossier.suspendre_livreur(session, faux_redis, admin.id, utilisateur.id, "Plainte.")
    await dossier.reactiver_livreur(session, admin.id, utilisateur.id)
    assert livreur.statut_validation == StatutValidation.VALIDE
    assert livreur.motif_rejet is None


# ============================================================ règle « validation requise »


async def test_passer_en_ligne_exige_la_validation_quand_elle_est_activee(
    session, stockage, monkeypatch
):
    monkeypatch.setattr(parametres, "livreur_validation_requise", True)
    admin = await creer_admin(session)
    utilisateur, livreur = await creer_livreur(session)
    erreur = await erreur_metier(livreurs.passer_en_ligne(session, utilisateur.id))
    assert erreur.statut == 403
    await accepter_tout(session, admin, await deposer_tout(session, utilisateur))
    await dossier.valider_dossier(session, admin.id, utilisateur.id)
    await livreurs.passer_en_ligne(session, utilisateur.id)
    assert livreur.en_ligne


# ============================================================ journal des décisions


async def test_les_decisions_sont_journalisees_sans_donnees_personnelles(session, stockage):
    admin = await creer_admin(session)
    utilisateur, _ = await creer_livreur(session)
    documents = await deposer_tout(session, utilisateur)
    await accepter_tout(session, admin, documents)
    await dossier.valider_dossier(session, admin.id, utilisateur.id)
    evenements = (
        await session.scalars(
            select(Evenement).where(Evenement.auteur == f"utilisateur:{admin.id}")
        )
    ).all()
    actions = sorted(e.detail["action"] for e in evenements)
    assert actions == ["dossier_valide"] + ["piece_acceptee"] * 4
    assert all(e.type == TypeEvenement.ACTION_MANUELLE for e in evenements)
    texte = " ".join(str(e.detail) for e in evenements)
    assert utilisateur.telephone not in texte and "Koffi" not in texte  # identifiants seulement


# ============================================================ lecture d'un fichier


async def test_ouvrir_une_piece_controle_les_droits(session, stockage):
    admin = await creer_admin(session)
    proprietaire, _ = await creer_livreur(session)
    autre, _ = await creer_livreur(session)
    document = await dossier.deposer_piece(session, proprietaire.id, TypeDocument.PERMIS, PDF)
    assert (await dossier.ouvrir_piece(session, document.id, admin)).type_mime == "application/pdf"
    assert (await dossier.ouvrir_piece(session, document.id, proprietaire)).nom == "permis.pdf"
    erreur = await erreur_metier(dossier.ouvrir_piece(session, document.id, autre))
    assert erreur.statut == 404  # on ne révèle même pas que la pièce existe


async def test_ouvrir_une_piece_refuse_de_sortir_du_dossier_de_stockage(session, stockage):
    admin = await creer_admin(session)
    utilisateur, _ = await creer_livreur(session)
    document = await dossier.deposer_piece(session, utilisateur.id, TypeDocument.PERMIS, PDF)
    # Un vrai fichier existe hors du dossier de stockage : seul le contrôle du chemin le protège.
    secret = stockage.parent / "secret.txt"
    secret.write_text("confidentiel")
    document.chemin_fichier = "../secret.txt"  # base corrompue ou malveillante
    erreur = await erreur_metier(dossier.ouvrir_piece(session, document.id, admin))
    assert erreur.statut == 404


async def test_fichier_disparu_du_disque(session, stockage):
    admin = await creer_admin(session)
    utilisateur, _ = await creer_livreur(session)
    document = await dossier.deposer_piece(session, utilisateur.id, TypeDocument.PERMIS, PDF)
    (stockage / document.chemin_fichier).unlink()
    erreur = await erreur_metier(dossier.ouvrir_piece(session, document.id, admin))
    assert erreur.statut == 404


# ============================================================ garde-fous de la base


async def test_la_base_refuse_une_piece_refusee_sans_motif(session, stockage):
    utilisateur, _ = await creer_livreur(session)
    document = await dossier.deposer_piece(session, utilisateur.id, TypeDocument.PHOTO, JPEG)
    document.statut = StatutDocument.REFUSE
    document.motif_refus = None
    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            await session.flush()


async def test_la_base_refuse_deux_pieces_du_meme_type(session, stockage):
    utilisateur, _ = await creer_livreur(session)
    await dossier.deposer_piece(session, utilisateur.id, TypeDocument.PHOTO, JPEG)
    session.add(
        DocumentLivreur(
            livreur_id=utilisateur.id,
            type=TypeDocument.PHOTO,
            chemin_fichier="doublon.jpg",
            statut=StatutDocument.EN_ATTENTE,
        )
    )
    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            await session.flush()


# ============================================================ API HTTP


@pytest_asyncio.fixture
async def livreur_http(session, stockage):
    return await creer_livreur(session)


def fichier(contenu, nom="piece.jpg", mime="image/jpeg"):
    return {"fichier": (nom, contenu, mime)}


async def test_api_non_authentifie(client):
    assert (await client.get("/admin/livreurs")).status_code == 401
    assert (await client.get("/livreurs/moi/dossier")).status_code == 401
    r = await client.put("/livreurs/moi/pieces/photo", files=fichier(JPEG))
    assert r.status_code == 401


async def test_api_droits_par_role(client, session, agir_comme, stockage):
    client_simple = await creer_utilisateur(session, Role.CLIENT)
    livreur, _ = await creer_livreur(session)
    agir_comme(client_simple)
    assert (await client.get("/admin/livreurs")).status_code == 403
    assert (await client.get("/livreurs/moi/dossier")).status_code == 403
    agir_comme(livreur)
    assert (await client.get("/admin/livreurs")).status_code == 403
    agir_comme(await creer_admin(session))
    assert (await client.get("/livreurs/moi/dossier")).status_code == 403


async def test_api_parcours_complet(client, session, agir_comme, stockage):
    admin = await creer_admin(session)
    utilisateur, _ = await creer_livreur(session, TypeVehicule.VELO)

    # le livreur consulte son dossier : tout est à fournir
    agir_comme(utilisateur)
    r = await client.get("/livreurs/moi/dossier")
    assert r.status_code == 200
    corps = r.json()
    assert corps["statut_validation"] == "en_attente"
    assert sorted(corps["pieces_manquantes"]) == ["photo", "piece_identite"]
    assert corps["complet"] is False

    # il dépose ses pièces
    for type_piece in ("piece_identite", "photo"):
        r = await client.put(f"/livreurs/moi/pieces/{type_piece}", files=fichier(JPEG))
        assert r.status_code == 200, r.text
        assert r.json()["statut"] == "en_attente"
    r = await client.put("/livreurs/moi/pieces/photo", files=fichier(b"pas une image", "x.jpg"))
    assert r.status_code == 415
    r = await client.put("/livreurs/moi/pieces/passeport", files=fichier(JPEG))
    assert r.status_code == 422  # type inconnu
    corps = (await client.get("/livreurs/moi/dossier")).json()
    assert corps["complet"] is True and len(corps["pieces"]) == 2

    # l'administrateur voit le dossier dans sa file, lit les pièces et les accepte
    agir_comme(admin)
    r = await client.get("/admin/livreurs", params={"statut": "en_attente"})
    assert r.status_code == 200
    assert str(utilisateur.id) in [d["livreur_id"] for d in r.json()]
    detail = (await client.get(f"/admin/livreurs/{utilisateur.id}")).json()
    for piece in detail["pieces"]:
        lecture = await client.get(f"/admin/pieces/{piece['id']}/fichier")
        assert lecture.status_code == 200 and lecture.content == JPEG
        assert lecture.headers["cache-control"] == "private, no-store"
        assert lecture.headers["x-content-type-options"] == "nosniff"
        assert lecture.headers["content-type"] == "image/jpeg"
    refus = await client.post(
        f"/admin/pieces/{detail['pieces'][0]['id']}/decision", json={"accepte": False}
    )
    assert refus.status_code == 422  # motif obligatoire
    avant = await client.post(f"/admin/livreurs/{utilisateur.id}/valider")
    assert avant.status_code == 409 and "en_attente" in avant.json()["detail"]
    for piece in detail["pieces"]:
        r = await client.post(f"/admin/pieces/{piece['id']}/decision", json={"accepte": True})
        assert r.status_code == 200 and r.json()["statut"] == "accepte"
    r = await client.post(f"/admin/livreurs/{utilisateur.id}/valider")
    assert r.status_code == 200 and r.json()["statut_validation"] == "valide"

    # plus de dépôt possible, puis suspension et réactivation
    agir_comme(utilisateur)
    r = await client.put("/livreurs/moi/pieces/photo", files=fichier(PNG, "p.png", "image/png"))
    assert r.status_code == 409
    agir_comme(admin)
    r = await client.post(f"/admin/livreurs/{utilisateur.id}/suspendre", json={"motif": "ab"})
    assert r.status_code == 422  # motif trop court
    r = await client.post(
        f"/admin/livreurs/{utilisateur.id}/suspendre", json={"motif": "Plainte client."}
    )
    assert r.status_code == 200 and r.json()["statut_validation"] == "suspendu"
    r = await client.post(f"/admin/livreurs/{utilisateur.id}/reactiver")
    assert r.status_code == 200 and r.json()["statut_validation"] == "valide"


async def test_api_rejet_visible_par_le_livreur(client, session, agir_comme, stockage):
    admin = await creer_admin(session)
    utilisateur, _ = await creer_livreur(session)
    agir_comme(utilisateur)
    await client.put("/livreurs/moi/pieces/permis", files=fichier(PDF, "p.pdf", "application/pdf"))
    agir_comme(admin)
    r = await client.post(
        f"/admin/livreurs/{utilisateur.id}/rejeter", json={"motif": "Permis expiré."}
    )
    assert r.status_code == 200
    agir_comme(utilisateur)
    corps = (await client.get("/livreurs/moi/dossier")).json()
    assert corps["statut_validation"] == "rejete" and corps["motif_rejet"] == "Permis expiré."


async def test_api_livreur_inconnu_et_fichier_trop_gros(
    client, session, agir_comme, stockage, monkeypatch
):
    import uuid

    admin = await creer_admin(session)
    agir_comme(admin)
    assert (await client.get(f"/admin/livreurs/{uuid.uuid4()}")).status_code == 404
    assert (await client.post(f"/admin/livreurs/{uuid.uuid4()}/valider")).status_code == 404
    utilisateur, _ = await creer_livreur(session)
    monkeypatch.setattr(parametres, "piece_taille_max_octets", 100)
    agir_comme(utilisateur)
    r = await client.put("/livreurs/moi/pieces/photo", files=fichier(JPEG + b"x" * 500))
    assert r.status_code == 413
