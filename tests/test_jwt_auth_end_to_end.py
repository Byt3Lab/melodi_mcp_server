"""Tests d'intégration de bout en bout pour l'authentification unifiée JWT."""

import json
import time
import jwt
import pytest
import respx
from httpx import Response as HttpxResponse
from starlette.testclient import TestClient

from melodi_mcp_server.mcp_server import MelodiMcpServerApp
from melodi_mcp_server.settings import Settings

JWT_SECRET = "very-secure-jwt-secret-key-32-characters-min"
MCP_API_KEY = "infra-mcp-key-xyz"


@pytest.fixture
def jwt_app_settings(mock_settings):
    data = mock_settings.model_dump()
    data["mcp_transport"] = "http"
    data["jwt_secret"] = JWT_SECRET
    data["mcp_api_key"] = MCP_API_KEY
    return Settings(**data)


@pytest.mark.asyncio
@respx.mock
async def test_jwt_auth_tools_call_end_to_end(jwt_app_settings, sample_catalog_payload):
    """Vérifie le flux complet : appel tools/call avec JWT -> validation -> injection vers MELODI ERP."""
    respx.get("http://test-melodi:8000/api/melodi_mcp/internal/tools").respond(
        status_code=200,
        json=sample_catalog_payload,
    )
    respx.get("http://test-melodi:8000/api/melodi_mcp/internal/health").respond(
        status_code=200,
        json={"status": "healthy"},
    )

    captured_requests = []

    def mock_execute_callback(request):
        captured_requests.append(request)
        return HttpxResponse(200, json={"success": True, "balance": 4500.75})

    respx.post("http://test-melodi:8000/api/melodi_mcp/internal/tools/accounting_get_balance/execute").mock(
        side_effect=mock_execute_callback
    )

    app = MelodiMcpServerApp(settings=jwt_app_settings)
    http_app = app.create_http_app()

    jwt_token = jwt.encode(
        {
            "token_id": "tok_test_e2e",
            "mcp_api_key": MCP_API_KEY,
            "user_id": "usr-12",
            "exp": int(time.time()) + 3600,
        },
        JWT_SECRET,
        algorithm="HS256",
    )

    with TestClient(http_app, base_url="http://127.0.0.1:8765") as client:
        # 1. Initialisation MCP
        init_resp = client.post(
            "/mcp",
            headers={"Authorization": f"Bearer {jwt_token}"},
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "cursor-client", "version": "1.0"},
                },
            },
        )
        assert init_resp.status_code == 200
        session_id = init_resp.headers.get("mcp-session-id")
        session_headers = {"Authorization": f"Bearer {jwt_token}"}
        if session_id:
            session_headers["mcp-session-id"] = session_id

        # Notification initialized
        client.post(
            "/mcp",
            headers=session_headers,
            json={
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
                "params": {},
            },
        )

        # 2. Appel d'un outil tools/call
        call_resp = client.post(
            "/mcp",
            headers=session_headers,
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "accounting_get_balance",
                    "arguments": {"account_id": "ACC-999"},
                },
            },
        )
        assert call_resp.status_code == 200
        # Streamable HTTP peut renvoyer du SSE ou du JSON
        text = call_resp.text
        assert "4500.75" in text

        # 3. Vérification de la transmission vers MELODI ERP
        assert len(captured_requests) == 1
        req = captured_requests[0]
        assert req.headers.get("X-Melodi-User-ID") == "usr-12"
        assert "X-Melodi-Org-ID" not in req.headers
