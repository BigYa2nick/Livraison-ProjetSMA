"""Course proposée à un livreur : ce qu'il voit, et sa réponse dans les 30 secondes (F-34).

Quand le Dispatcher retient un livreur, l'offre est « retenue » en base (D-04) : c'est elle qui dit
au livreur qu'une course l'attend, tant que le délai de confirmation n'est pas passé. L'API ne parle
jamais aux agents (D-02, D-03) : la réponse du livreur est **annoncée** par événement Redis, que le
gestionnaire d'agents transmet à son agent.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from geoalchemy2 import Geometry
from redis.asyncio import Redis
from sqlalchemy import cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from livraison.config import parametres
from livraison.persistance.enums import EtatCommande, MotifIncident, ResultatOffre, TypeEvenement
from livraison.persistance.modeles import Commande, Evenement, Offre
from livraison.services import evenements
from livraison.services.erreurs import ErreurMetier
from livraison.services.securite import code_correct


@dataclass(frozen=True)
class PropositionCourse:
    """La course proposée, sans donnée personnelle du destinataire (nom, téléphone)."""

    commande_id: uuid.UUID
    tour: int
    adresse_enlevement: str
    adresse_livraison: str
    taille_colis: str
    poids_kg: float | None
    description_colis: str | None
    priorite: str
    expire_le: datetime
    secondes_restantes: int
    distance_m: int | None = None
    gain_estime_xof: int | None = None


def _requete(livreur_id: uuid.UUID):
    return (
        select(Offre, Commande)
        .join(Commande, Commande.id == Offre.commande_id)
        .where(
            Offre.livreur_id == livreur_id,
            Offre.resultat == ResultatOffre.RETENUE,
            Commande.etat == EtatCommande.RECHERCHE_LIVREUR,
        )
        .order_by(Offre.decide_le.desc())
    )


def _gain_estime(prix_xof: int | None) -> int | None:
    """Rémunération estimée du livreur (80 % du prix facturé avant le module financier du lot 5)."""
    if prix_xof is None or prix_xof <= 0:
        return None
    return int(round(prix_xof * 0.80))


def _proposition(offre: Offre, commande: Commande, maintenant: datetime) -> PropositionCourse:
    expire_le = offre.decide_le + timedelta(seconds=parametres.attribution_confirmation_s)
    return PropositionCourse(
        commande_id=commande.id,
        tour=offre.tour,
        adresse_enlevement=commande.adresse_enlevement,
        adresse_livraison=commande.adresse_livraison,
        taille_colis=getattr(commande.taille_colis, "value", commande.taille_colis),
        poids_kg=None if commande.poids_kg is None else float(commande.poids_kg),
        description_colis=commande.description_colis,
        priorite=getattr(commande.priorite, "value", commande.priorite),
        expire_le=expire_le,
        secondes_restantes=max(0, int((expire_le - maintenant).total_seconds())),
        distance_m=commande.distance_m,
        gain_estime_xof=_gain_estime(commande.prix_xof),
    )


async def proposition_en_cours(
    session: AsyncSession, livreur_id: uuid.UUID, *, maintenant: datetime | None = None
) -> PropositionCourse | None:
    """La course que le livreur doit confirmer, ou None s'il n'en a pas (ou si le délai est passé)."""
    maintenant = maintenant or datetime.now(UTC)
    for offre, commande in (await session.execute(_requete(livreur_id))).all():
        proposition = _proposition(offre, commande, maintenant)
        if proposition.expire_le > maintenant:
            return proposition
    return None


async def repondre_a_la_proposition(
    session: AsyncSession,
    redis: Redis,
    livreur_id: uuid.UUID,
    commande_id: uuid.UUID,
    *,
    accepte: bool,
    maintenant: datetime | None = None,
) -> None:
    """Le livreur confirme ou décline : la réponse part vers son agent (événement Redis)."""
    maintenant = maintenant or datetime.now(UTC)
    trouvee = [
        _proposition(offre, commande, maintenant)
        for offre, commande in (await session.execute(_requete(livreur_id))).all()
        if commande.id == commande_id
    ]
    if not trouvee:
        raise ErreurMetier(404, "Aucune course ne vous est proposée pour cette commande.")
    if trouvee[0].expire_le <= maintenant:
        raise ErreurMetier(410, "Le délai de confirmation est dépassé : la course a été proposée à un autre livreur.")
    type_evenement = evenements.COURSE_CONFIRMEE if accepte else evenements.COURSE_DECLINEE
    parti = await evenements.publier(
        redis, type_evenement, livreur_id=str(livreur_id), commande_id=str(commande_id)
    )
    if not parti:
        raise ErreurMetier(503, "Votre réponse n'a pas pu être transmise. Réessayez dans un instant.")


# ------------------------------------------------------------------ course en cours (F-22, F-23)


ETATS_EN_COURS = (EtatCommande.ATTRIBUEE, EtatCommande.ENLEVEE, EtatCommande.EN_LIVRAISON)


@dataclass(frozen=True)
class CoordonneesGps:
    latitude: float
    longitude: float


@dataclass(frozen=True)
class CourseActive:
    """Détails de la course en cours attribuée au livreur."""

    commande_id: uuid.UUID
    etat: str
    priorite: str
    adresse_enlevement: str
    enlevement: CoordonneesGps
    adresse_livraison: str
    livraison: CoordonneesGps
    destinataire_nom: str
    destinataire_telephone: str
    consignes: str | None
    taille_colis: str
    poids_kg: float | None
    description_colis: str | None
    distance_m: int | None
    prix_xof: int
    gain_estime_xof: int | None
    attribuee_le: datetime | None
    enlevee_le: datetime | None


async def course_active(session: AsyncSession, livreur_id: uuid.UUID) -> CourseActive | None:
    """La course actuellement attribuée ou en cours de livraison pour ce livreur (F-22)."""
    enlevement = cast(Commande.enlevement, Geometry)
    livraison = cast(Commande.livraison, Geometry)
    resultat = await session.execute(
        select(
            Commande,
            func.ST_Y(enlevement),
            func.ST_X(enlevement),
            func.ST_Y(livraison),
            func.ST_X(livraison),
        )
        .where(
            Commande.livreur_id == livreur_id,
            Commande.etat.in_(ETATS_EN_COURS),
        )
        .order_by(Commande.attribuee_le.desc().nullslast(), Commande.cree_le.desc())
    )
    ligne = resultat.first()
    if ligne is None:
        return None
    cmd, lat_e, lon_e, lat_l, lon_l = ligne
    return CourseActive(
        commande_id=cmd.id,
        etat=cmd.etat.value,
        priorite=getattr(cmd.priorite, "value", cmd.priorite),
        adresse_enlevement=cmd.adresse_enlevement,
        enlevement=CoordonneesGps(float(lat_e), float(lon_e)),
        adresse_livraison=cmd.adresse_livraison,
        livraison=CoordonneesGps(float(lat_l), float(lon_l)),
        destinataire_nom=cmd.destinataire_nom,
        destinataire_telephone=cmd.destinataire_telephone,
        consignes=cmd.consignes,
        taille_colis=getattr(cmd.taille_colis, "value", cmd.taille_colis),
        poids_kg=None if cmd.poids_kg is None else float(cmd.poids_kg),
        description_colis=cmd.description_colis,
        distance_m=cmd.distance_m,
        prix_xof=cmd.prix_xof,
        gain_estime_xof=_gain_estime(cmd.prix_xof),
        attribuee_le=cmd.attribuee_le,
        enlevee_le=cmd.enlevee_le,
    )


async def marquer_enlevee(
    session: AsyncSession,
    redis: Redis | None,
    livreur_id: uuid.UUID,
    commande_id: uuid.UUID,
    *,
    maintenant: datetime | None = None,
) -> datetime:
    """Le livreur valide la prise en charge du colis chez l'expéditeur (F-23)."""
    maintenant = maintenant or datetime.now(UTC)
    cmd = await session.get(Commande, commande_id)
    if cmd is None or cmd.livreur_id != livreur_id:
        raise ErreurMetier(404, "Aucune course active trouvée pour cette commande.")
    if cmd.etat != EtatCommande.ATTRIBUEE:
        if cmd.etat in (EtatCommande.ENLEVEE, EtatCommande.EN_LIVRAISON):
            return cmd.enlevee_le or maintenant
        raise ErreurMetier(409, f"Impossible d'enlever un colis dans l'état « {cmd.etat.value} ».")

    cmd.etat = EtatCommande.ENLEVEE
    cmd.enlevee_le = maintenant
    await session.commit()

    await evenements.publier(
        redis,
        evenements.COMMANDE_ENLEVEE,
        commande_id=str(commande_id),
        livreur_id=str(livreur_id),
    )
    return maintenant


