"""Course proposée à un livreur : ce qu'il voit, et sa réponse dans les 30 secondes (F-34).

Quand le Dispatcher retient un livreur, l'offre est « retenue » en base (D-04) : c'est elle qui dit
au livreur qu'une course l'attend, tant que le délai de confirmation n'est pas passé. L'API ne parle
jamais aux agents (D-02, D-03) : la réponse du livreur est **annoncée** par événement Redis, que le
gestionnaire d'agents transmet à son agent.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from livraison.config import parametres
from livraison.persistance.enums import EtatCommande, ResultatOffre
from livraison.persistance.modeles import Commande, Offre
from livraison.services import evenements
from livraison.services.erreurs import ErreurMetier


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
