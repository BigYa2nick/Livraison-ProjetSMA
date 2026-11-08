"""Serveur applicatif. Lancement : uvicorn livraison.api.main:app --reload"""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from livraison.api.routes_admin import routeur as routeur_admin
from livraison.api.routes_auth import routeur as routeur_auth
from livraison.api.routes_dossier import routeur as routeur_dossier
from livraison.api.routes_livreur import routeur as routeur_livreur
from livraison.api.routes_suivi import routeur as routeur_suivi
from livraison.modele.telephone import TelephoneInvalide
from livraison.services.auth import ErreurAuthentification
from livraison.services.erreurs import ErreurMetier
from livraison.services.securite import ErreurConfiguration

app = FastAPI(title="Livraison SMA", version="0.4.0")
app.include_router(routeur_auth)
app.include_router(routeur_livreur)
app.include_router(routeur_dossier)
app.include_router(routeur_admin)
app.include_router(routeur_suivi)


@app.exception_handler(ErreurAuthentification)
async def _erreur_authentification(_: Request, erreur: ErreurAuthentification) -> JSONResponse:
    entetes = {"WWW-Authenticate": "Bearer"} if erreur.statut == 401 else None
    return JSONResponse({"detail": erreur.detail}, status_code=erreur.statut, headers=entetes)


@app.exception_handler(ErreurMetier)
async def _erreur_metier(_: Request, erreur: ErreurMetier) -> JSONResponse:
    return JSONResponse({"detail": erreur.detail}, status_code=erreur.statut)


@app.exception_handler(TelephoneInvalide)
async def _telephone_invalide(_: Request, erreur: TelephoneInvalide) -> JSONResponse:
    return JSONResponse({"detail": str(erreur)}, status_code=422)


@app.exception_handler(ErreurConfiguration)
async def _erreur_configuration(_: Request, erreur: ErreurConfiguration) -> JSONResponse:
    # Le détail est utile au développeur ; il reste dans les journaux, pas dans la réponse.
    import logging

    logging.getLogger("livraison").error("Configuration invalide : %s", erreur)
    return JSONResponse({"detail": "Erreur de configuration du serveur."}, status_code=500)


@app.get("/sante")
async def sante() -> dict:
    """Point de contrôle utilisé pour vérifier que le serveur répond."""
    return {"etat": "ok"}