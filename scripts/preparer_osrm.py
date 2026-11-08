"""Prépare les données d'itinéraires d'OSRM (carte de la Côte d'Ivoire).

Étapes : télécharge l'extrait OpenStreetMap, puis lance dans Docker osrm-extract, osrm-partition et
osrm-customize pour chaque profil (voiture et vélo). Résultats dans data/osrm/, hors de Git.

À lancer à la racine du dépôt, venv activé, Docker Desktop démarré :

    python scripts/preparer_osrm.py                       télécharge la carte, prépare les 2 profils
    python scripts/preparer_osrm.py --pbf C:\\carte.pbf    utilise un fichier déjà téléchargé
    python scripts/preparer_osrm.py --reset               refait tout, même si les données existent
    python scripts/preparer_osrm.py --dry-run             affiche les commandes sans rien exécuter

À relancer quand on veut mettre la carte à jour (avec --reset).
"""

import argparse
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
DONNEES = RACINE / "data" / "osrm"
# Si ce lien ne répond plus, télécharger l'extrait de la Côte d'Ivoire sur geofabrik.de
# puis relancer avec --pbf <fichier>.
URL_CARTE = "https://download.geofabrik.de/africa/ivory-coast-latest.osm.pbf"
IMAGE = os.environ.get("OSRM_IMAGE", "ghcr.io/project-osrm/osrm-backend:v5.27.1")
NOM_CARTE = (
    "carte"  # les fichiers produits s'appellent carte.osrm.*, comme dans docker-compose.osrm.yml
)
# profil OSRM (fichier .lua de l'image) -> dossier de données. La moto utilise le profil voiture.
PROFILS = ("car", "bicycle")


def executer(commande: list[str], dry_run: bool) -> None:
    print("  $", " ".join(commande))
    if dry_run:
        return
    try:
        resultat = subprocess.run(commande)
    except FileNotFoundError:
        sys.exit("Docker est introuvable : installer Docker Desktop et le démarrer.")
    if resultat.returncode != 0:
        sys.exit(
            f"La commande a échoué (code {resultat.returncode}). Si le message parle de mémoire, "
            "augmenter la mémoire de Docker Desktop (Settings, Resources) puis relancer."
        )


def telecharger(url: str, destination: Path, dry_run: bool) -> None:
    print(f"Téléchargement de la carte : {url}")
    if dry_run:
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    partiel = destination.with_suffix(destination.suffix + ".part")
    try:
        with urllib.request.urlopen(url, timeout=60) as reponse, open(partiel, "wb") as sortie:
            total = int(reponse.headers.get("Content-Length") or 0)
            recu = 0
            while bloc := reponse.read(1 << 20):
                sortie.write(bloc)
                recu += len(bloc)
                if total:
                    print(f"\r  {recu / 1e6:.0f} / {total / 1e6:.0f} Mo", end="", flush=True)
        print()
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        partiel.unlink(missing_ok=True)
        sys.exit(
            f"Téléchargement impossible ({type(e).__name__}: {e}).\n"
            "Télécharger l'extrait de la Côte d'Ivoire sur geofabrik.de, puis relancer avec --pbf."
        )
    partiel.replace(destination)
    print(f"  carte enregistrée : {destination} ({destination.stat().st_size / 1e6:.0f} Mo)")


def preparer_profil(profil: str, carte: Path, reset: bool, dry_run: bool) -> None:
    dossier = DONNEES / profil
    pret = dossier / f"{NOM_CARTE}.osrm.mldgr"
    print(f"\nProfil {profil} (dossier {dossier})")
    if pret.exists() and not reset:
        print("  déjà prêt, rien à faire (--reset pour refaire).")
        return
    if reset and dossier.exists() and not dry_run:
        shutil.rmtree(dossier)
    if not dry_run:
        dossier.mkdir(parents=True, exist_ok=True)
        shutil.copy2(carte, dossier / f"{NOM_CARTE}.osm.pbf")
    base = ["docker", "run", "--rm", "-v", f"{dossier}:/data", IMAGE]
    executer(
        [*base, "osrm-extract", "-p", f"/opt/{profil}.lua", f"/data/{NOM_CARTE}.osm.pbf"], dry_run
    )
    executer([*base, "osrm-partition", f"/data/{NOM_CARTE}.osrm"], dry_run)
    executer([*base, "osrm-customize", f"/data/{NOM_CARTE}.osrm"], dry_run)
    if not dry_run and not pret.exists():
        sys.exit(f"Le profil {profil} n'a pas produit {pret.name} : lire les messages ci-dessus.")
    if not dry_run:
        (dossier / f"{NOM_CARTE}.osm.pbf").unlink(
            missing_ok=True
        )  # l'original reste dans data/osrm/
        print("  prêt.")


def main() -> None:
    lecteur = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    lecteur.add_argument("--pbf", type=Path, help="fichier .osm.pbf déjà téléchargé")
    lecteur.add_argument("--url", default=URL_CARTE, help="adresse de téléchargement de la carte")
    lecteur.add_argument("--reset", action="store_true", help="refait la préparation depuis zéro")
    lecteur.add_argument(
        "--dry-run", action="store_true", help="affiche les commandes sans les lancer"
    )
    a = lecteur.parse_args()

    if a.pbf is not None:
        carte = a.pbf.resolve()
        if not carte.is_file() and not a.dry_run:
            sys.exit(f"Fichier introuvable : {carte}")
    else:
        carte = DONNEES / "ivory-coast-latest.osm.pbf"
        if a.reset or not carte.exists():
            telecharger(a.url, carte, a.dry_run)
        else:
            print(f"Carte déjà téléchargée : {carte} (--reset pour la retélécharger)")

    print(f"Image OSRM : {IMAGE}")
    for profil in PROFILS:
        preparer_profil(profil, carte, a.reset, a.dry_run)
    print(
        "\nÉtape suivante :\n"
        "  docker compose -f docker-compose.osrm.yml up -d\n"
        "  python scripts/verifier_osrm.py"
    )


if __name__ == "__main__":
    main()
