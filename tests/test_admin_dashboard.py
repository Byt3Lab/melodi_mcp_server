"""Tests pour les routes et fonctionnalités du Dashboard d'Administration."""

import pytest
import respx
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from melodi_mcp_server.admin.db import AdminDatabase
from melodi_mcp_server.mcp_server import MelodiMcpServerApp
from melodi_mcp_server.settings import Settings


@pytest.mark.asyncio
@respx.mock
async def test_23_admin_dashboard_rendering_and_apis(mock_settings, sample_catalog_payload):
    """Test 23: Initialisation admin, rendu HTML du dashboard et endpoints JSON status et tools."""
    respx.get("http://test-melodi:8000/api/melodi_mcp/internal/tools").respond(
        status_code=200,
        json=sample_catalog_payload,
    )
    respx.get("http://test-melodi:8000/api/melodi_mcp/internal/health").respond(
        status_code=200,
        json={"status": "healthy"},
    )

    app = MelodiMcpServerApp(settings=mock_settings)
    await app.startup()
    admin_db = AdminDatabase(":memory:")
    http_app = app.create_http_app(admin_db=admin_db)

    transport = ASGITransport(app=http_app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        # Avant initialisation, /admin affiche l'écran de configuration initiale
        uninit_resp = await client.get("/admin")
        assert uninit_resp.status_code == 200
        assert "Initialisation" in uninit_resp.text

        # Initialisation du premier compte administrateur avec le setup_key (ADMIN_API_KEY)
        setup_resp = await client.post(
            "/admin/api/setup",
            json={
                "setup_key": "admin",
                "username": "root_admin",
                "password": "strong_password_123",
            },
        )
        assert setup_resp.status_code == 200
        session_token = setup_resp.json()["session_token"]
        headers = {"Authorization": f"Bearer {session_token}"}

        # 1. Page HTML du Dashboard avec session authentifiée
        html_resp = await client.get("/admin", headers=headers)
        assert html_resp.status_code == 200
        assert "MELODI MCP Server" in html_resp.text
        assert "Catalogue des Outils Découverts" in html_resp.text

        # 2. Endpoint /admin/api/me retourne bien l'utilisateur courant
        me_resp = await client.get("/admin/api/me", headers=headers)
        assert me_resp.status_code == 200
        assert me_resp.json()["user"]["username"] == "root_admin"

        # 3. Endpoint de statut JSON
        status_resp = await client.get("/admin/api/status", headers=headers)
        assert status_resp.status_code == 200
        status_json = status_resp.json()
        assert "health" in status_json
        assert "metrics" in status_json
        assert status_json["health"]["catalog"]["tools_count"] == 2

        # 4. Endpoint du catalogue JSON
        tools_resp = await client.get("/admin/api/tools", headers=headers)
        assert tools_resp.status_code == 200
        tools_json = tools_resp.json()
        assert len(tools_json["tools"]) == 2
        assert tools_json["tools"][0]["name"] == "accounting_get_balance"

    await app.shutdown()


@pytest.mark.asyncio
@respx.mock
async def test_24_admin_dashboard_manual_sync(mock_settings, sample_catalog_payload):
    """Test 24: Déclenchement de la synchronisation manuelle via /admin/api/sync avec authentification."""
    respx.get("http://test-melodi:8000/api/melodi_mcp/internal/tools").respond(
        status_code=200,
        json=sample_catalog_payload,
    )
    respx.get("http://test-melodi:8000/api/melodi_mcp/internal/health").respond(
        status_code=200,
        json={"status": "healthy"},
    )

    app = MelodiMcpServerApp(settings=mock_settings)
    admin_db = AdminDatabase(":memory:")
    http_app = app.create_http_app(admin_db=admin_db)

    transport = ASGITransport(app=http_app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        # Création premier admin
        setup_resp = await client.post(
            "/admin/api/setup",
            json={
                "setup_key": "admin",
                "username": "sync_admin",
                "password": "strong_password_123",
            },
        )
        token = setup_resp.json()["session_token"]
        headers = {"Authorization": f"Bearer {token}"}

        # Synchronisation
        sync_resp = await client.post("/admin/api/sync", headers=headers)
        assert sync_resp.status_code == 200
        data = sync_resp.json()
        assert data["success"] is True
        assert data["tools_count"] == 2
        assert data["revision"] == "rev-test-1"

    await app.shutdown()


@pytest.mark.asyncio
async def test_admin_dashboard_api_key_protection():
    """Vérifie le cycle de sécurité : clé bootstrap ADMIN_API_KEY puis comptes SQLite obligatoires."""
    custom_settings = Settings(
        melodi_base_url="http://test-melodi:8000",
        admin_api_key=SecretStr("super_custom_secret"),
        admin_db_path=":memory:",
    )
    app = MelodiMcpServerApp(settings=custom_settings)
    admin_db = AdminDatabase(":memory:")
    http_app = app.create_http_app(admin_db=admin_db)

    transport = ASGITransport(app=http_app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        # 1. Avant initialisation : écran de setup
        res_setup_page = await client.get("/admin")
        assert res_setup_page.status_code == 200
        assert "Initialisation" in res_setup_page.text

        # 2. Tentative avec mauvaise clé bootstrap -> 401
        res_bad_setup = await client.post(
            "/admin/api/setup",
            json={"setup_key": "wrong_key", "username": "admin", "password": "password123"},
        )
        assert res_bad_setup.status_code == 401

        # 3. Initialisation réussie avec la bonne clé bootstrap
        res_ok_setup = await client.post(
            "/admin/api/setup",
            json={"setup_key": "super_custom_secret", "username": "boss", "password": "securepassword"},
        )
        assert res_ok_setup.status_code == 200
        session_token = res_ok_setup.json()["session_token"]

        # 4. Une fois initialisé, toute tentative de re-setup est bloquée
        res_re_setup = await client.post(
            "/admin/api/setup",
            json={"setup_key": "super_custom_secret", "username": "hacker", "password": "hack"},
        )
        assert res_re_setup.status_code == 400

        # Effacer les cookies pour tester les requêtes non authentifiées
        client.cookies.clear()

        # 5. La clé bootstrap ne permet plus d'accéder directement aux API
        res_key_bypass = await client.get("/admin/api/status?api_key=super_custom_secret")
        assert res_key_bypass.status_code == 401

        res_bearer_bypass = await client.get(
            "/admin/api/status",
            headers={"Authorization": "Bearer super_custom_secret"},
        )
        assert res_bearer_bypass.status_code == 401

        # 6. L'accès sans session retourne 401 pour l'API et affiche le formulaire de login pour la page HTML
        res_api_unauth = await client.get("/admin/api/status")
        assert res_api_unauth.status_code == 401

        res_login_page = await client.get("/admin")
        assert res_login_page.status_code == 200
        assert "Connexion" in res_login_page.text
        assert "Se connecter" in res_login_page.text

        # 7. Authentification par identifiant et mot de passe utilisateur
        login_res = await client.post(
            "/admin/api/login",
            json={"username": "boss", "password": "securepassword"},
        )
        assert login_res.status_code == 200
        new_token = login_res.json()["session_token"]

        # 8. Accès autorisé avec la session
        res_auth = await client.get("/admin/api/status", headers={"Authorization": f"Bearer {new_token}"})
        assert res_auth.status_code == 200

    await app.shutdown()
