"""Dossier du livreur : pièces justificatives et validation par l'administrateur (F-03).

Parcours :
1. le livreur renseigne son identité (nom, prénom), son véhicule, puis dépose ses pièces ;
2. l'administrateur examine chaque pièce (acceptée, ou refusée avec un motif) ;
3. quand le dossier est complet et toutes les pièces sont acceptées, il le valide ;
   il peut aussi le rejeter, puis suspendre ou réactiver un livreur déjà validé.

Chaque décision de l'administrateur est inscrite au journal `evenement` (NF-09), avec des
identifiants seulement : jamais de nom, de téléphone ni de motif (NF-07).
"""

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from livraison.config import parametres
from livraison.persistance.enums import (
    Role,
    StatutDocument,
    StatutValidation,
    TypeDocument,
    TypeEvenement,
    TypeVehicule,
)
from livraison.persistance.modeles import DocumentLivreur, Evenement, Livreur, Utilisateur
from livraison.services import livreurs
from livraison.services.erreurs import ErreurMetier

journal = logging.getLogger("livraison")

# -- règles du dossier ---------------------------------------------------------------------

_PIECES_COMMUNES = (TypeDocument.PIECE_IDENTITE, TypeDocument.PHOTO)
_PIECES_VEHICULE_MOTORISE = (TypeDocument.PERMIS, TypeDocument.CARTE_GRISE)

# Format détecté sur le contenu du fichier, jamais sur l'extension ni sur l'en-tête du client.
_SIGNATURES = (
    (b"\xff\xd8\xff", "image/jpeg", ".jpg"),
    (b"\x89PNG\r\n\x1a\n", "image/png", ".png"),
    (b"%PDF-", "application/pdf", ".pdf"),
)


def pieces_requises(type_vehicule: TypeVehicule) -> tuple[TypeDocument, ...]:
    """Un vélo n'a ni permis ni carte grise."""
    if type_vehicule == TypeVehicule.VELO:
        return _PIECES_COMMUNES
    return _PIECES_COMMUNES + _PIECES_VEHICULE_MOTORISE


def detecter_format(contenu: bytes) -> tuple[str, str] | None:
    """Renvoie (type MIME, extension) d'après les premiers octets, ou None si non accepté."""
    for signature, type_mime, extension in _SIGNATURES:
        if contenu.startswith(signature):
            return type_mime, extension
    return None


# -- vue d'ensemble d'un dossier -------------------------------------------------------------


@dataclass(frozen=True)
class Dossier:
    livreur: Livreur
    utilisateur: Utilisateur
    documents: list[DocumentLivreur]

    @property
    def requises(self) -> tuple[TypeDocument, ...]:
        return pieces_requises(self.livreur.type_vehicule)

    @property
    def pieces_manquantes(self) -> list[TypeDocument]:
        deposees = {d.type for d in self.documents}
        return [t for t in self.requises if t not in deposees]

    @property
    def identite_renseignee(self) -> bool:
        return bool(self.utilisateur.nom and self.utilisateur.prenom)

    @property
    def complet(self) -> bool:
        return self.identite_renseignee and not self.pieces_manquantes

    def problemes_validation(self) -> list[str]:
        """Ce qui empêche de valider le dossier (liste vide : il peut l'être)."""
        problemes = []
        if not self.identite_renseignee:
            problemes.append("nom et prénom non renseignés")
        if self.pieces_manquantes:
            manquantes = ", ".join(t.value for t in self.pieces_manquantes)
            problemes.append(f"pièces manquantes : {manquantes}")
        par_type = {d.type: d for d in self.documents}
        for type_piece in self.requises:
            document = par_type.get(type_piece)
            if document is not None and document.statut != StatutDocument.ACCEPTE:
                problemes.append(f"pièce {type_piece.value} : {document.statut.value}")
        return problemes


async def _documents(session: AsyncSession, livreur_ids: list[uuid.UUID]) -> dict:
    resultat = await session.scalars(
        select(DocumentLivreur)
        .where(DocumentLivreur.livreur_id.in_(livreur_ids))
        .order_by(DocumentLivreur.type)
    )
    par_livreur: dict[uuid.UUID, list[DocumentLivreur]] = {i: [] for i in livreur_ids}
    for document in resultat:
        par_livreur[document.livreur_id].append(document)
    return par_livreur


