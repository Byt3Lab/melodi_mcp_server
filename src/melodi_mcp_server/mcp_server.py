"""Adaptateur MCP centralisant les protocoles STDIO et HTTP (SSE / Streamable)."""

import asyncio
import logging
from typing import Any, Optional
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.responses import JSONResponse, RedirectResponse
from starlette.routing import Route

from mcp.server.lowlevel import Server
from mcp.server.lowlevel.server import NotificationOptions
from mcp.server.stdio import stdio_server
import mcp.types as types

from .admin.routes import create_admin_routes
from .health import HealthChecker
from .melodi_client import MelodiAsyncClient
from .security import McpAuthMiddleware, SecurityContext, current_user_token, current_jwt_payload
from .settings import Settings, get_settings
from .tool_dispatcher import ToolDispatcher
from .tool_registry import DynamicToolRegistry

logger = logging.getLogger(__name__)


class RootMcpApp:
    """Passerelle pour router le trafic MCP sur / ou rediriger les navigateurs vers /admin."""

    def __init__(self, mcp_handler, admin_prefix: str = "/admin"):
        self.mcp_handler = mcp_handler
        self.admin_prefix = admin_prefix

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["method"] == "GET":
            headers = dict(scope.get("headers", []))
            accept = headers.get(b"accept", b"").decode("latin1", errors="ignore")
            # Rediriger vers l'admin si la requête provient d'un navigateur
            if "text/html" in accept and "text/event-stream" not in accept:
                response = RedirectResponse(url=self.admin_prefix, status_code=307)
                await response(scope, receive, send)
                return
        # Sinon, traiter comme une requête MCP (VS Code, Copilot, Cursor, etc.)
        await self.mcp_handler(scope, receive, send)


