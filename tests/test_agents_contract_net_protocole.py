"""Messages du Contract Net : lecture stricte, aller-retour, rejet des contenus invalides."""

import uuid

import pytest

from livraison.agents import protocole as p

C = str(uuid.uuid4())
OBJETS = [
    p.DemandeAttribution(C, 2, 6000.0),
    p.AppelOffres(C, 1, (5.32, -4.01), (5.36, -3.99), "petit", "normale", 3000.0, 3.0, 2.5, "moto"),
    p.AppelOffres(C, 1, (5.32, -4.01), (5.36, -3.99), "grand", "urgente", 3000.0, 3.0),
    p.Proposition(C, 1, 420.0, 420.0, 0.0, 0, False),
    p.Proposition(C, 3, 1020.5, 180.0, 240.0, 2, True),
    p.Refus(C, 1, p.SATURE, "3 courses"),
    p.Refus(C, 1, p.HORS_ZONE),
    p.Decision(C, 1, 30.0),
    p.Decision(C, 1),
    p.Confirmation(C, 4),
    p.Annulation(C, 1, p.EXPIREE),
    p.ResultatAttribution(C, 1, p.RESULTAT_ATTRIBUEE, "livreur-x"),
    p.ResultatAttribution(C, 1, p.RESULTAT_REATTRIBUTION, "livreur-x", p.DECLINEE),
    p.EchecAttribution(C, 1, p.ECHEC_SANS_OFFRE, "rien"),
]


@pytest.mark.parametrize("objet", OBJETS, ids=lambda o: type(o).__name__)
def test_aller_retour(objet):
    contenu = objet.vers_contenu()
    assert type(objet).depuis_contenu(contenu) == objet


def test_conversation_aller_retour_et_refus_des_fils_invalides():
    assert p.lire_conversation(p.conversation(C, 3)) == (C, 3)
    for fil in (None, "", "abc", f"{C}:0", f"{C}:x", "pas-un-uuid:1", f"{C}"):
        with pytest.raises(p.MessageInvalide):
            p.lire_conversation(fil)


def test_adresses_des_agents():
    assert p.jid_commande(C).startswith(f"commande-{C}@")
    assert p.jid_dispatcher().startswith("dispatcher@")


@pytest.mark.parametrize(
    ("classe", "contenu"),
    [
        (p.DemandeAttribution, {"commande_id": C, "tour": 0, "rayon_m": 1000}),
        (p.DemandeAttribution, {"commande_id": "x", "tour": 1, "rayon_m": 1000}),
        (p.DemandeAttribution, {"commande_id": C, "tour": 1, "rayon_m": -1}),
        (p.DemandeAttribution, {"commande_id": C, "tour": True, "rayon_m": 10}),
        (p.AppelOffres, {"commande_id": C, "tour": 1}),
        (p.Proposition, {"commande_id": C, "tour": 1, "cout": -1, "eta_s": 1, "detour_s": 0, "charge": 0}),
        (p.Proposition, {"commande_id": C, "tour": 1, "cout": 1, "eta_s": 1, "detour_s": 0, "charge": 0, "estime": "oui"}),
        (p.Refus, {"commande_id": C, "tour": 1, "motif": "au_hasard"}),
        (p.Refus, {"commande_id": C, "tour": 1, "motif": p.SATURE, "detail": 5}),
        (p.Decision, {"commande_id": C, "tour": 1, "delai_confirmation_s": 0}),
        (p.Confirmation, {"commande_id": C, "tour": 1, "statut": "peut-etre"}),
        (p.Annulation, {"commande_id": C, "tour": 1, "motif": "ennui"}),
        (p.ResultatAttribution, {"commande_id": C, "tour": 1, "resultat": "?", "livreur_id": "a"}),
        (p.EchecAttribution, {"commande_id": C, "tour": 1}),
    ],
)
def test_contenus_invalides_sont_rejetes(classe, contenu):
    with pytest.raises(p.MessageInvalide):
        classe.depuis_contenu(contenu)


def test_appel_d_offres_sans_point_ou_avec_point_incomplet_est_rejete():
    base = p.AppelOffres(C, 1, (5.3, -4.0), (5.4, -4.1), "petit", "normale", 3000.0, 3.0).vers_contenu()
    for cle in ("enlevement", "livraison"):
        with pytest.raises(p.MessageInvalide):
            p.AppelOffres.depuis_contenu({**base, cle: None})
        with pytest.raises(p.MessageInvalide):
            p.AppelOffres.depuis_contenu({**base, cle: {"latitude": 5.3}})
