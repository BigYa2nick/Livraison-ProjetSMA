"""Fabriques de données pour les tests de l'attribution : clients, livreurs, commandes."""

import contextlib
import json
import secrets

from sqlalchemy import func

from livraison.persistance.enums import (
    EtatCommande,
    Priorite,
    Role,
    StatutValidation,
    TailleColis,
    TypeVehicule,
)
from livraison.persistance.modeles import Commande, Livreur, Utilisateur

PLATEAU = (5.3236, -4.0197)  # (latitude, longitude)
COCODY = (5.3600, -3.9900)
_telephones_utilises: set[str] = set()


def point_wkt(latitude: float, longitude: float):
    """Point PostGIS : longitude puis latitude."""
    return func.ST_GeogFromText(f"SRID=4326;POINT({longitude} {latitude})")


def _telephone() -> str:
    """Un numéro ivoirien au hasard : un test ne doit jamais dépendre de ce qui reste en base."""
    while True:
        numero = f"+22507{secrets.randbelow(10**8):08d}"
        if numero not in _telephones_utilises:
            _telephones_utilises.add(numero)
            return numero


async def creer_client(session) -> Utilisateur:
    client = Utilisateur(telephone=_telephone(), role=Role.CLIENT, nom="Client", prenom="Test")
    session.add(client)
    await session.flush()
    return client


async def creer_livreur(
    session,
    *,
    vehicule=TypeVehicule.MOTO,
    capacite_kg=25,
    en_ligne=True,
    statut=StatutValidation.VALIDE,
) -> str:
    """Crée un livreur en base et renvoie son identifiant (texte)."""
    utilisateur = Utilisateur(telephone=_telephone(), role=Role.LIVREUR, nom="L", prenom="Test")
    session.add(utilisateur)
    await session.flush()
    session.add(
        Livreur(
            utilisateur_id=utilisateur.id,
            type_vehicule=vehicule,
            capacite_kg=capacite_kg,
            statut_validation=statut,
            en_ligne=en_ligne,
            nb_evaluations=0,
        )
    )
    await session.flush()
    return str(utilisateur.id)


async def creer_commande(
    session,
    client=None,
    *,
    etat=EtatCommande.VALIDEE,
    enlevement=PLATEAU,
    livraison=COCODY,
    poids_kg=2.5,
    priorite=Priorite.NORMALE,
    livreur_id=None,
    **extra,
) -> str:
    """Crée une commande en base et renvoie son identifiant (texte)."""
    client = client or await creer_client(session)
    commande = Commande(
        client_id=client.id,
        etat=etat,
        priorite=priorite,
        adresse_enlevement="Plateau",
        enlevement=point_wkt(*enlevement),
        adresse_livraison="Cocody",
        livraison=point_wkt(*livraison),
        destinataire_nom="Destinataire",
        destinataire_telephone="+2250700000000",
        taille_colis=TailleColis.PETIT,
        poids_kg=poids_kg,
        prix_xof=2000,
        livreur_id=None if livreur_id is None else __import__("uuid").UUID(livreur_id),
        **extra,
    )
    session.add(commande)
    await session.flush()
    return str(commande.id)


class SessionPartagee:
    """Fabrique de sessions pour les agents : toujours la session de test, un seul appel à la fois.

    Les agents ouvrent leur propre session (`async with fabrique() as s`). Dans un test, on leur donne la
    session du test (annulée à la fin) ; le verrou évite que deux agents s'en servent en même temps.
    """

    def __init__(self, session):
        import asyncio

        self.session = session
        self._verrou = asyncio.Lock()

    @contextlib.asynccontextmanager
    async def __call__(self):
        async with self._verrou:
            yield self.session


class FauxRedis:
    """Redis de poche : garde les événements publiés pour que le test les relise."""

    def __init__(self):
        self.publies: list[dict] = []
        self.ferme = False

    async def publish(self, canal, message):
        self.publies.append(json.loads(message))

    async def aclose(self):
        self.ferme = True
