"""Routes et endpoints OAuth 2.0 pour la découverte et l'authentification des clients IA."""

from __future__ import annotations

import base64
import hmac
import logging
import time
from typing import Any, Optional
import jwt
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.routing import Route

from ..admin.db import AdminDatabase
from ..melodi_client import MelodiAsyncClient
from ..settings import Settings
from .templates import render_authorize_page

logger = logging.getLogger(__name__)


async def _parse_request_data(request: Request) -> dict[str, Any]:
    """Parse le corps de la requête qu'il soit au format JSON ou form-urlencoded."""
    content_type = request.headers.get("content-type", "").lower()
    if "application/json" in content_type:
        try:
            return await request.json()
        except Exception:
            return {}
    if "application/x-www-form-urlencoded" in content_type or "multipart/form-data" in content_type:
        try:
            form = await request.form()
            return dict(form)
        except Exception:
            return {}
    return dict(request.query_params)


def create_oauth_routes(
    db: AdminDatabase,
    settings: Settings,
    client: Optional[MelodiAsyncClient] = None,
) -> list[Route]:
    """Crée l'ensemble des routes d'autorisation et de découverte OAuth 2.0."""

    def _get_issuer(request: Request) -> str:
        """Détermine l'URL de base racine pour les métadonnées de découverte RFC 8414."""
        forwarded_proto = request.headers.get("x-forwarded-proto")
        forwarded_host = request.headers.get("x-forwarded-host")
        if forwarded_proto and forwarded_host:
            return f"{forwarded_proto}://{forwarded_host}".rstrip("/")
        return str(request.base_url).rstrip("/")

    def _get_discovery_metadata(request: Request) -> dict[str, Any]:
        issuer = _get_issuer(request)
        return {
            "issuer": issuer,
            "authorization_endpoint": f"{issuer}/oauth/authorize",
            "token_endpoint": f"{issuer}/oauth/token",
            "registration_endpoint": f"{issuer}/oauth/register",
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code"],
            "token_endpoint_auth_methods_supported": ["none", "client_secret_post", "client_secret_basic"],
            "code_challenge_methods_supported": ["S256", "plain"],
            "scopes_supported": ["mcp:all"],
        }

    async def oauth_discovery_endpoint(request: Request) -> JSONResponse:
        """Endpoint de découverte des métadonnées du serveur d'autorisation (RFC 8414)."""
        return JSONResponse(_get_discovery_metadata(request))

    async def openid_configuration_endpoint(request: Request) -> JSONResponse:
        """Endpoint de compatibilité OpenID Connect Discovery."""
        return JSONResponse(_get_discovery_metadata(request))

    async def authorize_get(request: Request) -> HTMLResponse:
        """Affiche la page web d'autorisation invitant l'utilisateur à coller son JWT MELODI."""
        params = request.query_params
        client_id = params.get("client_id", "ai_client")
        redirect_uri = params.get("redirect_uri", "")
        state = params.get("state")
        code_challenge = params.get("code_challenge")
        code_challenge_method = params.get("code_challenge_method")

        if not redirect_uri:
            return HTMLResponse(
                "<h3>Paramètre manquant : 'redirect_uri' est obligatoire pour lancer l'autorisation OAuth 2.0.</h3>",
                status_code=400,
            )

        return HTMLResponse(
            render_authorize_page(
                client_id=client_id,
                redirect_uri=redirect_uri,
                state=state,
                code_challenge=code_challenge,
                code_challenge_method=code_challenge_method,
            )
        )

    async def authorize_post(request: Request) -> Any:
        """Traite le formulaire de saisie du jeton JWT, effectue les vérifications et redirige."""
        data = await _parse_request_data(request)
        client_id = str(data.get("client_id") or "ai_client").strip()
        redirect_uri = str(data.get("redirect_uri") or "").strip()
        state = data.get("state")
        code_challenge = data.get("code_challenge")
        code_challenge_method = data.get("code_challenge_method")
        raw_jwt = str(data.get("jwt_token") or "").strip()

        def _error_view(msg: str) -> HTMLResponse:
            return HTMLResponse(
                render_authorize_page(
                    client_id=client_id,
                    redirect_uri=redirect_uri,
                    state=state,
                    code_challenge=code_challenge,
                    code_challenge_method=code_challenge_method,
                    error_message=msg,
                ),
                status_code=400,
            )

        if not redirect_uri:
            return HTMLResponse("<h3>Paramètre obligatoire 'redirect_uri' manquant.</h3>", status_code=400)

        if not raw_jwt:
            return _error_view("Veuillez coller votre jeton JWT d'accès généré dans MELODI ERP.")

        clean_token = raw_jwt[7:].strip() if raw_jwt.lower().startswith("bearer ") else raw_jwt

        # 1. Vérification de la clé secrète JWT active
        active_secret = (
            settings.jwt_secret.get_secret_value()
            if settings.jwt_secret
            else db.get_config("jwt_secret")
        )
        if not active_secret:
            return _error_view(
                "Le serveur MCP n'a pas encore de clé secrète JWT configurée. "
                "Configurez MCP_JWT_SECRET dans le Dashboard (/admin) ou via .env."
            )

        # 2. Validation cryptographique du jeton (HS256)
        try:
            payload = jwt.decode(clean_token, active_secret, algorithms=["HS256"])
        except jwt.ExpiredSignatureError:
            return _error_view("Le jeton JWT a expiré. Veuillez générer un nouveau jeton dans MELODI ERP.")
        except jwt.InvalidSignatureError:
            return _error_view(
                "Signature du jeton invalide. La clé secrète du jeton ne correspond pas à celle du serveur MCP."
            )
        except jwt.PyJWTError as e:
            return _error_view(f"Jeton JWT invalide ou corrompu : {e}")

        # 3. Contrôle de révocation (cache mémoire / base SQLite)
        token_id = payload.get("token_id") or payload.get("jti")
        if token_id and db.is_token_revoked(token_id):
            return _error_view(f"Ce jeton d'accès (ID: {token_id}) a été révoqué.")

        # 4. Vérification de la clé d'infrastructure mcp_api_key
        expected_mcp_key = (
            settings.mcp_api_key.get_secret_value()
            if settings.mcp_api_key
            else db.get_config("mcp_api_key")
        )
        jwt_mcp_key = payload.get("mcp_api_key") or payload.get("mcp_key")
        if expected_mcp_key:
            if not jwt_mcp_key or not hmac.compare_digest(str(jwt_mcp_key), str(expected_mcp_key)):
                return _error_view("La clé d'infrastructure 'mcp_api_key' contenue dans le jeton est invalide.")
        else:
            if not jwt_mcp_key:
                return _error_view("Le jeton JWT doit obligatoirement comporter la claim 'mcp_api_key'.")

        # 5. Vérification de l'identifiant utilisateur (user_id)
        extracted_user = (
            payload.get("user_id")
            or payload.get("sub")
            or payload.get("user_api_key")
            or payload.get("user_key")
            or payload.get("user_token")
        )
        if not extracted_user or not str(extracted_user).strip():
            return _error_view("Jeton incomplet : identifiant utilisateur 'user_id' manquant dans le payload JWT.")

        # 6. Test de connectivité avec l'instance ERP
        if client:
            try:
                await client.get_health()
            except Exception as conn_err:
                logger.warning("Connexion ERP non optimale lors de l'autorisation OAuth : %s", conn_err)

        # 7. Génération du code d'autorisation éphémère (5 min)
        code = db.create_oauth_code(
            client_id=client_id,
            redirect_uri=redirect_uri,
            jwt_token=clean_token,
            token_id=str(token_id) if token_id else None,
            user_id=str(extracted_user),
            code_challenge=str(code_challenge) if code_challenge else None,
            code_challenge_method=str(code_challenge_method) if code_challenge_method else None,
            ttl_seconds=300,
        )

        sep = "&" if "?" in redirect_uri else "?"
        redirect_target = f"{redirect_uri}{sep}code={code}"
        if state:
            redirect_target += f"&state={state}"

        logger.info(
            "Autorisation OAuth accordée avec succès pour le client '%s' (user: %s).",
            client_id,
            extracted_user,
        )
        return RedirectResponse(url=redirect_target, status_code=302)

    async def token_post(request: Request) -> JSONResponse:
        """Échange le code d'autorisation contre le jeton d'accès JWT."""
        data = await _parse_request_data(request)
        grant_type = str(data.get("grant_type") or "").strip()
        if grant_type != "authorization_code":
            return JSONResponse(
                {
                    "error": "unsupported_grant_type",
                    "error_description": "Seul le grant_type 'authorization_code' est supporté.",
                },
                status_code=400,
            )

        code = str(data.get("code") or "").strip()
        if not code:
            return JSONResponse(
                {"error": "invalid_request", "error_description": "Le paramètre 'code' est obligatoire."},
                status_code=400,
            )

        client_id = str(data.get("client_id") or "").strip()
        # Fallback Basic Auth pour client_id
        auth_header = request.headers.get("authorization", "")
        if not client_id and auth_header.lower().startswith("basic "):
            try:
                b64 = auth_header[6:].strip()
                creds = base64.b64decode(b64).decode("utf-8").split(":", 1)
                client_id = creds[0]
            except Exception:
                pass

        redirect_uri = str(data.get("redirect_uri") or "").strip() or None
        code_verifier = str(data.get("code_verifier") or "").strip() or None

        auth_record = db.consume_oauth_code(
            code=code,
            client_id=client_id or "ai_client",
            redirect_uri=redirect_uri,
            code_verifier=code_verifier,
        )

        if not auth_record:
            return JSONResponse(
                {
                    "error": "invalid_grant",
                    "error_description": (
                        "Code d'autorisation invalide, expiré, déjà consommé, "
                        "ou échec de la validation PKCE."
                    ),
                },
                status_code=400,
            )

        jwt_token = auth_record["jwt_token"]
        expires_in = 3600
        try:
            unverified = jwt.decode(jwt_token, options={"verify_signature": False})
            if "exp" in unverified:
                rem = int(unverified["exp"] - time.time())
                expires_in = max(60, rem)
        except Exception:
            pass

        logger.info(
            "Jeton d'accès OAuth délivré avec succès au client '%s'.",
            auth_record["client_id"],
        )
        return JSONResponse(
            {
                "access_token": jwt_token,
                "token_type": "Bearer",
                "expires_in": expires_in,
                "scope": "mcp:all",
            },
            headers={
                "Cache-Control": "no-store",
                "Pragma": "no-cache",
            },
        )

    async def register_post(request: Request) -> JSONResponse:
        """Enregistrement dynamique de clients OAuth (RFC 7591)."""
        data = await _parse_request_data(request)
        client_name = data.get("client_name") or "AI Client"
        redirect_uris = data.get("redirect_uris") or []
        if isinstance(redirect_uris, str):
            redirect_uris = [redirect_uris]

        client_info = db.register_oauth_client(
            client_name=str(client_name),
            redirect_uris=redirect_uris,
        )
        return JSONResponse(
            {
                "client_id": client_info["client_id"],
                "client_secret": client_info["client_secret"],
                "client_name": client_info["client_name"],
                "redirect_uris": client_info["redirect_uris"],
                "grant_types": ["authorization_code"],
                "response_types": ["code"],
            },
            status_code=201,
        )

    return [
        Route("/.well-known/oauth-authorization-server", oauth_discovery_endpoint, methods=["GET"]),
        Route("/.well-known/openid-configuration", openid_configuration_endpoint, methods=["GET"]),
        Route("/oauth/authorize", authorize_get, methods=["GET"]),
        Route("/oauth/authorize", authorize_post, methods=["POST"]),
        Route("/oauth/token", token_post, methods=["POST"]),
        Route("/oauth/register", register_post, methods=["POST"]),
    ]
