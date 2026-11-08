"""Registre des livreurs disponibles : la mémoire de l'agent Annuaire.

Du Python simple, sans SPADE ni réseau : il se teste très vite. L'Annuaire n'est **pas** une source
de vérité (décision D-04) : si le programme redémarre, le registre se reconstruit tout seul, parce
que chaque agent Livreur se réinscrit.
"""

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime

from livraison.services.itineraires import Point, distance_vol_d_oiseau_m


@dataclass
class EntreeLivreur:
    livreur_id: str
    type_vehicule: str
    capacite_kg: int
    charge: int = 0
    position: Point | None = None
    mesure_le: datetime | None = None  # heure de la mesure de la position
    vu_le: datetime | None = None  # dernier signe de vie de l'agent (inscription, mise à jour)


@dataclass(frozen=True)
class Candidat:
    """Un livreur proposé au Dispatcher, avec sa distance au point demandé."""

    livreur_id: str
    distance_m: float
    type_vehicule: str
    capacite_kg: int
    charge: int
    latitude: float
    longitude: float
    age_position_s: float

    def vers_contenu(self) -> dict:
        return {
            "livreur_id": self.livreur_id,
            "distance_m": round(self.distance_m, 1),
            "type_vehicule": self.type_vehicule,
            "capacite_kg": self.capacite_kg,
            "charge": self.charge,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "age_position_s": round(self.age_position_s, 1),
        }


class RegistreLivreurs:
    def __init__(
        self,
        age_position_max_s: float,
        horloge: Callable[[], datetime] = lambda: datetime.now(UTC),
    ):
        self._age_max = age_position_max_s
        self._horloge = horloge
        self._entrees: dict[str, EntreeLivreur] = {}

    def __len__(self) -> int:
        return len(self._entrees)

    def __contains__(self, livreur_id: str) -> bool:
        return livreur_id in self._entrees

    def obtenir(self, livreur_id: str) -> EntreeLivreur | None:
        return self._entrees.get(livreur_id)

    def inscrire(self, entree: EntreeLivreur) -> bool:
        """Inscrit ou met à jour un livreur. Renvoie True si c'est une première inscription."""
        nouveau = entree.livreur_id not in self._entrees
        entree.vu_le = self._horloge()
        self._entrees[entree.livreur_id] = entree
        return nouveau

    def desinscrire(self, livreur_id: str) -> bool:
        return self._entrees.pop(livreur_id, None) is not None

    def mettre_a_jour(
        self,
        livreur_id: str,
        *,
        position: Point | None = None,
        mesure_le: datetime | None = None,
        charge: int | None = None,
    ) -> bool:
        """Renvoie False si le livreur est inconnu (l'agent doit alors se réinscrire)."""
        entree = self._entrees.get(livreur_id)
        if entree is None:
            return False
        entree.vu_le = self._horloge()
        # Une position en retard (réseau lent) ne doit pas écraser une position plus récente.
        if position is not None and (
            entree.mesure_le is None or mesure_le is None or mesure_le >= entree.mesure_le
        ):
            entree.position = position
            entree.mesure_le = mesure_le
        if charge is not None:
            entree.charge = charge
        return True

    def purger(self, silence_max_s: float) -> list[str]:
        """Retire les livreurs dont l'agent est silencieux depuis trop longtemps (mort ou perdu)."""
        limite = self._horloge()
        retires = [
            i
            for i, e in self._entrees.items()
            if e.vu_le is not None and (limite - e.vu_le).total_seconds() > silence_max_s
        ]
        for livreur_id in retires:
            del self._entrees[livreur_id]
        return retires

    def rechercher(
        self,
        centre: Point,
        rayon_m: float,
        *,
        type_vehicule: str | None = None,
        poids_kg: int | None = None,
        exclure: Iterable[str] = (),
        inclure_perimees: bool = False,
        limite: int | None = None,
    ) -> list[Candidat]:
        """Livreurs à moins de `rayon_m` du centre, du plus proche au plus éloigné.

        Écartés : ceux sans position, ceux dont la position est trop ancienne (sauf
        `inclure_perimees`), les exclus, ceux d'un autre véhicule et ceux dont la capacité est
        inférieure au poids. La distance est à vol d'oiseau : c'est une présélection, le coût
        exact est calculé ensuite par chaque agent sur l'itinéraire réel.
        """
        exclus = set(exclure)
        maintenant = self._horloge()
        candidats: list[Candidat] = []
        for e in self._entrees.values():
            if e.livreur_id in exclus or e.position is None or e.mesure_le is None:
                continue
            if type_vehicule is not None and e.type_vehicule != type_vehicule:
                continue
            if poids_kg is not None and e.capacite_kg < poids_kg:
                continue
            age = (maintenant - e.mesure_le).total_seconds()
            if age > self._age_max and not inclure_perimees:
                continue
            distance = distance_vol_d_oiseau_m(centre, e.position)
            if distance > rayon_m:
                continue
            candidats.append(
                Candidat(
                    livreur_id=e.livreur_id,
                    distance_m=distance,
                    type_vehicule=e.type_vehicule,
                    capacite_kg=e.capacite_kg,
                    charge=e.charge,
                    latitude=e.position.latitude,
                    longitude=e.position.longitude,
                    age_position_s=age,
                )
            )
        candidats.sort(key=lambda c: (c.distance_m, c.livreur_id))
        return candidats if limite is None else candidats[:limite]