async def valider_livraison(
    session: AsyncSession,
    redis: Redis | None,
    livreur_id: uuid.UUID,
    commande_id: uuid.UUID,
    code_confirmation: str,
    *,
    maintenant: datetime | None = None,
) -> datetime:
    """Le livreur valide la remise du colis au destinataire avec le code secret OTP (F-23)."""
    maintenant = maintenant or datetime.now(UTC)
    cmd = await session.get(Commande, commande_id)
    if cmd is None or cmd.livreur_id != livreur_id:
        raise ErreurMetier(404, "Aucune course active trouvée pour cette commande.")
    if cmd.etat not in (EtatCommande.ENLEVEE, EtatCommande.EN_LIVRAISON):
        if cmd.etat == EtatCommande.LIVREE:
            return cmd.livree_le or maintenant
        raise ErreurMetier(
            409,
            f"Le colis doit être enlevé avant d'être livré (état actuel : « {cmd.etat.value} »).",
        )

    if not cmd.code_confirmation_hash:
        raise ErreurMetier(
            409,
            "Aucun code de livraison n'est associé à cette commande. "
            "Recréez la commande avec un code de confirmation.",
        )
    code_nettoye = code_confirmation.strip()
    if not code_correct(cmd.code_confirmation_hash, cmd.destinataire_telephone, code_nettoye):
        raise ErreurMetier(
            400,
            "Code de confirmation erroné. Vérifiez le code de livraison associé à cette commande ; "
            "ce n'est pas le code SMS de connexion.",
        )

    cmd.etat = EtatCommande.LIVREE
    cmd.livree_le = maintenant
    await session.commit()

    await evenements.publier(
        redis,
        evenements.COMMANDE_LIVREE,
        commande_id=str(commande_id),
        livreur_id=str(livreur_id),
    )
    return maintenant


