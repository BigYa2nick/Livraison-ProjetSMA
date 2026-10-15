"""Modèles de la base de données (SQLAlchemy 2, PostgreSQL + PostGIS).

Conventions :
- les montants sont des entiers en francs CFA (XOF), qui n'ont pas de centimes ;
- les dates sont stockées avec fuseau horaire (UTC) ;
- les positions sont des points GPS PostGIS de type geography (WGS 84), à lire dans
  l'ordre longitude, latitude ;
- les relations sont chargées explicitement (lazy="raise") : en asynchrone, un chargement
  implicite échouerait de toute façon.
Voir docs/modele-donnees.md pour le schéma complet et les choix de conception.
"""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from geoalchemy2 import Geography
from geoalchemy2.elements import WKBElement
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from livraison.persistance.base import (
    SRID_WGS84,
    Base,
    DateCreation,
    Horodatage,
    cle_uuid,
    colonne_enum,
    point,
)
from livraison.persistance.enums import (
    EtatCommande,
    EtatCompte,
    EtatPaiement,
    ModePaiement,
    Priorite,
    ResultatOffre,
    Role,
    StatutDocument,
    StatutValidation,
    TailleColis,
    TypeDocument,
    TypeEvenement,
    TypeVehicule,
)

# ---------------------------------------------------------------- comptes et accès


class Utilisateur(Base):
    """Tout compte : client, livreur, répartiteur ou administrateur (F-01, F-02)."""

    __tablename__ = "utilisateur"
    __table_args__ = (CheckConstraint(r"telephone ~ '^\+[0-9]{8,15}$'", name="telephone_e164"),)

    id: Mapped[uuid.UUID] = cle_uuid()
    telephone: Mapped[str] = mapped_column(String(20), unique=True)  # format +2250700000000
    nom: Mapped[str | None] = mapped_column(String(100))
    prenom: Mapped[str | None] = mapped_column(String(100))
    role: Mapped[Role] = mapped_column(colonne_enum(Role))
    etat: Mapped[EtatCompte] = mapped_column(
        colonne_enum(EtatCompte), default=EtatCompte.ACTIF, server_default=EtatCompte.ACTIF.value
    )
    # Haché (jamais en clair). Facultatif : clients et livreurs se connectent par code SMS.
    mot_de_passe_hash: Mapped[str | None] = mapped_column(String(255))
    cree_le: Mapped[DateCreation]
    maj_le: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    derniere_connexion_le: Mapped[Horodatage | None]
    # Limitation des tentatives de connexion par mot de passe (NF-05)
    echecs_connexion: Mapped[int] = mapped_column(SmallInteger, default=0, server_default="0")
    verrouille_jusqu_le: Mapped[Horodatage | None]

    livreur: Mapped["Livreur | None"] = relationship(
        back_populates="utilisateur", foreign_keys="Livreur.utilisateur_id", lazy="raise"
    )


class CodeVerification(Base):
    """Code à usage unique envoyé par SMS (F-01). Seul son haché est conservé."""

    __tablename__ = "code_verification"
    __table_args__ = (
        Index("ix_code_verification_telephone_cree_le", "telephone", "cree_le"),
        CheckConstraint("tentatives >= 0", name="tentatives_positives"),
    )

    id: Mapped[uuid.UUID] = cle_uuid()
    telephone: Mapped[str] = mapped_column(String(20))
    code_hash: Mapped[str] = mapped_column(String(255))
    tentatives: Mapped[int] = mapped_column(SmallInteger, default=0, server_default="0")
    expire_le: Mapped[Horodatage]
    utilise_le: Mapped[Horodatage | None]
    cree_le: Mapped[DateCreation]


# -------------------------------------------------------------------------- livreurs


