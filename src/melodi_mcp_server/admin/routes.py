"""Routes web et API JSON pour la console d'administration avec authentification SQLite."""

from __future__ import annotations

import hmac
import json
import logging
from typing import Any, Optional
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Route

from ..health import HealthChecker
from ..metrics import get_metrics
from ..settings import Settings, normalize_url_path
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

    # --- APIs Configuration Système ---

    async def get_config_api(request: Request) -> JSONResponse:
        """Retourne la configuration actuelle de connexion à MELODI ERP."""
        current_user = _get_current_user(request)
        if not current_user:
            return JSONResponse({"error": "Unauthorized"}, status_code=401)

        has_key = bool(settings.melodi_api_key and settings.melodi_api_key.get_secret_value())
        key_preview = ""
        if has_key:
            raw_k = settings.melodi_api_key.get_secret_value()
            key_preview = raw_k[:4] + "••••" + raw_k[-4:] if len(raw_k) > 8 else "••••••••"

        has_jwt_secret = bool(settings.jwt_secret and settings.jwt_secret.get_secret_value())
        jwt_secret_preview = ""
        if has_jwt_secret:
            raw_s = settings.jwt_secret.get_secret_value()
            jwt_secret_preview = raw_s[:4] + "••••" + raw_s[-4:] if len(raw_s) > 8 else "••••••••"

        has_mcp_api_key = bool(settings.mcp_api_key and settings.mcp_api_key.get_secret_value())
        mcp_api_key_preview = ""
        if has_mcp_api_key:
            raw_m = settings.mcp_api_key.get_secret_value()
            mcp_api_key_preview = raw_m[:4] + "••••" + raw_m[-4:] if len(raw_m) > 8 else "••••••••"

        from ..settings import mask_connection_url

        has_db_url = bool(settings.database_url and settings.database_url.get_secret_value())
        db_url_preview = mask_connection_url(settings.database_url.get_secret_value()) if has_db_url else ""

        has_redis = bool(settings.redis_url and settings.redis_url.get_secret_value())
        redis_url_preview = mask_connection_url(settings.redis_url.get_secret_value()) if has_redis else ""

        is_running_cluster = getattr(database, "is_cluster", False)

        return JSONResponse({
            "success": True,
            "melodi_base_url": settings.melodi_base_url,
            "melodi_api_prefix": settings.melodi_api_prefix,
            "has_melodi_api_key": has_key,
            "melodi_api_key_preview": key_preview,
            "has_jwt_secret": has_jwt_secret,
            "jwt_secret_preview": jwt_secret_preview,
            "has_mcp_api_key": has_mcp_api_key,
            "mcp_api_key_preview": mcp_api_key_preview,
            "catalog_sync_interval_seconds": settings.catalog_sync_interval_seconds,
            "user_role": current_user.get("role"),
            "cluster_mode": getattr(settings, "cluster_mode", False),
            "is_running_cluster": is_running_cluster,
            "has_database_url": has_db_url,
            "database_url_preview": db_url_preview,
            "has_redis_url": has_redis,
            "redis_url_preview": redis_url_preview,
            "mcp_stateless_http": getattr(settings, "mcp_stateless_http", False),
        })

    async def test_config_api(request: Request) -> JSONResponse:
        """Teste la joignabilité d'une URL et clé MELODI ERP sans modifier la configuration active."""
        current_user = _get_current_user(request)
        if not current_user:
            return JSONResponse({"error": "Unauthorized"}, status_code=401)
        if current_user.get("role") != "admin":
            return JSONResponse({"error": "Forbidden"}, status_code=403)

        body = await _parse_body(request)
        raw_url = (body.get("melodi_base_url") or "").strip().rstrip("/")
        if not raw_url.startswith(("http://", "https://")):
            return JSONResponse(
                {"success": False, "error": "L'URL doit débuter par 'http://' ou 'https://'"},
                status_code=400,
            )

        test_api_key = (body.get("melodi_api_key") or "").strip()
        if not test_api_key and settings.melodi_api_key:
            test_api_key = settings.melodi_api_key.get_secret_value()

        test_prefix = body.get("melodi_api_prefix")
        if test_prefix is not None:
            clean_prefix = normalize_url_path(str(test_prefix))
        else:
            clean_prefix = settings.melodi_api_prefix

        import time
        import httpx

        test_health_url = f"{raw_url}{clean_prefix}/health"
        start_t = time.time()
        try:
            async with httpx.AsyncClient(timeout=5.0, verify=settings.melodi_verify_ssl) as test_client:
                headers = {}
                if test_api_key:
                    headers["X-Internal-MCP-Key"] = test_api_key
                    headers["Authorization"] = f"Bearer {test_api_key}"
                resp = await test_client.get(test_health_url, headers=headers)
                latency_ms = round((time.time() - start_t) * 1000, 1)

                if resp.status_code == 200:
                    return JSONResponse({
                        "success": True,
                        "status_code": resp.status_code,
                        "latency_ms": latency_ms,
                        "message": f"Connexion réussie vers {raw_url}{clean_prefix} (HTTP 200, {latency_ms}ms)",
                    })
                else:
                    return JSONResponse({
                        "success": False,
                        "status_code": resp.status_code,
                        "latency_ms": latency_ms,
                        "error": f"Le serveur a répondu avec le statut HTTP {resp.status_code}",
                    }, status_code=400)
        except Exception as e:
            latency_ms = round((time.time() - start_t) * 1000, 1)
            return JSONResponse({
                "success": False,
                "latency_ms": latency_ms,
                "error": f"Impossible de joindre {raw_url}{clean_prefix} : {e}",
            }, status_code=400)

    async def test_cluster_config_api(request: Request) -> JSONResponse:
        """Teste la connectivité PostgreSQL et Redis pour la scalabilité cluster sans modifier la configuration active."""
        current_user = _get_current_user(request)
        if not current_user:
            return JSONResponse({"error": "Unauthorized"}, status_code=401)
        if current_user.get("role") != "admin":
            return JSONResponse({"error": "Forbidden"}, status_code=403)

        body = await _parse_body(request)
        test_db_url = (body.get("database_url") or "").strip()
        if not test_db_url and settings.database_url:
            test_db_url = settings.database_url.get_secret_value()

        test_redis_url = (body.get("redis_url") or "").strip()
        if not test_redis_url and settings.redis_url:
            test_redis_url = settings.redis_url.get_secret_value()

        from ..storage.tester import test_cluster_connections
        test_result = test_cluster_connections(test_db_url, test_redis_url)
        status_code = 200 if test_result["success"] else 400
        return JSONResponse(test_result, status_code=status_code)

    async def update_config_api(request: Request) -> JSONResponse:
        """Met à jour l'URL, le préfixe, la clé API MELODI, le secret JWT et/ou la clé MCP à chaud et persiste dans SQLite."""
        current_user = _get_current_user(request)
        if not current_user:
            return JSONResponse({"error": "Unauthorized"}, status_code=401)
        if current_user.get("role") != "admin":
            return JSONResponse({"error": "Forbidden"}, status_code=403)

        body = await _parse_body(request)
        raw_url = (body.get("melodi_base_url") or "").strip().rstrip("/")
        new_key = (body.get("melodi_api_key") or "").strip()
        raw_prefix = body.get("melodi_api_prefix")
        new_jwt_secret = body.get("jwt_secret")
        new_mcp_api_key = body.get("mcp_api_key")

        if raw_url and not raw_url.startswith(("http://", "https://")):
            return JSONResponse(
                {"success": False, "error": "L'URL doit débuter par 'http://' ou 'https://'"},
                status_code=400,
            )

        updated_items = []
        try:
            # 1. Mise à jour de l'URL si fournie
            if raw_url:
                registry.client.update_base_url(raw_url)
                database.set_config("melodi_base_url", raw_url)
                updated_items.append("URL")

            # 2. Mise à jour de la clé API MELODI si fournie
            if new_key:
                registry.client.update_api_key(new_key)
                database.set_config("melodi_api_key", new_key)
                updated_items.append("Clé API MELODI")

            # 3. Mise à jour du préfixe API MCP si fourni
            if raw_prefix is not None:
                new_prefix = registry.client.update_api_prefix(str(raw_prefix))
                database.set_config("melodi_api_prefix", new_prefix)
                updated_items.append("Préfixe API")

            # 4. Mise à jour de la clé secrète JWT si fournie
            if new_jwt_secret is not None:
                clean_jwt = str(new_jwt_secret).strip()
                if clean_jwt:
                    from pydantic import SecretStr
                    settings.jwt_secret = SecretStr(clean_jwt)
                    database.set_config("jwt_secret", clean_jwt)
                    updated_items.append("Clé secrète JWT")

            # 5. Mise à jour de la clé MCP API si fournie
            if new_mcp_api_key is not None:
                clean_mcp = str(new_mcp_api_key).strip()
                if clean_mcp:
                    from pydantic import SecretStr
                    settings.mcp_api_key = SecretStr(clean_mcp)
                    database.set_config("mcp_api_key", clean_mcp)
                    updated_items.append("Clé MCP API")

            # 6. Mise à jour des paramètres de scalabilité (Cluster & Haute Disponibilité)
            requires_restart = False
            raw_cluster_mode = body.get("cluster_mode")
            if raw_cluster_mode is not None:
                new_mode = str(raw_cluster_mode).lower() in ("true", "1", "yes")
                settings.cluster_mode = new_mode
                database.set_config("cluster_mode", "true" if new_mode else "false")
                updated_items.append(f"Mode Cluster ({'Activé' if new_mode else 'Désactivé'})")
                requires_restart = True

            raw_db_url = body.get("database_url")
            if raw_db_url is not None:
                clean_db = str(raw_db_url).strip()
                if clean_db:
                    from pydantic import SecretStr
                    settings.database_url = SecretStr(clean_db)
                    database.set_config("database_url", clean_db)
                    updated_items.append("URL Base de données PostgreSQL")
                    requires_restart = True
                elif clean_db == "":
                    settings.database_url = None
                    database.set_config("database_url", "")
                    updated_items.append("URL Base de données réinitialisée")
                    requires_restart = True

            raw_redis_url = body.get("redis_url")
            if raw_redis_url is not None:
                clean_redis = str(raw_redis_url).strip()
                if clean_redis:
                    from pydantic import SecretStr
                    settings.redis_url = SecretStr(clean_redis)
                    database.set_config("redis_url", clean_redis)
                    updated_items.append("URL Redis")
                    requires_restart = True
                elif clean_redis == "":
                    settings.redis_url = None
                    database.set_config("redis_url", "")
                    updated_items.append("URL Redis réinitialisée")
                    requires_restart = True

            raw_stateless = body.get("mcp_stateless_http")
            if raw_stateless is not None:
                new_stateless = str(raw_stateless).lower() in ("true", "1", "yes")
                settings.mcp_stateless_http = new_stateless
                database.set_config("mcp_stateless_http", "true" if new_stateless else "false")
                updated_items.append(f"Mode Stateless HTTP ({'Activé' if new_stateless else 'Désactivé'})")
                requires_restart = True

            if not updated_items:
                return JSONResponse(
                    {"success": False, "error": "Aucune modification demandée."},
                    status_code=400,
                )

            # 7. Synchronisation immédiate avec l'instance MELODI (si paramètres ERP modifiés)
            erp_modified = any(k in updated_items for k in ("URL", "Clé API MELODI", "Préfixe API"))
            count = len(registry.list_mcp_tools())
            if erp_modified:
                try:
                    count = await registry.sync_now(force=True)
                except Exception as sync_err:
                    logger.warning("Synchronisation immédiate post-configuration échouée : %s", sync_err)

            health_data = await health_checker.check()

            if len(updated_items) == 1:
                items_str = updated_items[0]
            elif len(updated_items) == 2:
                items_str = f"{updated_items[0]} et {updated_items[1]}"
            else:
                items_str = f"{', '.join(updated_items[:-1])} et {updated_items[-1]}"

            restart_msg = (
                " ⚠️ Les modifications de scalabilité ont été enregistrées. "
                "Veuillez redémarrer melodi_mcp_server pour appliquer la bascule."
                if requires_restart
                else ""
            )

            return JSONResponse({
                "success": True,
                "melodi_base_url": settings.melodi_base_url,
                "melodi_api_prefix": settings.melodi_api_prefix,
                "has_melodi_api_key": bool(settings.melodi_api_key and settings.melodi_api_key.get_secret_value()),
                "cluster_mode": settings.cluster_mode,
                "mcp_stateless_http": settings.mcp_stateless_http,
                "requires_restart": requires_restart,
                "tools_count": count,
                "health": health_data,
                "message": f"Paramètres ({items_str}) mis à jour avec succès.{restart_msg}",
            })
        except Exception as e:
            logger.error("Erreur lors de la mise à jour des paramètres MELODI : %s", e)
            return JSONResponse({"success": False, "error": str(e)}, status_code=400)

    # --- APIs Gestion des Jetons & Révocation ---

    def _is_service_or_admin(request: Request) -> bool:
        """Vérifie si l'appelant est soit un administrateur connecté, soit MELODI ERP authentifié par clé."""
        if _get_current_user(request):
            return True
        auth_hdr = request.headers.get("Authorization", "").strip()
        internal_key = request.headers.get("X-Internal-MCP-Key", "").strip()
        expected_key = settings.melodi_api_key.get_secret_value() if settings.melodi_api_key else None
        if expected_key:
            if auth_hdr.lower().startswith("bearer "):
                token = auth_hdr[7:].strip()
                if hmac.compare_digest(token, expected_key):
                    return True
            if internal_key and hmac.compare_digest(internal_key, expected_key):
                return True
        return False

    async def revoke_token_api(request: Request) -> JSONResponse:
        """Enregistre la révocation d'un jeton JWT (appelé par MELODI ERP ou depuis le dashboard)."""
        if not _is_service_or_admin(request):
            return JSONResponse({"error": "Unauthorized"}, status_code=401)

        body = await _parse_body(request)
        token_id = (body.get("token_id") or body.get("jti") or "").strip()
        if not token_id:
            return JSONResponse(
                {"success": False, "error": "Le paramètre 'token_id' est requis pour révoquer un jeton."},
                status_code=400,
            )

        user_id = body.get("user_id")
        expires_at = body.get("expires_at")
        reason = body.get("reason") or "Révoqué par MELODI ERP / Admin"

        try:
            database.revoke_token(
                token_id=token_id,
                user_id=str(user_id) if user_id is not None else None,
                expires_at=str(expires_at) if expires_at is not None else None,
                reason=str(reason),
            )
            return JSONResponse({
                "success": True,
                "token_id": token_id,
                "message": f"Le jeton '{token_id}' a été révoqué avec succès.",
            })
        except Exception as e:
            logger.error("Erreur lors de la révocation du jeton '%s' : %s", token_id, e)
            return JSONResponse({"success": False, "error": str(e)}, status_code=500)

    async def list_revoked_tokens_api(request: Request) -> JSONResponse:
        """Retourne la liste des jetons révoqués."""
        current_user = _get_current_user(request)
        if not current_user:
            return JSONResponse({"error": "Unauthorized"}, status_code=401)

        tokens = database.list_revoked_tokens(limit=100)
        return JSONResponse({"success": True, "revoked_tokens": tokens})

    async def delete_revoked_token_api(request: Request) -> JSONResponse:
        """Supprime un jeton de la liste des révocations (déblocage manuel)."""
        current_user = _get_current_user(request)
        if not current_user:
            return JSONResponse({"error": "Unauthorized"}, status_code=401)

        token_id = request.path_params.get("token_id", "").strip()
        if not token_id:
            return JSONResponse({"success": False, "error": "Token ID manquant."}, status_code=400)

        database.delete_revoked_token(token_id)
        return JSONResponse({"success": True, "message": f"Révocation du jeton '{token_id}' levée avec succès."})

    async def cleanup_tokens_api(request: Request) -> JSONResponse:
        """Déclenche la purge des jetons révoqués expirés (> 72h)."""
        current_user = _get_current_user(request)
        if not current_user:
            return JSONResponse({"error": "Unauthorized"}, status_code=401)

        deleted = database.cleanup_expired_revoked_tokens(max_age_hours=72)
        return JSONResponse({
            "success": True,
            "deleted_count": deleted,
            "message": f"Nettoyage effectué : {deleted} jeton(s) expiré(s) (> 72h) supprimé(s).",
        })

    routes = [
        # Pages HTML
        Route(f"{prefix}", dashboard_view, methods=["GET"]),
        Route(f"{prefix}/", dashboard_view, methods=["GET"]),
        # Authentification & Amorçage
        Route(f"{prefix}/api/setup", setup_api, methods=["POST"]),
        Route(f"{prefix}/api/login", login_api, methods=["POST"]),
        Route(f"{prefix}/api/logout", logout_api, methods=["POST"]),
        Route(f"{prefix}/api/me", me_api, methods=["GET"]),
        # Configuration Système
        Route(f"{prefix}/api/config", get_config_api, methods=["GET"]),
        Route(f"{prefix}/api/config", update_config_api, methods=["POST"]),
        Route(f"{prefix}/api/config/test", test_config_api, methods=["POST"]),
        Route(f"{prefix}/api/config/test-cluster", test_cluster_config_api, methods=["POST"]),
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
        # Gestion des Jetons & Révocation
        Route(f"{prefix}/api/tokens/revoke", revoke_token_api, methods=["POST"]),
        Route("/api/mcp/tokens/revoke", revoke_token_api, methods=["POST"]),
        Route(f"{prefix}/api/tokens/revoked", list_revoked_tokens_api, methods=["GET"]),
        Route(f"{prefix}/api/tokens/revoked/{{token_id:str}}", delete_revoked_token_api, methods=["DELETE"]),
        Route(f"{prefix}/api/tokens/cleanup", cleanup_tokens_api, methods=["POST"]),
    ]
    return routes