async def signaler_incident(
    session: AsyncSession,
    livreur_id: uuid.UUID,
    commande_id: uuid.UUID,
    motif: MotifIncident,
    *,
    maintenant: datetime | None = None,
) -> datetime:
    """Journalise un incident F-24 et termine la tentative de livraison en échec."""
    maintenant = maintenant or datetime.now(UTC)
    cmd = await session.get(Commande, commande_id)
    if cmd is None or cmd.livreur_id != livreur_id:
        raise ErreurMetier(404, "Aucune course active trouvée pour cette commande.")
    if cmd.etat not in (EtatCommande.ATTRIBUEE, EtatCommande.ENLEVEE, EtatCommande.EN_LIVRAISON):
        raise ErreurMetier(409, f"Impossible de signaler un incident dans l'état « {cmd.etat.value} ».")

    ancien_etat = cmd.etat
    cmd.etat = EtatCommande.ECHEC_LIVRAISON
    cmd.terminee_le = maintenant
    session.add(
        Evenement(
            commande_id=cmd.id,
            type=TypeEvenement.INCIDENT,
            auteur=f"livreur:{livreur_id}",
            detail={
                "motif": motif.value,
                "etat_avant": ancien_etat.value,
                "etat_apres": EtatCommande.ECHEC_LIVRAISON.value,
            },
        )
    )
    await session.commit()
    return maintenant
