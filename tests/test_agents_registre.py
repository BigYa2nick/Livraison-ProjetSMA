"""Registre de l'Annuaire : inscription, recherche autour d'un point, péremption."""

from datetime import UTC, datetime, timedelta

from livraison.agents.registre import EntreeLivreur, RegistreLivreurs
from livraison.services.itineraires import Point

DEBUT = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)
PLATEAU = Point(5.3236, -4.0197)


class Horloge:
    def __init__(self):
        self.maintenant = DEBUT

    def __call__(self) -> datetime:
        return self.maintenant

    def avancer(self, secondes: float) -> None:
        self.maintenant += timedelta(seconds=secondes)


def decale(nord_m: float) -> Point:
    """Un point situé `nord_m` mètres au nord du Plateau (1 degré de latitude = 111 195 m)."""
    return Point(PLATEAU.latitude + nord_m / 111_195, PLATEAU.longitude)


def entree(livreur_id, nord_m=0.0, *, vehicule="moto", capacite=25, charge=0, horloge=None):
    return EntreeLivreur(
        livreur_id=livreur_id,
        type_vehicule=vehicule,
        capacite_kg=capacite,
        charge=charge,
        position=decale(nord_m),
        mesure_le=(horloge or Horloge())(),
    )


def registre_avec(horloge, *entrees):
    registre = RegistreLivreurs(age_position_max_s=30, horloge=horloge)
    for e in entrees:
        registre.inscrire(e)
    return registre


def test_inscription_nouvelle_puis_mise_a_jour():
    registre = RegistreLivreurs(30)
    assert registre.inscrire(entree("a")) is True
    assert registre.inscrire(entree("a", 500)) is False  # déjà connu : on remplace
    assert len(registre) == 1 and "a" in registre


def test_desinscription():
    registre = RegistreLivreurs(30)
    registre.inscrire(entree("a"))
    assert registre.desinscrire("a") is True
    assert registre.desinscrire("a") is False
    assert len(registre) == 0


def test_mise_a_jour_d_un_livreur_inconnu_est_signalee():
    assert RegistreLivreurs(30).mettre_a_jour("inconnu", charge=1) is False


def test_mise_a_jour_position_et_charge():
    h = Horloge()
    registre = registre_avec(h, entree("a", horloge=h))
    h.avancer(5)
    assert registre.mettre_a_jour("a", position=decale(300), mesure_le=h(), charge=2) is True
    e = registre.obtenir("a")
    assert e.position == decale(300) and e.charge == 2 and e.mesure_le == h()


def test_une_position_en_retard_n_ecrase_pas_une_position_recente():
    h = Horloge()
    registre = registre_avec(h, entree("a", horloge=h))
    recente = DEBUT + timedelta(seconds=20)
    registre.mettre_a_jour("a", position=decale(500), mesure_le=recente)
    registre.mettre_a_jour("a", position=decale(100), mesure_le=DEBUT + timedelta(seconds=10))
    assert registre.obtenir("a").position == decale(500)


def test_recherche_dans_le_rayon_du_plus_proche_au_plus_loin():
    h = Horloge()
    registre = registre_avec(
        h,
        entree("loin", 2500, horloge=h),
        entree("proche", 200, horloge=h),
        entree("moyen", 1000, horloge=h),
    )
    trouves = registre.rechercher(PLATEAU, 3000)
    assert [c.livreur_id for c in trouves] == ["proche", "moyen", "loin"]
    assert trouves[0].distance_m == 200 or abs(trouves[0].distance_m - 200) < 1
    assert [c.livreur_id for c in registre.rechercher(PLATEAU, 1500)] == ["proche", "moyen"]
    assert registre.rechercher(PLATEAU, 100) == []


def test_recherche_filtres():
    h = Horloge()
    registre = registre_avec(
        h,
        entree("moto", 100, vehicule="moto", capacite=25, horloge=h),
        entree("velo", 200, vehicule="velo", capacite=10, horloge=h),
        entree("camion", 300, vehicule="voiture", capacite=300, horloge=h),
    )
    assert [c.livreur_id for c in registre.rechercher(PLATEAU, 1000, type_vehicule="velo")] == [
        "velo"
    ]
    assert [c.livreur_id for c in registre.rechercher(PLATEAU, 1000, poids_kg=100)] == ["camion"]
    assert [c.livreur_id for c in registre.rechercher(PLATEAU, 1000, exclure=["moto"])] == [
        "velo",
        "camion",
    ]
    assert len(registre.rechercher(PLATEAU, 1000, limite=2)) == 2


def test_un_livreur_sans_position_n_est_jamais_propose():
    registre = RegistreLivreurs(30)
    registre.inscrire(EntreeLivreur("sans", "moto", 25))
    assert registre.rechercher(PLATEAU, 100_000) == []


def test_position_perimee_ecartee_sauf_demande_contraire():
    h = Horloge()
    registre = registre_avec(h, entree("a", 100, horloge=h))
    h.avancer(31)  # plus de 30 s sans nouvelle position
    assert registre.rechercher(PLATEAU, 1000) == []
    tous = registre.rechercher(PLATEAU, 1000, inclure_perimees=True)
    assert [c.livreur_id for c in tous] == ["a"] and tous[0].age_position_s == 31


def test_purge_des_agents_silencieux():
    h = Horloge()
    registre = registre_avec(h, entree("vivant", horloge=h), entree("mort", horloge=h))
    h.avancer(50)
    registre.mettre_a_jour("vivant", charge=0)  # un signe de vie
    h.avancer(20)
    assert registre.purger(silence_max_s=60) == ["mort"]
    assert "vivant" in registre and "mort" not in registre
