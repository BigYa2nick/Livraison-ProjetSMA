def message_refus_ws(statut: int) -> str:
    if statut == 403:
        return "Accès refusé : connecte-toi avec un compte répartiteur ou administrateur."
    if statut == 401:
        return "Session expirée : reconnecte-toi avec ton compte répartiteur ou administrateur."
    return f"Connexion au suivi refusée (HTTP {statut}). Vérifie le serveur et reconnecte-toi."
