"""Tests du schéma sur une vraie base PostgreSQL/PostGIS.

Prérequis : `docker compose up -d` puis `alembic upgrade head`.
La fixture `session` (tests/conftest.py) annule tout à la fin de chaque test.
"""

import uuid

import pytest
from sqlalchemy import func, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from livraison.persistance.enums import (
    EtatCommande,
    EtatPaiement,
    ModePaiement,
    ResultatOffre,
    Role,
    TypeEvenement,
    TypeVehicule,
)  # fmt: skip
from livraison.persistance.modeles import (
    Commande,
    Evenement,
    Livreur,
    Offre,
    Paiement,
    Tarif,
    Utilisateur,
)  # fmt: skip


def _point(lon: float, lat: float):
    return func.ST_GeogFromText(f"SRID=4326;POINT({lon} {lat})")


def _utilisateur(telephone: str, role: Role) -> Utilisateur:
    return Utilisateur(telephone=telephone, role=role)


async def _creer_client_et_livreur(s: AsyncSession):
    client = _utilisateur("+2250700000001", Role.CLIENT)
    livreur_u = _utilisateur("+2250700000002", Role.LIVREUR)
    s.add_all([client, livreur_u])
    await s.flush()
    livreur = Livreur(utilisateur_id=livreur_u.id, type_vehicule=TypeVehicule.MOTO, capacite_kg=20)
    s.add(livreur)
    await s.flush()
    return client, livreur


def _commande(client_id, **kw) -> Commande:
    return Commande(
        client_id=client_id,
        adresse_enlevement="Plateau",
        enlevement=_point(-4.0197, 5.3236),
        adresse_livraison="Cocody",
        livraison=_point(-3.9870, 5.3480),
        destinataire_nom="Awa",
        destinataire_telephone="+2250700000003",
        prix_xof=1500,
        **kw,
    )


async def test_une_commande_a_ses_valeurs_par_defaut(session):
    client, _ = await _creer_client_et_livreur(session)
    c = _commande(client.id)
    session.add(c)
    await session.flush()
    await session.refresh(c)
    assert c.etat == EtatCommande.CREEE
    assert c.tentatives_attribution == 0
    assert c.cree_le is not None


async def test_telephone_invalide_refuse(session):
    session.add(_utilisateur("0700000000", Role.CLIENT))  # pas au format international
    with pytest.raises(IntegrityError):
        await session.flush()


async def test_telephone_unique(session):
    session.add_all(
        [_utilisateur("+2250700000009", Role.CLIENT), _utilisateur("+2250700000009", Role.CLIENT)]
    )
    with pytest.raises(IntegrityError):
        await session.flush()


async def test_commande_attribuee_sans_livreur_refusee(session):
    client, _ = await _creer_client_et_livreur(session)
    session.add(_commande(client.id, etat=EtatCommande.ATTRIBUEE))
    with pytest.raises(IntegrityError):
        await session.flush()


async def test_commande_attribuee_avec_livreur_acceptee(session):
    client, livreur = await _creer_client_et_livreur(session)
    session.add(
        _commande(client.id, etat=EtatCommande.ATTRIBUEE, livreur_id=livreur.utilisateur_id)
    )
    await session.flush()


async def test_prix_negatif_refuse(session):
    client, _ = await _creer_client_et_livreur(session)
    c = _commande(client.id)
    c.prix_xof = -1
    session.add(c)
    with pytest.raises(IntegrityError):
        await session.flush()


async def test_une_seule_offre_retenue_par_tour(session):
    client, livreur = await _creer_client_et_livreur(session)
    autre = _utilisateur("+2250700000004", Role.LIVREUR)
    session.add(autre)
    await session.flush()
    session.add(Livreur(utilisateur_id=autre.id, type_vehicule=TypeVehicule.VELO, capacite_kg=5))
    c = _commande(client.id)
    session.add(c)
    await session.flush()
    session.add(
        Offre(
            commande_id=c.id,
            livreur_id=livreur.utilisateur_id,
            resultat=ResultatOffre.RETENUE,
            cout=120.0,
        )
    )
    await session.flush()
    session.add(
        Offre(commande_id=c.id, livreur_id=autre.id, resultat=ResultatOffre.RETENUE, cout=150.0)
    )
    with pytest.raises(IntegrityError):
        await session.flush()


async def test_un_seul_paiement_confirme_par_commande(session):
    client, _ = await _creer_client_et_livreur(session)
    c = _commande(client.id)
    session.add(c)
    await session.flush()
    session.add(
        Paiement(
            commande_id=c.id,
            mode=ModePaiement.ESPECES,
            montant_xof=1500,
            etat=EtatPaiement.CONFIRME,
        )
    )
    await session.flush()
    session.add(
        Paiement(
            commande_id=c.id,
            mode=ModePaiement.MOBILE_MONEY,
            montant_xof=1500,
            etat=EtatPaiement.CONFIRME,
        )
    )
    with pytest.raises(IntegrityError):
        await session.flush()


async def test_un_seul_tarif_actif(session):
    # Le test passe que la base contienne déjà un tarif actif ou non : deux de plus échouent forcément.
    for nom in ("A", "B"):
        session.add(Tarif(nom=nom, part_fixe_xof=500, prix_km_xof=100, actif=True))
    with pytest.raises(IntegrityError):
        await session.flush()


async def test_journal_en_ajout_seul(session):
    ev = Evenement(
        type=TypeEvenement.MESSAGE_AGENT,
        auteur="dispatcher@localhost",
        performative="cfp",
        detail={"commande": str(uuid.uuid4())},
    )
    session.add(ev)
    await session.flush()
    with pytest.raises(DBAPIError, match="ajout seul"):
        await session.execute(text("UPDATE evenement SET auteur = 'x' WHERE id = :i"), {"i": ev.id})
