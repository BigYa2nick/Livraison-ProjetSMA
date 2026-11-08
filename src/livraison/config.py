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
    jwt_acces_minutes: int = 15
    jwt_refresh_jours: int = 7
 
   # Itinéraires (F-42) : un serveur OSRM par profil. 127.0.0.1 plutôt que localhost : sous Windows,
    # « localhost » essaie d'abord IPv6 et ajoute environ 2 secondes à chaque requête.
    osrm_url: str = "http://127.0.0.1:5000"  # profil voiture, aussi utilisé pour la moto
    osrm_url_velo: str = "http://127.0.0.1:5001"  # profil vélo
    osrm_timeout_s: float = 2.0
    osrm_max_table: int = 100  # nombre maximal de points d'une matrice (valeur par défaut d'OSRM)
    # Estimation de repli quand OSRM est injoignable : hypothèses à calibrer avec des trajets réels.
    itineraire_facteur_detour: float = 1.4  # route = vol d'oiseau x ce facteur
    vitesse_estimee_moto_kmh: float = 25.0
    vitesse_estimee_voiture_kmh: float = 20.0
    vitesse_estimee_velo_kmh: float = 12.0
 
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    jwt_secret: str = "mon_jwt_secret_aleatoire_pour_le_projet_SMA."
    jwt_acces_minutes: int = 15
    jwt_refresh_jours: int = 7
 
    # Environnement : "dev" autorise l'envoi de SMS simulé (le code s'affiche dans les journaux).
    environnement: str = "dev"
    sms_mode: str = "console"
    indicatif_pays_defaut: str = "225"
 
    # Codes de vérification SMS (NF-05 : limitation des tentatives)
    code_validite_secondes: int = 300
    code_tentatives_max: int = 5
    codes_max_par_fenetre: int = 3
    codes_fenetre_secondes: int = 600
 
    # Suivi GPS (F-12, F-20, F-34, NF-01)
    position_ttl_secondes: int = 120  # une position disparaît de Redis si elle n'est pas renouvelée
    position_age_max_secondes: int = 30  # au-delà, la position est jugée trop ancienne
    # La validation des dossiers livreurs (F-03) existe : mettre LIVREUR_VALIDATION_REQUISE=true
    # dans .env pour interdire à un livreur non validé de passer en ligne. Laissé à False par
    # défaut tant que l'on teste les prototypes GPS avec des comptes jamais validés.
    livreur_validation_requise: bool = False
 
    # Dossier livreur (F-03) : pièces justificatives stockées hors de la base, hors de Git.
    stockage_pieces_dir: str = "stockage/pieces"
    piece_taille_max_octets: int = 5 * 1024 * 1024
 
    # Agents (lot 2)
    agents_reconciliation_s: float = 30.0  # le gestionnaire compare la base et les agents toutes les N s
    agents_battement_s: float = 15.0  # signe de vie d'un agent Livreur à l'Annuaire
    annuaire_ttl_s: float = 60.0  # un agent silencieux plus longtemps est retiré de l'Annuaire
    annuaire_max_resultats: int = 50  # taille maximale d'une réponse de recherche
 
    # Contract Net (lot 2, F-31 à F-34) : valeurs de départ, à calibrer avec les premiers essais.
    attribution_collecte_s: float = 3.0  # attente des offres après l'appel d'offres (NF-01 : < 5 s)
    attribution_annuaire_attente_s: float = 2.0  # attente de la réponse de l'Annuaire
    attribution_confirmation_s: float = 30.0  # délai laissé au livreur humain pour confirmer (F-34)
    attribution_marge_s: float = 5.0  # marge du Dispatcher au-delà du délai de l'agent Livreur
    attribution_rayon_initial_m: int = 3000  # rayon de recherche au premier tour
    attribution_rayon_max_m: int = 20000  # le rayon ne dépasse jamais cette valeur
    attribution_facteur_rayon: float = 2.0  # le rayon est multiplié par ce facteur après un tour vide
    attribution_pause_s: float = 10.0  # pause avant de relancer un tour resté sans offre (F-33)
    attribution_tours_max: int = 6  # nombre de tours avant d'alerter le répartiteur
    attribution_penalite_charge_s: float = 300.0  # coût d'une offre : secondes par course déjà portée
    livreur_charge_max: int = 3  # courses qu'un livreur peut porter en même temps (au-delà : « sature »)
 
    # Connexion par mot de passe (répartiteurs et administrateurs)
    connexion_tentatives_max: int = 5
    connexion_verrouillage_secondes: int = 900
 
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
 