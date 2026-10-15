"""Dépendances FastAPI : utilisateur connecté et contrôle des rôles (F-02)."""

from collections.abc import Callable

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from livraison.persistance.enums import Role
from livraison.persistance.modeles import Utilisateur
from livraison.persistance.session import obtenir_session
from livraison.services import auth
from livraison.services.auth import ErreurAuthentification

schema_bearer = HTTPBearer(auto_error=False, description="Jeton d'accès obtenu via /auth/verifier")


async def utilisateur_courant(
    identifiants: HTTPAuthorizationCredentials | None = Depends(schema_bearer),
    session: AsyncSession = Depends(obtenir_session),
) -> Utilisateur:
    if identifiants is None:
        raise ErreurAuthentification(401, "Authentification requise.")
    return await auth.utilisateur_depuis_jeton(session, identifiants.credentials, "acces")


def exiger_roles(*roles: Role) -> Callable[..., Utilisateur]:
    """Dépendance qui n'accepte que les rôles indiqués. Exemple : Depends(exiger_roles(Role.ADMINISTRATEUR))."""

    async def verifier(utilisateur: Utilisateur = Depends(utilisateur_courant)) -> Utilisateur:
        if utilisateur.role not in roles:
            raise ErreurAuthentification(403, "Droits insuffisants pour cette action.")
        return utilisateur

    return verifier
