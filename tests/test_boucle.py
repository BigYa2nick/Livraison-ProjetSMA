"""Choix de la boucle asyncio pour XMPP (livraison/agents/boucle.py)."""

import asyncio
import sys

import pytest

from livraison.agents import boucle


class FaussePolitique:
    """Remplace WindowsSelectorEventLoopPolicy, qui n'existe que sous Windows."""


@pytest.fixture
def politique_posee(monkeypatch):
    posees = []
    monkeypatch.setattr(asyncio, "set_event_loop_policy", posees.append)
    return posees


def test_hors_windows_rien_ne_change(monkeypatch, politique_posee):
    monkeypatch.setattr(sys, "platform", "linux")
    boucle.utiliser_boucle_compatible_xmpp()
    assert politique_posee == []


def test_sous_windows_la_boucle_selector_est_imposee(monkeypatch, politique_posee):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(asyncio, "WindowsSelectorEventLoopPolicy", FaussePolitique, raising=False)
    boucle.utiliser_boucle_compatible_xmpp()
    assert len(politique_posee) == 1 and isinstance(politique_posee[0], FaussePolitique)
