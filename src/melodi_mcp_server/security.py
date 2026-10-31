"""Contrôles de sécurité locaux, gestion du contexte et sanitisation."""

import hmac
import json
import logging
import time
from contextvars import ContextVar
from typing import Any, Optional
from urllib.parse import parse_qs
import jsonschema
import jwt
from starlette.responses import Response

from .errors import ToolValidationError
from .settings import Settings, get_settings

logger = logging.getLogger(__name__)

# Variables de contexte asynchrones stockant le token utilisateur et le payload JWT de la requête en cours
current_user_token: ContextVar[Optional[str]] = ContextVar("current_user_token", default=None)
current_jwt_payload: ContextVar[Optional[dict[str, Any]]] = ContextVar("current_jwt_payload", default=None)


class SecurityContext:
    """Représente le contexte de sécurité de l'appelant MCP."""

    def __init__(
        self,
        client_id: Optional[str] = None,
        user_id: Optional[str] = None,
        user_key: Optional[str] = None,
        is_authenticated: bool = False,
    ):
        self.client_id = client_id or "mcp_client"
        self.user_id = user_id if is_authenticated else None
        self.user_key = user_key
        self.is_authenticated = is_authenticated or bool(user_key) or bool(user_id)


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
    """Intercepte les requêtes MCP pour authentifier l'accès via un en-tête unique Authorization.

    L'en-tête doit contenir un jeton JWT valide signé avec jwt_secret en HS256,
    contenant la clé d'infrastructure 'mcp_api_key' et l'identifiant utilisateur 'user_id'.
    L'identifiant utilisateur est injecté dans le ContextVar current_user_token pour transmission
    transparente vers MELODI ERP.
    """

    def __init__(
        self,
        app: Any,
        api_key: Optional[str] = None,
        jwt_secret: Optional[str] = None,
        admin_prefix: str = "/admin",
        settings: Optional[Settings] = None,
        db: Optional[Any] = None,
    ):
        self.app = app
        self._api_key = api_key.strip() if api_key and api_key.strip() else None
        self._jwt_secret = jwt_secret.strip() if jwt_secret and jwt_secret.strip() else None
        self.admin_prefix = admin_prefix
        self.settings = settings or get_settings()
        self.db = db

    def is_token_revoked(self, token_id: str) -> bool:
        """Vérifie si le token_id est présent dans la table des tokens révoqués."""
        if self.db and hasattr(self.db, "is_token_revoked"):
            return self.db.is_token_revoked(token_id)
        return False

    def get_jwt_secret(self) -> Optional[str]:
        """Retourne la clé secrète JWT active (mémoire ou settings)."""
        if self._jwt_secret:
            return self._jwt_secret
        if self.settings and self.settings.jwt_secret:
            return self.settings.jwt_secret.get_secret_value()
        return None

    def get_mcp_api_key(self) -> Optional[str]:
        """Retourne la clé MCP API active attendue de l'infrastructure."""
        if self._api_key:
            return self._api_key
        if self.settings and self.settings.mcp_api_key:
            return self.settings.mcp_api_key.get_secret_value()
        return None

    def update_security(
        self,
        jwt_secret: Optional[str] = None,
        mcp_api_key: Optional[str] = None,
    ) -> None:
        """Met à jour dynamiquement la configuration de sécurité en mémoire."""
        if jwt_secret is not None:
            self._jwt_secret = jwt_secret.strip() if jwt_secret.strip() else None
        if mcp_api_key is not None:
            self._api_key = mcp_api_key.strip() if mcp_api_key.strip() else None

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        # Exemption explicite pour OAuth, métadonnées de découverte, santé et console admin
        if (
            path.startswith("/oauth")
            or path.startswith("/.well-known")
            or path == "/health"
            or (self.admin_prefix and path.startswith(self.admin_prefix))
        ):
            await self.app(scope, receive, send)
            return

        # Les routes MCP à traiter incluent /mcp, /sse et la racine /
        is_mcp_path = path in ("/mcp", "/sse", "/") or path.startswith("/mcp/") or path.startswith("/sse/")

        if path == "/":
            headers = dict(scope.get("headers", []))
            accept = headers.get(b"accept", b"").decode("latin1", errors="ignore")
            # Si un navigateur visite la racine en HTML pour se rendre sur /admin, on le laisse passer
            if scope.get("method") == "GET" and "text/html" in accept and "text/event-stream" not in accept:
                await self.app(scope, receive, send)
                return

        user_token: Optional[str] = None
        jwt_payload: Optional[dict[str, Any]] = None

        if is_mcp_path:
            headers = dict(scope.get("headers", []))
            qs = scope.get("query_string", b"").decode("latin1", errors="ignore")
            params = parse_qs(qs) if qs else {}

            # 1. Extraction du jeton depuis l'en-tête Authorization (ou fallback query string pour SSE)
            raw_token: Optional[str] = None
            auth_val = headers.get(b"authorization", b"").decode("latin1", errors="ignore").strip()
            if auth_val:
                if auth_val.lower().startswith("bearer "):
                    raw_token = auth_val[7:].strip()
                else:
                    raw_token = auth_val
            elif params:
                param_token = params.get("token", [None])[0] or params.get("authorization", [None])[0]
                if param_token and param_token.strip():
                    raw_token = param_token.strip()
                    if raw_token.lower().startswith("bearer "):
                        raw_token = raw_token[7:].strip()

            if not raw_token:
                logger.warning(
                    "Tentative d'accès MCP non autorisé sur %s (en-tête Authorization manquant)",
                    path,
                )
                error_body = json.dumps({
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {
                        "code": -32000,
                        "message": (
                            "Unauthorized: En-tête 'Authorization: Bearer <jwt>' manquant. "
                            "Connectez-vous à MELODI MCP en fournissant le jeton JWT généré dans MELODI ERP."
                        ),
                    },
                }).encode("utf-8")
                response = Response(
                    content=error_body,
                    status_code=401,
                    media_type="application/json",
                    headers={
                        "WWW-Authenticate": (
                            'Bearer realm="MELODI MCP", '
                            'error="unauthorized", '
                            'error_description="Authentication required via OAuth 2.0 or JWT Bearer", '
                            'authorization_uri="/oauth/authorize"'
                        )
                    },
                )
                await response(scope, receive, send)
                return

            active_secret = self.get_jwt_secret()
            if not active_secret:
                logger.error(
                    "Accès MCP bloqué sur %s : aucune clé secrète JWT n'est configurée sur le serveur.",
                    path,
                )
                error_body = json.dumps({
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {
                        "code": -32000,
                        "message": (
                            "Unauthorized: Aucune clé secrète JWT n'est configurée sur le serveur MCP. "
                            "Configurez la clé secrète dans le Dashboard (/admin) ou via la variable MCP_JWT_SECRET."
                        ),
                    },
                }).encode("utf-8")
                response = Response(
                    content=error_body,
                    status_code=401,
                    media_type="application/json",
                    headers={"WWW-Authenticate": 'Bearer realm="MELODI MCP", error="invalid_token"'},
                )
                await response(scope, receive, send)
                return

            # 2. Décodage et validation cryptographique du jeton JWT (HS256)
            try:
                payload = jwt.decode(raw_token, active_secret, algorithms=["HS256"])
            except jwt.ExpiredSignatureError:
                logger.warning("Tentative d'accès MCP avec jeton JWT expiré sur %s", path)
                error_body = json.dumps({
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {
                        "code": -32000,
                        "message": (
                            "Unauthorized: Jeton JWT expiré. "
                            "Veuillez générer un nouveau jeton dans le module MCP de MELODI ERP."
                        ),
                    },
                }).encode("utf-8")
                response = Response(
                    content=error_body,
                    status_code=401,
                    media_type="application/json",
                    headers={"WWW-Authenticate": 'Bearer realm="MELODI MCP", error="invalid_token", error_description="The token is expired"'},
                )
                await response(scope, receive, send)
                return
            except jwt.InvalidSignatureError:
                logger.warning("Tentative d'accès MCP avec signature JWT invalide sur %s", path)
                error_body = json.dumps({
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {
                        "code": -32000,
                        "message": (
                            "Unauthorized: Signature du jeton JWT invalide. "
                            "La clé secrète ne correspond pas à celle configurée dans melodi_mcp_server."
                        ),
                    },
                }).encode("utf-8")
                response = Response(
                    content=error_body,
                    status_code=401,
                    media_type="application/json",
                    headers={"WWW-Authenticate": 'Bearer realm="MELODI MCP", error="invalid_token", error_description="Invalid signature"'},
                )
                await response(scope, receive, send)
                return
            except jwt.PyJWTError as e:
                logger.warning("Tentative d'accès MCP avec jeton JWT corrompu sur %s : %s", path, e)
                error_body = json.dumps({
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {
                        "code": -32000,
                        "message": f"Unauthorized: Jeton JWT invalide ou malformé ({e}).",
                    },
                }).encode("utf-8")
                response = Response(
                    content=error_body,
                    status_code=401,
                    media_type="application/json",
                    headers={"WWW-Authenticate": 'Bearer realm="MELODI MCP", error="invalid_token"'},
                )
                await response(scope, receive, send)
                return

            # 3. Validation de la date d'expiration (exp obligatoire et maximum 72h)
            exp_val = payload.get("exp")
            if not exp_val:
                logger.warning("Revendication 'exp' absente du payload JWT sur %s", path)
                error_body = json.dumps({
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {
                        "code": -32000,
                        "message": "Unauthorized: Jeton JWT incomplet. La date d'expiration 'exp' est obligatoire.",
                    },
                }).encode("utf-8")
                response = Response(
                    content=error_body,
                    status_code=401,
                    media_type="application/json",
                    headers={"WWW-Authenticate": 'Bearer realm="MELODI MCP", error="invalid_token"'},
                )
                await response(scope, receive, send)
                return

            try:
                now_ts = time.time()
                # 72 heures + marge de 5 minutes pour dérive d'horloge
                if float(exp_val) > (now_ts + (72 * 3600) + 300):
                    logger.warning("Durée de validité du jeton JWT supérieure à 72h (%s) sur %s", exp_val, path)
                    error_body = json.dumps({
                        "jsonrpc": "2.0",
                        "id": None,
                        "error": {
                            "code": -32000,
                            "message": "Unauthorized: La durée de validité du jeton JWT ne peut pas dépasser 72 heures.",
                        },
                    }).encode("utf-8")
                    response = Response(
                        content=error_body,
                        status_code=401,
                        media_type="application/json",
                        headers={"WWW-Authenticate": 'Bearer realm="MELODI MCP", error="invalid_token"'},
                    )
                    await response(scope, receive, send)
                    return
            except (ValueError, TypeError):
                pass

            # 4. Validation et vérification de la non-révocation du token_id
            token_id = payload.get("token_id") or payload.get("jti")
            if not token_id or not str(token_id).strip():
                logger.warning("Revendication 'token_id' absente du payload JWT sur %s", path)
                error_body = json.dumps({
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {
                        "code": -32000,
                        "message": (
                            "Unauthorized: Jeton JWT incomplet. "
                            "Le champ 'token_id' est obligatoire pour permettre la révocation."
                        ),
                    },
                }).encode("utf-8")
                response = Response(
                    content=error_body,
                    status_code=401,
                    media_type="application/json",
                    headers={"WWW-Authenticate": 'Bearer realm="MELODI MCP", error="invalid_token"'},
                )
                await response(scope, receive, send)
                return

            clean_token_id = str(token_id).strip()
            if self.is_token_revoked(clean_token_id):
                logger.warning("Tentative d'accès MCP avec token_id révoqué '%s' sur %s", clean_token_id, path)
                error_body = json.dumps({
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {
                        "code": -32000,
                        "message": "Unauthorized: Ce jeton JWT a été révoqué. Veuillez générer un nouveau jeton dans le module MCP de MELODI ERP.",
                    },
                }).encode("utf-8")
                response = Response(
                    content=error_body,
                    status_code=401,
                    media_type="application/json",
                    headers={"WWW-Authenticate": 'Bearer realm="MELODI MCP", error="invalid_token", error_description="Token revoked"'},
                )
                await response(scope, receive, send)
                return

            # 5. Validation de la clé d'infrastructure 'mcp_api_key'
            jwt_mcp_key = payload.get("mcp_api_key") or payload.get("mcp_key")
            expected_mcp_key = self.get_mcp_api_key()
            if expected_mcp_key:
                if not jwt_mcp_key or not hmac.compare_digest(str(jwt_mcp_key), str(expected_mcp_key)):
                    logger.warning("Clé mcp_api_key invalide dans le payload JWT sur %s", path)
                    error_body = json.dumps({
                        "jsonrpc": "2.0",
                        "id": None,
                        "error": {
                            "code": -32000,
                            "message": (
                                "Unauthorized: La clé d'infrastructure 'mcp_api_key' dans le jeton JWT est invalide ou manquante."
                            ),
                        },
                    }).encode("utf-8")
                    response = Response(
                        content=error_body,
                        status_code=401,
                        media_type="application/json",
                        headers={"WWW-Authenticate": 'Bearer realm="MELODI MCP", error="invalid_token"'},
                    )
                    await response(scope, receive, send)
                    return
            else:
                if not jwt_mcp_key:
                    logger.warning("Revendication 'mcp_api_key' absente du payload JWT sur %s", path)
                    error_body = json.dumps({
                        "jsonrpc": "2.0",
                        "id": None,
                        "error": {
                            "code": -32000,
                            "message": (
                                "Unauthorized: Le jeton JWT doit contenir le champ 'mcp_api_key'."
                            ),
                        },
                    }).encode("utf-8")
                    response = Response(
                        content=error_body,
                        status_code=401,
                        media_type="application/json",
                        headers={"WWW-Authenticate": 'Bearer realm="MELODI MCP", error="invalid_token"'},
                    )
                    await response(scope, receive, send)
                    return

            # 6. Validation et extraction de l'utilisateur ('user_id')
            extracted_user = payload.get("user_id") or payload.get("sub")
            if not extracted_user or not str(extracted_user).strip():
                logger.warning("Identifiant utilisateur absent du payload JWT sur %s", path)
                error_body = json.dumps({
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {
                        "code": -32000,
                        "message": (
                            "Unauthorized: Jeton JWT incomplet. "
                            "Le champ utilisateur 'user_id' est manquant dans le payload."
                        ),
                    },
                }).encode("utf-8")
                response = Response(
                    content=error_body,
                    status_code=401,
                    media_type="application/json",
                    headers={"WWW-Authenticate": 'Bearer realm="MELODI MCP", error="invalid_token"'},
                )
                await response(scope, receive, send)
                return

            user_token = str(extracted_user).strip()
            jwt_payload = payload

        # Positionner le jeton utilisateur et le payload dans les ContextVars pour la requête
        ctx_user = current_user_token.set(user_token)
        ctx_jwt = current_jwt_payload.set(jwt_payload)
        try:
            await self.app(scope, receive, send)
        finally:
            current_user_token.reset(ctx_user)
            current_jwt_payload.reset(ctx_jwt)
