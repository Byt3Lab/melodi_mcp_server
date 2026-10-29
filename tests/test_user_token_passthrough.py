"""Tests d'intégration pour le relais transparent des jetons utilisateurs (PAT) vers MELODI ERP."""

import json
import pytest
import respx
from httpx import ASGITransport, AsyncClient, Response as HttpxResponse
from pydantic import SecretStr
from starlette.responses import JSONResponse

from melodi_mcp_server.mcp_server import MelodiMcpServerApp
from melodi_mcp_server.security import SecurityContext, current_user_token
from melodi_mcp_server.settings import Settings
from melodi_mcp_server.tool_dispatcher import ToolDispatcher


@pytest.mark.asyncio
async def test_current_user_token_contextvar():
    """Vérifie le cycle de vie de la variable de contexte asynchrone current_user_token."""
    assert current_user_token.get() is None
    token = current_user_token.set("mcp_live_alice_123")
    try:
        assert current_user_token.get() == "mcp_live_alice_123"
    finally:
        current_user_token.reset(token)
    assert current_user_token.get() is None


@pytest.mark.asyncio
@respx.mock
async def test_tool_dispatcher_passes_user_token_to_melodi(mock_settings, sample_catalog_payload):
    """Vérifie que ToolDispatcher injecte le jeton utilisateur dans la requête vers MELODI."""
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
        return HttpxResponse(200, json={"success": True, "balance": 1500.0})

    respx.post("http://test-melodi:8000/api/melodi_mcp/internal/tools/accounting_get_balance/execute").mock(
        side_effect=mock_execute_callback
    )

    app = MelodiMcpServerApp(settings=mock_settings)
    await app.startup()

    try:
        # Appel d'un outil avec un SecurityContext portant le user_key
        sec_context = SecurityContext(
            user_key="mcp_live_user_token_456",
            is_authenticated=True,
        )

        result = await app.dispatcher.execute_tool(
            "accounting_get_balance",
            {"account_id": "ACC-001"},
            context=sec_context,
        )

        assert not result.is_error
        assert "1500" in result.content[0].text

        # Vérification des headers et du payload reçus par MELODI
        assert len(captured_requests) == 1
        req = captured_requests[0]
        assert req.headers.get("X-User-MCP-Key") == "mcp_live_user_token_456"
        assert req.headers.get("X-Internal-MCP-Key") == "super_secret_test_token_12345"

        body = json.loads(req.content.decode("utf-8"))
        assert body["context"]["user_key"] == "mcp_live_user_token_456"

    finally:
        await app.shutdown()


@pytest.mark.asyncio
@respx.mock
async def test_tool_dispatcher_handles_401_expired_token(mock_settings, sample_catalog_payload):
    """Vérifie que l'erreur 401 renvoie un message explicite guidant l'utilisateur vers Mes Tokens MCP."""
    respx.get("http://test-melodi:8000/api/melodi_mcp/internal/tools").respond(
        status_code=200,
        json=sample_catalog_payload,
    )
    respx.get("http://test-melodi:8000/api/melodi_mcp/internal/health").respond(
        status_code=200,
        json={"status": "healthy"},
    )
    respx.post("http://test-melodi:8000/api/melodi_mcp/internal/tools/accounting_get_balance/execute").respond(
        status_code=401,
        json={"success": False, "error": {"code": "AUTHENTICATION_REQUIRED", "message": "Token expiré"}},
    )

    app = MelodiMcpServerApp(settings=mock_settings)
    await app.startup()

    try:
        sec_context = SecurityContext(user_key="mcp_live_expired_token")
        result = await app.dispatcher.execute_tool(
            "accounting_get_balance",
            {"account_id": "ACC-001"},
            context=sec_context,
        )

        assert result.is_error
        error_text = result.content[0].text
        assert "Action refusée par MELODI" in error_text
        assert "Mes Tokens MCP" in error_text

    finally:
        await app.shutdown()


@pytest.mark.asyncio
@respx.mock
async def test_tool_dispatcher_handles_403_insufficient_permissions(mock_settings, sample_catalog_payload):
    """Vérifie que l'erreur 403 renvoie un message explicite sur les permissions manquantes."""
    respx.get("http://test-melodi:8000/api/melodi_mcp/internal/tools").respond(
        status_code=200,
        json=sample_catalog_payload,
    )
    respx.get("http://test-melodi:8000/api/melodi_mcp/internal/health").respond(
        status_code=200,
        json={"status": "healthy"},
    )
    respx.post("http://test-melodi:8000/api/melodi_mcp/internal/tools/accounting_get_balance/execute").respond(
        status_code=403,
        json={"success": False, "error": {"code": "PERMISSION_DENIED", "message": "Permission manquante"}},
    )

    app = MelodiMcpServerApp(settings=mock_settings)
    await app.startup()

    try:
        sec_context = SecurityContext(user_key="mcp_live_viewer_only")
        result = await app.dispatcher.execute_tool(
            "accounting_get_balance",
            {"account_id": "ACC-001"},
            context=sec_context,
        )

        assert result.is_error
        error_text = result.content[0].text
        assert "Droits insuffisants" in error_text
        assert "accounting_get_balance" in error_text

    finally:
        await app.shutdown()


@pytest.mark.asyncio
async def test_mcp_auth_middleware_bearer_extraction():
    """Vérifie que McpAuthMiddleware extrait les Bearer tokens dans current_user_token."""
    extracted_token = None

    async def dummy_app(scope, receive, send):
        nonlocal extracted_token
        extracted_token = current_user_token.get()
        res = JSONResponse({"status": "ok"})
        await res(scope, receive, send)

    from melodi_mcp_server.security import McpAuthMiddleware
    middleware = McpAuthMiddleware(dummy_app)

    transport = ASGITransport(app=middleware)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        # 1. Requête avec Bearer token
        resp = await client.post(
            "/mcp",
            headers={"Authorization": "Bearer mcp_live_token_777"},
            json={"jsonrpc": "2.0"},
        )
        assert resp.status_code == 200
        assert extracted_token == "mcp_live_token_777"

        # 2. Requête avec en-tête X-User-MCP-Key
        resp2 = await client.post(
            "/mcp",
            headers={"X-User-MCP-Key": "mcp_live_header_888"},
            json={"jsonrpc": "2.0"},
        )
        assert resp2.status_code == 200
        assert extracted_token == "mcp_live_header_888"

        # 3. Requête sans token
        resp3 = await client.post("/mcp", json={"jsonrpc": "2.0"})
        assert resp3.status_code == 200
        assert extracted_token is None