class Livreur(Base):
    """Profil livreur, en relation 1-1 avec un utilisateur de rôle « livreur » (F-03)."""

    __tablename__ = "livreur"
    __table_args__ = (
        CheckConstraint("capacite_kg > 0", name="capacite_positive"),
        CheckConstraint("note_moyenne BETWEEN 0 AND 5", name="note_entre_0_et_5"),
        Index("ix_livreur_derniere_position", "derniere_position", postgresql_using="gist"),
        Index("ix_livreur_en_ligne", "en_ligne", postgresql_where=text("en_ligne")),
    )

    utilisateur_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("utilisateur.id", ondelete="CASCADE"), primary_key=True
    )
    type_vehicule: Mapped[TypeVehicule] = mapped_column(colonne_enum(TypeVehicule))
    capacite_kg: Mapped[int] = mapped_column(Integer)
    immatriculation: Mapped[str | None] = mapped_column(String(30))
    statut_validation: Mapped[StatutValidation] = mapped_column(
        colonne_enum(StatutValidation),
        default=StatutValidation.EN_ATTENTE,
        server_default=StatutValidation.EN_ATTENTE.value,
    )
    valide_par_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("utilisateur.id"))
    valide_le: Mapped[Horodatage | None]
    en_ligne: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    note_moyenne: Mapped[Decimal | None] = mapped_column(Numeric(3, 2))
    nb_evaluations: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # Dernière position connue (la plus récente se trouve aussi dans Redis ; cette copie
    # permet de reconstruire les agents après un redémarrage du serveur, F-36).
    derniere_position: Mapped[WKBElement | None] = mapped_column(point())
    position_maj_le: Mapped[Horodatage | None]
    cree_le: Mapped[DateCreation]

    utilisateur: Mapped[Utilisateur] = relationship(
        back_populates="livreur", foreign_keys=[utilisateur_id], lazy="raise"
    )
    documents: Mapped[list["DocumentLivreur"]] = relationship(
        back_populates="livreur", lazy="raise"
    )


class DocumentLivreur(Base):
    """Pièce justificative du dossier livreur (identité, permis, carte grise, photo)."""

    __tablename__ = "document_livreur"

    id: Mapped[uuid.UUID] = cle_uuid()
    livreur_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("livreur.utilisateur_id", ondelete="CASCADE"), index=True
    )
    type: Mapped[TypeDocument] = mapped_column(colonne_enum(TypeDocument))
    chemin_fichier: Mapped[str] = mapped_column(String(500))  # le fichier n'est pas en base
    statut: Mapped[StatutDocument] = mapped_column(
        colonne_enum(StatutDocument),
        default=StatutDocument.EN_ATTENTE,
        server_default=StatutDocument.EN_ATTENTE.value,
    )
    motif_refus: Mapped[str | None] = mapped_column(String(300))
    cree_le: Mapped[DateCreation]

    livreur: Mapped[Livreur] = relationship(back_populates="documents", lazy="raise")


class Position(Base):
    """Historique des positions GPS d'un livreur en ligne (collectées uniquement en ligne, NF-07).

    Table à forte croissance : prévoir une purge ou un partitionnement au lot 7.
    """

    __tablename__ = "position"
    __table_args__ = (Index("ix_position_livreur_id_enregistre_le", "livreur_id", "enregistre_le"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    livreur_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("livreur.utilisateur_id", ondelete="CASCADE")
    )
    localisation: Mapped[WKBElement] = mapped_column(point())
    precision_m: Mapped[float | None] = mapped_column(Float)
    enregistre_le: Mapped[Horodatage]  # heure de la mesure, donnée par le téléphone
    recu_le: Mapped[DateCreation]  # heure de réception par le serveur


# ------------------------------------------------------------ clients, zones, tarifs


class Adresse(Base):
    """Carnet d'adresses d'un client (F-04)."""

    __tablename__ = "adresse"

    id: Mapped[uuid.UUID] = cle_uuid()
    client_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("utilisateur.id", ondelete="CASCADE"), index=True
    )
    libelle: Mapped[str] = mapped_column(String(50))  # « Domicile », « Bureau »...
    texte: Mapped[str] = mapped_column(String(300))
    localisation: Mapped[WKBElement] = mapped_column(point())
    cree_le: Mapped[DateCreation]


