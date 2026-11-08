"""Attribution d'une commande : tout ce que le Contract Net écrit et lit dans la base.

La base est la seule source de vérité (décision D-04) : l'état d'une commande, d'une offre et la charge
d'un livreur sont écrits ici **avant** d'être annoncés. Les agents (Dispatcher, Commande) n'ont aucun SQL
à eux : ils appellent ces fonctions, qui se testent sans réseau (tests/test_attribution_service.py).

Chaque fonction travaille dans la transaction de la session reçue et ne valide pas (`commit`) : c'est
à l'appelant de le faire, une fois toutes les écritures d'une étape terminées.
"""

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from geoalchemy2 import Geometry
from sqlalchemy import cast, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from livraison.config import parametres
from livraison.modele import attribution as regles
from livraison.persistance.enums import EtatCommande, ResultatOffre, TypeEvenement
from livraison.persistance.modeles import Commande, Evenement, Livreur, Offre
from livraison.services import dossier

ETATS_A_ATTRIBUER = (EtatCommande.VALIDEE, EtatCommande.RECHERCHE_LIVREUR)
ETATS_EN_COURS = (EtatCommande.ATTRIBUEE, EtatCommande.ENLEVEE, EtatCommande.EN_LIVRAISON)


class AttributionImpossible(Exception):
    """La commande ne peut pas (ou plus) être attribuée dans son état actuel."""


class ToursEpuises(AttributionImpossible):
    """Tous les tours autorisés ont été faits : le répartiteur doit reprendre la main (F-62)."""

    def __init__(self, tours: int):
        super().__init__(f"{tours} tours déjà faits")
        self.tours = tours


@dataclass(frozen=True)
class CommandeAAttribuer:
    """Ce que le Dispatcher doit savoir d'une commande pour lancer l'appel d'offres."""

    commande_id: str
    etat: EtatCommande
    priorite: str
    taille: str
    poids_kg: float | None
    enlevement: tuple[float, float]  # (latitude, longitude)
    livraison: tuple[float, float]
    tentatives: int
    rayon_m: int | None


@dataclass(frozen=True)
class TourPrepare:
    tour: int
    rayon_m: int


@dataclass(frozen=True)
class EntreeJournal:
    """Un message entre agents à ajouter au journal (F-63, équivalent du Sniffer de JADE)."""

    auteur: str
    destinataire: str | None
    performative: str
    detail: dict[str, Any]


def _uuid(valeur: str | uuid.UUID) -> uuid.UUID:
    return valeur if isinstance(valeur, uuid.UUID) else uuid.UUID(valeur)


def _uuids_valides(valeurs: Iterable[str]) -> list[uuid.UUID]:
    """Les identifiants qui sont de vrais UUID ; les autres (agents de test, etc.) n'existent pas en base."""
    resultat = []
    for valeur in valeurs:
        try:
            resultat.append(uuid.UUID(valeur))
        except ValueError:
            continue
    return resultat


def _maintenant() -> datetime:
    return datetime.now(UTC)


# -- lecture d'une commande ------------------------------------------------------------------------


async def lire_commande(session: AsyncSession, commande_id: str) -> CommandeAAttribuer | None:
    enlevement = cast(Commande.enlevement, Geometry)
    livraison = cast(Commande.livraison, Geometry)
    ligne = (
        await session.execute(
            select(
                Commande.etat,
                Commande.priorite,
                Commande.taille_colis,
                Commande.poids_kg,
                Commande.tentatives_attribution,
                Commande.rayon_recherche_m,
                func.ST_Y(enlevement),
                func.ST_X(enlevement),
                func.ST_Y(livraison),
                func.ST_X(livraison),
            ).where(Commande.id == _uuid(commande_id))
        )
    ).first()
    if ligne is None:
        return None
    etat, priorite, taille, poids, tentatives, rayon, lat_e, lon_e, lat_l, lon_l = ligne
    return CommandeAAttribuer(
        commande_id=str(_uuid(commande_id)),
        etat=EtatCommande(etat),
        priorite=getattr(priorite, "value", priorite),
        taille=getattr(taille, "value", taille),
        poids_kg=None if poids is None else float(poids),
        enlevement=(float(lat_e), float(lon_e)),
        livraison=(float(lat_l), float(lon_l)),
        tentatives=tentatives,
        rayon_m=rayon,
    )


