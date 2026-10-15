"""Authentification : inscription et connexion par code SMS, connexion par mot de passe, jetons.

Règles (F-01, F-02, NF-05) :
- clients et livreurs se connectent avec un code SMS à 6 chiffres, valable 5 minutes, utilisable
  une seule fois, limité à 5 essais ; au plus 3 codes demandés par fenêtre de 10 minutes ;
- répartiteurs et administrateurs se connectent avec un mot de passe (pas de SMS : un numéro
  détourné ne doit pas suffire pour accéder à des droits étendus) ; 5 échecs verrouillent le
  compte 15 minutes ;
- les réponses d'erreur ne révèlent jamais si un numéro est inscrit.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from livraison.config import parametres
from livraison.modele.telephone import TelephoneInvalide, normaliser_telephone
from livraison.persistance.enums import EtatCompte, Role
from livraison.persistance.modeles import CodeVerification, Utilisateur
from livraison.services import securite
from livraison.services.sms import EnvoiSms

ROLES_INSCRIPTION_PUBLIQUE = (Role.CLIENT, Role.LIVREUR)
ROLES_MOT_DE_PASSE = (Role.REPARTITEUR, Role.ADMINISTRATEUR)


class ErreurAuthentification(Exception):
    """Erreur d'authentification, avec le code HTTP à renvoyer."""

    def __init__(self, statut: int, detail: str):
        super().__init__(detail)
        self.statut = statut
        self.detail = detail


@dataclass
class Jetons:
    acces: str
    rafraichissement: str
    expire_dans_s: int


@dataclass
class ResultatConnexion:
    utilisateur: Utilisateur
    jetons: Jetons
    nouveau_compte: bool = False


def _maintenant() -> datetime:
    return datetime.now(UTC)


def _normaliser(telephone: str) -> str:
    return normaliser_telephone(telephone, parametres.indicatif_pays_defaut)


def emettre_jetons(utilisateur: Utilisateur) -> Jetons:
    role = utilisateur.role.value
    return Jetons(
        acces=securite.creer_jeton(utilisateur.id, role, "acces", securite.duree_acces()),
        rafraichissement=securite.creer_jeton(
            utilisateur.id, role, "rafraichissement", securite.duree_rafraichissement()
        ),
        expire_dans_s=int(securite.duree_acces().total_seconds()),
    )


async def _utilisateur_par_telephone(
    session: AsyncSession, telephone: str, *, verrouiller: bool = False
) -> Utilisateur | None:
    requete = select(Utilisateur).where(Utilisateur.telephone == telephone)
    if verrouiller:
        requete = requete.with_for_update()
    return await session.scalar(requete)


# ------------------------------------------------------------------------- code SMS


async def demander_code(session: AsyncSession, envoi: EnvoiSms, telephone: str) -> None:
    """Génère et envoie un code. Même réponse que le numéro soit inscrit ou non."""
    tel = _normaliser(telephone)
    maintenant = _maintenant()

    depuis = maintenant - timedelta(seconds=parametres.codes_fenetre_secondes)
    nb_recents = await session.scalar(
        select(func.count())
        .select_from(CodeVerification)
        .where(CodeVerification.telephone == tel, CodeVerification.cree_le >= depuis)
    )
    if (nb_recents or 0) >= parametres.codes_max_par_fenetre:
        raise ErreurAuthentification(
            429, "Trop de demandes de code. Réessayez dans quelques minutes."
        )

    utilisateur = await _utilisateur_par_telephone(session, tel)
    if utilisateur is not None and utilisateur.role in ROLES_MOT_DE_PASSE:
        return  # le personnel n'utilise pas le SMS ; aucune différence visible pour l'appelant

    code = securite.generer_code()
    session.add(
        CodeVerification(
            telephone=tel,
            code_hash=securite.hacher_code(tel, code),
            cree_le=maintenant,  # explicite : now() de PostgreSQL est figé pendant une transaction
            expire_le=maintenant + timedelta(seconds=parametres.code_validite_secondes),
        )
    )
    await session.flush()
    await envoi.envoyer_code(tel, code)