async def charger_dossier(session: AsyncSession, livreur_id: uuid.UUID) -> Dossier:
    livreur = await session.get(Livreur, livreur_id)
    utilisateur = await session.get(Utilisateur, livreur_id)
    if livreur is None or utilisateur is None:
        raise ErreurMetier(404, "Livreur introuvable.")
    documents = (await _documents(session, [livreur_id]))[livreur_id]
    return Dossier(livreur, utilisateur, documents)


async def lister_dossiers(
    session: AsyncSession,
    statut: StatutValidation | None = None,
    limite: int = 50,
    decalage: int = 0,
) -> list[Dossier]:
    """Dossiers du plus ancien au plus récent : c'est la file d'examen de l'administrateur."""
    requete = select(Livreur, Utilisateur).join(
        Utilisateur, Utilisateur.id == Livreur.utilisateur_id
    )
    if statut is not None:
        requete = requete.where(Livreur.statut_validation == statut)
    requete = (
        requete.order_by(Livreur.cree_le.asc(), Livreur.utilisateur_id)
        .limit(limite)
        .offset(decalage)
    )
    lignes = (await session.execute(requete)).all()
    documents = await _documents(session, [livreur.utilisateur_id for livreur, _ in lignes])
    return [
        Dossier(livreur, utilisateur, documents[livreur.utilisateur_id])
        for livreur, utilisateur in lignes
    ]


# -- stockage des fichiers -------------------------------------------------------------------


def _racine() -> Path:
    return Path(parametres.stockage_pieces_dir).resolve()


def _chemin_sur(chemin_relatif: str) -> Path | None:
    """Chemin absolu du fichier, ou None s'il sortirait du dossier de stockage."""
    racine = _racine()
    chemin = (racine / chemin_relatif).resolve()
    return chemin if chemin.is_relative_to(racine) else None


def _supprimer_fichier(chemin_relatif: str) -> None:
    chemin = _chemin_sur(chemin_relatif)
    if chemin is None:
        return
    try:
        chemin.unlink(missing_ok=True)
    except OSError as e:
        journal.warning("Suppression impossible d'une ancienne pièce : %s", e)


def _ecrire_fichier(chemin: Path, contenu: bytes) -> None:
    chemin.parent.mkdir(parents=True, exist_ok=True)
    chemin.write_bytes(contenu)


# -- côté livreur ------------------------------------------------------------------------------


async def deposer_piece(
    session: AsyncSession, utilisateur_id: uuid.UUID, type_piece: TypeDocument, contenu: bytes
) -> DocumentLivreur:
    """Dépose (ou remplace) une pièce. Le contenu est contrôlé : taille puis format réel."""
    livreur = await livreurs.livreur_ou_erreur(session, utilisateur_id)
    if livreur.statut_validation == StatutValidation.SUSPENDU:
        raise ErreurMetier(403, "Votre compte livreur est suspendu.")
    if livreur.statut_validation == StatutValidation.VALIDE:
        raise ErreurMetier(
            409, "Votre dossier est validé : contactez l'administrateur pour modifier une pièce."
        )
    if not contenu:
        raise ErreurMetier(422, "Le fichier est vide.")
    maximum = parametres.piece_taille_max_octets
    if len(contenu) > maximum:
        raise ErreurMetier(413, f"Fichier trop volumineux (maximum {maximum // (1024 * 1024)} Mo).")
    format_fichier = detecter_format(contenu)
    if format_fichier is None or (
        type_piece == TypeDocument.PHOTO and format_fichier[0] == "application/pdf"
    ):
        accepte = "JPEG ou PNG" if type_piece == TypeDocument.PHOTO else "JPEG, PNG ou PDF"
        raise ErreurMetier(415, f"Format non accepté : envoyez un fichier {accepte}.")
    type_mime, extension = format_fichier

    existant = await session.scalar(
        select(DocumentLivreur).where(
            DocumentLivreur.livreur_id == utilisateur_id, DocumentLivreur.type == type_piece
        )
    )
    if existant is not None and existant.statut == StatutDocument.ACCEPTE:
        raise ErreurMetier(
            409, "Cette pièce a déjà été acceptée : elle ne peut plus être remplacée."
        )

    chemin_relatif = f"{utilisateur_id}/{uuid.uuid4().hex}{extension}"
    chemin = _chemin_sur(chemin_relatif)
    if chemin is None:  # impossible avec un nom généré, mais on ne présume pas
        raise ErreurMetier(500, "Chemin de stockage invalide.")
    await asyncio.to_thread(_ecrire_fichier, chemin, contenu)

    ancien_chemin = existant.chemin_fichier if existant is not None else None
    try:
        if existant is None:
            document = DocumentLivreur(
                livreur_id=utilisateur_id,
                type=type_piece,
                chemin_fichier=chemin_relatif,
                type_mime=type_mime,
                taille_octets=len(contenu),
                statut=StatutDocument.EN_ATTENTE,
            )
            session.add(document)
        else:
            document = existant
            document.chemin_fichier = chemin_relatif
            document.type_mime = type_mime
            document.taille_octets = len(contenu)
            document.statut = StatutDocument.EN_ATTENTE
            document.motif_refus = None
            document.examine_par_id = None
            document.examine_le = None
            document.cree_le = datetime.now(UTC)
        if livreur.statut_validation == StatutValidation.REJETE:
            # Un dossier rejeté qui reçoit une pièce corrigée repart à l'examen.
            livreur.statut_validation = StatutValidation.EN_ATTENTE
            livreur.motif_rejet = None
        await session.flush()
    except Exception:
        _supprimer_fichier(chemin_relatif)
        raise
    if ancien_chemin is not None:
        _supprimer_fichier(ancien_chemin)
    return document


