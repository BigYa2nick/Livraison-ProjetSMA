"""La vérification du serveur XMPP avant les tests d'agents réels (tests/aides_xmpp.py)."""

import pytest

import aides_xmpp

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def etat_vierge(monkeypatch, tmp_path):
    monkeypatch.setattr(aides_xmpp, "_resultat", None)
    monkeypatch.setattr(aides_xmpp, "_serveur_absent", False)
    monkeypatch.chdir(tmp_path)  # le fichier de journal est écrit dans le dossier courant


async def test_serveur_absent_tests_ignores_sans_reessayer(monkeypatch):
    appels = []
    monkeypatch.setattr(aides_xmpp, "sonde_reseau", lambda: appels.append(1) or "absent")
    for _ in range(3):
        with pytest.raises(pytest.skip.Exception):
            await aides_xmpp.verifier_xmpp()
    assert len(appels) == 1  # la présence n'est vérifiée qu'une fois


async def test_serveur_muet_echec_immediat(monkeypatch):
    monkeypatch.setattr(aides_xmpp, "sonde_reseau", lambda: "muet")
    with pytest.raises(pytest.fail.Exception, match="ne répond pas à un début de session"):
        await aides_xmpp.verifier_xmpp()


async def test_agent_qui_ne_se_connecte_pas_diagnostic_et_journal(monkeypatch, tmp_path):
    async def echec(self, auto_register=False):
        raise TimeoutError

    monkeypatch.setattr(aides_xmpp, "sonde_reseau", lambda: "ok")
    monkeypatch.setattr(aides_xmpp.AgentAnnuaire, "start", echec)
    with pytest.raises(pytest.fail.Exception) as e:
        await aides_xmpp.verifier_xmpp()
    message = str(e.value)
    assert "TimeoutError" in message and "boucle asyncio :" in message and "xmpp_sonde.log" in message
    assert (tmp_path / "xmpp_sonde.log").exists()
    with pytest.raises(pytest.fail.Exception):  # le résultat est gardé : pas de nouvel essai de 15 s
        await aides_xmpp.verifier_xmpp()


async def test_agent_connecte_aucun_echec_et_pas_de_journal(monkeypatch, tmp_path):
    async def ok(self, auto_register=False):
        return None

    async def arret(self):
        return None

    monkeypatch.setattr(aides_xmpp, "sonde_reseau", lambda: "ok")
    monkeypatch.setattr(aides_xmpp.AgentAnnuaire, "start", ok)
    monkeypatch.setattr(aides_xmpp.AgentAnnuaire, "stop", arret)
    await aides_xmpp.verifier_xmpp()
    await aides_xmpp.verifier_xmpp()
    assert not (tmp_path / "xmpp_sonde.log").exists()
