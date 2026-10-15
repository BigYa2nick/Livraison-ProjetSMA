"""Briques de sécurité : codes SMS, mots de passe et jetons JWT (NF-05).

Rien n'est stocké en clair : les codes SMS sont hachés avec une clé secrète (HMAC), les mots de
passe avec scrypt (inclus dans Python, avec sel aléatoire). Les jetons JWT sont signés (HS256).
"""

import base64
import hashlib
import hmac
import secrets
import uuid
from datetime import UTC, datetime, timedelta

import jwt

from livraison.config import parametres

LONGUEUR_CODE = 6
LONGUEUR_MIN_SECRET_JWT = 32
LONGUEUR_MIN_MOT_DE_PASSE = 10

_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 2**14, 8, 1


class ErreurConfiguration(RuntimeError):
    """La configuration (.env) est incomplète ou dangereuse."""


class ErreurJeton(Exception):
    """Jeton absent, falsifié, expiré ou du mauvais type."""


def _secret() -> str:
    secret = parametres.jwt_secret
    if secret.startswith("changez-moi") or len(secret) < LONGUEUR_MIN_SECRET_JWT:
        raise ErreurConfiguration(
            "JWT_SECRET doit être une chaîne aléatoire d'au moins 32 caractères dans .env. "
            'Pour en générer une : python -c "import secrets; print(secrets.token_urlsafe(48))"'
        )
    return secret


# ------------------------------------------------------------------------- codes SMS


def generer_code() -> str:
    """Code à usage unique de 6 chiffres, tiré avec un générateur cryptographique."""
    return f"{secrets.randbelow(10**LONGUEUR_CODE):0{LONGUEUR_CODE}d}"


def hacher_code(telephone: str, code: str) -> str:
    """Haché du code, lié au numéro. Une clé secrète empêche de retrouver le code par force brute."""
    message = f"{telephone}:{code}".encode()
    return hmac.new(_secret().encode(), message, hashlib.sha256).hexdigest()


def code_correct(hache_stocke: str, telephone: str, code_saisi: str) -> bool:
    return hmac.compare_digest(hache_stocke, hacher_code(telephone, code_saisi))


# ------------------------------------------------------------------------ mots de passe


def valider_mot_de_passe(mot_de_passe: str) -> None:
    if len(mot_de_passe) < LONGUEUR_MIN_MOT_DE_PASSE:
        raise ValueError(
            f"Le mot de passe doit contenir au moins {LONGUEUR_MIN_MOT_DE_PASSE} caractères."
        )


def hacher_mot_de_passe(mot_de_passe: str) -> str:
    sel = secrets.token_bytes(16)
    empreinte = hashlib.scrypt(
        mot_de_passe.encode(), salt=sel, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=32
    )
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${base64.b64encode(sel).decode()}${base64.b64encode(empreinte).decode()}"


def verifier_mot_de_passe(mot_de_passe: str, hache: str) -> bool:
    try:
        _, n, r, p, sel_b64, empreinte_b64 = hache.split("$")
        sel = base64.b64decode(sel_b64)
        attendu = base64.b64decode(empreinte_b64)
        calcule = hashlib.scrypt(
            mot_de_passe.encode(), salt=sel, n=int(n), r=int(r), p=int(p), dklen=len(attendu)
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(calcule, attendu)


# Haché sans mot de passe correspondant : sert à garder un temps de réponse comparable quand le
# compte n'existe pas (on ne révèle pas quels numéros sont inscrits).
HACHE_FACTICE = hacher_mot_de_passe(secrets.token_urlsafe(16))


# ------------------------------------------------------------------------------ jetons


def creer_jeton(utilisateur_id: uuid.UUID, role: str, type_: str, duree: timedelta) -> str:
    maintenant = datetime.now(UTC)
    charge = {
        "sub": str(utilisateur_id),
        "role": role,
        "typ": type_,  # "acces" ou "rafraichissement"
        "iat": maintenant,
        "exp": maintenant + duree,
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(charge, _secret(), algorithm="HS256")


def decoder_jeton(jeton: str, type_attendu: str) -> dict:
    """Vérifie la signature, l'expiration et le type du jeton ; renvoie son contenu."""
    try:
        contenu = jwt.decode(
            jeton, _secret(), algorithms=["HS256"], options={"require": ["exp", "sub", "typ"]}
        )
    except jwt.PyJWTError as e:
        raise ErreurJeton("Jeton invalide ou expiré.") from e
    if contenu["typ"] != type_attendu:
        raise ErreurJeton("Type de jeton incorrect.")
    return contenu


def duree_acces() -> timedelta:
    return timedelta(minutes=parametres.jwt_acces_minutes)


def duree_rafraichissement() -> timedelta:
    return timedelta(days=parametres.jwt_refresh_jours)
