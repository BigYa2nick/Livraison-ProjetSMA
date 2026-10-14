"""Configuration de l'application, lue depuis le fichier .env."""
from urllib.parse import quote_plus
 
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL
 

class Parametres(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    postgres_user: str = "livraison"
    postgres_password: str = "Livraison2026Sma"
    postgres_db: str = "livraison"
    postgres_host: str = "localhost"
    postgres_port: int = 5432

    redis_url: str = "redis://localhost:6379/0"

    xmpp_host: str = "localhost"
    xmpp_port: int = 5222
    xmpp_domaine: str = "localhost"
    xmpp_mot_de_passe_agents: str = "@12345"

    osrm_url: str = "http://localhost:5000"

    api_host: str = "127.0.0.1"
    api_port: int = 8000
    jwt_secret: str = "mon_jwt_secret_aleatoire_pour_le_projet_SMA."

    @property
    def postgres_dsn(self) -> str:
        """Adresse de connexion « brute » (asyncpg, psql). Le mot de passe est encodé."""
        return (
            f"postgresql://{quote_plus(self.postgres_user)}:{quote_plus(self.postgres_password)}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )
 
    @property
    def sqlalchemy_url(self) -> URL:
        """Adresse de connexion SQLAlchemy asynchrone (gère les caractères spéciaux)."""
        return URL.create(
            "postgresql+asyncpg",
            username=self.postgres_user,
            password=self.postgres_password,
            host=self.postgres_host,
            port=self.postgres_port,
            database=self.postgres_db,
        )
 
    def jid(self, nom: str) -> str:
        """Identifiant XMPP d'un agent, par exemple dispatcher@localhost."""
        return f"{nom}@{self.xmpp_domaine}"
 
 
parametres = Parametres()