async def lire_commandes_a_attribuer(session: AsyncSession) -> list[str]:
    """Commandes qui attendent un livreur et dont les tours ne sont pas épuisés (réconciliation)."""
    lignes = await session.execute(
        select(Commande.id)
        .where(
            Commande.etat.in_(ETATS_A_ATTRIBUER),
            Commande.tentatives_attribution < parametres.attribution_tours_max,
        )
        .order_by(Commande.cree_le)
    )
    return [str(i) for (i,) in lignes.all()]


# -- préparation d'un tour (agent Commande) -----------------------------------------------------------


async def preparer_tour(session: AsyncSession, commande_id: str) -> TourPrepare:
    """Ouvre le tour suivant : numéro, rayon de recherche, état « recherche_livreur ».

    Tout est écrit en base (`tentatives_attribution`, `rayon_recherche_m`) : après un redémarrage, le
    tour et le rayon se retrouvent (D-04). Le rayon grandit après un tour sans livreur retenu.
    """
    id_commande = _uuid(commande_id)
    commande = (
        await session.execute(select(Commande).where(Commande.id == id_commande).with_for_update())
    ).scalar_one_or_none()
    if commande is None:
        raise AttributionImpossible(f"commande {commande_id} introuvable")
    if commande.etat not in ETATS_A_ATTRIBUER:
        raise AttributionImpossible(f"commande à l'état « {commande.etat} » : plus à attribuer")
    if regles.tours_epuises(commande.tentatives_attribution, parametres.attribution_tours_max):
        raise ToursEpuises(commande.tentatives_attribution)

    tour = commande.tentatives_attribution + 1
    vide = True
    if tour > 1:
        selectionnee = await session.scalar(
            select(func.count())
            .select_from(Offre)
            .where(
                Offre.commande_id == id_commande,
                Offre.tour == tour - 1,
                Offre.resultat.in_((ResultatOffre.RETENUE, ResultatOffre.DECLINEE)),
            )
        )
        vide = not selectionnee
    rayon = regles.rayon_du_tour(
        commande.rayon_recherche_m,
        tour_precedent_vide=vide,
        initial=parametres.attribution_rayon_initial_m,
        facteur=parametres.attribution_facteur_rayon,
        maximum=parametres.attribution_rayon_max_m,
    )
    ancien_etat = commande.etat
    commande.tentatives_attribution = tour
    commande.rayon_recherche_m = rayon
    commande.etat = EtatCommande.RECHERCHE_LIVREUR
    if ancien_etat != EtatCommande.RECHERCHE_LIVREUR:
        _noter_changement_etat(session, id_commande, ancien_etat, EtatCommande.RECHERCHE_LIVREUR)
    await session.flush()
    return TourPrepare(tour, rayon)


# -- candidats ---------------------------------------------------------------------------------------


async def livreurs_exclus(session: AsyncSession, commande_id: str) -> set[str]:
    """Livreurs qui ont décliné cette commande (ou laissé passer le délai) : on ne les redemande pas."""
    lignes = await session.execute(
        select(Offre.livreur_id).where(
            Offre.commande_id == _uuid(commande_id), Offre.resultat == ResultatOffre.DECLINEE
        )
    )
    return {str(i) for (i,) in lignes.all()}


async def livreurs_eligibles(session: AsyncSession, livreur_ids: Iterable[str]) -> list[str]:
    """Parmi les candidats de l'Annuaire, ceux que la base déclare en ligne et autorisés à livrer.

    L'Annuaire n'est pas une source de vérité (D-04) ; l'autorisation vient du dossier du livreur
    (`dossier.peut_recevoir_des_courses`). L'ordre reçu est conservé.
    """
    demandes = list(livreur_ids)
    ids = _uuids_valides(demandes)
    if not ids:
        return []
    livreurs = (
        (await session.execute(select(Livreur).where(Livreur.utilisateur_id.in_(ids), Livreur.en_ligne)))
        .scalars()
        .all()
    )
    autorises = {str(x.utilisateur_id) for x in livreurs if dossier.peut_recevoir_des_courses(x)}
    return [i for i in demandes if i in autorises]


