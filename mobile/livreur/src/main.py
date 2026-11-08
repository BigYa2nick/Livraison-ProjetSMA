"""Prototype GPS du livreur (Flet) : sert à tester le suivi en arrière-plan sur un vrai téléphone.

Trois modes de collecte, pour comparer ce qui marche vraiment :
- flux     : le GPS pousse les positions, avec un service Android au premier plan (le seul mode
             qui peut continuer écran éteint) ;
- sondage  : l'application demande la position toutes les 5 secondes ;
- simulé   : fausses positions le long d'un trajet à Abidjan (test sans GPS, sur ordinateur).

L'écran affiche en continu les trous dans la réception du GPS et le retard d'envoi : ce sont
les mesures qui décident si Flet suffit ou s'il faut un module natif (voir docs/prototype-gps.md).
"""

import asyncio
from datetime import UTC, datetime

import flet as ft
import flet_geolocator as ftg

from client_api import ClientApi, ErreurApi, ErreurReseau
from emetteur import Emetteur, SuiviCadence
from simulateur import Simulateur

def _instant_mesure(p) -> datetime:
    """Heure de la mesure GPS.

    Si l'horodatage fourni par Flet est absurde (ex. 1970 sous Windows desktop),
    on utilise l'heure du PC à la place.
    """
    maintenant = datetime.now(UTC)
    ts = getattr(p, "timestamp", None)
    try:
        if ts is not None:
            ecart = (maintenant - ts).total_seconds()
            if -60 <= ecart <= 3600:  # entre 1 min dans le futur et 1 h dans le passé
                return ts
    except TypeError:  # datetime sans fuseau horaire
        pass
    return maintenant

INTERVALLE_S = 5
SEUIL_TROU_S = 15
MODES = {
    "flux": "GPS en flux (service au premier plan)",
    "sondage": "GPS par sondage (toutes les 5 s)",
    "simule": "Simulation (sans GPS)",
}


class Etat:
    def __init__(self):
        self.api: ClientApi | None = None
        self.emetteur: Emetteur | None = None
        self.cadence = SuiviCadence(seuil_s=SEUIL_TROU_S)
        self.en_ligne = False
        self.mode = "flux"
        self.service_gps: ftg.Geolocator | None = None
        self.tache: asyncio.Task | None = None
        self.debut: datetime | None = None


