"""Envoi des SMS. En développement, le code est simplement écrit dans les journaux du serveur."""

import logging
from typing import Protocol

from livraison.config import parametres
from livraison.services.securite import ErreurConfiguration

journal = logging.getLogger("livraison.sms")


class EnvoiSms(Protocol):
    async def envoyer_code(self, telephone: str, code: str) -> None: ...


class SmsConsole:
    """Envoi simulé : n'envoie rien, affiche le code dans les journaux."""

    async def envoyer_code(self, telephone: str, code: str) -> None:
        journal.warning("[SMS SIMULÉ] Code de vérification pour %s : %s", telephone, code)


def obtenir_envoi_sms() -> EnvoiSms:
    """Dépendance FastAPI. Un vrai fournisseur SMS sera ajouté ici au moment du déploiement."""
    if parametres.sms_mode == "console":
        if parametres.environnement != "dev":
            raise ErreurConfiguration(
                "SMS_MODE=console est réservé au développement (ENVIRONNEMENT=dev) : "
                "les codes seraient écrits en clair dans les journaux."
            )
        return SmsConsole()
    raise ErreurConfiguration(f"Mode SMS inconnu : {parametres.sms_mode!r}.")
