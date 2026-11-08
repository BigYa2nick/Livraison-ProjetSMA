"""Agent Livreur : réactions aux messages de l'Annuaire et à la position, sans réseau."""

import json
from datetime import UTC, datetime, timedelta

import pytest

from livraison.agents import protocole as p
from livraison.agents.livreur import AgentLivreur

MAINTENANT = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)


@pytest.fixture
def agent():
    return AgentLivreur(
        "livreur-abc@localhost",
        "x",
        livreur_id="abc",
        type_vehicule="moto",
        capacite_kg=25,
        annuaire_jid="annuaire-unit@localhost",
    )


def vide_la_file(agent):
    messages = []
    while not agent._sortie.empty():
        messages.append(agent._sortie.get_nowait())
    return messages


def reponse(ontologie, performative, contenu=None):
    m = p.creer_message("livreur-abc@localhost", performative, ontologie, contenu)
    m.sender = "annuaire-unit@localhost/res"
    return m


def test_l_inscription_decrit_le_livreur(agent):
    agent.position, agent.mesure_le = (5.32, -4.02), MAINTENANT
    contenu = agent.inscription.vers_contenu()
    assert contenu["livreur_id"] == "abc" and contenu["type_vehicule"] == "moto"
    assert contenu["capacite_kg"] == 25 and contenu["latitude"] == 5.32


def test_demande_d_inscription(agent):
    agent.demander_inscription()
    (message,) = vide_la_file(agent)
    assert str(message.to) == "annuaire-unit@localhost"
    assert message.get_metadata("performative") == p.REQUEST
    assert message.get_metadata("ontology") == p.ONTO_INSCRIPTION
    assert json.loads(message.body)["livreur_id"] == "abc"
    assert not agent.inscrit.is_set()


def test_confirmation_d_inscription(agent):
    agent.traiter(reponse(p.ONTO_INSCRIPTION, p.INFORM, {"statut": "inscrit"}))
    assert agent.inscrit.is_set()


def test_confirmation_de_desinscription(agent):
    agent.traiter(reponse(p.ONTO_DESINSCRIPTION, p.INFORM, {"statut": "desinscrit"}))
    assert agent.desinscrit.is_set()


def test_l_annuaire_ne_connait_plus_le_livreur_il_se_reinscrit(agent):
    agent.inscrit.set()
    agent.traiter(reponse(p.ONTO_MAJ, p.FAILURE, {"motif": "inconnu"}))
    (message,) = vide_la_file(agent)
    assert message.get_metadata("ontology") == p.ONTO_INSCRIPTION
    assert message.get_metadata("performative") == p.REQUEST
    assert not agent.inscrit.is_set()  # on attend la nouvelle confirmation


def test_une_nouvelle_position_est_annoncee(agent):
    agent.position_recue(5.33, -4.01, MAINTENANT)
    assert agent.position == (5.33, -4.01) and agent.mesure_le == MAINTENANT
    (message,) = vide_la_file(agent)
    assert message.get_metadata("ontology") == p.ONTO_MAJ
    assert message.get_metadata("performative") == p.INFORM
    contenu = json.loads(message.body)
    assert contenu["latitude"] == 5.33 and contenu["longitude"] == -4.01


def test_une_position_plus_ancienne_est_ignoree(agent):
    agent.position_recue(5.33, -4.01, MAINTENANT)
    vide_la_file(agent)
    agent.position_recue(5.10, -4.00, MAINTENANT - timedelta(seconds=30))
    assert agent.position == (5.33, -4.01)
    assert vide_la_file(agent) == []  # rien n'est envoyé


def test_changement_de_charge(agent):
    agent.changer_charge(2)
    (message,) = vide_la_file(agent)
    assert json.loads(message.body)["charge"] == 2
    with pytest.raises(ValueError):
        agent.changer_charge(-1)
    assert agent.charge == 2


def test_l_annuaire_qui_ne_comprend_pas_est_journalise(agent, caplog):
    with caplog.at_level("ERROR", logger="livraison.agents.livreur"):
        agent.traiter(reponse(p.ONTO_INSCRIPTION, p.NOT_UNDERSTOOD, {"motif": "x"}))
    assert "not-understood" in caplog.text
