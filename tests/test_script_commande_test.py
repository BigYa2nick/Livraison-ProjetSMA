import uuid

import pytest
from scripts.creer_commande_test import (
    TELEPHONE_DESTINATAIRE_TEST,
    annuler_commandes_test,
    client_de_test,
)
from sqlalchemy import select

from aides_commandes import creer_commande, creer_livreur
from livraison.persistance.enums import EtatCommande, Role, TypeEvenement
from livraison.persistance.modeles import Commande, Evenement, Utilisateur

pytestmark = pytest.mark.asyncio


async def test_annulation_limitee_aux_commandes_test_inachevees(session):
    client = await client_de_test(session)
    client.role = Role.LIVREUR
    livreur_id = await creer_livreur(session)

    async def commande_test(etat, *, livreur=None):
        commande_id = await creer_commande(
            session,
            client=client,
            etat=etat,
            livreur_id=livreur,
        )
        commande = await session.get(Commande, uuid.UUID(commande_id))
        commande.destinataire_nom = "Destinataire test"
        commande.destinataire_telephone = TELEPHONE_DESTINATAIRE_TEST
        commande.adresse_enlevement = "Point de test départ"
        commande.adresse_livraison = "Point de test arrivée"
        return commande

    a_annuler = [
        await commande_test(EtatCommande.VALIDEE),
        await commande_test(EtatCommande.ENLEVEE, livreur=livreur_id),
    ]
    terminee = await commande_test(EtatCommande.LIVREE)

    autre_client = Utilisateur(
        telephone="+2250700000099",
        role=Role.CLIENT,
        nom="Autre",
        prenom="Client",
    )
    session.add(autre_client)
    await session.flush()
    autre_commande = await creer_commande(
        session,
        client=autre_client,
        etat=EtatCommande.ENLEVEE,
        livreur_id=livreur_id,
    )
    autre_commande_obj = await session.get(Commande, uuid.UUID(autre_commande))
    autre_commande_obj.destinataire_nom = "Destinataire test"
    autre_commande_obj.destinataire_telephone = TELEPHONE_DESTINATAIRE_TEST
    autre_commande_obj.adresse_enlevement = "Point de test départ"
    autre_commande_obj.adresse_livraison = "Point de test arrivée"

    ids = await annuler_commandes_test(session)

    assert set(ids) == {str(commande.id) for commande in a_annuler}
    for commande in a_annuler:
        await session.refresh(commande)
        assert commande.etat == EtatCommande.ANNULEE
        assert commande.terminee_le is not None
    await session.refresh(terminee)
    await session.refresh(autre_commande_obj)
    assert terminee.etat == EtatCommande.LIVREE
    assert autre_commande_obj.etat == EtatCommande.ENLEVEE
    evenements = list(
        await session.scalars(
            select(Evenement).where(
                Evenement.commande_id.in_([commande.id for commande in a_annuler])
            )
        )
    )
    assert len(evenements) == 2
    assert all(e.type == TypeEvenement.CHANGEMENT_ETAT for e in evenements)
    details = {str(e.commande_id): e.detail for e in evenements}
    assert details == {
        str(a_annuler[0].id): {"de": "validee", "vers": EtatCommande.ANNULEE.value},
        str(a_annuler[1].id): {"de": "enlevee", "vers": EtatCommande.ANNULEE.value},
    }
