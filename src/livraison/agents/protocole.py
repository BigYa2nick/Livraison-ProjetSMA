"""Langage commun des agents : performatives FIPA-ACL, ontologies et contenu des messages.

Voir docs/conception/03-sequences-protocole.md. Les messages sont des messages SPADE dont le corps
est un objet JSON. Deux métadonnées les identifient :
- `performative` : ce que l'émetteur veut faire (request, inform, query-ref, failure...) ;
- `ontology` : de quoi on parle (inscription, mise à jour, recherche...).

Ce module ne dépend que de SPADE pour construire les messages : tout le reste est du Python simple.

Deux familles de messages : l'Annuaire (inscription, mise à jour, recherche) et le Contract Net
(attribution d'une commande, voir docs/contract-net.md). Chaque message du Contract Net d'une même
commande et d'un même tour partage le fil `<commande_id>:<tour>` (`conversation`).
"""

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

from spade.message import Message

from livraison.config import parametres

# -- performatives FIPA-ACL --------------------------------------------------------------------
REQUEST = "request"
INFORM = "inform"
QUERY_REF = "query-ref"
FAILURE = "failure"
NOT_UNDERSTOOD = "not-understood"
# Contract Net (appel d'offres)
CFP = "cfp"
PROPOSE = "propose"
REFUSE = "refuse"
ACCEPT_PROPOSAL = "accept-proposal"
REJECT_PROPOSAL = "reject-proposal"
CANCEL = "cancel"

# -- ontologies de l'Annuaire --------------------------------------------------------------------
ONTO_INSCRIPTION = "annuaire.inscription"
ONTO_DESINSCRIPTION = "annuaire.desinscription"
ONTO_MAJ = "annuaire.maj"
ONTO_RECHERCHE = "annuaire.recherche"
ONTOLOGIES_ANNUAIRE = (ONTO_INSCRIPTION, ONTO_DESINSCRIPTION, ONTO_MAJ, ONTO_RECHERCHE)

# -- ontologies de l'attribution ---------------------------------------------------------------------
ONTO_ATTRIBUTION = "attribution.demande"  # agent Commande vers Dispatcher : attribuer une commande
ONTO_RESULTAT = "attribution.resultat"  # Dispatcher vers agent Commande : ce qui s'est passé
ONTO_CONTRACT_NET = "contractnet"  # Dispatcher et agents Livreur : CFP, PROPOSE, ACCEPT...

# Motifs de refus d'un agent Livreur (REFUSE) : docs/conception/03-sequences-protocole.md, § 3.2,
# plus « itineraire » (aucune route entre le livreur et l'enlèvement).
SATURE = "sature"
HORS_ZONE = "hors_zone"
TROP_LOURD = "trop_lourd"
GPS_ANCIEN = "gps_ancien"
VEHICULE = "vehicule"
HORS_LIGNE = "hors_ligne"
ITINERAIRE = "itineraire"
MOTIFS_REFUS = (SATURE, HORS_ZONE, TROP_LOURD, GPS_ANCIEN, VEHICULE, HORS_LIGNE, ITINERAIRE)

# Motifs d'un CANCEL (le livreur humain n'a pas pris la course) : « sature » est aussi possible.
DECLINEE = "declinee"
EXPIREE = "expiree"

# Résultats annoncés à l'agent Commande
RESULTAT_ATTRIBUEE = "attribuee"
RESULTAT_REATTRIBUTION = "reattribution"
ECHEC_SANS_OFFRE = "sans_offre"
ECHEC_COMMANDE = "commande_invalide"
ECHEC_ANNUAIRE = "annuaire_muet"
ECHEC_INTERNE = "erreur_interne"

PREFIXE_LIVREUR = "livreur-"
PREFIXE_COMMANDE = "commande-"


class MessageInvalide(ValueError):
    """Le message reçu n'a pas le format attendu."""


# -- adresses --------------------------------------------------------------------------------------


def jid_annuaire() -> str:
    return parametres.jid("annuaire")


