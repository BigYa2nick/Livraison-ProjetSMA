"""Couche de persistance : modèles SQLAlchemy, énumérations et sessions de base de données."""

from livraison.persistance import modeles  # noqa: F401  (enregistre les tables dans Base.metadata)
from livraison.persistance.base import Base

__all__ = ["Base"]