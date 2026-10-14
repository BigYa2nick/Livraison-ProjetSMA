"""Valeurs fixes du domaine (rôles, états, modes...).

Les valeurs sont stockées en texte dans la base (pas de type ENUM natif PostgreSQL) :
ajouter une valeur ne demande alors qu'une migration simple.
"""

from enum import StrEnum


class Role(StrEnum):
    CLIENT = "client"
    LIVREUR = "livreur"
    REPARTITEUR = "repartiteur"
    ADMINISTRATEUR = "administrateur"


class EtatCompte(StrEnum):
    ACTIF = "actif"
    SUSPENDU = "suspendu"
    SUPPRIME = "supprime"


class StatutValidation(StrEnum):
    """Validation du dossier livreur par l'administrateur (F-03)."""

    EN_ATTENTE = "en_attente"
    VALIDE = "valide"
    REJETE = "rejete"
    SUSPENDU = "suspendu"


class TypeVehicule(StrEnum):
    MOTO = "moto"
    VOITURE = "voiture"
    VELO = "velo"


class TypeDocument(StrEnum):
    PIECE_IDENTITE = "piece_identite"
    PERMIS = "permis"
    CARTE_GRISE = "carte_grise"
    PHOTO = "photo"


class StatutDocument(StrEnum):
    EN_ATTENTE = "en_attente"
    ACCEPTE = "accepte"
    REFUSE = "refuse"


class TailleColis(StrEnum):
    PETIT = "petit"
    MOYEN = "moyen"
    GRAND = "grand"


class Priorite(StrEnum):
    NORMALE = "normale"
    URGENTE = "urgente"


class EtatCommande(StrEnum):
    """Cycle de vie d'une commande (cahier des charges, § 3.4)."""

    CREEE = "creee"
    VALIDEE = "validee"  # payée en ligne, ou validée pour un paiement à la livraison
    RECHERCHE_LIVREUR = "recherche_livreur"
    ATTRIBUEE = "attribuee"
    ENLEVEE = "enlevee"
    EN_LIVRAISON = "en_livraison"
    LIVREE = "livree"
    CLOTUREE = "cloturee"
    # états d'exception
    ANNULEE = "annulee"
    ECHEC_LIVRAISON = "echec_livraison"
    LITIGE = "litige"


# États finaux : une commande qui y arrive n'évolue plus (exigence NF-04).
ETATS_FINAUX = frozenset(
    {EtatCommande.CLOTUREE, EtatCommande.ANNULEE, EtatCommande.ECHEC_LIVRAISON}
)


class ResultatOffre(StrEnum):
    """Issue d'une offre dans un appel d'offres (Contract Net)."""

    PROPOSEE = "proposee"  # PROPOSE reçu, en attente de décision
    RETENUE = "retenue"  # ACCEPT_PROPOSAL envoyé
    REJETEE = "rejetee"  # REJECT_PROPOSAL envoyé
    REFUSEE = "refusee"  # REFUSE de l'agent livreur (saturé, hors zone...)
    DECLINEE = "declinee"  # le livreur humain a décliné ou n'a pas répondu à temps
    EXPIREE = "expiree"  # aucune réponse de l'agent dans le délai de collecte


class ModePaiement(StrEnum):
    MOBILE_MONEY = "mobile_money"
    CARTE = "carte"
    ESPECES = "especes"


class EtatPaiement(StrEnum):
    EN_ATTENTE = "en_attente"
    CONFIRME = "confirme"
    ECHOUE = "echoue"
    REMBOURSE = "rembourse"


class TypeEvenement(StrEnum):
    CHANGEMENT_ETAT = "changement_etat"
    MESSAGE_AGENT = "message_agent"  # journal des messages FIPA-ACL (équivalent du Sniffer)
    INCIDENT = "incident"  # destinataire absent, adresse introuvable, panne, accident (F-24)
    PAIEMENT = "paiement"
    NOTIFICATION = "notification"
    ACTION_MANUELLE = "action_manuelle"  # réattribution ou annulation par le répartiteur