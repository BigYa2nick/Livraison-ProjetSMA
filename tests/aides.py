"""Fonctions utilitaires partagées par les tests d'API."""

from livraison.persistance.enums import Role
from livraison.persistance.modeles import Utilisateur
from livraison.services import securite

MDP = "MotDePasse-Solide-1"


class SmsEspion:
    """Remplace l'envoi de SMS : garde les codes pour que le test puisse les relire."""

    def __init__(self):
        self.envoyes: list[tuple[str, str]] = []

    async def envoyer_code(self, telephone: str, code: str) -> None:
        self.envoyes.append((telephone, code))

    @property
    def dernier_code(self) -> str:
        return self.envoyes[-1][1]


def bearer(jeton: str) -> dict:
    return {"Authorization": f"Bearer {jeton}"}


async def connecter(client, sms, role="client", tel="07 08 09 10 11"):
    """Demande un code, le valide et renvoie la réponse de /auth/verifier."""
    await client.post("/auth/code", json={"telephone": tel})
    return await client.post(
        "/auth/verifier", json={"telephone": tel, "code": sms.dernier_code, "role": role}
    )


async def creer_personnel(session, role=Role.ADMINISTRATEUR, tel="+2250102030405"):
    utilisateur = Utilisateur(
        telephone=tel, role=role, mot_de_passe_hash=securite.hacher_mot_de_passe(MDP)
    )
    session.add(utilisateur)
    await session.flush()
    return utilisateur
