"""Règles pures de l'attribution : rayon des tours, exclusions, choix de l'offre.

Du Python simple, sans réseau ni base : ces règles se testent instantanément. Les agents (Dispatcher,
Commande) et le service d'attribution les appliquent ; ils n'en dupliquent aucune.
Voir docs/contract-net.md.
"""

from collections.abc import Iterable

from livraison.modele.cout import Offre, classer_offres


def rayon_suivant(rayon_m: int, *, facteur: float, maximum: int) -> int:
    """Rayon du tour suivant, après un tour resté sans offre : multiplié, sans dépasser le maximum."""
    if rayon_m <= 0 or facteur < 1 or maximum <= 0:
        raise ValueError("Rayon, facteur ou maximum invalide.")
    return min(round(rayon_m * facteur), maximum)


def rayon_du_tour(
    rayon_precedent_m: int | None, *, tour_precedent_vide: bool, initial: int, facteur: float, maximum: int
) -> int:
    """Rayon de recherche d'un tour.

    - premier tour : le rayon initial ;
    - après un tour sans offre : le rayon précédent élargi ;
    - après un refus du livreur humain : le même rayon (des offres existaient, il suffit d'exclure
      le livreur qui a décliné).
    """
    if rayon_precedent_m is None:
        return min(initial, maximum)
    if tour_precedent_vide:
        return rayon_suivant(rayon_precedent_m, facteur=facteur, maximum=maximum)
    return rayon_precedent_m


def tours_epuises(tour: int, tours_max: int) -> bool:
    """Vrai quand le tour `tour` est le dernier autorisé : après lui, on alerte le répartiteur."""
    return tour >= tours_max


def candidats_a_consulter(propositions: Iterable[str], exclus: Iterable[str]) -> list[str]:
    """Livreurs à interroger, sans doublon, sans les exclus, dans l'ordre reçu."""
    ecartes = set(exclus)
    vus: set[str] = set()
    retenus: list[str] = []
    for livreur_id in propositions:
        if livreur_id in ecartes or livreur_id in vus:
            continue
        vus.add(livreur_id)
        retenus.append(livreur_id)
    return retenus


def ordre_de_preference(offres: list[Offre]) -> list[Offre]:
    """Les offres valables, de la meilleure à la moins bonne (à coût égal : la première reçue)."""
    return classer_offres(offres)