def jid_dispatcher() -> str:
    return parametres.jid("dispatcher")


def jid_livreur(livreur_id: str) -> str:
    return parametres.jid(f"{PREFIXE_LIVREUR}{livreur_id}")


def jid_commande(commande_id: str) -> str:
    return parametres.jid(f"{PREFIXE_COMMANDE}{commande_id}")


def livreur_id_depuis_jid(jid: str) -> str | None:
    """`livreur-<id>@domaine` donne `<id>` ; tout autre JID donne None."""
    local = jid.split("@", 1)[0]
    return local.removeprefix(PREFIXE_LIVREUR) if local.startswith(PREFIXE_LIVREUR) else None


def conversation(commande_id: str, tour: int) -> str:
    """Identifiant de conversation d'un appel d'offres : `<commande_id>:<tour>`."""
    return f"{commande_id}:{tour}"


def lire_conversation(thread: str | None) -> tuple[str, int]:
    """`<commande_id>:<tour>` donne (commande_id, tour) ; tout autre fil lève MessageInvalide."""
    commande_id, _, tour = (thread or "").partition(":")
    try:
        uuid.UUID(commande_id)
        numero = int(tour)
    except ValueError as e:
        raise MessageInvalide(f"identifiant de conversation invalide : {thread!r}") from e
    if numero < 1:
        raise MessageInvalide("le tour commence à 1")
    return commande_id, numero


# -- construction et lecture des messages ----------------------------------------------------------


def creer_message(
    destinataire: str,
    performative: str,
    ontologie: str,
    contenu: dict[str, Any] | None = None,
    *,
    thread: str | None = None,
) -> Message:
    message = Message(to=destinataire)
    message.set_metadata("performative", performative)
    message.set_metadata("ontology", ontologie)
    message.set_metadata("language", "json")
    if thread:
        message.thread = thread
    message.body = json.dumps(contenu or {}, ensure_ascii=False)
    return message


def repondre(recu: Message, performative: str, contenu: dict[str, Any] | None = None) -> Message:
    """Réponse à un message reçu : même fil de conversation, même ontologie."""
    reponse = recu.make_reply()
    reponse.set_metadata("performative", performative)
    reponse.set_metadata("ontology", recu.get_metadata("ontology") or "")
    reponse.set_metadata("language", "json")
    reponse.body = json.dumps(contenu or {}, ensure_ascii=False)
    return reponse


def lire_contenu(message: Message) -> dict[str, Any]:
    try:
        contenu = json.loads(message.body or "")
    except json.JSONDecodeError as e:
        raise MessageInvalide(f"corps non JSON : {e.msg}") from e
    if not isinstance(contenu, dict):
        raise MessageInvalide("le corps doit être un objet JSON")
    return contenu


# -- contenus -------------------------------------------------------------------------------------


def _texte(contenu: dict, cle: str) -> str:
    valeur = contenu.get(cle)
    if not isinstance(valeur, str) or not valeur:
        raise MessageInvalide(f"« {cle} » manquant ou invalide")
    return valeur


def _nombre(contenu: dict, cle: str, *, obligatoire: bool = True) -> float | None:
    valeur = contenu.get(cle)
    if valeur is None:
        if obligatoire:
            raise MessageInvalide(f"« {cle} » manquant")
        return None
    if isinstance(valeur, bool) or not isinstance(valeur, int | float):
        raise MessageInvalide(f"« {cle} » doit être un nombre")
    return float(valeur)


def _instant(contenu: dict, cle: str) -> datetime | None:
    valeur = contenu.get(cle)
    if valeur is None:
        return None
    try:
        instant = datetime.fromisoformat(valeur)
    except (TypeError, ValueError) as e:
        raise MessageInvalide(f"« {cle} » n'est pas une date ISO") from e
    if instant.tzinfo is None:
        raise MessageInvalide(f"« {cle} » doit porter un fuseau horaire")
    return instant


