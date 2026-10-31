"""Configuration centralisée pour melodi_mcp_server."""

from typing import Any, Literal, Optional
from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def normalize_url_path(path: str) -> str:
    """Assure qu'un chemin d'URL commence par / et ne se termine pas par /."""
    cleaned = path.strip()
    if not cleaned:
        return ""
    if not cleaned.startswith("/"):
        cleaned = "/" + cleaned
    if cleaned.endswith("/") and len(cleaned) > 1:
        cleaned = cleaned.rstrip("/")
    return cleaned


class Settings(BaseSettings):
    """Paramètres applicatifs validés au démarrage."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Connexion MELODI ERP ---
    melodi_base_url: str = Field(
        default="http://localhost:8000",
        description="URL de base de l'instance MELODI ERP",
    )
    melodi_api_prefix: str = Field(
        default="/api/melodi_mcp/internal",
        description="Préfixe configurable des routes de l'API MCP MELODI",
    )
    melodi_api_key: SecretStr = Field(
        default=SecretStr("mcp-internal-secret-token"),
        description="Clé secrète d'authentification pour l'API MELODI (défaut ERP: mcp-internal-secret-token)",
    )
    melodi_timeout_seconds: float = Field(
        default=30.0,
        gt=0.0,
        description="Délai d'attente maximum pour les requêtes vers MELODI",
    )
    melodi_verify_ssl: bool = Field(
        default=True,
        description="Vérifier la validité des certificats TLS/SSL",
    )

    # --- Transport MCP ---
    mcp_transport: Literal["stdio", "http", "sse"] = Field(
        default="stdio",
        description="Transport MCP actif ('stdio' pour local, 'http'/'sse' pour réseau)",
    )
    mcp_server_host: str = Field(
        default="127.0.0.1",
        description="Adresse d'écoute du serveur HTTP",
    )
    mcp_server_port: int = Field(
        default=8765,
        ge=1,
        le=65535,
        description="Port d'écoute du serveur HTTP",
    )
    mcp_api_key: Optional[SecretStr] = Field(
        default=None,
        description="Clé d'infrastructure MCP optionnelle (validée dans le payload JWT sous mcp_api_key)",
    )
    jwt_secret: Optional[SecretStr] = Field(
        default=None,
        validation_alias=AliasChoices("jwt_secret", "mcp_jwt_secret"),
        description="Clé secrète de signature JWT (HS256) partagée avec MELODI ERP pour sécuriser la connexion MCP",
    )

    # --- Console Web d'Administration ---
    admin_dashboard_enabled: bool = Field(
        default=True,
        description="Activer le dashboard d'administration web",
    )
    admin_prefix: str = Field(
        default="/admin",
        description="Préfixe d'accès à la console d'administration",
    )
    admin_api_key: Optional[SecretStr] = Field(
        default=SecretStr("admin"),
        description="Clé d'amorçage initiale pour créer le premier compte admin (par défaut: admin)",
    )
    admin_db_path: str = Field(
        default="data/admin.db",
        description="Chemin du fichier de base de données SQLite pour l'administration",
    )
    admin_session_ttl_hours: int = Field(
        default=24,
        ge=1,
        description="Durée de validité d'une session administrateur en heures",
    )

    # --- Catalogue & Exécution ---
    catalog_sync_interval_seconds: int = Field(
        default=60,
        ge=5,
        description="Intervalle de synchronisation automatique du catalogue (en secondes)",
    )
    max_request_size_bytes: int = Field(
        default=1024 * 1024,  # 1 Mo
        gt=0,
        description="Taille maximale autorisée pour les arguments d'un outil",
    )
    max_response_size_bytes: int = Field(
        default=5 * 1024 * 1024,  # 5 Mo
        gt=0,
        description="Taille maximale acceptée pour la réponse d'un outil",
    )

    # --- Journalisation ---
    log_level: str = Field(
        default="INFO",
        description="Niveau de journalisation standard (DEBUG, INFO, WARNING, ERROR)",
    )

    @field_validator("melodi_base_url")
    @classmethod
    def validate_base_url(cls, v: str) -> str:
        v = v.strip().rstrip("/")
        if not v.startswith(("http://", "https://")):
            raise ValueError("L'URL de base MELODI doit débuter par http:// ou https://")
        return v

    @field_validator("melodi_api_prefix")
    @classmethod
    def validate_api_prefix(cls, v: str) -> str:
        return normalize_url_path(v)

    @field_validator("admin_prefix")
    @classmethod
    def validate_admin_prefix(cls, v: str) -> str:
        return normalize_url_path(v)

    @field_validator("admin_api_key", "mcp_api_key", "jwt_secret", mode="before")
    @classmethod
    def validate_optional_secrets(cls, v: Any) -> Any:
        if v == "" or (isinstance(v, SecretStr) and not v.get_secret_value()):
            return None
        return v

    def get_melodi_endpoint(self, endpoint_name: str) -> str:
        """Construit l'URL complète pour un endpoint MELODI donné en respectant le préfixe configuré."""
        endpoint = endpoint_name.strip()
        if not endpoint.startswith("/"):
            endpoint = "/" + endpoint
        return f"{self.melodi_base_url}{self.melodi_api_prefix}{endpoint}"


# Singleton par défaut ou chargement explicite
_current_settings: Optional[Settings] = None


def get_settings() -> Settings:
    """Retourne l'instance actuelle des paramètres."""
    global _current_settings
    if _current_settings is None:
        _current_settings = Settings()
    return _current_settings


def set_settings(settings: Settings) -> None:
    """Définit explicitement l'instance des paramètres (utile pour les tests)."""
    global _current_settings
    _current_settings = settings
