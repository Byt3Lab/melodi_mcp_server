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

        # 2. Requête sans authentification sur /mcp -> 401 (clé infra absente)
        r_unauth = client.post("/mcp", json=init_payload)
        assert r_unauth.status_code == 401
        assert "WWW-Authenticate" in r_unauth.headers
        assert r_unauth.json()["error"]["code"] == -32000
        assert "X-MCP-API-KEY" in r_unauth.json()["error"]["message"]

        # 3. Requête sans authentification sur / -> 401
        r_root_unauth = client.post("/", json=init_payload)
        assert r_root_unauth.status_code == 401

        # 4. Requête avec Bearer user mais sans X-MCP-API-KEY -> 401
        r_bearer_only = client.post(
            "/mcp",
            headers={"Authorization": "Bearer mcp_live_user_token_123"},
            json=init_payload,
        )
        assert r_bearer_only.status_code == 401
        assert "X-MCP-API-KEY" in r_bearer_only.json()["error"]["message"]

        # 5. Requête avec X-MCP-API-KEY invalide -> 401
        r_bad_key = client.post(
            "/mcp",
            headers={
                "X-MCP-API-KEY": "mauvaise-cle",
                "Authorization": "Bearer mcp_live_user_token_123",
            },
            json=init_payload,
        )
        assert r_bad_key.status_code == 401

        # 6. Requête avec X-MCP-API-KEY valide mais sans token utilisateur -> 401
        r_no_user_token = client.post(
            "/mcp",
            headers={"X-MCP-API-KEY": "test-secret-token-xyz"},
            json=init_payload,
        )
        assert r_no_user_token.status_code == 401
        assert "Authorization" in r_no_user_token.json()["error"]["message"]

        # 7. Requête avec double authentification valide sur /mcp -> 200
        r_ok_mcp = client.post(
            "/mcp",
            headers={
                "X-MCP-API-KEY": "test-secret-token-xyz",
                "Authorization": "Bearer mcp_live_user_token_123",
            },
            json=init_payload,
        )
        assert r_ok_mcp.status_code == 200

        # 8. Requête avec double authentification valide sur / -> 200
        r_ok_root = client.post(
            "/",
            headers={
                "X-MCP-API-KEY": "test-secret-token-xyz",
                "Authorization": "Bearer mcp_live_user_token_123",
            },
            json=init_payload,
        )
        assert r_ok_root.status_code == 200

        # 9. Requête avec double authentification valide sur /sse -> 200
        r_ok_sse = client.post(
            "/sse",
            headers={
                "X-MCP-API-KEY": "test-secret-token-xyz",
                "Authorization": "Bearer mcp_live_user_token_123",
            },
            json=init_payload,
        )
        assert r_ok_sse.status_code == 200

        # 10. Requête avec paramètre d'URL invalide ?mcp_api_key=mauvais -> 401
        r_bad_query = client.post(
            "/mcp?mcp_api_key=mauvaise-cle",
            headers={"Authorization": "Bearer mcp_live_user_token_123"},
            json=init_payload,
        )
        assert r_bad_query.status_code == 401

        # 11. Requête avec paramètre d'URL valide ?mcp_api_key=... et Bearer sur /mcp -> 200
        r_ok_query_mcp = client.post(
            "/mcp?mcp_api_key=test-secret-token-xyz",
            headers={"Authorization": "Bearer mcp_live_user_token_123"},
            json=init_payload,
        )
        assert r_ok_query_mcp.status_code == 200

        # 12. Requête avec en-têtes alternatifs (X-API-KEY + X-User-MCP-Key) sur / -> 200
        r_ok_alt_headers = client.post(
            "/",
            headers={
                "X-API-KEY": "test-secret-token-xyz",
                "X-User-MCP-Key": "mcp_live_user_token_123",
            },
            json=init_payload,
        )
        assert r_ok_alt_headers.status_code == 200
