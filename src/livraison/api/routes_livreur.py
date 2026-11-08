"""Routes du livreur connecté : /livreurs/moi/..."""

import uuid

from fastapi import APIRouter, Depends, status
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from livraison.api.dependances import exiger_roles
from livraison.api.schemas_courses import PropositionCourseSortie, ReponseCourseSortie
from livraison.api.schemas_gps import (
    EtatEnLigne,
    PositionAccepteeSortie,
    PositionEntree,
    ProfilLivreurEntree,
    ProfilLivreurSortie,
)
from livraison.persistance.enums import Role, TypeVehicule
from livraison.persistance.modeles import Utilisateur
from livraison.persistance.session import obtenir_session
from livraison.services import courses, livreurs, positions
from livraison.services.redis_client import obtenir_redis

routeur = APIRouter(prefix="/livreurs/moi", tags=["Livreur"])

# Toutes ces routes sont réservées au rôle « livreur ».
livreur_connecte = exiger_roles(Role.LIVREUR)


@routeur.put("/profil", response_model=ProfilLivreurSortie)
async def definir_profil(
    profil: ProfilLivreurEntree,
    utilisateur: Utilisateur = Depends(livreur_connecte),
    session: AsyncSession = Depends(obtenir_session),
):
    """Crée ou met à jour le profil du livreur (véhicule, capacité)."""
    return await livreurs.definir_profil(
        session,
        utilisateur.id,
        TypeVehicule(profil.type_vehicule),
        profil.capacite_kg,
        profil.immatriculation,
    )


@routeur.get("/profil", response_model=ProfilLivreurSortie)
async def lire_profil(
    utilisateur: Utilisateur = Depends(livreur_connecte),
    session: AsyncSession = Depends(obtenir_session),
):
    return await livreurs.livreur_ou_erreur(session, utilisateur.id)


@routeur.post("/en-ligne", response_model=EtatEnLigne)
async def passer_en_ligne(
    utilisateur: Utilisateur = Depends(livreur_connecte),
    session: AsyncSession = Depends(obtenir_session),
    redis: Redis = Depends(obtenir_redis),
):
    """Le livreur devient disponible : l'application peut alors envoyer sa position (F-20)."""
    livreur = await livreurs.passer_en_ligne(session, utilisateur.id, redis)
    return EtatEnLigne(en_ligne=livreur.en_ligne)


@routeur.post("/hors-ligne", response_model=EtatEnLigne)
async def passer_hors_ligne(
    utilisateur: Utilisateur = Depends(livreur_connecte),
    session: AsyncSession = Depends(obtenir_session),
    redis: Redis = Depends(obtenir_redis),
):
    """La position n'est plus collectée ni affichée (NF-07)."""
    livreur = await livreurs.passer_hors_ligne(session, redis, utilisateur.id)
    return EtatEnLigne(en_ligne=livreur.en_ligne)


@routeur.post("/position", response_model=PositionAccepteeSortie)
async def envoyer_position(
    position: PositionEntree,
    utilisateur: Utilisateur = Depends(livreur_connecte),
    session: AsyncSession = Depends(obtenir_session),
    redis: Redis = Depends(obtenir_redis),
):
    """Reçoit une position GPS ; la réponse indique le retard de transmission."""
    livreur = await livreurs.livreur_ou_erreur(session, utilisateur.id)
    acceptee = await positions.enregistrer_position(
        session,
        redis,
        livreur,
        position.latitude,
        position.longitude,
        position.precision_m,
        position.mesure_le,
    )
    return PositionAccepteeSortie(recu_le=acceptee.recu_le, retard_s=acceptee.retard_s)


@routeur.get("/proposition", response_model=PropositionCourseSortie | None)
async def proposition_en_cours(
    utilisateur: Utilisateur = Depends(livreur_connecte),
    session: AsyncSession = Depends(obtenir_session),
):
    """La course que le Dispatcher propose au livreur, à confirmer avant `expire_le` (null : aucune)."""
    return await courses.proposition_en_cours(session, utilisateur.id)


@routeur.post(
    "/proposition/{commande_id}/confirmer",
    response_model=ReponseCourseSortie,
    status_code=status.HTTP_202_ACCEPTED,
)
async def confirmer_la_course(
    commande_id: uuid.UUID,
    utilisateur: Utilisateur = Depends(livreur_connecte),
    session: AsyncSession = Depends(obtenir_session),
    redis: Redis = Depends(obtenir_redis),
):
    """Le livreur prend la course : sa réponse est transmise à son agent, qui l'attend 30 secondes."""
    await courses.repondre_a_la_proposition(session, redis, utilisateur.id, commande_id, accepte=True)
    return ReponseCourseSortie(commande_id=commande_id, reponse="confirmee")


@routeur.post(
    "/proposition/{commande_id}/decliner",
    response_model=ReponseCourseSortie,
    status_code=status.HTTP_202_ACCEPTED,
)
async def decliner_la_course(
    commande_id: uuid.UUID,
    utilisateur: Utilisateur = Depends(livreur_connecte),
    session: AsyncSession = Depends(obtenir_session),
    redis: Redis = Depends(obtenir_redis),
):
    """Le livreur refuse la course : elle est aussitôt proposée à un autre livreur."""
    await courses.repondre_a_la_proposition(session, redis, utilisateur.id, commande_id, accepte=False)
    return ReponseCourseSortie(commande_id=commande_id, reponse="declinee")
