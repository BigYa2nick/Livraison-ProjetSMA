"""Valide tous les livreurs de la base de développement (statut_validation = 'valide').

Utile en développement pour permettre aux livreurs de test de recevoir des courses sans
passer par tout le circuit de dépôt et d'examen des pièces justificatives (F-03).
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sqlalchemy import select, update

from livraison.agents import boucle
from livraison.persistance.enums import StatutValidation
from livraison.persistance.modeles import Livreur, Utilisateur
from livraison.persistance.session import FabriqueSession, moteur


async def main():
    boucle.utiliser_boucle_compatible_xmpp()
    async with FabriqueSession() as session:
        # Met à jour tous les livreurs en attente pour les passer à 'valide'
        resultat = await session.execute(
            update(Livreur)
            .where(Livreur.statut_validation != StatutValidation.SUSPENDU)
            .values(statut_validation=StatutValidation.VALIDE)
        )
        await session.commit()
        print(f"Livreurs validés : {resultat.rowcount}")

        # Affiche la liste des livreurs
        livreurs = (
            await session.execute(
                select(Utilisateur.telephone, Utilisateur.prenom, Utilisateur.nom, Livreur.en_ligne, Livreur.statut_validation)
                .join(Utilisateur, Utilisateur.id == Livreur.utilisateur_id)
            )
        ).all()
        for tel, prenom, nom, en_ligne, statut in livreurs:
            print(f"  {tel} ({prenom or ''} {nom or ''}) : en_ligne={en_ligne}, statut={statut.value if hasattr(statut, 'value') else statut}")

    await moteur.dispose()


if __name__ == "__main__":
    asyncio.run(main())
