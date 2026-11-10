"""Schémas de la course proposée au livreur (F-34)."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from livraison.persistance.enums import MotifIncident


class PropositionCourseSortie(BaseModel):
    """La course à confirmer avant `expire_le`. Aucune donnée personnelle du destinataire."""

    commande_id: uuid.UUID
    tour: int
    adresse_enlevement: str
    adresse_livraison: str
    taille_colis: str
    poids_kg: float | None
    description_colis: str | None
    priorite: str
    distance_m: int | None = None
    gain_estime_xof: int | None = None
    expire_le: datetime
    secondes_restantes: int


class ReponseCourseSortie(BaseModel):
    """Réponse transmise à l'agent du livreur ; l'attribution elle-même se fait ensuite."""

    commande_id: uuid.UUID
    reponse: Literal["confirmee", "declinee"]


class PointGps(BaseModel):
    latitude: float
    longitude: float


class CourseActiveSortie(BaseModel):
    """Détails de la course en cours pour le livreur attribué (F-22, F-23)."""

    commande_id: uuid.UUID
    etat: Literal["attribuee", "enlevee", "en_livraison"]
    priorite: str
    adresse_enlevement: str
    enlevement: PointGps
    adresse_livraison: str
    livraison: PointGps
    destinataire_nom: str
    destinataire_telephone: str
    consignes: str | None = None
    taille_colis: str
    poids_kg: float | None = None
    description_colis: str | None = None
    distance_m: int | None = None
    prix_xof: int
    gain_estime_xof: int | None = None
    attribuee_le: datetime | None = None
    enlevee_le: datetime | None = None


class ValidationLivraisonEntree(BaseModel):
    """Code secret à usage unique (OTP) fourni par le destinataire (F-23)."""

    code_confirmation: str = Field(pattern=r"^\d{6}$")


class SignalementIncidentEntree(BaseModel):
    """Un des motifs d'incident autorisés au livreur (F-24)."""

    motif: MotifIncident


class SignalementIncidentSortie(BaseModel):
    commande_id: uuid.UUID
    motif: MotifIncident
    nouvel_etat: Literal["echec_livraison"]
    horodatage: datetime


class ChangementEtatCourseSortie(BaseModel):
    commande_id: uuid.UUID
    nouvel_etat: str
    horodatage: datetime
