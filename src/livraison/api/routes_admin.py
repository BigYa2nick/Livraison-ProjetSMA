"""Administration des dossiers livreurs : /admin/... (F-03, F-62)."""

import uuid

from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from livraison.api.dependances import exiger_roles
from livraison.api.schemas_dossier import (
    DecisionPiece,
    DossierSortie,
    MotifEntree,
    PieceSortie,
    dossier_sortie,
)
from livraison.persistance.enums import Role, StatutValidation
from livraison.persistance.modeles import Utilisateur
from livraison.persistance.session import obtenir_session
from livraison.services import dossier as service
from livraison.services.redis_client import obtenir_redis

routeur = APIRouter(prefix="/admin", tags=["Administration des livreurs"])

admin_connecte = exiger_roles(Role.ADMINISTRATEUR)


@routeur.get("/livreurs", response_model=list[DossierSortie])
async def lister_dossiers(
    statut: StatutValidation | None = Query(default=None, description="Filtre sur le statut."),
    limite: int = Query(default=50, ge=1, le=200),
    decalage: int = Query(default=0, ge=0),
    _: Utilisateur = Depends(admin_connecte),
    session: AsyncSession = Depends(obtenir_session),
):
    """File d'examen : les dossiers du plus ancien au plus récent (essayez `statut=en_attente`)."""
    dossiers = await service.lister_dossiers(session, statut, limite, decalage)
    return [dossier_sortie(d) for d in dossiers]


@routeur.get("/livreurs/{livreur_id}", response_model=DossierSortie)
async def lire_dossier(
    livreur_id: uuid.UUID,
    _: Utilisateur = Depends(admin_connecte),
    session: AsyncSession = Depends(obtenir_session),
):
    return dossier_sortie(await service.charger_dossier(session, livreur_id))


@routeur.get("/pieces/{document_id}/fichier")
async def telecharger_piece(
    document_id: uuid.UUID,
    admin: Utilisateur = Depends(admin_connecte),
    session: AsyncSession = Depends(obtenir_session),
):
    """Le fichier lui-même. Jamais mis en cache, jamais servi sans authentification."""
    fichier = await service.ouvrir_piece(session, document_id, admin)
    return FileResponse(
        fichier.chemin,
        media_type=fichier.type_mime,
        filename=fichier.nom,
        content_disposition_type="inline",
        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
    )


@routeur.post("/pieces/{document_id}/decision", response_model=PieceSortie)
async def examiner_piece(
    document_id: uuid.UUID,
    decision: DecisionPiece,
    admin: Utilisateur = Depends(admin_connecte),
    session: AsyncSession = Depends(obtenir_session),
):
    """Accepte une pièce, ou la refuse avec un motif que le livreur verra."""
    return await service.examiner_piece(
        session, admin.id, document_id, decision.accepte, decision.motif
    )


@routeur.post("/livreurs/{livreur_id}/valider", response_model=DossierSortie)
async def valider_dossier(
    livreur_id: uuid.UUID,
    admin: Utilisateur = Depends(admin_connecte),
    session: AsyncSession = Depends(obtenir_session),
):
    """Valide le dossier : identité renseignée, toutes les pièces requises déposées et acceptées."""
    return dossier_sortie(await service.valider_dossier(session, admin.id, livreur_id))


@routeur.post("/livreurs/{livreur_id}/rejeter", response_model=DossierSortie)
async def rejeter_dossier(
    livreur_id: uuid.UUID,
    corps: MotifEntree,
    admin: Utilisateur = Depends(admin_connecte),
    session: AsyncSession = Depends(obtenir_session),
):
    return dossier_sortie(await service.rejeter_dossier(session, admin.id, livreur_id, corps.motif))


@routeur.post("/livreurs/{livreur_id}/suspendre", response_model=DossierSortie)
async def suspendre_livreur(
    livreur_id: uuid.UUID,
    corps: MotifEntree,
    admin: Utilisateur = Depends(admin_connecte),
    session: AsyncSession = Depends(obtenir_session),
    redis: Redis = Depends(obtenir_redis),
):
    """Suspend le livreur : hors ligne immédiatement, retiré de la carte."""
    return dossier_sortie(
        await service.suspendre_livreur(session, redis, admin.id, livreur_id, corps.motif)
    )


@routeur.post("/livreurs/{livreur_id}/reactiver", response_model=DossierSortie)
async def reactiver_livreur(
    livreur_id: uuid.UUID,
    admin: Utilisateur = Depends(admin_connecte),
    session: AsyncSession = Depends(obtenir_session),
):
    return dossier_sortie(await service.reactiver_livreur(session, admin.id, livreur_id))