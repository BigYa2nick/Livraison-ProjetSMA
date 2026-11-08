"""Carte de suivi en direct des livreurs, pour le répartiteur et l'administrateur (F-60).

Lancement (serveur API démarré, compte répartiteur ou administrateur créé, voir scripts/creer_personnel.py) :
    flet run src/livraison/interfaces/console_suivi.py            # fenêtre de bureau
    flet run --web src/livraison/interfaces/console_suivi.py      # dans le navigateur

Affiche la position de chaque livreur en ligne, son âge, et le délai moyen entre la mesure sur
le téléphone et l'affichage ici (objectif NF-01 : moins de 10 secondes).
"""

import asyncio
import json
import statistics
from datetime import UTC, datetime

import flet as ft
import flet_map as fm
import httpx
import websockets

CENTRE_ABIDJAN = (5.3364, -4.0267)
SEUIL_PERIMEE_S = 30


def _instant(texte: str) -> datetime:
    return datetime.fromisoformat(texte)


async def main(page: ft.Page):
    page.title = "Suivi des livreurs"
    page.padding = 0  # la carte occupe toute la fenêtre
    page.window.maximized = True

    positions: dict[str, dict] = {}
    marqueurs: dict[str, fm.Marker] = {}
    delais: list[float] = []  # délai mesure -> affichage, en secondes (50 derniers)
    couche = fm.MarkerLayer(markers=[])

    entete = ft.Text("Non connecté", size=14, weight=ft.FontWeight.BOLD)
    resume = ft.Text("", size=13)
    liste = ft.ListView(controls=[], height=150, spacing=2, visible=False)

    carte = fm.Map(
        expand=True,
        initial_center=fm.MapLatitudeLongitude(*CENTRE_ABIDJAN),
        initial_zoom=12,
        layers=[
            fm.TileLayer(
                url_template="https://tile.openstreetmap.org/{z}/{x}/{y}.png",
                user_agent_package_name="ci.univ.livraison.suivi",
            ),
            couche,
        ],
    )

    def couleur(position: dict):
        return ft.Colors.RED_600 if position["perimee"] else ft.Colors.GREEN_700

    def appliquer(position: dict):
        """Ajoute ou déplace le marqueur d'un livreur."""
        identifiant = position["livreur_id"]
        position.setdefault("perimee", False)
        positions[identifiant] = position
        point = fm.MapLatitudeLongitude(position["latitude"], position["longitude"])
        if identifiant in marqueurs:
            marqueurs[identifiant].coordinates = point
            marqueurs[identifiant].content.color = couleur(position)
        else:
            marqueurs[identifiant] = fm.Marker(
                coordinates=point,
                content=ft.Icon(ft.Icons.LOCATION_ON, color=couleur(position), size=34),
                width=40,
                height=40,
            )
            couche.markers.append(marqueurs[identifiant])

    def retirer(identifiant: str):
        positions.pop(identifiant, None)
        marqueur = marqueurs.pop(identifiant, None)
        if marqueur is not None:
            couche.markers.remove(marqueur)

    def rafraichir_liste():
        maintenant = datetime.now(UTC)
        lignes = []
        for identifiant, p in sorted(positions.items()):
            age = (maintenant - _instant(p["mesure_le"])).total_seconds()
            p["perimee"] = age > SEUIL_PERIMEE_S
            if identifiant in marqueurs:
                marqueurs[identifiant].content.color = couleur(p)
            precision = f"  ±{p['precision_m']:.0f} m" if p.get("precision_m") else ""
            lignes.append(
                ft.Text(
                    f"{identifiant[:8]}  {p['latitude']:.5f}, {p['longitude']:.5f}"
                    f"  il y a {age:.0f} s{precision}" + ("  PÉRIMÉE" if p["perimee"] else ""),
                    size=12,
                    color=couleur(p),
                )
            )
        liste.controls = lignes
        liste.visible = bool(lignes)
        if delais:
            resume.value = (
                f"{len(positions)} livreur(s) en ligne   délai mesure -> carte : "
                f"médian {statistics.median(delais):.1f} s, max {max(delais):.1f} s"
                f" ({len(delais)} positions)"
            )
        else:
            resume.value = f"{len(positions)} livreur(s) en ligne"
        page.update()

    def sur_message(message: dict):
        if message["type"] == "instantane":
            for identifiant in list(positions):
                retirer(identifiant)
            for p in message["positions"]:
                appliquer(p)
        elif message["type"] == "position":
            appliquer(message)
            delais.append((datetime.now(UTC) - _instant(message["mesure_le"])).total_seconds())
            del delais[:-50]
        elif message["type"] == "hors_ligne":
            retirer(message["livreur_id"])

    async def ecouter(adresse_ws: str):
        """Reste connecté au flux ; se reconnecte seul après une coupure."""
        while True:
            try:
                async with websockets.connect(adresse_ws) as flux:
                    entete.value = "Connecté : suivi en direct"
                    page.update()
                    async for brut in flux:
                        sur_message(json.loads(brut))
            except Exception as e:  # noqa: BLE001
                entete.value = f"Flux interrompu ({type(e).__name__}), nouvelle tentative..."
                page.update()
            await asyncio.sleep(2)

    async def boucle_rafraichissement():
        while True:
            rafraichir_liste()
            await asyncio.sleep(1)

    async def se_connecter(e):
        serveur = champ_serveur.value.rstrip("/")
        try:
            async with httpx.AsyncClient(base_url=serveur, timeout=10) as http:
                r = await http.post(
                    "/auth/connexion",
                    json={"telephone": champ_telephone.value, "mot_de_passe": champ_mdp.value},
                )
            if r.status_code != 200:
                entete.value = f"Connexion refusée : {r.json().get('detail', r.status_code)}"
                page.update()
                return
        except httpx.HTTPError as ex:
            entete.value = f"Serveur injoignable : {type(ex).__name__}"
            page.update()
            return
        jeton = r.json()["access_token"]
        adresse_ws = serveur.replace("http", "ws", 1) + f"/ws/suivi?token={jeton}"
        section_connexion.visible = False
        page.update()
        page.run_task(ecouter, adresse_ws)
        page.run_task(boucle_rafraichissement)

    def basculer_plein_ecran(e):
        page.window.full_screen = not page.window.full_screen
        page.update()

    champ_serveur = ft.TextField(
        label="Adresse du serveur", value="http://127.0.0.1:8000", width=260
    )
    champ_telephone = ft.TextField(label="Téléphone", width=200)
    champ_mdp = ft.TextField(
        label="Mot de passe", password=True, can_reveal_password=True, width=220
    )
    section_connexion = ft.Row(
        [champ_serveur, champ_telephone, champ_mdp, ft.Button("Connexion", on_click=se_connecter)],
        wrap=True,
    )

    # La carte remplit la fenêtre ; le panneau d'information et le bouton plein écran sont posés dessus.
    panneau = ft.Container(
        left=10,
        top=10,
        width=560,
        padding=10,
        border_radius=10,
        bgcolor=ft.Colors.with_opacity(0.9, ft.Colors.SURFACE),
        content=ft.Column([entete, section_connexion, resume, liste], spacing=6, tight=True),
    )
    bouton_plein_ecran = ft.Container(
        right=10,
        top=10,
        border_radius=20,
        bgcolor=ft.Colors.with_opacity(0.9, ft.Colors.SURFACE),
        content=ft.IconButton(
            icon=ft.Icons.FULLSCREEN, tooltip="Plein écran", on_click=basculer_plein_ecran
        ),
    )
    page.add(ft.Stack([carte, panneau, bouton_plein_ecran], expand=True))


ft.run(main)