def _position(contenu: dict) -> tuple[float, float] | None:
    latitude = _nombre(contenu, "latitude", obligatoire=False)
    longitude = _nombre(contenu, "longitude", obligatoire=False)
    if latitude is None and longitude is None:
        return None
    if latitude is None or longitude is None:
        raise MessageInvalide("latitude et longitude vont ensemble")
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        raise MessageInvalide("coordonnées hors limites")
    return latitude, longitude


@dataclass(frozen=True)
class Inscription:
    """Un agent Livreur se déclare à l'Annuaire (service « livreur »)."""

    livreur_id: str
    type_vehicule: str
    capacite_kg: int
    charge: int = 0
    position: tuple[float, float] | None = None  # (latitude, longitude)
    mesure_le: datetime | None = None

    def vers_contenu(self) -> dict[str, Any]:
        contenu: dict[str, Any] = {
            "livreur_id": self.livreur_id,
            "type_vehicule": self.type_vehicule,
            "capacite_kg": self.capacite_kg,
            "charge": self.charge,
        }
        if self.position is not None:
            contenu["latitude"], contenu["longitude"] = self.position
            contenu["mesure_le"] = self.mesure_le.isoformat() if self.mesure_le else None
        return contenu

    @classmethod
    def depuis_contenu(cls, contenu: dict) -> "Inscription":
        capacite = _nombre(contenu, "capacite_kg")
        charge = _nombre(contenu, "charge", obligatoire=False) or 0
        if capacite <= 0 or charge < 0:
            raise MessageInvalide("capacité ou charge invalide")
        position = _position(contenu)
        mesure = _instant(contenu, "mesure_le")
        if position is not None and mesure is None:
            raise MessageInvalide("« mesure_le » est obligatoire avec une position")
        return cls(
            livreur_id=_texte(contenu, "livreur_id"),
            type_vehicule=_texte(contenu, "type_vehicule"),
            capacite_kg=int(capacite),
            charge=int(charge),
            position=position,
            mesure_le=mesure,
        )


@dataclass(frozen=True)
class MiseAJour:
    """Nouvelle position et/ou nouvelle charge d'un livreur déjà inscrit."""

    livreur_id: str
    position: tuple[float, float] | None = None
    mesure_le: datetime | None = None
    charge: int | None = None

    def vers_contenu(self) -> dict[str, Any]:
        contenu: dict[str, Any] = {"livreur_id": self.livreur_id}
        if self.position is not None:
            contenu["latitude"], contenu["longitude"] = self.position
            contenu["mesure_le"] = self.mesure_le.isoformat() if self.mesure_le else None
        if self.charge is not None:
            contenu["charge"] = self.charge
        return contenu

    @classmethod
    def depuis_contenu(cls, contenu: dict) -> "MiseAJour":
        position = _position(contenu)
        mesure = _instant(contenu, "mesure_le")
        if position is not None and mesure is None:
            raise MessageInvalide("« mesure_le » est obligatoire avec une position")
        charge = _nombre(contenu, "charge", obligatoire=False)
        if charge is not None and charge < 0:
            raise MessageInvalide("charge invalide")
        return cls(
            livreur_id=_texte(contenu, "livreur_id"),
            position=position,
            mesure_le=mesure,
            charge=None if charge is None else int(charge),
        )


