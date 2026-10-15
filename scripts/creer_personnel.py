"""Crée (ou met à jour) un compte répartiteur ou administrateur.

Ces rôles ne s'obtiennent pas par l'inscription publique. Exemple :
    python scripts/creer_personnel.py --telephone 0708091011 --role administrateur --nom Kouassi

Le mot de passe est demandé au clavier (il ne s'affiche pas et n'est jamais enregistré en clair).
"""

import argparse
import asyncio
import getpass
import sys

from sqlalchemy import select

from livraison.config import parametres
from livraison.modele.telephone import TelephoneInvalide, normaliser_telephone
from livraison.persistance.enums import EtatCompte, Role
from livraison.persistance.modeles import Utilisateur
from livraison.persistance.session import FabriqueSession, moteur
from livraison.services import securite


async def creer(telephone: str, role: Role, nom: str | None, prenom: str | None, mdp: str) -> str:
    async with FabriqueSession() as session:
        utilisateur = await session.scalar(
            select(Utilisateur).where(Utilisateur.telephone == telephone)
        )
        if utilisateur is None:
            utilisateur = Utilisateur(telephone=telephone, role=role, etat=EtatCompte.ACTIF)
            session.add(utilisateur)
            action = "créé"
        elif utilisateur.role in (Role.CLIENT, Role.LIVREUR):
            raise SystemExit(
                "Ce numéro appartient déjà à un compte client ou livreur : utilisez un autre numéro."
            )
        else:
            utilisateur.role = role
            action = "mis à jour"
        utilisateur.mot_de_passe_hash = securite.hacher_mot_de_passe(mdp)
        utilisateur.echecs_connexion = 0
        utilisateur.verrouille_jusqu_le = None
        if nom:
            utilisateur.nom = nom
        if prenom:
            utilisateur.prenom = prenom
        await session.commit()
    await moteur.dispose()
    return action


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--telephone", required=True)
    parser.add_argument("--role", required=True, choices=["repartiteur", "administrateur"])
    parser.add_argument("--nom")
    parser.add_argument("--prenom")
    args = parser.parse_args()

    try:
        telephone = normaliser_telephone(args.telephone, parametres.indicatif_pays_defaut)
    except TelephoneInvalide as e:
        sys.exit(str(e))

    mdp = getpass.getpass("Mot de passe : ")
    if mdp != getpass.getpass("Confirmer le mot de passe : "):
        sys.exit("Les deux mots de passe sont différents.")
    try:
        securite.valider_mot_de_passe(mdp)
    except ValueError as e:
        sys.exit(str(e))

    action = asyncio.run(creer(telephone, Role(args.role), args.nom, args.prenom, mdp))
    print(f"Compte {args.role} {action} : {telephone}")


if __name__ == "__main__":
    main()
