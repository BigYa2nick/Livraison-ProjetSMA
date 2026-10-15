"""Tests des briques de sécurité (sans base de données)."""

import uuid
from datetime import timedelta

import jwt
import pytest

from livraison.config import parametres
from livraison.services import securite
from livraison.services.securite import ErreurConfiguration, ErreurJeton

TEL = "+2250708091011"


def test_le_code_a_six_chiffres():
    for _ in range(200):
        code = securite.generer_code()
        assert len(code) == 6 and code.isdigit()


def test_hache_du_code_lie_au_numero():
    h = securite.hacher_code(TEL, "123456")
    assert h == securite.hacher_code(TEL, "123456")
    assert h != securite.hacher_code("+2250102030405", "123456")
    assert "123456" not in h


def test_verification_du_code():
    h = securite.hacher_code(TEL, "123456")
    assert securite.code_correct(h, TEL, "123456")
    assert not securite.code_correct(h, TEL, "654321")


def test_mot_de_passe_hache_et_verifie():
    h = securite.hacher_mot_de_passe("MotDePasse-Solide-1")
    assert "MotDePasse" not in h
    assert securite.verifier_mot_de_passe("MotDePasse-Solide-1", h)
    assert not securite.verifier_mot_de_passe("autre-mot-de-passe", h)
    assert h != securite.hacher_mot_de_passe("MotDePasse-Solide-1")  # sel différent à chaque fois


def test_hache_illisible_refuse_sans_planter():
    assert not securite.verifier_mot_de_passe("x", "n'importe quoi")


def test_mot_de_passe_trop_court():
    with pytest.raises(ValueError, match="au moins"):
        securite.valider_mot_de_passe("court")
    securite.valider_mot_de_passe("assez-long-12")


def test_jeton_valide():
    uid = uuid.uuid4()
    jeton = securite.creer_jeton(uid, "client", "acces", timedelta(minutes=5))
    contenu = securite.decoder_jeton(jeton, "acces")
    assert contenu["sub"] == str(uid) and contenu["role"] == "client"


def test_jeton_expire():
    jeton = securite.creer_jeton(uuid.uuid4(), "client", "acces", timedelta(seconds=-5))
    with pytest.raises(ErreurJeton):
        securite.decoder_jeton(jeton, "acces")


def test_jeton_du_mauvais_type():
    jeton = securite.creer_jeton(uuid.uuid4(), "client", "rafraichissement", timedelta(minutes=5))
    with pytest.raises(ErreurJeton):
        securite.decoder_jeton(jeton, "acces")


def test_jeton_falsifie_ou_signe_avec_une_autre_cle():
    jeton = securite.creer_jeton(uuid.uuid4(), "client", "acces", timedelta(minutes=5))
    with pytest.raises(ErreurJeton):
        securite.decoder_jeton(jeton[:-3] + "abc", "acces")
    autre = jwt.encode(
        {"sub": str(uuid.uuid4()), "typ": "acces", "exp": 9999999999},
        "une-autre-cle-secrete-de-plus-de-32-caracteres",
        algorithm="HS256",
    )
    with pytest.raises(ErreurJeton):
        securite.decoder_jeton(autre, "acces")


@pytest.mark.parametrize("secret", ["changez-moi-par-une-longue-chaine-aleatoire", "trop-court"])
def test_secret_jwt_faible_refuse(monkeypatch, secret):
    monkeypatch.setattr(parametres, "jwt_secret", secret)
    with pytest.raises(ErreurConfiguration, match="JWT_SECRET"):
        securite.creer_jeton(uuid.uuid4(), "client", "acces", timedelta(minutes=5))