class ZoneService(Base):
    """Zone géographique où le service est ouvert ; une commande hors zone est refusée (F-43)."""

    __tablename__ = "zone_service"
    __table_args__ = (Index("ix_zone_service_geometrie", "geometrie", postgresql_using="gist"),)

    id: Mapped[uuid.UUID] = cle_uuid()
    nom: Mapped[str] = mapped_column(String(100), unique=True)
    geometrie: Mapped[Any] = mapped_column(
        Geography(geometry_type="MULTIPOLYGON", srid=SRID_WGS84, spatial_index=False)
    )
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    cree_le: Mapped[DateCreation]


class Tarif(Base):
    """Paramètres de tarification fixés par l'administrateur (F-50).

    Prix = part fixe + prix au km x distance + suppléments (taille, urgence, nuit).
    Un seul tarif est actif à la fois ; les anciens sont conservés pour l'historique.
    """

    __tablename__ = "tarif"
    __table_args__ = (
        CheckConstraint(
            "part_fixe_xof >= 0 AND prix_km_xof >= 0 AND supplement_moyen_xof >= 0 "
            "AND supplement_grand_xof >= 0 AND supplement_urgent_xof >= 0 "
            "AND supplement_nuit_xof >= 0",
            name="montants_positifs",
        ),
        CheckConstraint(
            "nuit_debut_h BETWEEN 0 AND 23 AND nuit_fin_h BETWEEN 0 AND 23", name="heures_valides"
        ),
        # Un seul enregistrement peut avoir actif = vrai.
        Index("uq_tarif_un_seul_actif", "actif", unique=True, postgresql_where=text("actif")),
    )

    id: Mapped[uuid.UUID] = cle_uuid()
    nom: Mapped[str] = mapped_column(String(100))
    part_fixe_xof: Mapped[int] = mapped_column(Integer)
    prix_km_xof: Mapped[int] = mapped_column(Integer)
    supplement_moyen_xof: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    supplement_grand_xof: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    supplement_urgent_xof: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    supplement_nuit_xof: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    nuit_debut_h: Mapped[int] = mapped_column(SmallInteger, default=22, server_default="22")
    nuit_fin_h: Mapped[int] = mapped_column(SmallInteger, default=6, server_default="6")
    actif: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    cree_le: Mapped[DateCreation]


# ------------------------------------------------------------------------- commandes