@dataclass(frozen=True)
class Recherche:
    """Demande du Dispatcher : quels livreurs sont disponibles autour de ce point ?"""

    latitude: float
    longitude: float
    rayon_m: float
    type_vehicule: str | None = None
    poids_kg: int | None = None
    exclure: tuple[str, ...] = field(default_factory=tuple)

    def vers_contenu(self) -> dict[str, Any]:
        return {
            "latitude": self.latitude,
            "longitude": self.longitude,
            "rayon_m": self.rayon_m,
            "type_vehicule": self.type_vehicule,
            "poids_kg": self.poids_kg,
            "exclure": list(self.exclure),
        }

    @classmethod
    def depuis_contenu(cls, contenu: dict) -> "Recherche":
        position = _position(contenu)
        if position is None:
            raise MessageInvalide("latitude et longitude sont obligatoires")
        rayon = _nombre(contenu, "rayon_m")
        if rayon <= 0:
            raise MessageInvalide("« rayon_m » doit être positif")
        type_vehicule = contenu.get("type_vehicule")
        if type_vehicule is not None and not isinstance(type_vehicule, str):
            raise MessageInvalide("« type_vehicule » doit être un texte")
        poids = _nombre(contenu, "poids_kg", obligatoire=False)
        exclure = contenu.get("exclure") or []
        if not isinstance(exclure, list) or not all(isinstance(x, str) for x in exclure):
            raise MessageInvalide("« exclure » doit être une liste de textes")
        return cls(
            latitude=position[0],
            longitude=position[1],
            rayon_m=rayon,
            type_vehicule=type_vehicule,
            poids_kg=None if poids is None else int(poids),
            exclure=tuple(exclure),
        )


# -- contenus du Contract Net ---------------------------------------------------------------------


def _commande_et_tour(contenu: dict) -> tuple[str, int]:
    commande_id = _texte(contenu, "commande_id")
    try:
        uuid.UUID(commande_id)
    except ValueError as e:
        raise MessageInvalide("« commande_id » n'est pas un identifiant valide") from e
    tour = contenu.get("tour")
    if isinstance(tour, bool) or not isinstance(tour, int) or tour < 1:
        raise MessageInvalide("« tour » doit être un entier à partir de 1")
    return commande_id, tour


def _point_requis(contenu: dict, cle: str) -> tuple[float, float]:
    brut = contenu.get(cle)
    if not isinstance(brut, dict):
        raise MessageInvalide(f"« {cle} » doit être un point (latitude, longitude)")
    point = _position(brut)
    if point is None:
        raise MessageInvalide(f"« {cle} » : latitude et longitude sont obligatoires")
    return point


def _point_vers_contenu(point: tuple[float, float]) -> dict[str, float]:
    return {"latitude": point[0], "longitude": point[1]}


@dataclass(frozen=True)
class DemandeAttribution:
    """L'agent Commande demande au Dispatcher de lancer un tour d'appel d'offres."""

    commande_id: str
    tour: int
    rayon_m: float

    def vers_contenu(self) -> dict[str, Any]:
        return {"commande_id": self.commande_id, "tour": self.tour, "rayon_m": self.rayon_m}

    @classmethod
    def depuis_contenu(cls, contenu: dict) -> "DemandeAttribution":
        commande_id, tour = _commande_et_tour(contenu)
        rayon = _nombre(contenu, "rayon_m")
        if rayon <= 0:
            raise MessageInvalide("« rayon_m » doit être positif")
        return cls(commande_id, tour, rayon)


@dataclass(frozen=True)
class AppelOffres:
    """CFP : la course à proposer, sans donnée personnelle (ni nom ni téléphone du destinataire)."""

    commande_id: str
    tour: int
    enlevement: tuple[float, float]  # (latitude, longitude)
    livraison: tuple[float, float]
    taille: str
    priorite: str
    rayon_m: float
    delai_s: float  # délai de réponse accordé aux agents
    poids_kg: float | None = None
    type_vehicule: str | None = None  # véhicule exigé, s'il y en a un

    def vers_contenu(self) -> dict[str, Any]:
        return {
            "commande_id": self.commande_id,
            "tour": self.tour,
            "enlevement": _point_vers_contenu(self.enlevement),
            "livraison": _point_vers_contenu(self.livraison),
            "taille": self.taille,
            "poids_kg": self.poids_kg,
            "priorite": self.priorite,
            "rayon_m": self.rayon_m,
            "delai_s": self.delai_s,
            "type_vehicule": self.type_vehicule,
        }

    @classmethod
    def depuis_contenu(cls, contenu: dict) -> "AppelOffres":
        commande_id, tour = _commande_et_tour(contenu)
        rayon = _nombre(contenu, "rayon_m")
        delai = _nombre(contenu, "delai_s")
        if rayon <= 0 or delai <= 0:
            raise MessageInvalide("« rayon_m » et « delai_s » doivent être positifs")
        poids = _nombre(contenu, "poids_kg", obligatoire=False)
        if poids is not None and poids < 0:
            raise MessageInvalide("« poids_kg » ne peut pas être négatif")
        vehicule = contenu.get("type_vehicule")
        if vehicule is not None and not isinstance(vehicule, str):
            raise MessageInvalide("« type_vehicule » doit être un texte")
        return cls(
            commande_id=commande_id,
            tour=tour,
            enlevement=_point_requis(contenu, "enlevement"),
            livraison=_point_requis(contenu, "livraison"),
            taille=_texte(contenu, "taille"),
            priorite=_texte(contenu, "priorite"),
            rayon_m=rayon,
            delai_s=delai,
            poids_kg=poids,
            type_vehicule=vehicule,
        )