async def verifier_code(
    session: AsyncSession, telephone: str, code: str, role_inscription: Role = Role.CLIENT
) -> ResultatConnexion:
    """Vérifie le code ; crée le compte au premier passage ; renvoie les jetons."""
    if role_inscription not in ROLES_INSCRIPTION_PUBLIQUE:
        raise ErreurAuthentification(400, "Rôle d'inscription non autorisé.")
    tel = _normaliser(telephone)
    maintenant = _maintenant()

    # Seul le code le plus récent et encore valide compte. Verrou de ligne : deux essais
    # simultanés ne peuvent pas dépasser la limite de tentatives.
    ligne = await session.scalar(
        select(CodeVerification)
        .where(
            CodeVerification.telephone == tel,
            CodeVerification.utilise_le.is_(None),
            CodeVerification.expire_le > maintenant,
        )
        .order_by(CodeVerification.cree_le.desc())
        .limit(1)
        .with_for_update()
    )
    if ligne is None:
        raise ErreurAuthentification(401, "Code invalide ou expiré.")
    if ligne.tentatives >= parametres.code_tentatives_max:
        raise ErreurAuthentification(
            429, "Trop de tentatives incorrectes. Demandez un nouveau code."
        )
    if not securite.code_correct(ligne.code_hash, tel, code):
        ligne.tentatives += 1
        await session.commit()  # l'échec doit être enregistré malgré l'erreur renvoyée
        raise ErreurAuthentification(401, "Code invalide ou expiré.")

    utilisateur = await _utilisateur_par_telephone(session, tel, verrouiller=True)
    nouveau = utilisateur is None
    if utilisateur is None:
        utilisateur = Utilisateur(telephone=tel, role=role_inscription, etat=EtatCompte.ACTIF)
        session.add(utilisateur)
    elif utilisateur.role in ROLES_MOT_DE_PASSE:
        raise ErreurAuthentification(403, "Ce compte se connecte avec un mot de passe.")
    if utilisateur.etat != EtatCompte.ACTIF:
        raise ErreurAuthentification(403, "Compte suspendu ou supprimé.")

    ligne.utilise_le = maintenant
    utilisateur.derniere_connexion_le = maintenant
    await session.flush()
    return ResultatConnexion(utilisateur, emettre_jetons(utilisateur), nouveau_compte=nouveau)


# --------------------------------------------------------------------- mot de passe


async def connexion_mot_de_passe(
    session: AsyncSession, telephone: str, mot_de_passe: str
) -> ResultatConnexion:
    """Connexion du personnel (répartiteurs, administrateurs)."""
    identifiants_invalides = ErreurAuthentification(401, "Identifiants invalides.")
    try:
        tel = _normaliser(telephone)
    except TelephoneInvalide:
        securite.verifier_mot_de_passe(mot_de_passe, securite.HACHE_FACTICE)
        raise identifiants_invalides from None

    utilisateur = await _utilisateur_par_telephone(session, tel, verrouiller=True)
    if (
        utilisateur is None
        or utilisateur.role not in ROLES_MOT_DE_PASSE
        or not utilisateur.mot_de_passe_hash
    ):
        securite.verifier_mot_de_passe(mot_de_passe, securite.HACHE_FACTICE)
        raise identifiants_invalides

    maintenant = _maintenant()
    if utilisateur.verrouille_jusqu_le and utilisateur.verrouille_jusqu_le > maintenant:
        raise ErreurAuthentification(
            429, "Compte temporairement verrouillé après plusieurs échecs. Réessayez plus tard."
        )

    if not securite.verifier_mot_de_passe(mot_de_passe, utilisateur.mot_de_passe_hash):
        utilisateur.echecs_connexion += 1
        if utilisateur.echecs_connexion >= parametres.connexion_tentatives_max:
            utilisateur.verrouille_jusqu_le = maintenant + timedelta(
                seconds=parametres.connexion_verrouillage_secondes
            )
            utilisateur.echecs_connexion = 0
        await session.commit()  # l'échec doit être enregistré malgré l'erreur renvoyée
        raise identifiants_invalides

    if utilisateur.etat != EtatCompte.ACTIF:
        raise ErreurAuthentification(403, "Compte suspendu ou supprimé.")

    utilisateur.echecs_connexion = 0
    utilisateur.verrouille_jusqu_le = None
    utilisateur.derniere_connexion_le = maintenant
    await session.flush()
    return ResultatConnexion(utilisateur, emettre_jetons(utilisateur))


# ------------------------------------------------------------------------- jetons


async def utilisateur_depuis_jeton(
    session: AsyncSession, jeton: str, type_attendu: str
) -> Utilisateur:
    """Retrouve l'utilisateur d'un jeton valide. Un compte suspendu perd l'accès immédiatement."""
    try:
        contenu = securite.decoder_jeton(jeton, type_attendu)
        utilisateur_id = uuid.UUID(contenu["sub"])
    except (securite.ErreurJeton, ValueError):
        raise ErreurAuthentification(401, "Jeton invalide ou expiré.") from None
    utilisateur = await session.get(Utilisateur, utilisateur_id)
    if utilisateur is None or utilisateur.etat != EtatCompte.ACTIF:
        raise ErreurAuthentification(401, "Jeton invalide ou expiré.")
    return utilisateur


async def rafraichir(session: AsyncSession, jeton_rafraichissement: str) -> Jetons:
    utilisateur = await utilisateur_depuis_jeton(
        session, jeton_rafraichissement, "rafraichissement"
    )
    return emettre_jetons(utilisateur)
