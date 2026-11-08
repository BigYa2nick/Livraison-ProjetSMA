"""Erreur métier générique, convertie en réponse HTTP par l'API."""


class ErreurMetier(Exception):
    """Une règle du service n'est pas respectée ; porte le code HTTP à renvoyer."""

    def __init__(self, statut: int, detail: str):
        super().__init__(detail)
        self.statut = statut
        self.detail = detail
