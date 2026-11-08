"""Schémas de la course proposée au livreur (F-34)."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel


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
    expire_le: datetime
    secondes_restantes: int


class ReponseCourseSortie(BaseModel):
    """Réponse transmise à l'agent du livreur ; l'attribution elle-même se fait ensuite."""

    commande_id: uuid.UUID
    reponse: Literal["confirmee", "declinee"]
