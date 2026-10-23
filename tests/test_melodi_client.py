"""Tests d'intégration HTTP pour MelodiAsyncClient avec respx."""

import httpx
import pytest
import respx
from melodi_mcp_server.errors import (
    MelodiAuthenticationError,
    MelodiConnectionError,
    MelodiContractError,
    MelodiTimeoutError,
    ToolNotFoundError,
)
from melodi_mcp_server.melodi_client import MelodiAsyncClient
from melodi_mcp_server.settings import Settings


@pytest.mark.asyncio
@respx.mock
async def test_03_melodi_client_connection_and_headers(mock_settings):
    """Test 3: Connexion au client MELODI, utilisation du préfixe dynamique et vérification des headers."""
    route = respx.get("http://test-melodi:8000/internal/mcp/health").respond(
        status_code=200,
        json={"status": "healthy"},
    )

    async with MelodiAsyncClient(mock_settings) as client:
        health = await client.get_health()
        assert health["status"] == "healthy"
        assert route.called
        request = route.calls.last.request
        assert request.headers["Authorization"] == "Bearer super_secret_test_token_12345"
        assert request.headers["X-Melodi-Service-Token"] == "super_secret_test_token_12345"
        assert "X-Correlation-ID" in request.headers


@pytest.mark.asyncio
@respx.mock
async def test_04_melodi_client_network_unavailability_and_timeout(mock_settings):
    """Test 4: Gestion d'une indisponibilité réseau ou d'un timeout."""
    # Simulation d'un timeout
    respx.get("http://test-melodi:8000/internal/mcp/health").mock(
        side_effect=httpx.TimeoutException("Délai d'attente réseau expiré")
    )

    async with MelodiAsyncClient(mock_settings) as client:
        with pytest.raises(MelodiTimeoutError):
            await client.get_health()

    # Simulation d'une erreur de connexion (serveur injoignable)
    respx.get("http://test-melodi:8000/internal/mcp/health").mock(
        side_effect=httpx.ConnectError("Connexion refusée")
    )
    async with MelodiAsyncClient(mock_settings) as client:
        with pytest.raises(MelodiConnectionError):
            await client.get_health()


@pytest.mark.asyncio
@respx.mock
async def test_05_melodi_client_get_manifest(mock_settings, sample_manifest_payload):
    """Test 5: Chargement et désérialisation du manifeste MELODI."""
    respx.get("http://test-melodi:8000/internal/mcp/manifest").respond(
        status_code=200,
        json=sample_manifest_payload,
    )

    async with MelodiAsyncClient(mock_settings) as client:
        manifest = await client.get_manifest()
        assert manifest["module"] == "melodi_mcp"
        assert manifest["catalog_revision"] == "rev-test-1"
        assert manifest["capabilities"]["dynamic_tools"] is True


@pytest.mark.asyncio
@respx.mock
async def test_06_melodi_client_get_tools(mock_settings, sample_catalog_payload):
    """Test 6: Chargement du catalogue d'outils."""
    respx.get("http://test-melodi:8000/internal/mcp/tools").respond(
        status_code=200,
        json=sample_catalog_payload,
    )

    async with MelodiAsyncClient(mock_settings) as client:
        catalog = await client.get_tools()
        assert catalog["catalog_revision"] == "rev-test-1"
        assert len(catalog["tools"]) == 2
        assert catalog["tools"][0]["name"] == "accounting_get_balance"


@pytest.mark.asyncio
@respx.mock
async def test_melodi_client_handles_auth_error_and_404(mock_settings):
    """Vérifie la levée des exceptions typées pour 401 et 404."""
    respx.get("http://test-melodi:8000/internal/mcp/health").respond(status_code=401)
    respx.get("http://test-melodi:8000/internal/mcp/tools").respond(status_code=404)

    async with MelodiAsyncClient(mock_settings) as client:
        with pytest.raises(MelodiAuthenticationError):
            await client.get_health()
        with pytest.raises(ToolNotFoundError):
            await client.get_tools()
