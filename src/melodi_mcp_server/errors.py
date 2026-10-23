"""Hiérarchie des exceptions typées pour melodi_mcp_server."""

from typing import Any, Optional


class MelodiMcpError(Exception):
    """Exception de base pour melodi_mcp_server."""

    def __init__(self, message: str, details: Optional[dict[str, Any]] = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"error": self.__class__.__name__, "message": self.message}
        if self.details:
            result["details"] = self.details
        return result


class ConfigurationError(MelodiMcpError):
    """Erreur liée à la configuration ou variables d'environnement invalides."""
    pass


class MelodiConnectionError(MelodiMcpError):
    """Échec de communication réseau avec l'API MELODI ERP."""
    pass


class MelodiTimeoutError(MelodiConnectionError):
    """Dépassement du délai d'attente lors d'un appel à MELODI."""
    pass


class MelodiAuthenticationError(MelodiMcpError):
    """Échec d'authentification auprès de MELODI (401 Unauthorized)."""
    pass


class MelodiAuthorizationError(MelodiMcpError):
    """Droits insuffisants pour accéder à une ressource ou un outil (403 Forbidden)."""
    pass


class MelodiContractError(MelodiMcpError):
    """Incompatibilité ou réponse inattendue par rapport au contrat d'interface."""
    pass


class ToolNotFoundError(MelodiMcpError):
    """Outil demandé introuvable dans le catalogue actif."""
    pass


class ToolValidationError(MelodiMcpError):
    """Validation des arguments de l'outil échouée."""
    pass


class ToolExecutionError(MelodiMcpError):
    """Erreur remontée lors de l'exécution de l'outil par MELODI."""
    pass


class CatalogSyncError(MelodiMcpError):
    """Erreur survenue lors de la synchronisation dynamique du catalogue."""
    pass