@dataclass(frozen=True)
class Proposition:
    """PROPOSE : l'offre d'un agent Livreur (coût en secondes, voir modele/cout.py)."""

    commande_id: str
    tour: int
    cout: float
    eta_s: float  # durée jusqu'à l'enlèvement
    detour_s: float
    charge: int
    estime: bool = False  # True : durée estimée à vol d'oiseau, OSRM n'a pas répondu

    def vers_contenu(self) -> dict[str, Any]:
        return {
            "commande_id": self.commande_id,
            "tour": self.tour,
            "cout": self.cout,
            "eta_s": self.eta_s,
            "detour_s": self.detour_s,
            "charge": self.charge,
            "estime": self.estime,
        }

    @classmethod
    def depuis_contenu(cls, contenu: dict) -> "Proposition":
        commande_id, tour = _commande_et_tour(contenu)
        cout, eta, detour = (_nombre(contenu, c) for c in ("cout", "eta_s", "detour_s"))
        charge = _nombre(contenu, "charge")
        if min(cout, eta, detour, charge) < 0:
            raise MessageInvalide("coût, durées et charge ne peuvent pas être négatifs")
        estime = contenu.get("estime", False)
        if not isinstance(estime, bool):
            raise MessageInvalide("« estime » doit être vrai ou faux")
        return cls(commande_id, tour, cout, eta, detour, int(charge), estime)


@dataclass(frozen=True)
class Refus:
    """REFUSE : l'agent Livreur ne peut pas faire la course, et dit pourquoi."""

    commande_id: str
    tour: int
    motif: str
    detail: str | None = None

    def vers_contenu(self) -> dict[str, Any]:
        return {
            "commande_id": self.commande_id,
            "tour": self.tour,
            "motif": self.motif,
            "detail": self.detail,
        }

    @classmethod
    def depuis_contenu(cls, contenu: dict) -> "Refus":
        commande_id, tour = _commande_et_tour(contenu)
        motif = _texte(contenu, "motif")
        if motif not in MOTIFS_REFUS:
            raise MessageInvalide(f"motif de refus inconnu : {motif!r}")
        detail = contenu.get("detail")
        if detail is not None and not isinstance(detail, str):
            raise MessageInvalide("« detail » doit être un texte")
        return cls(commande_id, tour, motif, detail)


@dataclass(frozen=True)
class Decision:
    """ACCEPT-PROPOSAL ou REJECT-PROPOSAL : la décision du Dispatcher pour une offre."""

    commande_id: str
    tour: int
    delai_confirmation_s: float | None = None  # présent seulement pour une acceptation

    def vers_contenu(self) -> dict[str, Any]:
        contenu: dict[str, Any] = {"commande_id": self.commande_id, "tour": self.tour}
        if self.delai_confirmation_s is not None:
            contenu["delai_confirmation_s"] = self.delai_confirmation_s
        return contenu

    @classmethod
    def depuis_contenu(cls, contenu: dict) -> "Decision":
        commande_id, tour = _commande_et_tour(contenu)
        delai = _nombre(contenu, "delai_confirmation_s", obligatoire=False)
        if delai is not None and delai <= 0:
            raise MessageInvalide("« delai_confirmation_s » doit être positif")
        return cls(commande_id, tour, delai)


