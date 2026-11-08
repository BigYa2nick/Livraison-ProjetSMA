"""Formats des requêtes et réponses du suivi GPS et du profil livreur."""

from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class ProfilLivreurEntree(BaseModel):
    type_vehicule: Literal["moto", "voiture", "velo"]
    capacite_kg: int = Field(gt=0, le=500)
    immatriculation: str | None = Field(default=None, max_length=30)


class ProfilLivreurSortie(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    type_vehicule: str
    capacite_kg: int
    immatriculation: str | None
    statut_validation: str
    en_ligne: bool


class EtatEnLigne(BaseModel):
    en_ligne: bool


class PositionEntree(BaseModel):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    precision_m: float | None = Field(default=None, ge=0)
    # Heure de la mesure sur le téléphone, avec fuseau horaire (ex. 2026-10-15T12:00:00Z).
    mesure_le: AwareDatetime


class PositionAccepteeSortie(BaseModel):
    recu_le: AwareDatetime
    retard_s: float


class PositionSuivi(BaseModel):
    livreur_id: str
    latitude: float
    longitude: float
    precision_m: float | None
    mesure_le: str
    recu_le: str
    age_s: float
    perimee: bool