async def charge_en_base(
    session: AsyncSession, livreur_id: str, *, maintenant: datetime | None = None
) -> int:
    """Courses que le livreur porte déjà, ou qu'on est en train de lui faire confirmer.

    = commandes attribuées, enlevées ou en livraison
    + offres retenues dont la confirmation est encore possible (délai + marge de confirmation).
    Une offre retenue plus ancienne est considérée abandonnée : elle ne bloque pas le livreur.
    """
    id_livreur = _uuid(livreur_id)
    maintenant = maintenant or _maintenant()
    fenetre = timedelta(seconds=parametres.attribution_confirmation_s + parametres.attribution_marge_s)
    en_cours = await session.scalar(
        select(func.count())
        .select_from(Commande)
        .where(Commande.livreur_id == id_livreur, Commande.etat.in_(ETATS_EN_COURS))
    )
    en_attente = await session.scalar(
        select(func.count())
        .select_from(Offre)
        .join(Commande, Commande.id == Offre.commande_id)
        .where(
            Offre.livreur_id == id_livreur,
            Offre.resultat == ResultatOffre.RETENUE,
            Commande.etat == EtatCommande.RECHERCHE_LIVREUR,
            Offre.decide_le > maintenant - fenetre,
        )
    )
    return (en_cours or 0) + (en_attente or 0)


# -- offres ---------------------------------------------------------------------------------------------


async def enregistrer_reponses(
    session: AsyncSession,
    commande_id: str,
    tour: int,
    *,
    propositions: dict[str, tuple[float, int]],
    refus: dict[str, str],
    silencieux: Iterable[str] = (),
) -> None:
    """Écrit les réponses d'un appel d'offres : une ligne `offre` par livreur consulté.

    `propositions` : livreur -> (coût, durée jusqu'à l'enlèvement en secondes) ; `refus` : livreur ->
    motif ; `silencieux` : livreurs qui n'ont pas répondu dans le délai de collecte.
    Rejouer l'écriture du même tour ne duplique rien (contrainte d'unicité).
    """
    id_commande = _uuid(commande_id)
    lignes: list[dict[str, Any]] = []
    for livreur_id, (cout, eta_s) in propositions.items():
        lignes.append(
            {"livreur_id": livreur_id, "cout": cout, "eta_s": eta_s, "resultat": ResultatOffre.PROPOSEE}
        )
    for livreur_id, motif in refus.items():
        lignes.append({"livreur_id": livreur_id, "resultat": ResultatOffre.REFUSEE, "motif_refus": motif})
    for livreur_id in silencieux:
        lignes.append({"livreur_id": livreur_id, "resultat": ResultatOffre.EXPIREE})
    for ligne in lignes:
        ligne["commande_id"] = id_commande
        ligne["tour"] = tour
        ligne["livreur_id"] = _uuid(ligne["livreur_id"])
    if not lignes:
        return
    valeurs = [
        {"cout": None, "eta_s": None, "motif_refus": None, **ligne, "resultat": ligne["resultat"].value}
        for ligne in lignes
    ]
    await session.execute(
        insert(Offre)
        .values(valeurs)
        .on_conflict_do_nothing(index_elements=["commande_id", "tour", "livreur_id"])
    )


async def retenir_offre(session: AsyncSession, commande_id: str, tour: int, livreur_id: str) -> bool:
    """Retient l'offre d'un livreur pour ce tour, si la commande et le livreur le permettent (D-06).

    Garde-fous, dans la transaction de l'appelant :
    - la commande est toujours en recherche de livreur (pas annulée entre-temps) ;
    - le livreur n'est pas saturé : sa charge **en base** reste sous `livreur_charge_max` (F-32). La
      ligne du livreur est verrouillée, donc deux décisions simultanées pour le même livreur se
      suivent au lieu de se doubler ;
    - une seule offre retenue par commande et par tour (index unique de la base).

    Renvoie False si l'un des garde-fous refuse : le Dispatcher essaie alors l'offre suivante.
    """
    id_commande, id_livreur = _uuid(commande_id), _uuid(livreur_id)
    etat = await session.scalar(
        select(Commande.etat).where(Commande.id == id_commande).with_for_update()
    )
    if etat != EtatCommande.RECHERCHE_LIVREUR:
        return False
    await session.execute(select(Livreur.utilisateur_id).where(Livreur.utilisateur_id == id_livreur).with_for_update())
    if await charge_en_base(session, livreur_id) >= parametres.livreur_charge_max:
        return False
    resultat = await session.execute(
        update(Offre)
        .where(
            Offre.commande_id == id_commande,
            Offre.tour == tour,
            Offre.livreur_id == id_livreur,
            Offre.resultat == ResultatOffre.PROPOSEE,
        )
        .values(resultat=ResultatOffre.RETENUE, decide_le=_maintenant())
    )
    return resultat.rowcount == 1


