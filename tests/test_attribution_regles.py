"""Règles pures de l'attribution : rayon des tours, exclusions, ordre des offres."""

import pytest

from livraison.modele import attribution as r
from livraison.modele.cout import Offre, classer_offres, cout_itineraire, meilleure_offre

PARAMS = {"initial": 3000, "facteur": 2.0, "maximum": 20000}


def test_premier_tour_rayon_initial():
    assert r.rayon_du_tour(None, tour_precedent_vide=True, **PARAMS) == 3000


def test_tour_vide_elargit_le_rayon_jusqu_au_maximum():
    rayon = r.rayon_du_tour(None, tour_precedent_vide=False, **PARAMS)
    suite = []
    for _ in range(4):
        rayon = r.rayon_du_tour(rayon, tour_precedent_vide=True, **PARAMS)
        suite.append(rayon)
    assert suite == [6000, 12000, 20000, 20000]


def test_apres_un_refus_humain_le_rayon_ne_change_pas():
    assert r.rayon_du_tour(6000, tour_precedent_vide=False, **PARAMS) == 6000


def test_rayon_invalide():
    with pytest.raises(ValueError):
        r.rayon_suivant(0, facteur=2, maximum=10)
    with pytest.raises(ValueError):
        r.rayon_suivant(100, facteur=0.5, maximum=10)


@pytest.mark.parametrize(("tour", "attendu"), [(1, False), (5, False), (6, True), (7, True)])
def test_tours_epuises(tour, attendu):
    assert r.tours_epuises(tour, 6) is attendu


def test_candidats_sans_doublon_ni_exclus_dans_l_ordre():
    assert r.candidats_a_consulter(["b", "a", "b", "c", "d"], exclus={"a", "d"}) == ["b", "c"]


def test_classement_des_offres_premiere_recue_a_cout_egal():
    offres = [Offre("L1", 300), Offre("L2", 120), Offre("L3", 120), Offre("L4", 500)]
    assert [o.livreur for o in classer_offres(offres)] == ["L2", "L3", "L1", "L4"]
    assert meilleure_offre(offres).livreur == "L2"
    assert [o.livreur for o in r.ordre_de_preference(offres)] == ["L2", "L3", "L1", "L4"]


def test_cout_itineraire_reprend_l_exemple_du_document_de_conception():
    """docs/conception/03-sequences-protocole.md : A = 420 s, B = 180 + 240 + 2 x 300 = 1 020 s."""
    a = cout_itineraire(420, 0, penalite_s=300)
    b = cout_itineraire(180, 2, penalite_s=300, detour_s=240)
    assert (a, b) == (420, 1020)
    assert meilleure_offre([Offre("A", a), Offre("B", b)]).livreur == "A"


@pytest.mark.parametrize(
    "arguments",
    [(-1, 0, 300, 0), (10, -1, 300, 0), (10, 0, -5, 0), (10, 0, 300, -1)],
)
def test_cout_itineraire_refuse_les_valeurs_negatives(arguments):
    duree, charge, penalite, detour = arguments
    with pytest.raises(ValueError):
        cout_itineraire(duree, charge, penalite_s=penalite, detour_s=detour)
