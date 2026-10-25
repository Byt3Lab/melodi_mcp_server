"""Tests de la sécurité et de l'authentification par clé API sur les endpoints MCP."""

import pytest
import respx
from pydantic import SecretStr
from starlette.testclient import TestClient

from melodi_mcp_server.mcp_server import MelodiMcpServerApp
from melodi_mcp_server.settings import Settings


@pytest.fixture
def auth_settings(mock_settings):
    """Configuration avec une clé API MCP définie."""
    data = mock_settings.model_dump()
    data["mcp_transport"] = "http"
    data["mcp_api_key"] = "test-secret-token-xyz"
    return Settings(**data)


@pytest.mark.asyncio
@respx.mock
async def test_mcp_auth_disabled_by_default(mock_settings, sample_catalog_payload):
    """Sans MCP_API_KEY, les requêtes MCP doivent être acceptées sans token."""
    respx.get("http://test-melodi:8000/api/melodi_mcp/internal/tools").respond(
        status_code=200, json=sample_catalog_payload
    )
    app = MelodiMcpServerApp(settings=mock_settings)
    http_app = app.create_http_app()

    init_payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "test-client", "version": "1.0"},
        },
    }

    with TestClient(http_app, base_url="http://127.0.0.1:8765") as client:
        # Initialisation sur /mcp sans token -> succès (200)
        r = client.post("/mcp", json=init_payload)
        assert r.status_code == 200

        # Initialisation sur / sans token -> succès (200)
        r_root = client.post("/", json=init_payload)
        assert r_root.status_code == 200


@pytest.mark.asyncio
@respx.mock
async def test_mcp_auth_enforced_when_configured(auth_settings, sample_catalog_payload):
    """Avec MCP_API_KEY configurée, les requêtes sans token ou avec mauvais token sont rejetées (401)."""
    respx.get("http://test-melodi:8000/api/melodi_mcp/internal/tools").respond(
        status_code=200, json=sample_catalog_payload
    )
    app = MelodiMcpServerApp(settings=auth_settings)
    http_app = app.create_http_app()

    init_payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "test-client", "version": "1.0"},
        },
    }

    with TestClient(http_app, base_url="http://127.0.0.1:8765") as client:
        # 1. /health reste accessible publiquement
        r_health = client.get("/health")
        assert r_health.status_code == 200
        health_json = r_health.json()
        assert health_json["server"]["auth_required"] is True

        # 2. Requête sans authentification sur /mcp -> 401
        r_unauth = client.post("/mcp", json=init_payload)
        assert r_unauth.status_code == 401
        assert "WWW-Authenticate" in r_unauth.headers
        assert r_unauth.json()["error"]["code"] == -32000

        # 3. Requête sans authentification sur / -> 401
        r_root_unauth = client.post("/", json=init_payload)
        assert r_root_unauth.status_code == 401

        # 4. Requête avec un autre en-tête (ex: Authorization Bearer) -> 401 (rejeté car seul X-MCP-API-KEY est accepté)
        r_bearer = client.post(
            "/mcp",
            headers={"Authorization": "Bearer test-secret-token-xyz"},
            json=init_payload,
        )
        assert r_bearer.status_code == 401
        assert "X-MCP-API-KEY" in r_bearer.headers.get("WWW-Authenticate", "")

        # 5. Requête avec X-MCP-API-KEY invalide -> 401
        r_bad_key = client.post(
            "/mcp",
            headers={"X-MCP-API-KEY": "mauvaise-cle"},
            json=init_payload,
        )
        assert r_bad_key.status_code == 401

        # 6. Requête avec X-MCP-API-KEY valide sur /mcp -> 200
        r_ok_mcp = client.post(
            "/mcp",
            headers={"X-MCP-API-KEY": "test-secret-token-xyz"},
            json=init_payload,
        )
        assert r_ok_mcp.status_code == 200

        # 7. Requête avec X-MCP-API-KEY valide sur / -> 200
        r_ok_root = client.post(
            "/",
            headers={"X-MCP-API-KEY": "test-secret-token-xyz"},
            json=init_payload,
        )
        assert r_ok_root.status_code == 200

        # 8. Requête avec X-MCP-API-KEY valide sur /sse -> 200
        r_ok_sse = client.post(
            "/sse",
            headers={"X-MCP-API-KEY": "test-secret-token-xyz"},
            json=init_payload,
        )
        assert r_ok_sse.status_code == 200
