"""Serveur applicatif. Lancement : uvicorn livraison.api.main:app --reload"""
from fastapi import FastAPI

app = FastAPI(title="Livraison SMA", version="0.1.0")


@app.get("/sante")
async def sante() -> dict:
    """Point de contrôle utilisé pour vérifier que le serveur répond."""
    return {"etat": "ok"}