async def rejeter_les_autres(
    session: AsyncSession, commande_id: str, tour: int, gagnant_id: str | None
) -> list[str]:
    """Marque « rejetée » toute offre encore en attente du tour, sauf celle du gagnant.

    Renvoie les livreurs concernés : le Dispatcher leur envoie un REJECT-PROPOSAL.
    """
    condition = [
        Offre.commande_id == _uuid(commande_id),
        Offre.tour == tour,
        Offre.resultat == ResultatOffre.PROPOSEE,
    ]
    if gagnant_id is not None:
        condition.append(Offre.livreur_id != _uuid(gagnant_id))
    lignes = await session.execute(
        update(Offre)
        .where(*condition)
        .values(resultat=ResultatOffre.REJETEE, decide_le=_maintenant())
        .returning(Offre.livreur_id)
    )
    return [str(i) for (i,) in lignes.all()]


async def declarer_non_prise(
    session: AsyncSession, commande_id: str, tour: int, livreur_id: str, motif: str
) -> None:
    """L'offre retenue n'aboutit pas : le livreur humain a décliné ou n'a pas répondu à temps.

    `declinee` et `expiree` donnent une offre « déclinée » (le livreur est exclu de cette commande) ;
    tout autre motif (`sature`, `proposition_inconnue`) donne une offre « refusée » : le livreur
    pourra être sollicité à un autre tour.
    """
    resultat = (
        ResultatOffre.DECLINEE if motif in ("declinee", "expiree") else ResultatOffre.REFUSEE
    )
    await session.execute(
        update(Offre)
        .where(
            Offre.commande_id == _uuid(commande_id),
            Offre.tour == tour,
            Offre.livreur_id == _uuid(livreur_id),
            Offre.resultat == ResultatOffre.RETENUE,
        )
        .values(resultat=resultat, motif_refus=motif, decide_le=_maintenant())
    )


async def confirmer_attribution(
    session: AsyncSession, commande_id: str, tour: int, livreur_id: str
) -> bool:
    """Le livreur a confirmé : la commande lui est attribuée (état `attribuee`).

    Renvoie False si la commande n'est plus en recherche (annulée pendant la confirmation) ou si
    l'offre n'est plus retenue : rien n'est alors modifié.
    """
    id_commande, id_livreur = _uuid(commande_id), _uuid(livreur_id)
    commande = (
        await session.execute(select(Commande).where(Commande.id == id_commande).with_for_update())
    ).scalar_one_or_none()
    if commande is None or commande.etat != EtatCommande.RECHERCHE_LIVREUR:
        return False
    retenue = await session.scalar(
        select(func.count())
        .select_from(Offre)
        .where(
            Offre.commande_id == id_commande,
            Offre.tour == tour,
            Offre.livreur_id == id_livreur,
            Offre.resultat == ResultatOffre.RETENUE,
        )
    )
    if not retenue:
        return False
    commande.livreur_id = id_livreur
    commande.etat = EtatCommande.ATTRIBUEE
    commande.attribuee_le = _maintenant()
    _noter_changement_etat(session, id_commande, EtatCommande.RECHERCHE_LIVREUR, EtatCommande.ATTRIBUEE)
    await session.flush()
    return True


# -- journal -------------------------------------------------------------------------------------------------


def _noter_changement_etat(
    session: AsyncSession, commande_id: uuid.UUID, de: EtatCommande, vers: EtatCommande
) -> None:
    session.add(
        Evenement(
            commande_id=commande_id,
            type=TypeEvenement.CHANGEMENT_ETAT,
            auteur="agents",
            detail={"de": de.value, "vers": vers.value},
        )
    )


async def journaliser_messages(
    session: AsyncSession, commande_id: str, entrees: Iterable[EntreeJournal]
) -> int:
    """Ajoute des messages entre agents au journal de la commande (F-63). Renvoie leur nombre."""
    id_commande = _uuid(commande_id)
    nombre = 0
    for entree in entrees:
        session.add(
            Evenement(
                commande_id=id_commande,
                type=TypeEvenement.MESSAGE_AGENT,
                auteur=entree.auteur[:100],
                destinataire=None if entree.destinataire is None else entree.destinataire[:100],
                performative=entree.performative[:30],
                detail=entree.detail,
            )
        )
        nombre += 1
    if nombre:
        await session.flush()
    return nombre


async def signaler_echec(session: AsyncSession, commande_id: str, tours: int) -> None:
    """Aucun livreur après tous les tours : alerte pour le répartiteur (réattribution manuelle, F-62)."""
    session.add(
        Evenement(
            commande_id=_uuid(commande_id),
            type=TypeEvenement.NOTIFICATION,
            auteur="agents",
            detail={"destinataire": "repartiteur", "motif": "aucun_livreur_trouve", "tours": tours},
        )
    )
    await session.flush()
