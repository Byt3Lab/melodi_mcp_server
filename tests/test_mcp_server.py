"""Tests du cycle de vie du serveur MCP et des transports."""

import pytest
import respx
from melodi_mcp_server.mcp_server import MelodiMcpServerApp
from melodi_mcp_server.settings import Settings


@pytest.mark.asyncio
@respx.mock
async def test_20_mcp_server_initialization_and_transports(mock_settings, sample_catalog_payload):
    """Test 20: Comportement et initialisation du transport configuré."""
    respx.get("http://test-melodi:8000/api/melodi_mcp/internal/tools").respond(
        status_code=200,
        json=sample_catalog_payload,
    )

    app = MelodiMcpServerApp(settings=mock_settings)

    # Initialisation / startup
    await app.startup()
    assert app.registry.tools_count == 2

    # Vérification de l'application HTTP
    http_app = app.create_http_app()
    assert http_app is not None

    # Fermeture propre
    await app.shutdown()
    assert app.client._client.is_closed
