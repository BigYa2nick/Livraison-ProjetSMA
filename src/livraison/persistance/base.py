"""Socle des modèles : classe de base, conventions de nommage et colonnes réutilisables."""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Annotated

from geoalchemy2 import Geography
from sqlalchemy import DateTime, Enum, MetaData, Uuid, func, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Noms de contraintes prévisibles : indispensable pour que les migrations (Alembic)
# puissent les modifier ou les supprimer proprement plus tard.
CONVENTION_NOMS = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

SRID_WGS84 = 4326  # latitude/longitude GPS


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=CONVENTION_NOMS)


# Types de colonnes réutilisés dans les modèles.
Horodatage = Annotated[datetime, mapped_column(DateTime(timezone=True))]
DateCreation = Annotated[
    datetime, mapped_column(DateTime(timezone=True), server_default=func.now())
]


def cle_uuid() -> Mapped[uuid.UUID]:
    """Clé primaire UUID : non devinable, donc adaptée aux identifiants exposés par l'API."""
    return mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()")
    )


def colonne_enum(enum_cls: type[StrEnum], **kwargs) -> Enum:
    """Colonne texte + contrainte CHECK limitant aux valeurs de l'énumération."""
    return Enum(
        enum_cls,
        native_enum=False,
        length=30,
        create_constraint=True,
        validate_strings=True,
        name=enum_cls.__name__.lower(),
        values_callable=lambda e: [m.value for m in e],
        **kwargs,
    )


def point() -> Geography:
    """Point GPS (longitude, latitude) en WGS 84.

    L'index spatial est déclaré à la main dans __table_args__ de chaque modèle
    (spatial_index=False ici) pour que les migrations Alembic le gèrent normalement.
    """
    return Geography(geometry_type="POINT", srid=SRID_WGS84, spatial_index=False)