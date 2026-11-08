"""Dossier du livreur connecté : /livreurs/moi/dossier et /livreurs/moi/pieces/... (F-03)."""

from fastapi import APIRouter, Depends, File, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from livraison.api.routes_livreur import livreur_connecte
from livraison.api.schemas_dossier import DossierSortie, PieceSortie, dossier_sortie
from livraison.config import parametres
from livraison.persistance.enums import TypeDocument
from livraison.persistance.modeles import Utilisateur
from livraison.persistance.session import obtenir_session
from livraison.services import dossier as service
from livraison.services import livreurs

routeur = APIRouter(prefix="/livreurs/moi", tags=["Dossier livreur"])


@routeur.get("/dossier", response_model=DossierSortie)
async def lire_dossier(
    utilisateur: Utilisateur = Depends(livreur_connecte),
    session: AsyncSession = Depends(obtenir_session),
):
    """Statut du dossier, pièces déposées (avec leur décision) et pièces encore à fournir."""
    # Le profil doit exister : sans véhicule, on ne sait pas quelles pièces sont exigées.
    await livreurs.livreur_ou_erreur(session, utilisateur.id)
    return dossier_sortie(await service.charger_dossier(session, utilisateur.id))


@routeur.put("/pieces/{type_piece}", response_model=PieceSortie)
async def deposer_piece(
    type_piece: TypeDocument,
    fichier: UploadFile = File(description="JPEG, PNG ou PDF (JPEG ou PNG pour la photo)."),
    utilisateur: Utilisateur = Depends(livreur_connecte),
    session: AsyncSession = Depends(obtenir_session),
):
    """Dépose une pièce, ou remplace celle du même type tant qu'elle n'est pas acceptée."""
    contenu = await fichier.read(parametres.piece_taille_max_octets + 1)
    return await service.deposer_piece(session, utilisateur.id, type_piece, contenu)
