"""Réinitialise le mot de passe d'un compte du personnel (répartiteur ou administrateur).

    python scripts/reinitialiser_mot_de_passe.py --telephone 0101010101

Le mot de passe est demandé au clavier (il ne s'affiche pas et n'est pas gardé dans l'historique).
Le compte est aussi déverrouillé : plusieurs mots de passe faux de suite le bloquent un moment.
Seuls les comptes répartiteur et administrateur sont concernés : les clients et les livreurs n'ont pas
de mot de passe, ils se connectent par un code envoyé par SMS.

Si le compte n'existe pas, le créer avec scripts/creer_personnel.py (docs : LANCEMENT.md, § 2.6).
"""

import argparse
import asyncio
import getpass
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

from livraison.persistance.enums import Role  # noqa: E402
from livraison.persistance.modeles import Utilisateur  # noqa: E402
from livraison.persistance.session import FabriqueSession, moteur  # noqa: E402
from livraison.services import securite  # noqa: E402

LONGUEUR_MIN = 8
ROLES_PERSONNEL = (Role.REPARTITEUR, Role.ADMINISTRATEUR)


class ErreurReinitialisation(Exception):
    """Aucune réinitialisation possible : le message dit pourquoi et quoi faire."""


async def reinitialiser(session: AsyncSession, telephone: str, mot_de_passe: str) -> Utilisateur:
    """Change le mot de passe du compte du personnel dont le numéro finit par `telephone`."""
    chiffres = re.sub(r"\D", "", telephone)
    if len(chiffres) < 8:
        raise ErreurReinitialisation("Numéro trop court : donne le numéro complet, par exemple 0101010101.")
    if len(mot_de_passe) < LONGUEUR_MIN:
        raise ErreurReinitialisation(f"Mot de passe trop court : {LONGUEUR_MIN} caractères au moins.")
    comptes = (
        (
            await session.execute(
                select(Utilisateur).where(
                    Utilisateur.telephone.like(f"%{chiffres}"), Utilisateur.role.in_(ROLES_PERSONNEL)
                )
            )
        )
        .scalars()
        .all()
    )
    if not comptes:
        raise ErreurReinitialisation(
            f"Aucun compte répartiteur ou administrateur pour {telephone}. "
            "Le créer : python scripts/creer_personnel.py --telephone ... --role repartiteur --nom ..."
        )
    if len(comptes) > 1:
        numeros = ", ".join(c.telephone for c in comptes)
        raise ErreurReinitialisation(f"Plusieurs comptes correspondent ({numeros}) : donne le numéro complet.")
    compte = comptes[0]
    compte.mot_de_passe_hash = securite.hacher_mot_de_passe(mot_de_passe)
    compte.echecs_connexion = 0
    compte.verrouille_jusqu_le = None
    await session.flush()
    return compte


def demander_mot_de_passe() -> str:
    premier = getpass.getpass("Nouveau mot de passe : ")
    if premier != getpass.getpass("Retape-le : "):
        raise ErreurReinitialisation("Les deux saisies sont différentes : rien n'a été modifié.")
    return premier


async def main() -> int:
    analyseur = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    analyseur.add_argument("--telephone", required=True, help="numéro du compte, ex. 0101010101")
    args = analyseur.parse_args()
    try:
        mot_de_passe = demander_mot_de_passe()
        async with FabriqueSession() as session:
            compte = await reinitialiser(session, args.telephone, mot_de_passe)
            await session.commit()
    except ErreurReinitialisation as e:
        print(f"Échec : {e}")
        return 1
    finally:
        await moteur.dispose()
    print(f"Mot de passe réinitialisé pour {compte.telephone} ({compte.role.value}). Compte déverrouillé.")
    print("Note-le dans ton gestionnaire de mots de passe : il ne peut pas être relu, seulement remplacé.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