class Commande(Base):
    """Une demande de livraison, de sa création à son état final (§ 3.4)."""

    __tablename__ = "commande"
    __table_args__ = (
        CheckConstraint("prix_xof >= 0", name="prix_positif"),
        CheckConstraint("tentatives_attribution >= 0", name="tentatives_positives"),
        # Une commande en cours de livraison a toujours un livreur (NF-04).
        CheckConstraint(
            "etat NOT IN ('attribuee', 'enlevee', 'en_livraison') OR livreur_id IS NOT NULL",
            name="livreur_si_en_cours",
        ),
        # File d'attente du Dispatcher : commandes à attribuer, urgentes d'abord (F-33, F-35).
        Index("ix_commande_etat_priorite_cree_le", "etat", "priorite", "cree_le"),
        Index("ix_commande_client_id_cree_le", "client_id", "cree_le"),
        Index("ix_commande_livreur_id_etat", "livreur_id", "etat"),
        Index("ix_commande_enlevement", "enlevement", postgresql_using="gist"),
    )

    id: Mapped[uuid.UUID] = cle_uuid()
    client_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("utilisateur.id"))
    livreur_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("livreur.utilisateur_id"))
    tarif_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tarif.id"))

    etat: Mapped[EtatCommande] = mapped_column(
        colonne_enum(EtatCommande),
        default=EtatCommande.CREEE,
        server_default=EtatCommande.CREEE.value,
    )
    priorite: Mapped[Priorite] = mapped_column(
        colonne_enum(Priorite), default=Priorite.NORMALE, server_default=Priorite.NORMALE.value
    )

    # Trajet
    adresse_enlevement: Mapped[str] = mapped_column(String(300))
    enlevement: Mapped[WKBElement] = mapped_column(point())
    adresse_livraison: Mapped[str] = mapped_column(String(300))
    livraison: Mapped[WKBElement] = mapped_column(point())
    destinataire_nom: Mapped[str] = mapped_column(String(100))
    destinataire_telephone: Mapped[str] = mapped_column(String(20))
    distance_m: Mapped[int | None] = mapped_column(Integer)
    duree_estimee_s: Mapped[int | None] = mapped_column(Integer)

    # Colis
    taille_colis: Mapped[TailleColis] = mapped_column(
        colonne_enum(TailleColis), default=TailleColis.PETIT, server_default=TailleColis.PETIT.value
    )
    poids_kg: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    description_colis: Mapped[str | None] = mapped_column(String(300))
    consignes: Mapped[str | None] = mapped_column(Text)

    prix_xof: Mapped[int] = mapped_column(Integer)
    programmee_pour: Mapped[Horodatage | None]  # livraison programmée (F-16)

    # Suivi de l'attribution (reprise après redémarrage, F-33 et F-36)
    tentatives_attribution: Mapped[int] = mapped_column(SmallInteger, default=0, server_default="0")
    rayon_recherche_m: Mapped[int | None] = mapped_column(Integer)

    # Preuve de livraison (F-23) : code à usage unique (haché) ou photo
    code_confirmation_hash: Mapped[str | None] = mapped_column(String(255))
    photo_preuve: Mapped[str | None] = mapped_column(String(500))

    # Horodatages utiles aux indicateurs (délai d'attribution, délai de livraison)
    cree_le: Mapped[DateCreation]
    validee_le: Mapped[Horodatage | None]
    attribuee_le: Mapped[Horodatage | None]
    enlevee_le: Mapped[Horodatage | None]
    livree_le: Mapped[Horodatage | None]
    terminee_le: Mapped[Horodatage | None]  # clôture, annulation ou échec définitif
    maj_le: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    offres: Mapped[list["Offre"]] = relationship(back_populates="commande", lazy="raise")
    paiements: Mapped[list["Paiement"]] = relationship(back_populates="commande", lazy="raise")


class Offre(Base):
    """Réponse d'un agent Livreur à un appel d'offres (Contract Net, § 3.3)."""

    __tablename__ = "offre"
    __table_args__ = (
        UniqueConstraint(
            "commande_id", "tour", "livreur_id", name="uq_offre_commande_tour_livreur"
        ),
        CheckConstraint("tour >= 1", name="tour_positif"),
        # Au plus une offre retenue à la fois pour un tour : pas de double attribution (F-32).
        Index(
            "uq_offre_une_retenue_par_tour",
            "commande_id",
            "tour",
            unique=True,
            postgresql_where=text("resultat = 'retenue'"),
        ),
    )

    id: Mapped[uuid.UUID] = cle_uuid()
    commande_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("commande.id", ondelete="CASCADE"), index=True
    )
    livreur_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("livreur.utilisateur_id"))
    tour: Mapped[int] = mapped_column(
        SmallInteger, default=1, server_default="1"
    )  # n° d'appel d'offres
    cout: Mapped[float | None] = mapped_column(Float)  # vide si le livreur refuse
    eta_s: Mapped[int | None] = mapped_column(Integer)  # arrivée estimée au point d'enlèvement
    resultat: Mapped[ResultatOffre] = mapped_column(
        colonne_enum(ResultatOffre),
        default=ResultatOffre.PROPOSEE,
        server_default=ResultatOffre.PROPOSEE.value,
    )
    motif_refus: Mapped[str | None] = mapped_column(String(200))
    cree_le: Mapped[DateCreation]
    decide_le: Mapped[Horodatage | None]

    commande: Mapped[Commande] = relationship(back_populates="offres", lazy="raise")


