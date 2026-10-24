"""Routes web et API JSON pour la console d'administration."""

import logging
from typing import Any
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Route

from ..health import HealthChecker
from ..metrics import get_metrics
from ..settings import Settings, get_settings
from ..tool_registry import DynamicToolRegistry
from .templates import DASHBOARD_HTML, LOGIN_HTML

logger = logging.getLogger(__name__)


def create_admin_routes(
    registry: DynamicToolRegistry,
    health_checker: HealthChecker,
    settings: Settings,
) -> list[Route]:
    """Crée les routes Starlette pour la console d'administration."""
    metrics = get_metrics()
    prefix = settings.admin_prefix.rstrip("/")

    def _check_auth(request: Request) -> bool:
        """Vérifie l'accès si ADMIN_API_KEY est configurée."""
        if not settings.admin_api_key or not settings.admin_api_key.get_secret_value():
            return True
        expected = settings.admin_api_key.get_secret_value()

        # 1. Si un paramètre de requête explicite est fourni
        if "api_key" in request.query_params:
            return request.query_params["api_key"] == expected

        # 2. Si un en-tête Authorization explicite est fourni
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            return auth_header[7:].strip() == expected

        # 3. Si un en-tête X-API-Key explicite est fourni
        if "X-API-Key" in request.headers:
            return request.headers["X-API-Key"] == expected

        # 4. Repli sur le cookie de session
        cookie_key = request.cookies.get("admin_api_key", "")
        if cookie_key == expected:
            return True

        # 5. Si la clé est la clé par défaut 'admin', accès direct autorisé sans forcer la saisie
        if expected == "admin":
            return True

        return False

    async def dashboard_view(request: Request) -> HTMLResponse:
        """Page HTML principale du dashboard ou mire de connexion."""
        if not _check_auth(request):
            return HTMLResponse(LOGIN_HTML, status_code=200)
        response = HTMLResponse(DASHBOARD_HTML)
        if settings.admin_api_key and settings.admin_api_key.get_secret_value():
            expected = settings.admin_api_key.get_secret_value()
            query_key = request.query_params.get("api_key", "")
            if query_key == expected or expected == "admin":
                response.set_cookie("admin_api_key", expected, path="/", samesite="lax")
        return response

    async def status_api(request: Request) -> JSONResponse:
        """Endpoint JSON d'état de santé et de métriques en temps réel."""
        if not _check_auth(request):
            return JSONResponse({"error": "Unauthorized"}, status_code=401)
        health_data = await health_checker.check()
        metrics_data = metrics.get_summary()
        return JSONResponse({"health": health_data, "metrics": metrics_data})

    async def tools_api(request: Request) -> JSONResponse:
        """Endpoint JSON listant tous les outils disponibles et leurs détails."""
        if not _check_auth(request):
            return JSONResponse({"error": "Unauthorized"}, status_code=401)
        tools = registry.list_internal_tools()
        tools_list = [
            {
                "name": t.name,
                "description": t.description,
                "input_schema": t.input_schema,
                "read_only": t.read_only,
                "requires_confirmation": t.requires_confirmation,
                "timeout_seconds": t.timeout_seconds,
                "required_permissions": t.required_permissions,
            }
            for t in tools
        ]
        return JSONResponse({"tools": tools_list, "revision": registry.catalog_revision})

    async def sync_api(request: Request) -> JSONResponse:
        """Déclenche une synchronisation immédiate et forcée du catalogue."""
        if not _check_auth(request):
            return JSONResponse({"error": "Unauthorized"}, status_code=401)
        try:
            count = await registry.sync_now(force=True)
            return JSONResponse({"success": True, "tools_count": count, "revision": registry.catalog_revision})
        except Exception as e:
            logger.error("Erreur lors de la synchronisation manuelle depuis l'admin : %s", e)
            return JSONResponse({"success": False, "error": str(e)}, status_code=500)

    routes = [
        Route(f"{prefix}", dashboard_view, methods=["GET"]),
        Route(f"{prefix}/", dashboard_view, methods=["GET"]),
        Route(f"{prefix}/api/status", status_api, methods=["GET"]),
        Route(f"{prefix}/api/tools", tools_api, methods=["GET"]),
        Route(f"{prefix}/api/sync", sync_api, methods=["POST"]),
    ]
    return routes