# -- lecture d'une pièce ------------------------------------------------------------------------


@dataclass(frozen=True)
class FichierPiece:
    chemin: Path
    type_mime: str
    nom: str


async def ouvrir_piece(
    session: AsyncSession, document_id: uuid.UUID, demandeur: Utilisateur
) -> FichierPiece:
    """Un administrateur lit toutes les pièces ; un livreur seulement les siennes."""
    document = await session.get(DocumentLivreur, document_id)
    autorise = document is not None and (
        demandeur.role == Role.ADMINISTRATEUR or document.livreur_id == demandeur.id
    )
    if not autorise:
        raise ErreurMetier(404, "Pièce introuvable.")  # même réponse : on ne révèle rien
    chemin = _chemin_sur(document.chemin_fichier)
    if chemin is None or not chemin.is_file():
        raise ErreurMetier(404, "Fichier introuvable.")
    return FichierPiece(
        chemin=chemin,
        type_mime=document.type_mime or "application/octet-stream",
        nom=f"{document.type.value}{chemin.suffix}",
    )


# -- décisions de l'administrateur -------------------------------------------------------------


def _motif_ou_erreur(motif: str | None, message: str) -> str:
    propre = (motif or "").strip()
    if not propre:
        raise ErreurMetier(422, message)
    return propre


def _journaliser(session: AsyncSession, admin_id: uuid.UUID, action: str, **identifiants) -> None:
    session.add(
        Evenement(
            type=TypeEvenement.ACTION_MANUELLE,
            auteur=f"utilisateur:{admin_id}",
            detail={"action": action, **{k: str(v) for k, v in identifiants.items()}},
        )
    )


async def examiner_piece(
    session: AsyncSession,
    admin_id: uuid.UUID,
    document_id: uuid.UUID,
    accepte: bool,
    motif: str | None,
) -> DocumentLivreur:
    document = await session.get(DocumentLivreur, document_id)
    if document is None:
        raise ErreurMetier(404, "Pièce introuvable.")
    livreur = await session.get(Livreur, document.livreur_id)
    if livreur.statut_validation == StatutValidation.VALIDE:
        raise ErreurMetier(
            409, "Le dossier est validé : suspendez le livreur avant de revoir une pièce."
        )
    motif_propre = (
        None
        if accepte
        else _motif_ou_erreur(motif, "Un motif est obligatoire pour refuser une pièce.")
    )
    document.statut = StatutDocument.ACCEPTE if accepte else StatutDocument.REFUSE
    document.motif_refus = motif_propre
    document.examine_par_id = admin_id
    document.examine_le = datetime.now(UTC)
    _journaliser(
        session,
        admin_id,
        "piece_acceptee" if accepte else "piece_refusee",
        livreur_id=document.livreur_id,
        document_id=document.id,
    )
    await session.flush()
    return document