# ------------------------------------------------------------ paiement et facturation


class Paiement(Base):
    """Tentative de paiement d'une commande (F-51, F-52). Aucune donnée de carte (NF-06)."""

    __tablename__ = "paiement"
    __table_args__ = (
        CheckConstraint("montant_xof >= 0", name="montant_positif"),
        CheckConstraint(
            "montant_rembourse_xof BETWEEN 0 AND montant_xof", name="remboursement_valide"
        ),
        # Au plus un paiement confirmé par commande.
        Index(
            "uq_paiement_un_confirme_par_commande",
            "commande_id",
            unique=True,
            postgresql_where=text("etat = 'confirme'"),
        ),
    )

    id: Mapped[uuid.UUID] = cle_uuid()
    commande_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("commande.id"), index=True)
    mode: Mapped[ModePaiement] = mapped_column(colonne_enum(ModePaiement))
    montant_xof: Mapped[int] = mapped_column(Integer)
    montant_rembourse_xof: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    etat: Mapped[EtatPaiement] = mapped_column(
        colonne_enum(EtatPaiement),
        default=EtatPaiement.EN_ATTENTE,
        server_default=EtatPaiement.EN_ATTENTE.value,
    )
    reference_prestataire: Mapped[str | None] = mapped_column(String(100), unique=True)
    cree_le: Mapped[DateCreation]
    confirme_le: Mapped[Horodatage | None]

    commande: Mapped[Commande] = relationship(back_populates="paiements", lazy="raise")


class Facture(Base):
    """Facture ou reçu d'une commande livrée (F-53). Le numéro est séquentiel et sans trou."""

    __tablename__ = "facture"
    __table_args__ = (
        CheckConstraint("montant_xof >= 0 AND tva_xof >= 0", name="montants_positifs"),
    )

    id: Mapped[uuid.UUID] = cle_uuid()
    numero: Mapped[int] = mapped_column(Integer, Identity(), unique=True)
    commande_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("commande.id"), unique=True)
    montant_xof: Mapped[int] = mapped_column(Integer)
    tva_xof: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    chemin_pdf: Mapped[str | None] = mapped_column(String(500))
    emise_le: Mapped[DateCreation]


class Evaluation(Base):
    """Note donnée par le client au livreur après une livraison (F-15)."""

    __tablename__ = "evaluation"
    __table_args__ = (CheckConstraint("note BETWEEN 1 AND 5", name="note_entre_1_et_5"),)

    id: Mapped[uuid.UUID] = cle_uuid()
    commande_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("commande.id"), unique=True)
    client_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("utilisateur.id"))
    livreur_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("livreur.utilisateur_id"), index=True)
    note: Mapped[int] = mapped_column(SmallInteger)
    commentaire: Mapped[str | None] = mapped_column(String(500))
    cree_le: Mapped[DateCreation]


# ------------------------------------------------------------------------- traçabilité


class Evenement(Base):
    """Journal d'une commande et des messages entre agents (F-61, F-63, NF-09).

    Table en ajout seul : un déclencheur PostgreSQL (créé dans la migration) interdit
    toute modification ou suppression d'une ligne. Pour respecter le droit à l'effacement
    (NF-07), on n'y met que des identifiants, jamais de nom ni de numéro de téléphone.
    """

    __tablename__ = "evenement"
    __table_args__ = (
        Index("ix_evenement_commande_id_cree_le", "commande_id", "cree_le"),
        Index("ix_evenement_type_cree_le", "type", "cree_le"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    commande_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("commande.id"))
    type: Mapped[TypeEvenement] = mapped_column(colonne_enum(TypeEvenement))
    auteur: Mapped[str] = mapped_column(String(100))  # JID d'un agent ou « utilisateur:<id> »
    destinataire: Mapped[str | None] = mapped_column(String(100))
    performative: Mapped[str | None] = mapped_column(String(30))  # request, propose, inform...
    detail: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    cree_le: Mapped[DateCreation]
