"""Routes d'authentification : /auth/..."""

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from livraison.api.dependances import utilisateur_courant
from livraison.api.schemas_auth import (
    ConnexionMotDePasse,
    DemandeCode,
    DemandeRafraichissement,
    JetonsSortie,
    Message,
    MiseAJourProfil,
    UtilisateurSortie,
    VerificationCode,
)
from livraison.persistance.enums import Role
from livraison.persistance.modeles import Utilisateur
from livraison.persistance.session import obtenir_session
from livraison.services import auth
from livraison.services.auth import Jetons
from livraison.services.sms import EnvoiSms, obtenir_envoi_sms

routeur = APIRouter(prefix="/auth", tags=["Authentification"])


def _sortie(jetons: Jetons, nouveau_compte: bool = False) -> JetonsSortie:
    return JetonsSortie(
        access_token=jetons.acces,
        refresh_token=jetons.rafraichissement,
        expires_in=jetons.expire_dans_s,
        nouveau_compte=nouveau_compte,
    )


@routeur.post("/code", response_model=Message, status_code=status.HTTP_202_ACCEPTED)
async def demander_code(
    demande: DemandeCode,
    session: AsyncSession = Depends(obtenir_session),
    envoi: EnvoiSms = Depends(obtenir_envoi_sms),
) -> Message:
    """Envoie un code de vérification par SMS (F-01). La réponse est la même pour tout numéro."""
    await auth.demander_code(session, envoi, demande.telephone)
    return Message(message="Si le numéro est valide, un code de vérification a été envoyé.")


@routeur.post("/verifier", response_model=JetonsSortie)
async def verifier_code(
    demande: VerificationCode, session: AsyncSession = Depends(obtenir_session)
) -> JetonsSortie:
    """Valide le code, crée le compte à la première connexion et renvoie les jetons."""
    resultat = await auth.verifier_code(
        session, demande.telephone, demande.code, Role(demande.role)
    )
    return _sortie(resultat.jetons, resultat.nouveau_compte)


@routeur.post("/connexion", response_model=JetonsSortie)
async def connexion(
    demande: ConnexionMotDePasse, session: AsyncSession = Depends(obtenir_session)
) -> JetonsSortie:
    """Connexion par mot de passe, réservée aux répartiteurs et aux administrateurs."""
    resultat = await auth.connexion_mot_de_passe(session, demande.telephone, demande.mot_de_passe)
    return _sortie(resultat.jetons)


@routeur.post("/rafraichir", response_model=JetonsSortie)
async def rafraichir(
    demande: DemandeRafraichissement, session: AsyncSession = Depends(obtenir_session)
) -> JetonsSortie:
    """Échange un jeton de rafraîchissement contre une nouvelle paire de jetons."""
    return _sortie(await auth.rafraichir(session, demande.refresh_token))


@routeur.get("/moi", response_model=UtilisateurSortie)
async def moi(utilisateur: Utilisateur = Depends(utilisateur_courant)) -> Utilisateur:
    """Profil de l'utilisateur connecté."""
    return utilisateur


@routeur.patch("/moi", response_model=UtilisateurSortie)
async def modifier_profil(
    profil: MiseAJourProfil,
    utilisateur: Utilisateur = Depends(utilisateur_courant),
    session: AsyncSession = Depends(obtenir_session),
) -> Utilisateur:
    """Complète le profil (nom, prénom) après la première connexion."""
    if profil.nom is not None:
        utilisateur.nom = profil.nom
    if profil.prenom is not None:
        utilisateur.prenom = profil.prenom
    await session.flush()
    await session.refresh(utilisateur)
    return utilisateur
