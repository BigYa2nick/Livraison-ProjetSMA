"""Formats des requêtes et réponses de l'authentification."""

import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class DemandeCode(BaseModel):
    telephone: str = Field(examples=["07 08 09 10 11"])


class VerificationCode(BaseModel):
    telephone: str = Field(examples=["07 08 09 10 11"])
    code: str = Field(pattern=r"^\d{6}$", examples=["123456"])
    # Utilisé seulement à la première connexion (création du compte).
    role: Literal["client", "livreur"] = "client"


class ConnexionMotDePasse(BaseModel):
    telephone: str
    mot_de_passe: str = Field(min_length=1, max_length=200)


class DemandeRafraichissement(BaseModel):
    refresh_token: str


class JetonsSortie(BaseModel):
    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int = Field(description="Durée de validité du jeton d'accès, en secondes")
    nouveau_compte: bool = False


class UtilisateurSortie(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    telephone: str
    nom: str | None
    prenom: str | None
    role: str


class MiseAJourProfil(BaseModel):
    nom: str | None = Field(default=None, min_length=1, max_length=100)
    prenom: str | None = Field(default=None, min_length=1, max_length=100)


class Message(BaseModel):
    message: str
