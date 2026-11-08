"""Envoi fiable des positions : celles qui n'ont pas pu partir sont gardées puis renvoyées."""

import asyncio
import time
from collections import deque
from collections.abc import Awaitable, Callable
from datetime import datetime

from client_api import ErreurApi, ErreurReseau

# Erreurs du serveur pour lesquelles renvoyer la même position ne servirait à rien.
_STATUTS_DEFINITIFS = {409, 422}


class Emetteur:
    """File d'envoi des positions, dans l'ordre chronologique.

    - réseau coupé : les positions s'accumulent (500 au maximum), puis partent dès le retour ;
    - position refusée par le serveur (hors ligne, horodatage invalide) : abandonnée.
    """

    def __init__(self, envoyer: Callable[[dict], Awaitable[dict]], taille_max: int = 500):
        self._envoyer = envoyer
        self._verrou = asyncio.Lock()
        self.attente: deque[dict] = deque()
        self.taille_max = taille_max
        self.envoyees = 0
        self.perdues = 0  # supprimées parce que la file était pleine
        self.abandonnees = 0  # refusées par le serveur
        self.echecs_reseau = 0
        self.dernier_retard_s: float | None = None  # mesure sur téléphone -> réception serveur
        self.derniere_latence_ms: float | None = None  # durée de la requête
        self.derniere_erreur: str | None = None

    async def pousser(self, position: dict) -> None:
        if len(self.attente) >= self.taille_max:
            self.attente.popleft()
            self.perdues += 1
        self.attente.append(position)
        await self.vider()

    async def vider(self) -> None:
        async with self._verrou:
            while self.attente:
                position = self.attente[0]
                debut = time.perf_counter()
                try:
                    reponse = await self._envoyer(position)
                except ErreurReseau as e:
                    self.echecs_reseau += 1
                    self.derniere_erreur = str(e)
                    return  # on réessaiera au prochain appel
                except ErreurApi as e:
                    self.derniere_erreur = str(e)
                    if e.statut in _STATUTS_DEFINITIFS:
                        self.attente.popleft()
                        self.abandonnees += 1
                        continue
                    return
                self.attente.popleft()
                self.envoyees += 1
                self.derniere_erreur = None
                self.derniere_latence_ms = (time.perf_counter() - debut) * 1000
                self.dernier_retard_s = reponse.get("retard_s")


class SuiviCadence:
    """Repère les trous dans la réception des positions du GPS (écran éteint, économie d'énergie...)."""

    def __init__(self, seuil_s: float = 15.0):
        self.seuil_s = seuil_s
        self._derniere: datetime | None = None
        self.recues = 0
        self.nb_trous = 0
        self.trou_max_s = 0.0

    def enregistrer(self, instant: datetime) -> float | None:
        """Note une position reçue. Renvoie la durée du trou (en secondes) s'il dépasse le seuil."""
        self.recues += 1
        trou = None
        if self._derniere is not None:
            ecart = (instant - self._derniere).total_seconds()
            self.trou_max_s = max(self.trou_max_s, ecart)
            if ecart > self.seuil_s:
                self.nb_trous += 1
                trou = ecart
        self._derniere = instant
        return trou

    def reinitialiser(self) -> None:
        self.__init__(self.seuil_s)
