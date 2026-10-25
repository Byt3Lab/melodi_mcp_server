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
from .security import McpAuthMiddleware
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
            result = await self.dispatcher.execute_tool(req.name, args)
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

    def create_http_app(self) -> Starlette:
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

        # Ajout du Dashboard d'Administration si activé
        if self.settings.admin_dashboard_enabled:
            admin_routes = create_admin_routes(
                self.registry,
                self.health_checker,
                self.settings,
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
            try:
                if session_mgr:
                    async with session_mgr.run():
                        yield
                else:
                    yield
            finally:
                await self.shutdown()

        middleware = []
        api_key_str = self.settings.mcp_api_key.get_secret_value() if self.settings.mcp_api_key else None
        if api_key_str:
            middleware.append(
                Middleware(
                    McpAuthMiddleware,
                    api_key=api_key_str,
                    admin_prefix=self.settings.admin_prefix,
                )
            )
            logger.info("Authentification MCP activée (clé requise pour /mcp, / et /sse).")

        return Starlette(routes=routes, middleware=middleware, lifespan=lifespan)
