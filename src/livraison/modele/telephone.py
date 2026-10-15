"""Normalisation des numéros de téléphone au format international (E.164)."""

import re

_FORMAT_E164 = re.compile(r"^\+[0-9]{8,15}$")


class TelephoneInvalide(ValueError):
    """Le numéro saisi ne peut pas être converti en numéro international."""


def normaliser_telephone(brut: str, indicatif_defaut: str = "225") -> str:
    """Convertit une saisie en numéro international, par exemple « 07 08 09 10 11 » en « +2250708091011 ».

    Règles (numérotation ivoirienne à 10 chiffres, le 0 initial fait partie du numéro) :
    - « +… » ou « 00… » : numéro international, vérifié tel quel ;
    - 10 chiffres : numéro local, l'indicatif du pays par défaut est ajouté ;
    - indicatif suivi de 10 chiffres : le « + » manquant est ajouté.
    """
    texte = re.sub(r"[\s.\-()]", "", (brut or "").strip())
    if texte.startswith("00"):
        texte = "+" + texte[2:]
    if not texte.startswith("+"):
        if not texte.isdigit():
            raise TelephoneInvalide("Numéro de téléphone invalide.")
        if len(texte) == 10:
            texte = f"+{indicatif_defaut}{texte}"
        elif texte.startswith(indicatif_defaut) and len(texte) == len(indicatif_defaut) + 10:
            texte = f"+{texte}"
        else:
            raise TelephoneInvalide("Numéro de téléphone invalide.")
    if not _FORMAT_E164.match(texte):
        raise TelephoneInvalide("Numéro de téléphone invalide.")
    return texte
