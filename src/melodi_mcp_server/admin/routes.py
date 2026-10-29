"""Routes web et API JSON pour la console d'administration avec authentification SQLite."""

from __future__ import annotations

import json
import logging
from typing import Any, Optional
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Route

from ..health import HealthChecker
from ..metrics import get_metrics
from ..settings import Settings
from ..tool_registry import DynamicToolRegistry
from .db import AdminDatabase
from .templates import DASHBOARD_HTML, LOGIN_HTML, SETUP_HTML

logger = logging.getLogger(__name__)


def create_admin_routes(
    registry: DynamicToolRegistry,
    health_checker: HealthChecker,
    settings: Settings,
    db: Optional[AdminDatabase] = None,
) -> list[Route]:
    """Crée les routes Starlette pour la console d'administration."""
    metrics = get_metrics()
    prefix = settings.admin_prefix.rstrip("/")
    database = db or AdminDatabase(settings.admin_db_path)

    async def _parse_body(request: Request) -> dict[str, Any]:
        """Extrait le corps JSON ou données de formulaire de la requête."""
        try:
            return await request.json()
        except Exception:
            try:
                form = await request.form()
                return dict(form)
            except Exception:
                return {}

    def _get_current_user(request: Request) -> Optional[dict[str, Any]]:
        """Identifie l'utilisateur connecté via cookie de session ou jeton d'API."""
        # 1. Vérification du cookie de session
        session_id = request.cookies.get("admin_session")
        if session_id:
            user = database.get_user_by_session(session_id)
            if user:
                return user

        # 2. Vérification par jeton Bearer (session_id ou ADMIN_API_KEY)
        auth_header = request.headers.get("Authorization", "")
        token = None
        if auth_header.startswith("Bearer "):
            token = auth_header[7:].strip()
        elif "X-API-Key" in request.headers:
            token = request.headers["X-API-Key"].strip()
        elif "api_key" in request.query_params:
            token = request.query_params["api_key"].strip()

        if token:
            # Tester si le jeton est un ID de session valide
            user = database.get_user_by_session(token)
            if user:
                return user

        return None

    # --- Pages Web HTML ---

    async def dashboard_view(request: Request) -> HTMLResponse:
        """Page principale de l'administration : Setup, Login ou Dashboard."""
        # Phase 1 : Si aucun administrateur n'est encore enregistré -> Mode Amorçage
        if not database.has_users():
            return HTMLResponse(SETUP_HTML, status_code=200)

        # Phase 2 : Vérification de l'authentification de l'utilisateur
        user = _get_current_user(request)
        if not user:
            return HTMLResponse(LOGIN_HTML, status_code=200)

        # Phase 3 : Visiteur authentifié -> Affichage du Dashboard
        return HTMLResponse(DASHBOARD_HTML, status_code=200)

    # --- APIs d'Authentification & Amorçage ---

    async def setup_api(request: Request) -> JSONResponse:
        """Crée le premier compte administrateur si la base est vierge."""
        if database.has_users():
            return JSONResponse(
                {"success": False, "error": "Le serveur a déjà été configuré. Connectez-vous."},
                status_code=400,
            )

        body = await _parse_body(request)
        setup_key = (body.get("setup_key") or "").strip()
        username = (body.get("username") or "").strip()
        password = str(body.get("password") or "")

        expected_key = (
            settings.admin_api_key.get_secret_value()
            if (settings.admin_api_key and settings.admin_api_key.get_secret_value())
            else "admin"
        )

        if setup_key != expected_key:
            return JSONResponse(
                {"success": False, "error": "Clé d'amorçage (ADMIN_API_KEY) invalide."},
                status_code=401,
            )

        try:
            admin_user = database.setup_first_admin(username=username, password=password)
            session_id = database.create_session(admin_user["id"], ttl_hours=settings.admin_session_ttl_hours)

            res = JSONResponse({
                "success": True,
                "user": admin_user,
                "session_id": session_id,
                "session_token": session_id,
            })
            max_age = settings.admin_session_ttl_hours * 3600
            res.set_cookie(
                "admin_session",
                session_id,
                max_age=max_age,
                httponly=True,
                samesite="lax",
                path="/",
            )
            return res
        except Exception as e:
            logger.error("Erreur lors du setup initial : %s", e)
            return JSONResponse({"success": False, "error": str(e)}, status_code=400)

    async def login_api(request: Request) -> JSONResponse:
        """Authentifie un utilisateur et établit une session."""
        body = await _parse_body(request)
        username = (body.get("username") or "").strip()
        password = str(body.get("password") or "")

        user = database.authenticate(username=username, password=password)
        if not user:
            return JSONResponse(
                {"success": False, "error": "Identifiant ou mot de passe incorrect."},
                status_code=401,
            )

        session_id = database.create_session(user["id"], ttl_hours=settings.admin_session_ttl_hours)
        res = JSONResponse({
            "success": True,
            "user": user,
            "session_id": session_id,
            "session_token": session_id,
        })
        max_age = settings.admin_session_ttl_hours * 3600
        res.set_cookie(
            "admin_session",
            session_id,
            max_age=max_age,
            httponly=True,
            samesite="lax",
            path="/",
        )
        return res

    async def logout_api(request: Request) -> JSONResponse:
        """Détruit la session active et efface le cookie."""
        session_id = request.cookies.get("admin_session")
        if session_id:
            database.delete_session(session_id)
        res = JSONResponse({"success": True})
        res.delete_cookie("admin_session", path="/")
        return res

    async def me_api(request: Request) -> JSONResponse:
        """Retourne le profil de l'utilisateur connecté."""
        user = _get_current_user(request)
        if not user:
            return JSONResponse({"error": "Unauthorized"}, status_code=401)
        return JSONResponse({"user": user})

    # --- APIs de Gestion des Utilisateurs (CRUD) ---

    async def list_users_api(request: Request) -> JSONResponse:
        """Liste tous les utilisateurs (accessible uniquement aux administrateurs)."""
        current_user = _get_current_user(request)
        if not current_user:
            return JSONResponse({"error": "Unauthorized"}, status_code=401)
        if current_user.get("role") != "admin":
            return JSONResponse({"error": "Forbidden"}, status_code=403)

        users = database.list_users()
        return JSONResponse({"users": users})

    async def create_user_api(request: Request) -> JSONResponse:
        """Crée un nouvel utilisateur (accessible uniquement aux administrateurs)."""
        current_user = _get_current_user(request)
        if not current_user:
            return JSONResponse({"error": "Unauthorized"}, status_code=401)
        if current_user.get("role") != "admin":
            return JSONResponse({"error": "Forbidden"}, status_code=403)

        body = await _parse_body(request)
        username = (body.get("username") or "").strip()
        password = str(body.get("password") or "")
        role = (body.get("role") or "admin").strip().lower()

        try:
            new_user = database.create_user(username=username, password=password, role=role)
            return JSONResponse({"success": True, "user": new_user}, status_code=201)
        except Exception as e:
            return JSONResponse({"success": False, "error": str(e)}, status_code=400)

    async def update_password_api(request: Request) -> JSONResponse:
        """Met à jour le mot de passe d'un utilisateur."""
        current_user = _get_current_user(request)
        if not current_user:
            return JSONResponse({"error": "Unauthorized"}, status_code=401)
        if current_user.get("role") != "admin":
            return JSONResponse({"error": "Forbidden"}, status_code=403)

        user_id = int(request.path_params["user_id"])
        body = await _parse_body(request)
        new_password = str(body.get("password") or "")

        try:
            database.update_password(user_id=user_id, new_password=new_password)
            return JSONResponse({"success": True})
        except Exception as e:
            return JSONResponse({"success": False, "error": str(e)}, status_code=400)

    async def toggle_status_api(request: Request) -> JSONResponse:
        """Active ou désactive un compte utilisateur."""
        current_user = _get_current_user(request)
        if not current_user:
            return JSONResponse({"error": "Unauthorized"}, status_code=401)
        if current_user.get("role") != "admin":
            return JSONResponse({"error": "Forbidden"}, status_code=403)

        user_id = int(request.path_params["user_id"])
        try:
            database.toggle_user_status(user_id=user_id, current_user_id=current_user.get("id"))
            return JSONResponse({"success": True})
        except Exception as e:
            return JSONResponse({"success": False, "error": str(e)}, status_code=400)

    async def delete_user_api(request: Request) -> JSONResponse:
        """Supprime un compte utilisateur avec vérifications."""
        current_user = _get_current_user(request)
        if not current_user:
            return JSONResponse({"error": "Unauthorized"}, status_code=401)
        if current_user.get("role") != "admin":
            return JSONResponse({"error": "Forbidden"}, status_code=403)

        user_id = int(request.path_params["user_id"])
        try:
            database.delete_user(user_id=user_id, current_user_id=current_user.get("id"))
            return JSONResponse({"success": True})
        except Exception as e:
            return JSONResponse({"success": False, "error": str(e)}, status_code=400)

    # --- APIs Métriques, Outils & Synchronisation ---

    async def status_api(request: Request) -> JSONResponse:
        """Endpoint JSON d'état de santé et de métriques en temps réel."""
        if not _get_current_user(request):
            return JSONResponse({"error": "Unauthorized"}, status_code=401)
        health_data = await health_checker.check()
        metrics_data = metrics.get_summary()
        return JSONResponse({"health": health_data, "metrics": metrics_data})

    async def tools_api(request: Request) -> JSONResponse:
        """Endpoint JSON listant tous les outils disponibles et leurs détails."""
        if not _get_current_user(request):
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
        current_user = _get_current_user(request)
        if not current_user:
            return JSONResponse({"error": "Unauthorized"}, status_code=401)
        if current_user.get("role") != "admin":
            return JSONResponse({"error": "Forbidden"}, status_code=403)

        try:
            count = await registry.sync_now(force=True)
            return JSONResponse({"success": True, "tools_count": count, "revision": registry.catalog_revision})
        except Exception as e:
            logger.error("Erreur lors de la synchronisation manuelle depuis l'admin : %s", e)
            return JSONResponse({"success": False, "error": str(e)}, status_code=500)

    routes = [
        # Pages HTML
        Route(f"{prefix}", dashboard_view, methods=["GET"]),
        Route(f"{prefix}/", dashboard_view, methods=["GET"]),
        # Authentification & Amorçage
        Route(f"{prefix}/api/setup", setup_api, methods=["POST"]),
        Route(f"{prefix}/api/login", login_api, methods=["POST"]),
        Route(f"{prefix}/api/logout", logout_api, methods=["POST"]),
        Route(f"{prefix}/api/me", me_api, methods=["GET"]),
        # CRUD Utilisateurs
        Route(f"{prefix}/api/users", list_users_api, methods=["GET"]),
        Route(f"{prefix}/api/users", create_user_api, methods=["POST"]),
        Route(f"{prefix}/api/users/{{user_id:int}}/password", update_password_api, methods=["POST"]),
        Route(f"{prefix}/api/users/{{user_id:int}}/toggle-status", toggle_status_api, methods=["POST"]),
        Route(f"{prefix}/api/users/{{user_id:int}}", delete_user_api, methods=["DELETE"]),
        # Métriques & Outils
        Route(f"{prefix}/api/status", status_api, methods=["GET"]),
        Route(f"{prefix}/api/tools", tools_api, methods=["GET"]),
        Route(f"{prefix}/api/sync", sync_api, methods=["POST"]),
    ]
    return routes
