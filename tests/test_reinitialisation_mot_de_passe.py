"""Réinitialisation du mot de passe du personnel (scripts/reinitialiser_mot_de_passe.py)."""

import importlib.util
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from aides_commandes import creer_livreur
from livraison.persistance.enums import Role
from livraison.persistance.modeles import Utilisateur

pytestmark = pytest.mark.asyncio

CHEMIN = Path(__file__).resolve().parents[1] / "scripts" / "reinitialiser_mot_de_passe.py"


@pytest.fixture(scope="module")
def script():
    spec = importlib.util.spec_from_file_location("reinitialiser_mot_de_passe", CHEMIN)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
def hachage_previsible(script, monkeypatch):
    monkeypatch.setattr(script.securite, "hacher_mot_de_passe", lambda mdp: f"hache::{mdp}")


async def personnel(session, telephone, role=Role.REPARTITEUR, **options):
    compte = Utilisateur(telephone=telephone, role=role, nom="Demo", mot_de_passe_hash="ancien", **options)
    session.add(compte)
    await session.flush()
    return compte


async def test_le_mot_de_passe_est_remplace_et_le_compte_deverrouille(session, script):
    compte = await personnel(
        session, "+2250101010101", echecs_connexion=5, verrouille_jusqu_le=datetime.now(UTC) + timedelta(hours=1)
    )
    resultat = await script.reinitialiser(session, "0101010101", "Nouveau-Mdp-2026")
    assert resultat.id == compte.id
    assert compte.mot_de_passe_hash == "hache::Nouveau-Mdp-2026"
    assert compte.echecs_connexion == 0 and compte.verrouille_jusqu_le is None


@pytest.mark.parametrize("saisie", ["0101010101", "01 01 01 01 01", "+225 01 01 01 01 01", "0101010101 "])
async def test_le_numero_se_saisit_comme_on_veut(session, script, saisie):
    compte = await personnel(session, "+2250101010101", role=Role.ADMINISTRATEUR)
    await script.reinitialiser(session, saisie, "Nouveau-Mdp-2026")
    assert compte.mot_de_passe_hash == "hache::Nouveau-Mdp-2026"


async def test_un_livreur_n_est_jamais_touche(session, script):
    livreur = await creer_livreur(session)
    compte = await session.get(Utilisateur, uuid.UUID(livreur))
    chiffres = compte.telephone[-10:]
    with pytest.raises(script.ErreurReinitialisation, match="Aucun compte"):
        await script.reinitialiser(session, chiffres, "Nouveau-Mdp-2026")
    assert compte.mot_de_passe_hash is None


@pytest.mark.parametrize(
    ("telephone", "mot_de_passe", "message"),
    [("0101", "Nouveau-Mdp-2026", "trop court"), ("0101010101", "court", "au moins"), ("0999999999", "Nouveau-Mdp-2026", "Aucun compte")],
)
async def test_refus_sans_rien_modifier(session, script, telephone, mot_de_passe, message):
    compte = await personnel(session, "+2250101010101")
    with pytest.raises(script.ErreurReinitialisation, match=message):
        await script.reinitialiser(session, telephone, mot_de_passe)
    assert compte.mot_de_passe_hash == "ancien"


async def test_deux_comptes_possibles_il_faut_preciser(session, script):
    a = await personnel(session, "+2250101010101")
    b = await personnel(session, "+2250201010101", role=Role.ADMINISTRATEUR)
    with pytest.raises(script.ErreurReinitialisation, match="Plusieurs"):
        await script.reinitialiser(session, "01010101", "Nouveau-Mdp-2026")
    assert a.mot_de_passe_hash == b.mot_de_passe_hash == "ancien"


async def test_deux_saisies_differentes_ne_changent_rien(script, monkeypatch):
    reponses = iter(["Premier-Mdp-2026", "Autre-Mdp-2026"])
    monkeypatch.setattr(script.getpass, "getpass", lambda invite: next(reponses))
    with pytest.raises(script.ErreurReinitialisation, match="différentes"):
        script.demander_mot_de_passe()
