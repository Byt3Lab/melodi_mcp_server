"""Contrôles de sécurité locaux, gestion du contexte et sanitisation."""

import hmac
import json
import logging
from contextvars import ContextVar
from typing import Any, Optional
from urllib.parse import parse_qs
import jsonschema
from starlette.responses import Response

from .errors import ToolValidationError
from .settings import Settings, get_settings

logger = logging.getLogger(__name__)

# Variable de contexte asynchrone stockant le token utilisateur de la requête en cours
current_user_token: ContextVar[Optional[str]] = ContextVar("current_user_token", default=None)


class SecurityContext:
    """Représente le contexte de sécurité de l'appelant MCP."""

    def __init__(
        self,
        client_id: Optional[str] = None,
        user_id: Optional[str] = None,
        org_id: Optional[str] = None,
        user_key: Optional[str] = None,
        is_authenticated: bool = False,
    ):
        self.client_id = client_id or "mcp_client"
        self.user_id = user_id if is_authenticated else None
        self.org_id = org_id if is_authenticated else None
        self.user_key = user_key
        self.is_authenticated = is_authenticated or bool(user_key)


def validate_payload_size(data: Any, max_bytes: int) -> None:
    """Vérifie que la charge utile sérialisée en JSON ne dépasse pas la limite configurée."""
    try:
        serialized = json.dumps(data)
        size = len(serialized.encode("utf-8"))
        if size > max_bytes:
            raise ToolValidationError(
                f"La taille des arguments ({size} octets) dépasse le plafond autorisé ({max_bytes} octets)"
            )
    except (TypeError, ValueError) as e:
        raise ToolValidationError(f"Données non sérialisables en JSON : {e}")


def validate_tool_arguments(schema: dict[str, Any], arguments: dict[str, Any]) -> None:
    """Valide les arguments d'un outil par rapport à son schéma JSON Schema."""
    if not isinstance(arguments, dict):
        raise ToolValidationError("Les arguments de l'outil doivent être fournis sous la forme d'un dictionnaire")

    try:
        validator = jsonschema.Draft7Validator(schema)
        errors = list(validator.iter_errors(arguments))
        if errors:
            details = [f"{e.path}: {e.message}" if e.path else e.message for e in errors]
            raise ToolValidationError(
                f"Arguments invalides : {'; '.join(details)}",
                details={"errors": details},
            )
    except jsonschema.SchemaError as e:
        logger.error("Schéma JSON corrompu : %s", e)
        raise ToolValidationError(f"Erreur de validation de schéma : {e.message}")


def sanitize_result_for_logging(data: Any) -> Any:
    """Masque les champs potentiellement sensibles dans les dictionnaires pour la traçabilité."""
    sensitive_keys = {"password", "secret", "token", "api_key", "authorization", "credit_card"}
    if isinstance(data, dict):
        sanitized = {}
        for k, v in data.items():
            if any(s in k.lower() for s in sensitive_keys):
                sanitized[k] = "[MASQUÉ]"
            else:
                sanitized[k] = sanitize_result_for_logging(v)
        return sanitized
    if isinstance(data, list):
        return [sanitize_result_for_logging(item) for item in data]
    return data


class McpAuthMiddleware:
    """Intercepte les requêtes MCP pour extraire le jeton d'authentification utilisateur

    (Bearer, X-User-MCP-Key, etc.) et l'injecter dans un ContextVar pour transmission
    transparente vers MELODI ERP. Si une clé globale `mcp_api_key` est configurée,
    l'accès anonyme sans token est rejeté.
    """

    def __init__(
        self,
        app: Any,
        api_key: Optional[str] = None,
        admin_prefix: str = "/admin",
    ):
        self.app = app
        self.api_key = api_key.strip() if api_key and api_key.strip() else None
        self.admin_prefix = admin_prefix

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        # Les routes MCP à traiter incluent /mcp, /sse et la racine /
        is_mcp_path = path in ("/mcp", "/sse", "/") or path.startswith("/mcp/") or path.startswith("/sse/")

        if path == "/":
            headers = dict(scope.get("headers", []))
            accept = headers.get(b"accept", b"").decode("latin1", errors="ignore")
            # Si un navigateur visite la racine en HTML pour se rendre sur /admin, on le laisse passer
            if scope.get("method") == "GET" and "text/html" in accept and "text/event-stream" not in accept:
                await self.app(scope, receive, send)
                return

        token: Optional[str] = None
        if is_mcp_path:
            headers = dict(scope.get("headers", []))

            # 1. Extraction Bearer token
            auth_val = headers.get(b"authorization", b"").decode("latin1", errors="ignore").strip()
            if auth_val.lower().startswith("bearer "):
                token = auth_val[7:].strip()

            # 2. Extraction en-têtes spécifiques
            if not token:
                for h_name in (b"x-user-mcp-key", b"x-mcp-user-key", b"x-mcp-api-key", b"x-api-key"):
                    val = headers.get(h_name, b"").decode("latin1", errors="ignore").strip()
                    if val:
                        token = val
                        break

            # 3. Extraction paramètres d'URL (query string)
            if not token:
                qs = scope.get("query_string", b"").decode("latin1", errors="ignore")
                if qs:
                    params = parse_qs(qs)
                    for q_name in ("token", "user_key", "mcp_api_key", "api_key"):
                        param_val = params.get(q_name, [None])[0]
                        if param_val:
                            token = param_val.strip()
                            break

            # Si une clé statique d'infrastructure self.api_key est configurée, elle doit être respectée
            if self.api_key:
                if not token or not hmac.compare_digest(token, self.api_key):
                    logger.warning(
                        "Tentative d'accès MCP non autorisé sur %s (jeton d'authentification absent ou invalide)",
                        path,
                    )
                    error_body = json.dumps({
                        "jsonrpc": "2.0",
                        "id": None,
                        "error": {
                            "code": -32000,
                            "message": (
                                "Unauthorized: Jeton d'authentification MCP manquant ou invalide. "
                                "Fournissez l'en-tête 'Authorization: Bearer <token>' ou 'X-MCP-API-KEY: <clé>'."
                            ),
                        },
                    }).encode("utf-8")

                    response = Response(
                        content=error_body,
                        status_code=401,
                        media_type="application/json",
                        headers={"WWW-Authenticate": 'ApiKey realm="MELODI MCP", header="X-MCP-API-KEY"'},
                    )
                    await response(scope, receive, send)
                    return

        # Positionner le jeton utilisateur dans la variable de contexte pour la durée de la requête
        ctx_token = current_user_token.set(token)
        try:
            await self.app(scope, receive, send)
        finally:
            current_user_token.reset(ctx_token)
