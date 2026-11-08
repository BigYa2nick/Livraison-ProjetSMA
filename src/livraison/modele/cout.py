"""Calcul du coût d'une offre et choix de la meilleure offre.

Deux versions du coût :
- la version « grille » reprend la formule du TP JADE et sert de test de non-régression ;
- la version « itinéraire réel » (lot 2) utilise les durées calculées par le service d'itinéraires.

Du Python simple, sans réseau ni base : la pénalité par course portée est un paramètre, qui vient de
la configuration (`ATTRIBUTION_PENALITE_CHARGE_S`) chez l'appelant.
"""

from dataclasses import dataclass

PENALITE_CHARGE = 3  # version « grille » : coût d'une course portée, en cases


@dataclass(frozen=True)
class Offre:
    livreur: str
    cout: float


def cout_grille(position: tuple[int, int], destination: tuple[int, int], charge: int) -> int:
    """Formule du TP : distance de Manhattan + 3 x charge."""
    (x, y), (xd, yd) = position, destination
    return abs(xd - x) + abs(yd - y) + PENALITE_CHARGE * charge


def cout_itineraire(
    duree_enlevement_s: float, charge: int, *, penalite_s: float, detour_s: float = 0.0
) -> float:
    """Coût d'une offre, en secondes (docs/conception/03-sequences-protocole.md, § 3.2).

    coût = durée(position du livreur vers l'enlèvement) + durée du détour + pénalité x charge

    `charge` est le nombre de courses que le livreur porte déjà. Le détour vaut 0 au lot 2 : le
    regroupement des courses (lot 7) le calculera ; la formule le prend déjà en compte.
    """
    if duree_enlevement_s < 0 or detour_s < 0:
        raise ValueError("Une durée ne peut pas être négative.")
    if charge < 0:
        raise ValueError("La charge ne peut pas être négative.")
    if penalite_s < 0:
        raise ValueError("La pénalité ne peut pas être négative.")
    return duree_enlevement_s + detour_s + penalite_s * charge


def classer_offres(offres: list[Offre]) -> list[Offre]:
    """Offres du moins cher au plus cher ; à coût égal, la première reçue passe devant."""
    return sorted(offres, key=lambda o: o.cout)  # le tri de Python est stable


def meilleure_offre(offres: list[Offre]) -> Offre | None:
    """Offre de coût minimal ; en cas d'égalité, la première reçue l'emporte."""
    return min(offres, key=lambda o: o.cout, default=None)