@dataclass(frozen=True)
class Confirmation:
    """INFORM (confirmation) : le livreur humain a pris la course."""

    commande_id: str
    tour: int

    def vers_contenu(self) -> dict[str, Any]:
        return {"commande_id": self.commande_id, "tour": self.tour, "statut": "confirme"}

    @classmethod
    def depuis_contenu(cls, contenu: dict) -> "Confirmation":
        commande_id, tour = _commande_et_tour(contenu)
        if contenu.get("statut") != "confirme":
            raise MessageInvalide("« statut » doit valoir « confirme »")
        return cls(commande_id, tour)


@dataclass(frozen=True)
class Annulation:
    """CANCEL : le livreur humain a décliné, n'a pas répondu à temps, ou ne peut plus porter la course."""

    commande_id: str
    tour: int
    motif: str  # declinee, expiree ou sature

    def vers_contenu(self) -> dict[str, Any]:
        return {"commande_id": self.commande_id, "tour": self.tour, "motif": self.motif}

    @classmethod
    def depuis_contenu(cls, contenu: dict) -> "Annulation":
        commande_id, tour = _commande_et_tour(contenu)
        motif = _texte(contenu, "motif")
        if motif not in (DECLINEE, EXPIREE, SATURE):
            raise MessageInvalide(f"motif d'annulation inconnu : {motif!r}")
        return cls(commande_id, tour, motif)


@dataclass(frozen=True)
class ResultatAttribution:
    """Le Dispatcher informe l'agent Commande : attribuée, ou à réattribuer (INFORM)."""

    commande_id: str
    tour: int
    resultat: str  # RESULTAT_ATTRIBUEE ou RESULTAT_REATTRIBUTION
    livreur_id: str
    motif: str | None = None  # pour une réattribution : declinee, expiree, sature

    def vers_contenu(self) -> dict[str, Any]:
        return {
            "commande_id": self.commande_id,
            "tour": self.tour,
            "resultat": self.resultat,
            "livreur_id": self.livreur_id,
            "motif": self.motif,
        }

    @classmethod
    def depuis_contenu(cls, contenu: dict) -> "ResultatAttribution":
        commande_id, tour = _commande_et_tour(contenu)
        resultat = _texte(contenu, "resultat")
        if resultat not in (RESULTAT_ATTRIBUEE, RESULTAT_REATTRIBUTION):
            raise MessageInvalide(f"résultat inconnu : {resultat!r}")
        motif = contenu.get("motif")
        if motif is not None and not isinstance(motif, str):
            raise MessageInvalide("« motif » doit être un texte")
        return cls(commande_id, tour, resultat, _texte(contenu, "livreur_id"), motif)


@dataclass(frozen=True)
class EchecAttribution:
    """Le tour n'a abouti à aucune attribution (FAILURE du Dispatcher vers l'agent Commande)."""

    commande_id: str
    tour: int
    motif: str  # ECHEC_SANS_OFFRE, ECHEC_COMMANDE, ECHEC_ANNUAIRE ou ECHEC_INTERNE
    detail: str | None = None

    def vers_contenu(self) -> dict[str, Any]:
        return {
            "commande_id": self.commande_id,
            "tour": self.tour,
            "motif": self.motif,
            "detail": self.detail,
        }

    @classmethod
    def depuis_contenu(cls, contenu: dict) -> "EchecAttribution":
        commande_id, tour = _commande_et_tour(contenu)
        detail = contenu.get("detail")
        if detail is not None and not isinstance(detail, str):
            raise MessageInvalide("« detail » doit être un texte")
        return cls(commande_id, tour, _texte(contenu, "motif"), detail)


def contenu_vers_dict(objet: Any) -> dict[str, Any]:
    """Pour les réponses : un dataclass en dictionnaire JSON."""
    return asdict(objet)
