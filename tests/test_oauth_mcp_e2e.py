"""Test d'intégration End-to-End : OAuth 2.0 -> Token -> Exécution MCP -> Vérification ERP -> Révocation."""

import json
import time
import jwt
import pytest
import respx
from httpx import Response as HttpxResponse
from starlette.testclient import TestClient

from melodi_mcp_server.admin.db import AdminDatabase
from melodi_mcp_server.mcp_server import MelodiMcpServerApp
from melodi_mcp_server.settings import Settings

JWT_SECRET = "oauth-e2e-secret-key-at-least-32-chars-long"
MCP_API_KEY = "infra-mcp-key-secure"


@pytest.fixture
def e2e_settings(mock_settings):
    data = mock_settings.model_dump()
    data["mcp_transport"] = "http"
    data["jwt_secret"] = JWT_SECRET
    data["mcp_api_key"] = MCP_API_KEY
    return Settings(**data)


@pytest.mark.asyncio
@respx.mock
async def test_full_oauth_to_mcp_execution_and_revocation(e2e_settings, sample_catalog_payload):
    """Vérifie le cycle de vie complet :

    1. Découverte 401 sur /mcp.
    2. Saisie du JWT sur /oauth/authorize et échange sur /oauth/token.
    3. Exécution d'un outil MCP avec le token reçu -> vérification des headers ERP.
    4. Révocation du token -> rejet immédiat de l'accès MCP.
    """
    respx.get("http://test-melodi:8000/api/melodi_mcp/internal/tools").respond(
        status_code=200, json=sample_catalog_payload
    )
    respx.get("http://test-melodi:8000/api/melodi_mcp/internal/health").respond(
        status_code=200, json={"status": "healthy"}
    )

    captured_erp_requests = []

    def mock_erp_execute(request):
        captured_erp_requests.append(request)
        return HttpxResponse(200, json={"success": True, "balance": 12890.50})

    respx.post("http://test-melodi:8000/api/melodi_mcp/internal/tools/accounting_get_balance/execute").mock(
        side_effect=mock_erp_execute
    )

    admin_db = AdminDatabase(":memory:")
    app = MelodiMcpServerApp(settings=e2e_settings)
    http_app = app.create_http_app(admin_db=admin_db)

    with TestClient(http_app, base_url="http://127.0.0.1:8765") as client:
        # Étape 1 : Le client IA tente d'accéder à /mcp sans token et reçoit 401 avec orientation OAuth
        probe_resp = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        )
        assert probe_resp.status_code == 401
        assert 'authorization_uri="/oauth/authorize"' in probe_resp.headers.get("www-authenticate", "")

        # Étape 2 : L'utilisateur colle son jeton JWT généré dans MELODI ERP sur /oauth/authorize
        user_jwt = jwt.encode(
            {
                "token_id": "tok_e2e_user_42",
                "mcp_api_key": MCP_API_KEY,
                "user_id": "user_sophie_88",
                "exp": int(time.time()) + 3600,
            },
            JWT_SECRET,
            algorithm="HS256",
        )

        auth_resp = client.post(
            "/oauth/authorize",
            data={
                "client_id": "chatgpt_assistant",
                "redirect_uri": "https://chat.openai.com/aip/callback",
                "state": "oauth_state_123",
                "jwt_token": user_jwt,
            },
            follow_redirects=False,
        )
        assert auth_resp.status_code == 302
        location = auth_resp.headers["location"]
        code = location.split("code=")[1].split("&")[0]

        # Étape 3 : Le client IA échange le code contre le token Bearer
        token_resp = client.post(
            "/oauth/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "client_id": "chatgpt_assistant",
                "redirect_uri": "https://chat.openai.com/aip/callback",
            },
        )
        assert token_resp.status_code == 200
        token_payload = token_resp.json()
        assert token_payload["token_type"] == "Bearer"
        access_token = token_payload["access_token"]
        assert access_token == user_jwt

        # Étape 4 : Le client IA initialise la session MCP avec le token OAuth reçu
        session_headers = {"Authorization": f"Bearer {access_token}"}
        init_resp = client.post(
            "/mcp",
            headers=session_headers,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "chatgpt-client", "version": "1.0"},
                },
            },
        )
        assert init_resp.status_code == 200
        session_id = init_resp.headers.get("mcp-session-id")
        if session_id:
            session_headers["mcp-session-id"] = session_id

        # Notification initialized
        client.post(
            "/mcp",
            headers=session_headers,
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        )

        # Exécution de l'outil
        tool_call_resp = client.post(
            "/mcp",
            headers=session_headers,
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "accounting_get_balance",
                    "arguments": {"account_id": "acc_001"},
                },
            },
        )
        assert tool_call_resp.status_code == 200
        assert "12890.5" in tool_call_resp.text

        # Étape 5 : Vérification des en-têtes et du contexte reçus par MELODI ERP
        assert len(captured_erp_requests) == 1
        erp_req = captured_erp_requests[0]
        assert erp_req.headers["X-Melodi-User-ID"] == "user_sophie_88"
        assert erp_req.headers.get("X-User-MCP-Key") == "user_sophie_88"
        assert "X-Melodi-Org-ID" not in erp_req.headers
        erp_body = json.loads(erp_req.content.decode("utf-8"))
        assert erp_body["context"]["user_id"] == "user_sophie_88"

        # Étape 6 : Révocation du token dans la base (ex: via le dashboard admin ou l'ERP)
        admin_db.revoke_token("tok_e2e_user_42", reason="Session terminée par l'admin")

        # Étape 7 : Le prochain appel du client IA sur /mcp est immédiatement bloqué
        blocked_resp = client.post(
            "/mcp",
            headers=session_headers,
            json={
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "accounting_get_balance",
                    "arguments": {"account_id": "acc_001"},
                },
            },
        )
        assert blocked_resp.status_code == 401
        assert "révoqué" in blocked_resp.json()["error"]["message"]
