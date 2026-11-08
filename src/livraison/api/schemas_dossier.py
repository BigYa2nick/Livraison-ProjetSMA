"""Formats des requêtes et réponses du dossier livreur (F-03)."""

import uuid

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from livraison.services.dossier import Dossier


class PieceSortie(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    type: str
    statut: str
    motif_refus: str | None
    type_mime: str | None
    taille_octets: int | None
    depose_le: AwareDatetime = Field(validation_alias="cree_le")
    examine_le: AwareDatetime | None


class DossierSortie(BaseModel):
    livreur_id: uuid.UUID
    nom: str | None
    prenom: str | None
    type_vehicule: str
    capacite_kg: int
    immatriculation: str | None
    statut_validation: str
    motif_rejet: str | None
    valide_le: AwareDatetime | None
    inscrit_le: AwareDatetime
    pieces_requises: list[str]
    pieces_manquantes: list[str]
    complet: bool
    pieces: list[PieceSortie]


def dossier_sortie(dossier: Dossier) -> DossierSortie:
    livreur, utilisateur = dossier.livreur, dossier.utilisateur
    return DossierSortie(
        livreur_id=livreur.utilisateur_id,
        nom=utilisateur.nom,
        prenom=utilisateur.prenom,
        type_vehicule=livreur.type_vehicule.value,
        capacite_kg=livreur.capacite_kg,
        immatriculation=livreur.immatriculation,
        statut_validation=livreur.statut_validation.value,
        motif_rejet=livreur.motif_rejet,
        valide_le=livreur.valide_le,
        inscrit_le=livreur.cree_le,
        pieces_requises=[t.value for t in dossier.requises],
        pieces_manquantes=[t.value for t in dossier.pieces_manquantes],
        complet=dossier.complet,
        pieces=[PieceSortie.model_validate(d) for d in dossier.documents],
    )


class DecisionPiece(BaseModel):
    accepte: bool
    motif: str | None = Field(
        default=None, max_length=300, description="Obligatoire quand la pièce est refusée."
    )


class MotifEntree(BaseModel):
    motif: str = Field(min_length=3, max_length=300, examples=["Photo floue, à refaire."])
