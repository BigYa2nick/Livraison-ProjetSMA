"""Verrou à priorité : quand plusieurs tâches attendent, la plus prioritaire passe la première.

Le Dispatcher décide « quel livreur pour cette commande » une commande à la fois (D-06). Avec un verrou
ordinaire, la première conversation arrivée l'emporte. Ici, les commandes urgentes passent avant les autres
(F-35) ; à rang égal, c'est le premier arrivé, premier servi.

Limite : le verrou ne fait qu'ordonner les conversations qui attendent en même temps. Il n'enlève jamais la
place à une décision déjà prise. Du `asyncio` pur, sans réseau ni base : voir tests/test_verrou_priorite.py.
"""

import asyncio
import contextlib
import heapq
import itertools
from collections.abc import AsyncIterator


class VerrouPrioritaire:
    """Usage : `async with verrou(rang): ...` ; le plus petit rang passe en premier."""

    def __init__(self) -> None:
        self._occupe = False
        # (rang, numéro d'arrivée, futur) : le tas donne le plus petit rang, puis le plus ancien.
        self._attente: list[tuple[int, int, asyncio.Future[None]]] = []
        self._arrivees = itertools.count()

    @property
    def en_attente(self) -> int:
        """Nombre de tâches qui attendent le verrou (sans compter celle qui le tient)."""
        return sum(1 for _, _, futur in self._attente if not futur.done())

    @property
    def occupe(self) -> bool:
        return self._occupe

    async def acquerir(self, rang: int) -> None:
        if not self._occupe and not self._attente:
            self._occupe = True
            return
        futur: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        heapq.heappush(self._attente, (rang, next(self._arrivees), futur))
        try:
            await futur
        except asyncio.CancelledError:
            if futur.done() and not futur.cancelled():
                self.liberer()  # le verrou nous avait été donné : on le passe au suivant
            raise  # sinon notre futur annulé reste dans le tas, `liberer` l'ignorera

    def liberer(self) -> None:
        while self._attente:
            _, _, futur = heapq.heappop(self._attente)
            if not futur.done():
                futur.set_result(None)  # le verrou passe directement au suivant, sans être libre
                return
        self._occupe = False

    @contextlib.asynccontextmanager
    async def __call__(self, rang: int = 1) -> AsyncIterator[None]:
        await self.acquerir(rang)
        try:
            yield
        finally:
            self.liberer()