class MelodiMcpServerApp:
    """Gestionnaire de cycle de vie et adaptateur de protocole MCP pour MELODI ERP."""

    def __init__(
        self,
        settings: Optional[Settings] = None,
        client: Optional[MelodiAsyncClient] = None,
        registry: Optional[DynamicToolRegistry] = None,
        dispatcher: Optional[ToolDispatcher] = None,
    ):
        self.settings = settings or get_settings()
        self.client = client or MelodiAsyncClient(self.settings)
        self.registry = registry or DynamicToolRegistry(self.client, self.settings)
        self.dispatcher = dispatcher or ToolDispatcher(self.registry, self.client, self.settings)
        self.health_checker = HealthChecker(self.registry, self.client, self.settings)

        self.server = Server("melodi-mcp-server", version="0.1.0")
        self._register_handlers()

    def _register_handlers(self) -> None:
        """Enregistre les handlers MCP officiels auprès du serveur bas-niveau."""

        async def handle_list_tools(
            ctx: Any, req: types.PaginatedRequestParams
        ) -> types.ListToolsResult:
            tools = self.registry.list_mcp_tools()
            logger.debug("Diffusion de la liste des outils MCP (%d outils disponibles)", len(tools))
            return types.ListToolsResult(tools=tools)

        async def handle_call_tool(
            ctx: Any, req: types.CallToolRequestParams
        ) -> types.CallToolResult:
            args = req.arguments or {}
            token = current_user_token.get()
            jwt_data = current_jwt_payload.get() or {}
            sec_context = SecurityContext(
                user_key=token,
                user_id=str(jwt_data.get("user_id") or token) if (jwt_data.get("user_id") is not None or token) else None,
                is_authenticated=bool(token),
            )
            result = await self.dispatcher.execute_tool(req.name, args, context=sec_context)
            return result

        self.server.add_request_handler("tools/list", types.PaginatedRequestParams, handle_list_tools)
        self.server.add_request_handler("tools/call", types.CallToolRequestParams, handle_call_tool)

    async def startup(self) -> None:
        """Initialise les ressources, charge le catalogue initial et démarre le polling."""
        logger.info("Initialisation de melodi_mcp_server...")
        try:
            await self.registry.sync_now()
        except Exception as e:
            logger.warning(
                "Le serveur démarre sans catalogue initial suite à une erreur : %s (le serveur tentera de se resynchroniser périodiquement)",
                e,
            )
        await self.registry.start_sync_loop()

    async def shutdown(self) -> None:
        """Arrête proprement les tâches asynchrones et ferme les connexions HTTP."""
        logger.info("Arrêt de melodi_mcp_server...")
        await self.registry.stop_sync_loop()
        await self.client.aclose()

    async def run_stdio(self) -> None:
        """Exécute le serveur sur l'entrée/sortie standard (STDIO)."""
        await self.startup()
        try:
            async with stdio_server() as (read_stream, write_stream):
                init_options = self.server.create_initialization_options(
                    notification_options=NotificationOptions(tools_changed=True)
                )
                logger.info("Serveur MCP démarré en mode STDIO (flux JSON-RPC sur stdin/stdout).")
                await self.server.run(read_stream, write_stream, init_options)
        finally:
            await self.shutdown()

    def create_http_app(self, admin_db: Optional[Any] = None) -> Starlette:
        """Crée l'application Web unifiée pour le transport HTTP Streamable / SSE et l'Admin."""
        mcp_app = self.server.streamable_http_app()

        mcp_handler = None
        for r in mcp_app.routes:
            if getattr(r, "path", "") == "/mcp":
                mcp_handler = r.endpoint
                break

        routes = list(mcp_app.routes)
        if mcp_handler:
            # Compatibilité étendue avec les clients MCP (VS Code Copilot, Cursor, etc.)
            # permettant de cibler soit '/', '/mcp', ou '/sse'
            routes.append(Route("/", RootMcpApp(mcp_handler, self.settings.admin_prefix)))
            routes.append(Route("/sse", mcp_handler))

        async def health_endpoint(request) -> JSONResponse:
            health_data = await self.health_checker.check()
            status_code = 200 if health_data["status"] in ("HEALTHY", "DEGRADED") else 503
            return JSONResponse(health_data, status_code=status_code)

        routes.append(Route("/health", health_endpoint, methods=["GET"]))

        # Initialisation de la base de données SQLite partagée
        if admin_db is None:
            from melodi_mcp_server.admin.db import AdminDatabase
            admin_db = AdminDatabase(self.settings.admin_db_path)

        self._admin_db = admin_db

        # Ajout des routes OAuth 2.0 (Découverte RFC 8414, Authorize & Token)
        from melodi_mcp_server.oauth import create_oauth_routes
        oauth_routes = create_oauth_routes(admin_db, self.settings, client=self.client)
        routes.extend(oauth_routes)
        logger.info("Serveur d'autorisation OAuth 2.0 activé (/oauth/authorize, /oauth/token, /.well-known).")

        # Ajout du Dashboard d'Administration si activé
        if self.settings.admin_dashboard_enabled:

            saved_url = admin_db.get_config("melodi_base_url")
            if saved_url:
                try:
                    self.client.update_base_url(saved_url)
                except Exception as e:
                    logger.warning("Impossible d'appliquer l'URL persistée '%s' : %s", saved_url, e)

            saved_key = admin_db.get_config("melodi_api_key")
            if saved_key:
                try:
                    self.client.update_api_key(saved_key)
                except Exception as e:
                    logger.warning("Impossible d'appliquer la clé API persistée : %s", e)

            saved_prefix = admin_db.get_config("melodi_api_prefix")
            if saved_prefix is not None:
                try:
                    self.client.update_api_prefix(saved_prefix)
                except Exception as e:
                    logger.warning("Impossible d'appliquer le préfixe API persisté '%s' : %s", saved_prefix, e)

            saved_jwt_secret = admin_db.get_config("jwt_secret")
            if saved_jwt_secret:
                try:
                    from pydantic import SecretStr
                    self.settings.jwt_secret = SecretStr(saved_jwt_secret)
                except Exception as e:
                    logger.warning("Impossible d'appliquer la clé secrète JWT persistée : %s", e)

            saved_mcp_api_key = admin_db.get_config("mcp_api_key")
            if saved_mcp_api_key:
                try:
                    from pydantic import SecretStr
                    self.settings.mcp_api_key = SecretStr(saved_mcp_api_key)
                except Exception as e:
                    logger.warning("Impossible d'appliquer la clé MCP API persistée : %s", e)

            admin_routes = create_admin_routes(
                self.registry,
                self.health_checker,
                self.settings,
                db=admin_db,
            )
            routes.extend(admin_routes)
            logger.info(
                "Dashboard d'administration activé sur le préfixe : %s",
                self.settings.admin_prefix,
            )

        from contextlib import asynccontextmanager

        session_mgr = self.server._session_manager

        @asynccontextmanager
        async def lifespan(app):
            await self.startup()
            # Nettoyage automatique des tokens expirés (> 72h) et des codes OAuth au démarrage
            if hasattr(self, "_admin_db") and self._admin_db:
                try:
                    self._admin_db.cleanup_expired_revoked_tokens(max_age_hours=72)
                    self._admin_db.cleanup_expired_oauth_codes()
                except Exception as e:
                    logger.warning("Erreur lors de la purge initiale des tokens expirés : %s", e)
            try:
                if session_mgr:
                    async with session_mgr.run():
                        yield
                else:
                    yield
            finally:
                await self.shutdown()

        api_key_str = self.settings.mcp_api_key.get_secret_value() if self.settings.mcp_api_key else None
        jwt_secret_str = self.settings.jwt_secret.get_secret_value() if self.settings.jwt_secret else None
        middleware = [
            Middleware(
                McpAuthMiddleware,
                api_key=api_key_str,
                jwt_secret=jwt_secret_str,
                admin_prefix=self.settings.admin_prefix,
                settings=self.settings,
                db=admin_db,
            )
        ]
        if jwt_secret_str:
            logger.info("Authentification MCP unifiée par jeton JWT active (Authorization: Bearer <jwt> requis).")
        else:
            logger.info("Serveur MCP en attente de configuration de jwt_secret.")

        return Starlette(routes=routes, middleware=middleware, lifespan=lifespan)