async def valider_dossier(
    session: AsyncSession, admin_id: uuid.UUID, livreur_id: uuid.UUID
) -> Dossier:
    dossier = await charger_dossier(session, livreur_id)
    livreur = dossier.livreur
    if livreur.statut_validation == StatutValidation.VALIDE:
        raise ErreurMetier(409, "Le dossier est déjà validé.")
    if livreur.statut_validation == StatutValidation.SUSPENDU:
        raise ErreurMetier(409, "Ce livreur est suspendu : réactivez-le au lieu de valider.")
    problemes = dossier.problemes_validation()
    if problemes:
        raise ErreurMetier(
            409, "Dossier à compléter ou à corriger : " + " ; ".join(problemes) + "."
        )
    livreur.statut_validation = StatutValidation.VALIDE
    livreur.valide_par_id = admin_id
    livreur.valide_le = datetime.now(UTC)
    livreur.motif_rejet = None
    _journaliser(session, admin_id, "dossier_valide", livreur_id=livreur_id)
    await session.flush()
    return dossier


async def rejeter_dossier(
    session: AsyncSession, admin_id: uuid.UUID, livreur_id: uuid.UUID, motif: str | None
) -> Dossier:
    motif_propre = _motif_ou_erreur(motif, "Un motif est obligatoire pour rejeter un dossier.")
    dossier = await charger_dossier(session, livreur_id)
    livreur = dossier.livreur
    if livreur.statut_validation != StatutValidation.EN_ATTENTE:
        raise ErreurMetier(
            409,
            "Seul un dossier en attente peut être rejeté "
            "(pour un livreur validé, utilisez la suspension).",
        )
    livreur.statut_validation = StatutValidation.REJETE
    livreur.motif_rejet = motif_propre
    livreur.valide_par_id = None
    livreur.valide_le = None
    _journaliser(session, admin_id, "dossier_rejete", livreur_id=livreur_id)
    await session.flush()
    return dossier


async def suspendre_livreur(
    session: AsyncSession,
    redis: Redis,
    admin_id: uuid.UUID,
    livreur_id: uuid.UUID,
    motif: str | None,
) -> Dossier:
    """Le livreur passe hors ligne tout de suite et disparaît de la carte (F-62)."""
    motif_propre = _motif_ou_erreur(motif, "Un motif est obligatoire pour suspendre un livreur.")
    dossier = await charger_dossier(session, livreur_id)
    if dossier.livreur.statut_validation == StatutValidation.SUSPENDU:
        raise ErreurMetier(409, "Ce livreur est déjà suspendu.")
    await livreurs.passer_hors_ligne(session, redis, livreur_id)
    dossier.livreur.statut_validation = StatutValidation.SUSPENDU
    dossier.livreur.motif_rejet = motif_propre
    _journaliser(session, admin_id, "livreur_suspendu", livreur_id=livreur_id)
    await session.flush()
    return dossier


async def reactiver_livreur(
    session: AsyncSession, admin_id: uuid.UUID, livreur_id: uuid.UUID
) -> Dossier:
    dossier = await charger_dossier(session, livreur_id)
    livreur = dossier.livreur
    if livreur.statut_validation != StatutValidation.SUSPENDU:
        raise ErreurMetier(409, "Seul un livreur suspendu peut être réactivé.")
    livreur.statut_validation = StatutValidation.VALIDE
    livreur.valide_par_id = admin_id
    livreur.valide_le = datetime.now(UTC)
    livreur.motif_rejet = None
    _journaliser(session, admin_id, "livreur_reactive", livreur_id=livreur_id)
    await session.flush()
    return dossier


# Utilisé par le Dispatcher : en production seuls les livreurs validés reçoivent des courses.
# En développement (LIVREUR_VALIDATION_REQUISE=false), un compte en attente peut aussi tester.
def peut_recevoir_des_courses(livreur: Livreur) -> bool:
    if not parametres.livreur_validation_requise:
        return livreur.statut_validation not in (
            StatutValidation.SUSPENDU,
            StatutValidation.REJETE,
        )
    return livreur.statut_validation == StatutValidation.VALIDE