async def main(page: ft.Page):
    page.title = "Livreur - prototype GPS"
    page.theme_mode = ft.ThemeMode.LIGHT
    page.padding = 12
    page.scroll = ft.ScrollMode.AUTO

    etat = Etat()
    # Service « outils » : permissions, réglages, position ponctuelle. Il ne diffuse rien tout seul.
    outils = ftg.Geolocator()
    page.services.append(outils)

    journal = ft.ListView(controls=[], height=260, spacing=2, auto_scroll=True)
    statistiques = ft.Text("", size=13, selectable=True)
    permission = ft.Text("Permission : inconnue", size=13)

    def log(message: str, couleur=None):
        heure = datetime.now().strftime("%H:%M:%S")
        journal.controls.append(
            ft.Text(f"{heure}  {message}", size=12, color=couleur, selectable=True)
        )
        if len(journal.controls) > 150:
            del journal.controls[:30]
        page.update()

    def afficher_statistiques():
        if etat.emetteur is None:
            return
        e, c = etat.emetteur, etat.cadence
        duree = (datetime.now(UTC) - etat.debut).total_seconds() / 60 if etat.debut else 0
        retard = f"{e.dernier_retard_s:.1f} s" if e.dernier_retard_s is not None else "-"
        latence = f"{e.derniere_latence_ms:.0f} ms" if e.derniere_latence_ms is not None else "-"
        statistiques.value = (
            f"En ligne depuis {duree:.1f} min\n"
            f"Positions GPS reçues : {c.recues}   trous > {SEUIL_TROU_S} s : {c.nb_trous}"
            f"   plus long trou : {c.trou_max_s:.0f} s\n"
            f"Envoyées : {e.envoyees}   en attente : {len(e.attente)}   refusées : {e.abandonnees}"
            f"   perdues : {e.perdues}\n"
            f"Dernier retard mesure->serveur : {retard}   requête : {latence}"
            + (f"\nDernière erreur : {e.derniere_erreur}" if e.derniere_erreur else "")
        )
        page.update()

    async def traiter(
        latitude: float, longitude: float, precision: float | None, instant: datetime
    ):
        trou = etat.cadence.enregistrer(instant)
        if trou:
            log(f"Trou de {trou:.0f} s entre deux positions GPS", ft.Colors.ORANGE_700)
        await etat.emetteur.pousser(
            {
                "latitude": latitude,
                "longitude": longitude,
                "precision_m": precision,
                "mesure_le": instant,
            }
        )
        afficher_statistiques()

    # --------------------------------------------------------------- sources de position

    async def sur_position(e: ftg.GeolocatorPositionChangeEvent):
        p = e.position
        if p is None or p.latitude is None or p.longitude is None:
            return
        instant = _instant_mesure(p)
        if instant.tzinfo is None:  # l'horodatage doit porter un fuseau ; on suppose UTC
            instant = instant.replace(tzinfo=UTC)
        await traiter(p.latitude, p.longitude, p.accuracy, instant)

    async def boucle_sondage():
        while etat.en_ligne:
            debut = asyncio.get_running_loop().time()
            try:
                p = await outils.get_current_position(
                    ftg.GeolocatorConfiguration(accuracy=ftg.GeolocatorPositionAccuracy.BEST)
                )
                instant = _instant_mesure(p)
                if instant.tzinfo is None:
                    instant = instant.replace(tzinfo=UTC)
                await traiter(p.latitude, p.longitude, p.accuracy, instant)
            except Exception as ex:  # noqa: BLE001 - on veut tout voir, sans jamais planter
                log(f"Sondage GPS en échec : {type(ex).__name__} {ex}", ft.Colors.RED_600)
            ecoule = asyncio.get_running_loop().time() - debut
            await asyncio.sleep(max(0.0, INTERVALLE_S - ecoule))

    async def boucle_simulation():
        simulateur = Simulateur()
        while etat.en_ligne:
            p = simulateur.avancer(INTERVALLE_S)
            await traiter(p["latitude"], p["longitude"], p["precision_m"], datetime.now(UTC))
            await asyncio.sleep(INTERVALLE_S)

    def configuration_flux() -> ftg.GeolocatorConfiguration:
        if page.platform == ft.PagePlatform.ANDROID:
            return ftg.GeolocatorAndroidConfiguration(
                accuracy=ftg.GeolocatorPositionAccuracy.BEST,
                distance_filter=0,
                interval_duration=ft.Duration(seconds=INTERVALLE_S),
                # La notification permanente garde l'application vivante écran éteint.
                foreground_notification_config=ftg.ForegroundNotificationConfiguration(
                    notification_title="Livraison SMA",
                    notification_text="Suivi de position actif",
                    notification_enable_wake_lock=True,
                    notification_set_ongoing=True,
                ),
            )
        return ftg.GeolocatorConfiguration(accuracy=ftg.GeolocatorPositionAccuracy.BEST)

    async def demarrer_gps():
        if etat.mode == "flux":
            etat.service_gps = ftg.Geolocator(
                configuration=configuration_flux(),
                on_position_change=sur_position,
                on_error=lambda e: log(f"Erreur GPS : {e.data}", ft.Colors.RED_600),
            )
            page.services.append(etat.service_gps)
            page.update()
            log("Flux GPS démarré (service au premier plan sur Android).")
        elif etat.mode == "sondage":
            etat.tache = asyncio.create_task(boucle_sondage())
            log("Sondage GPS démarré (une position toutes les 5 s).")
        else:
            etat.tache = asyncio.create_task(boucle_simulation())
            log("Simulation démarrée.")

    async def arreter_gps():
        if etat.tache is not None:
            etat.tache.cancel()
            etat.tache = None
        if etat.service_gps is not None:
            try:
                page.services.remove(etat.service_gps)
                page.update()
            except ValueError:
                pass
            etat.service_gps = None

    # ------------------------------------------------------------------------ actions

    async def basculer_en_ligne(e):
        if not etat.en_ligne:
            try:
                await etat.api.passer_en_ligne()
            except (ErreurApi, ErreurReseau) as ex:
                log(f"Impossible de passer en ligne : {ex}", ft.Colors.RED_600)
                return
            etat.en_ligne = True
            etat.cadence.reinitialiser()
            etat.debut = datetime.now(UTC)
            bouton_ligne.content = "Passer hors ligne"
            log(f"En ligne, mode : {MODES[etat.mode]}", ft.Colors.GREEN_700)
            await demarrer_gps()
        else:
            etat.en_ligne = False
            await arreter_gps()
            bouton_ligne.content = "Passer en ligne"
            try:
                await etat.api.passer_hors_ligne()
            except (ErreurApi, ErreurReseau) as ex:
                log(
                    f"Hors ligne en local, mais le serveur n'a pas répondu : {ex}",
                    ft.Colors.ORANGE_700,
                )
            log("Hors ligne.")
            afficher_statistiques()
        page.update()

    def changer_mode(e):
        etat.mode = e.control.value
        log(f"Mode choisi : {MODES[etat.mode]} (appliqué au prochain passage en ligne)")

    async def autoriser(e):
        try:
            if not await outils.is_location_service_enabled():
                log("La localisation du téléphone est désactivée : activez-la.", ft.Colors.RED_600)
                await outils.open_location_settings()
                return
            statut = await outils.request_permission()
            permission.value = f"Permission : {statut.name}"
            log(f"Permission localisation : {statut.name}")
            if statut != ftg.GeolocatorPermissionStatus.ALWAYS:
                log(
                    "Pour le suivi écran éteint : Réglages > Autorisations > Position > "
                    "« Toujours autoriser », puis autoriser les notifications.",
                    ft.Colors.ORANGE_700,
                )
        except Exception as ex:  # noqa: BLE001
            log(f"Permission impossible : {type(ex).__name__} {ex}", ft.Colors.RED_600)
        page.update()

    async def ouvrir_reglages(e):
        try:
            await outils.open_app_settings()
        except Exception as ex:  # noqa: BLE001
            log(f"Réglages indisponibles : {ex}", ft.Colors.RED_600)

    async def demander_code(e):
        try:
            etat.api = ClientApi(champ_serveur.value)
            await etat.api.demander_code(champ_telephone.value)
            log("Code demandé : il s'affiche dans la console du serveur (mode développement).")
        except (ErreurApi, ErreurReseau) as ex:
            log(f"Échec : {ex}", ft.Colors.RED_600)

    async def valider_code(e):
        try:
            nouveau = await etat.api.verifier_code(champ_telephone.value, champ_code.value)
            await etat.api.definir_profil("moto", 25)  # profil par défaut pour le test
            etat.emetteur = Emetteur(
                lambda p: etat.api.envoyer_position(
                    p["latitude"], p["longitude"], p["precision_m"], p["mesure_le"]
                )
            )
            section_connexion.visible = False
            section_gps.visible = True
            log("Compte créé et connecté." if nouveau else "Connecté.", ft.Colors.GREEN_700)
        except (ErreurApi, ErreurReseau, AttributeError) as ex:
            log(f"Échec : {ex}", ft.Colors.RED_600)
        page.update()

    page.on_app_lifecycle_state_change = lambda e: log(f"Application : {e.state.name}")

    # -------------------------------------------------------------------------- écran

    champ_serveur = ft.TextField(label="Adresse du serveur", value="http://192.168.1.7:8000")
    champ_telephone = ft.TextField(
        label="Téléphone", value="07 00 00 00 01", keyboard_type=ft.KeyboardType.PHONE
    )
    champ_code = ft.TextField(label="Code reçu par SMS", keyboard_type=ft.KeyboardType.NUMBER)
    section_connexion = ft.Column(
        [
            ft.Text("Connexion livreur", size=18, weight=ft.FontWeight.BOLD),
            champ_serveur,
            champ_telephone,
            ft.Button("Demander le code", on_click=demander_code),
            champ_code,
            ft.Button("Valider", on_click=valider_code),
        ]
    )

    bouton_ligne = ft.Button("Passer en ligne", icon=ft.Icons.GPS_FIXED, on_click=basculer_en_ligne)
    section_gps = ft.Column(
        [
            ft.Text("Suivi GPS", size=18, weight=ft.FontWeight.BOLD),
            ft.Dropdown(
                label="Mode de collecte",
                value=etat.mode,
                options=[ft.DropdownOption(key=cle, text=nom) for cle, nom in MODES.items()],
                on_select=changer_mode,
            ),
            ft.Row(
                [
                    ft.Button("Autoriser la position", on_click=autoriser),
                    ft.Button("Réglages", on_click=ouvrir_reglages),
                ],
                wrap=True,
            ),
            permission,
            bouton_ligne,
            statistiques,
        ],
        visible=False,
    )

    page.add(
        ft.SafeArea(
            content=ft.Column(
                [
                    section_connexion,
                    section_gps,
                    ft.Divider(),
                    ft.Text("Journal", weight=ft.FontWeight.BOLD),
                    journal,
                ]
            )
        )
    )
    log("Application prête.")


ft.run(main)