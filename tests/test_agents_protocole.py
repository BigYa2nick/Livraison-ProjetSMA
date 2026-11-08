"""Protocole des agents : construction et validation des messages."""

import json
from datetime import UTC, datetime

import pytest

from livraison.agents import protocole as p
from livraison.agents.protocole import Inscription, MessageInvalide, MiseAJour, Recherche

MAINTENANT = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)


def passer_par_json(contenu: dict) -> dict:
    return json.loads(json.dumps(contenu))


def test_inscription_aller_retour():
    avant = Inscription("abc", "moto", 25, 1, (5.32, -4.02), MAINTENANT)
    apres = Inscription.depuis_contenu(passer_par_json(avant.vers_contenu()))
    assert apres == avant


def test_inscription_sans_position():
    apres = Inscription.depuis_contenu(
        passer_par_json(Inscription("abc", "velo", 10).vers_contenu())
    )
    assert apres.position is None and apres.mesure_le is None and apres.charge == 0


def test_mise_a_jour_aller_retour():
    avant = MiseAJour("abc", (5.32, -4.02), MAINTENANT, 3)
    assert MiseAJour.depuis_contenu(passer_par_json(avant.vers_contenu())) == avant
    seulement_charge = MiseAJour("abc", charge=2)
    assert (
        MiseAJour.depuis_contenu(passer_par_json(seulement_charge.vers_contenu()))
        == seulement_charge
    )


def test_recherche_aller_retour():
    avant = Recherche(5.32, -4.02, 3000.0, "moto", 5, ("x", "y"))
    assert Recherche.depuis_contenu(passer_par_json(avant.vers_contenu())) == avant


@pytest.mark.parametrize(
    "contenu",
    [
        {},  # rien
        {"livreur_id": "a", "type_vehicule": "moto"},  # capacité absente
        {"livreur_id": "a", "type_vehicule": "moto", "capacite_kg": 0},
        {"livreur_id": "a", "type_vehicule": "moto", "capacite_kg": 25, "charge": -1},
        {
            "livreur_id": "a",
            "type_vehicule": "moto",
            "capacite_kg": True,
        },  # un booléen n'est pas un nombre
        {"livreur_id": "", "type_vehicule": "moto", "capacite_kg": 25},
        {
            "livreur_id": "a",
            "type_vehicule": "moto",
            "capacite_kg": 25,
            "latitude": 5.3,
        },  # longitude absente
        {
            "livreur_id": "a",
            "type_vehicule": "moto",
            "capacite_kg": 25,
            "latitude": 95,
            "longitude": 0,
            "mesure_le": MAINTENANT.isoformat(),
        },
        {
            "livreur_id": "a",
            "type_vehicule": "moto",
            "capacite_kg": 25,
            "latitude": 5.3,
            "longitude": -4.0,
        },  # sans mesure_le
        {
            "livreur_id": "a",
            "type_vehicule": "moto",
            "capacite_kg": 25,
            "latitude": 5.3,
            "longitude": -4.0,
            "mesure_le": "2026-10-24T12:00:00",
        },  # sans fuseau
        {
            "livreur_id": "a",
            "type_vehicule": "moto",
            "capacite_kg": 25,
            "latitude": 5.3,
            "longitude": -4.0,
            "mesure_le": "hier",
        },
    ],
)
def test_inscription_invalide(contenu):
    with pytest.raises(MessageInvalide):
        Inscription.depuis_contenu(contenu)


@pytest.mark.parametrize(
    "contenu",
    [
        {},
        {"latitude": 5.3, "longitude": -4.0},  # rayon absent
        {"latitude": 5.3, "longitude": -4.0, "rayon_m": 0},
        {"latitude": 5.3, "longitude": -4.0, "rayon_m": -5},
        {"rayon_m": 100},  # pas de point
        {"latitude": 5.3, "longitude": -4.0, "rayon_m": 100, "exclure": "abc"},
        {"latitude": 5.3, "longitude": -4.0, "rayon_m": 100, "exclure": [1, 2]},
        {"latitude": 5.3, "longitude": -4.0, "rayon_m": 100, "type_vehicule": 7},
    ],
)
def test_recherche_invalide(contenu):
    with pytest.raises(MessageInvalide):
        Recherche.depuis_contenu(contenu)


def test_construction_d_un_message():
    m = p.creer_message(
        "annuaire@localhost", p.REQUEST, p.ONTO_INSCRIPTION, {"a": "é"}, thread="t1"
    )
    assert m.get_metadata("performative") == p.REQUEST
    assert m.get_metadata("ontology") == p.ONTO_INSCRIPTION
    assert m.thread == "t1"
    assert p.lire_contenu(m) == {"a": "é"}


def test_reponse_garde_le_fil_et_l_ontologie():
    m = p.creer_message("annuaire@localhost", p.QUERY_REF, p.ONTO_RECHERCHE, {}, thread="t2")
    m.sender = "dispatcher@localhost/x"
    r = p.repondre(m, p.INFORM, {"total": 0})
    assert r.get_metadata("performative") == p.INFORM
    assert r.get_metadata("ontology") == p.ONTO_RECHERCHE
    assert r.thread == "t2" and str(r.to.bare) == "dispatcher@localhost"


@pytest.mark.parametrize("corps", ["", "pas du json", "[1, 2]", '"texte"', "42", "null"])
def test_corps_illisible(corps):
    m = p.creer_message("a@localhost", p.INFORM, p.ONTO_MAJ)
    m.body = corps
    with pytest.raises(MessageInvalide):
        p.lire_contenu(m)


def test_adresses_des_agents():
    assert p.jid_annuaire().startswith("annuaire@")
    assert p.livreur_id_depuis_jid(p.jid_livreur("abc-123")) == "abc-123"
    assert p.livreur_id_depuis_jid("annuaire@localhost") is None
    assert p.livreur_id_depuis_jid("livreur-x@localhost/ressource") == "x"
