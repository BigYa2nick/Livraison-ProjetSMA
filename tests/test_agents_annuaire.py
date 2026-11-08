"""Agent Annuaire : traitement des messages, sans réseau (la même logique tourne en vrai dans
test_agents_xmpp.py)."""

import json
from datetime import UTC, datetime

import pytest

from livraison.agents import protocole as p
from livraison.agents.annuaire import AgentAnnuaire
from livraison.agents.protocole import Inscription, MiseAJour, Recherche

MAINTENANT = datetime.now(UTC)
POSITION = (5.3236, -4.0197)


@pytest.fixture
def annuaire():
    return AgentAnnuaire("annuaire-unit@localhost", "x")


def message(ontologie, performative, contenu, expediteur="livreur-a@localhost/res"):
    m = p.creer_message("annuaire-unit@localhost", performative, ontologie, contenu, thread="fil")
    m.sender = expediteur
    return m


def inscrire(annuaire, livreur_id="a", position=POSITION, capacite=25, vehicule="moto"):
    contenu = Inscription(livreur_id, vehicule, capacite, 0, position, MAINTENANT).vers_contenu()
    return annuaire.traiter(
        message(p.ONTO_INSCRIPTION, p.REQUEST, contenu, f"livreur-{livreur_id}@localhost/res")
    )


def lire(reponse):
    return json.loads(reponse.body)


def test_inscription():
    annuaire = AgentAnnuaire("annuaire-unit@localhost", "x")
    (reponse,) = inscrire(annuaire)
    assert reponse.get_metadata("performative") == p.INFORM
    assert lire(reponse) == {"statut": "inscrit", "nouveau": True}
    assert reponse.thread == "fil"
    assert "a" in annuaire.registre
    (encore,) = inscrire(annuaire)
    assert lire(encore)["nouveau"] is False  # se réinscrire est permis


def test_un_agent_ne_peut_pas_inscrire_un_autre_livreur(annuaire):
    contenu = Inscription("b", "moto", 25).vers_contenu()
    (reponse,) = annuaire.traiter(
        message(p.ONTO_INSCRIPTION, p.REQUEST, contenu, "livreur-a@localhost/res")
    )
    assert reponse.get_metadata("performative") == p.FAILURE
    assert lire(reponse)["motif"] == "identite"
    assert len(annuaire.registre) == 0


def test_un_agent_qui_n_est_pas_un_livreur_ne_peut_pas_s_inscrire(annuaire):
    contenu = Inscription("a", "moto", 25).vers_contenu()
    (reponse,) = annuaire.traiter(
        message(p.ONTO_INSCRIPTION, p.REQUEST, contenu, "pirate@localhost/res")
    )
    assert reponse.get_metadata("performative") == p.FAILURE and len(annuaire.registre) == 0


def test_mise_a_jour_reussie_ne_repond_pas(annuaire):
    inscrire(annuaire)
    maj = MiseAJour("a", (5.33, -4.01), MAINTENANT, 2).vers_contenu()
    assert annuaire.traiter(message(p.ONTO_MAJ, p.INFORM, maj)) == []
    entree = annuaire.registre.obtenir("a")
    assert entree.charge == 2 and entree.position.latitude == 5.33


def test_mise_a_jour_d_un_inconnu_demande_de_se_reinscrire(annuaire):
    maj = MiseAJour("a", charge=1).vers_contenu()
    (reponse,) = annuaire.traiter(message(p.ONTO_MAJ, p.INFORM, maj))
    assert reponse.get_metadata("performative") == p.FAILURE
    assert lire(reponse) == {"motif": "inconnu"}


def test_mise_a_jour_au_nom_d_un_autre_est_refusee(annuaire):
    inscrire(annuaire, "a")
    inscrire(annuaire, "b")
    maj = MiseAJour("b", charge=9).vers_contenu()
    (reponse,) = annuaire.traiter(message(p.ONTO_MAJ, p.INFORM, maj, "livreur-a@localhost/res"))
    assert lire(reponse)["motif"] == "identite"
    assert annuaire.registre.obtenir("b").charge == 0


def test_desinscription(annuaire):
    inscrire(annuaire)
    (reponse,) = annuaire.traiter(message(p.ONTO_DESINSCRIPTION, p.REQUEST, {}))
    assert lire(reponse) == {"statut": "desinscrit", "existait": True}
    assert len(annuaire.registre) == 0
    (encore,) = annuaire.traiter(message(p.ONTO_DESINSCRIPTION, p.REQUEST, {}))
    assert lire(encore)["existait"] is False


def test_recherche(annuaire):
    inscrire(annuaire, "proche", (5.3237, -4.0197))
    inscrire(annuaire, "loin", (5.40, -4.0197))
    demande = Recherche(*POSITION, 3000.0).vers_contenu()
    (reponse,) = annuaire.traiter(
        message(p.ONTO_RECHERCHE, p.QUERY_REF, demande, "dispatcher@localhost/res")
    )
    contenu = lire(reponse)
    assert reponse.get_metadata("performative") == p.INFORM
    assert contenu["total"] == 1 and contenu["candidats"][0]["livreur_id"] == "proche"
    assert set(contenu["candidats"][0]) >= {
        "distance_m",
        "charge",
        "capacite_kg",
        "latitude",
        "longitude",
    }


def test_recherche_limitee_en_taille(annuaire, monkeypatch):
    monkeypatch.setattr("livraison.agents.annuaire.parametres.annuaire_max_resultats", 2)
    for i in range(5):
        inscrire(annuaire, f"l{i}", (5.3236 + i * 0.0001, -4.0197))
    demande = Recherche(*POSITION, 3000.0).vers_contenu()
    (reponse,) = annuaire.traiter(message(p.ONTO_RECHERCHE, p.QUERY_REF, demande))
    contenu = lire(reponse)
    assert contenu["total"] == 5 and len(contenu["candidats"]) == 2


@pytest.mark.parametrize(
    ("ontologie", "performative", "corps"),
    [
        (p.ONTO_INSCRIPTION, p.REQUEST, "pas du json"),
        (p.ONTO_INSCRIPTION, p.REQUEST, "{}"),
        (p.ONTO_MAJ, p.INFORM, "[]"),
        (p.ONTO_RECHERCHE, p.QUERY_REF, '{"latitude": 5.3}'),
        (p.ONTO_RECHERCHE, p.REQUEST, "{}"),  # mauvaise performative
        (p.ONTO_INSCRIPTION, p.INFORM, "{}"),  # mauvaise performative
    ],
)
def test_message_incomprehensible_ne_fait_pas_planter(annuaire, ontologie, performative, corps):
    m = message(ontologie, performative, {})
    m.body = corps
    (reponse,) = annuaire.traiter(m)
    assert reponse.get_metadata("performative") == p.NOT_UNDERSTOOD
    assert len(annuaire.registre) == 0
