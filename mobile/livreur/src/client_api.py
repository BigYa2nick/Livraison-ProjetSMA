"""Client HTTP du serveur Livraison SMA, utilisé par l'application du livreur."""

from datetime import datetime

import httpx


class ErreurReseau(Exception):
    """Le serveur n'a pas pu être joint (pas de réseau, délai dépassé...). On peut réessayer."""


class ErreurApi(Exception):
    """Le serveur a répondu par une erreur."""

    def __init__(self, statut: int, detail: str):
        super().__init__(f"{statut} {detail}")
        self.statut = statut
        self.detail = detail


class ClientApi:
    def __init__(self, base_url: str, http: httpx.AsyncClient | None = None, delai_s: float = 10.0):
        self._http = http or httpx.AsyncClient(base_url=base_url.strip().rstrip("/"), timeout=delai_s)
        self.acces: str | None = None
        self.rafraichissement: str | None = None

    async def fermer(self) -> None:
        await self._http.aclose()

    # -- appel générique -------------------------------------------------------------

    async def _requete(self, methode: str, chemin: str, json: dict | None = None, *, auth=True):
        for essai in range(2):
            entetes = {"Authorization": f"Bearer {self.acces}"} if auth and self.acces else {}
            try:
                r = await self._http.request(methode, chemin, json=json, headers=entetes)
            except httpx.HTTPError as e:  # coupure réseau, délai dépassé, serveur arrêté...
                raise ErreurReseau(f"{type(e).__name__}: {e}") from e
            if r.status_code == 401 and auth and essai == 0 and await self._rafraichir():
                continue  # jeton d'accès expiré : on réessaie avec le nouveau
            if r.status_code >= 400:
                try:
                    detail = r.json().get("detail", r.text)
                except ValueError:
                    detail = r.text
                raise ErreurApi(r.status_code, str(detail))
            return r.json()

    async def _rafraichir(self) -> bool:
        if not self.rafraichissement:
            return False
        try:
            r = await self._http.post(
                "/auth/rafraichir", json={"refresh_token": self.rafraichissement}
            )
        except httpx.HTTPError as e:
            raise ErreurReseau(f"{type(e).__name__}: {e}") from e
        if r.status_code != 200:
            return False
        self._memoriser(r.json())
        return True

    def _memoriser(self, jetons: dict) -> None:
        self.acces = jetons["access_token"]
        self.rafraichissement = jetons["refresh_token"]

    # -- authentification ------------------------------------------------------------

    async def demander_code(self, telephone: str) -> None:
        await self._requete("POST", "/auth/code", {"telephone": telephone}, auth=False)

    async def verifier_code(self, telephone: str, code: str) -> bool:
        """Valide le code SMS. Renvoie True si le compte vient d'être créé."""
        jetons = await self._requete(
            "POST",
            "/auth/verifier",
            {"telephone": telephone, "code": code, "role": "livreur"},
            auth=False,
        )
        self._memoriser(jetons)
        return jetons["nouveau_compte"]

    # -- livreur ---------------------------------------------------------------------

    async def definir_profil(
        self, type_vehicule: str = "moto", capacite_kg: int = 25, immatriculation: str | None = None
    ) -> dict:
        return await self._requete(
            "PUT",
            "/livreurs/moi/profil",
            {
                "type_vehicule": type_vehicule,
                "capacite_kg": capacite_kg,
                "immatriculation": immatriculation,
            },
        )

    async def passer_en_ligne(self) -> dict:
        return await self._requete("POST", "/livreurs/moi/en-ligne")

    async def passer_hors_ligne(self) -> dict:
        return await self._requete("POST", "/livreurs/moi/hors-ligne")

    async def envoyer_position(
        self, latitude: float, longitude: float, precision_m: float | None, mesure_le: datetime
    ) -> dict:
        return await self._requete(
            "POST",
            "/livreurs/moi/position",
            {
                "latitude": latitude,
                "longitude": longitude,
                "precision_m": precision_m,
                "mesure_le": mesure_le.isoformat(),
            },
        )