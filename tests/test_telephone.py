"""Tests de la normalisation des numéros de téléphone."""

import pytest

from livraison.modele.telephone import TelephoneInvalide, normaliser_telephone


@pytest.mark.parametrize(
    ("saisie", "attendu"),
    [
        ("0708091011", "+2250708091011"),
        ("07 08 09 10 11", "+2250708091011"),
        ("07.08.09.10.11", "+2250708091011"),
        ("+225 07 08 09 10 11", "+2250708091011"),
        ("00225 0708091011", "+2250708091011"),
        ("2250708091011", "+2250708091011"),
        ("+33 6 12 34 56 78", "+33612345678"),
        ("  0102030405  ", "+2250102030405"),
    ],
)
def test_numeros_valides(saisie, attendu):
    assert normaliser_telephone(saisie) == attendu


@pytest.mark.parametrize(
    "saisie", ["", "abc", "0708", "+12", "07 08 09 10 1x", "+2250708091011999999"]
)
def test_numeros_invalides(saisie):
    with pytest.raises(TelephoneInvalide):
        normaliser_telephone(saisie)
