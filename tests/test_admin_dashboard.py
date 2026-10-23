"""Tests pour les routes et fonctionnalités du Dashboard d'Administration."""

import pytest
import respx
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from melodi_mcp_server.mcp_server import MelodiMcpServerApp
from melodi_mcp_server.settings import Settings


@pytest.mark.asyncio
@respx.mock
async def test_23_admin_dashboard_rendering_and_apis(mock_settings, sample_catalog_payload):
    """Test 23: Rendu HTML du dashboard et fonctionnement des endpoints JSON status et tools."""
    respx.get("http://test-melodi:8000/internal/mcp/tools").respond(
        status_code=200,
        json=sample_catalog_payload,
    )
    respx.get("http://test-melodi:8000/internal/mcp/health").respond(
        status_code=200,
        json={"status": "healthy"},
    )

    app = MelodiMcpServerApp(settings=mock_settings)
    await app.startup()
    http_app = app.create_http_app()

    transport = ASGITransport(app=http_app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        # 1. Page HTML du Dashboard
        html_resp = await client.get("/admin")
        assert html_resp.status_code == 200
        assert "MELODI MCP Server" in html_resp.text
        assert "Catalogue des Outils Découverts" in html_resp.text

        # 2. Endpoint de statut JSON
        status_resp = await client.get("/admin/api/status")
        assert status_resp.status_code == 200
        status_json = status_resp.json()
        assert "health" in status_json
        assert "metrics" in status_json
        assert status_json["health"]["catalog"]["tools_count"] == 2

        # 3. Endpoint du catalogue JSON
        tools_resp = await client.get("/admin/api/tools")
        assert tools_resp.status_code == 200
        tools_json = tools_resp.json()
        assert len(tools_json["tools"]) == 2
        assert tools_json["tools"][0]["name"] == "accounting_get_balance"

    await app.shutdown()


@pytest.mark.asyncio
@respx.mock
async def test_24_admin_dashboard_manual_sync(mock_settings, sample_catalog_payload):
    """Test 24: Déclenchement de la synchronisation manuelle via /admin/api/sync."""
    respx.get("http://test-melodi:8000/internal/mcp/tools").respond(
        status_code=200,
        json=sample_catalog_payload,
    )
    respx.get("http://test-melodi:8000/internal/mcp/health").respond(
        status_code=200,
        json={"status": "healthy"},
    )

    app = MelodiMcpServerApp(settings=mock_settings)
    http_app = app.create_http_app()

    transport = ASGITransport(app=http_app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        sync_resp = await client.post("/admin/api/sync")
        assert sync_resp.status_code == 200
        data = sync_resp.json()
        assert data["success"] is True
        assert data["tools_count"] == 2
        assert data["revision"] == "rev-test-1"

    await app.shutdown()


@pytest.mark.asyncio
async def test_admin_dashboard_api_key_protection():
    """Vérifie la protection par clé API d'administration si ADMIN_API_KEY est configurée."""
    protected_settings = Settings(
        melodi_base_url="http://test-melodi:8000",
        admin_api_key=SecretStr("super_admin_pass"),
    )
    app = MelodiMcpServerApp(settings=protected_settings)
    http_app = app.create_http_app()

    transport = ASGITransport(app=http_app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        # Sans clé -> 401
        res = await client.get("/admin")
        assert res.status_code == 401

        # Avec Bearer token valide -> 200
        res_auth = await client.get("/admin", headers={"Authorization": "Bearer super_admin_pass"})
        assert res_auth.status_code == 200

        # Avec query param ?api_key=... -> 200
        res_query = await client.get("/admin?api_key=super_admin_pass")
        assert res_query.status_code == 200

    await app.shutdown()
