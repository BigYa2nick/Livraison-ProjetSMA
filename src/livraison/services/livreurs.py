"""Profil du livreur et passage en ligne ou hors ligne (F-03, F-20)."""

import json
import uuid

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from livraison.config import parametres
from livraison.persistance.enums import StatutValidation, TypeVehicule
from livraison.persistance.modeles import Livreur
from livraison.services import evenements
from livraison.services.erreurs import ErreurMetier
from livraison.services.positions import CANAL_POSITIONS, cle_position


async def obtenir_livreur(session: AsyncSession, utilisateur_id: uuid.UUID) -> Livreur | None:
    return await session.get(Livreur, utilisateur_id)


async def livreur_ou_erreur(session: AsyncSession, utilisateur_id: uuid.UUID) -> Livreur:
    livreur = await obtenir_livreur(session, utilisateur_id)
    if livreur is None:
        raise ErreurMetier(409, "Complétez d'abord votre profil de livreur (véhicule, capacité).")
    return livreur


async def definir_profil(
    session: AsyncSession,
    utilisateur_id: uuid.UUID,
    type_vehicule: TypeVehicule,
    capacite_kg: int,
    immatriculation: str | None,
) -> Livreur:
    """Crée le profil livreur, ou le met à jour (le statut de validation n'est pas modifiable ici)."""
    livreur = await obtenir_livreur(session, utilisateur_id)
    if livreur is None:
        livreur = Livreur(
            utilisateur_id=utilisateur_id,
            type_vehicule=type_vehicule,
            capacite_kg=capacite_kg,
            immatriculation=immatriculation,
            statut_validation=StatutValidation.EN_ATTENTE,
            en_ligne=False,
            nb_evaluations=0,
        )
        session.add(livreur)
    else:
        livreur.type_vehicule = type_vehicule
        livreur.capacite_kg = capacite_kg
        livreur.immatriculation = immatriculation
    await session.flush()
    return livreur


async def passer_en_ligne(
    session: AsyncSession, utilisateur_id: uuid.UUID, redis: Redis | None = None
) -> Livreur:
    """Le livreur devient disponible. Avec `redis`, l'événement est annoncé aux agents (F-30)."""
    livreur = await livreur_ou_erreur(session, utilisateur_id)
    if livreur.statut_validation == StatutValidation.SUSPENDU:
        raise ErreurMetier(403, "Votre compte livreur est suspendu.")
    if parametres.livreur_validation_requise and (
        livreur.statut_validation != StatutValidation.VALIDE
    ):
        raise ErreurMetier(403, "Votre dossier doit être validé avant de passer en ligne.")
    livreur.en_ligne = True
    await session.flush()
    await evenements.publier(
        redis,
        evenements.LIVREUR_EN_LIGNE,
        livreur_id=str(utilisateur_id),
        type_vehicule=TypeVehicule(livreur.type_vehicule).value,
        capacite_kg=livreur.capacite_kg,
    )
    return livreur


async def passer_hors_ligne(
    session: AsyncSession, redis: Redis, utilisateur_id: uuid.UUID
) -> Livreur:
    """Hors ligne : la position n'est plus collectée (NF-07) et disparaît de la carte."""
    livreur = await livreur_ou_erreur(session, utilisateur_id)
    livreur.en_ligne = False
    await session.flush()
    await redis.delete(cle_position(utilisateur_id))
    await redis.publish(
        CANAL_POSITIONS,
        json.dumps({"type": "hors_ligne", "livreur_id": str(utilisateur_id)}),
    )
    await evenements.publier(redis, evenements.LIVREUR_HORS_LIGNE, livreur_id=str(utilisateur_id))
    return livreur
