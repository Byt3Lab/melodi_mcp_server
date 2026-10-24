"""Tests pour le module HealthChecker."""

import pytest
import respx
from melodi_mcp_server.health import HealthChecker
from melodi_mcp_server.melodi_client import MelodiAsyncClient
from melodi_mcp_server.tool_registry import DynamicToolRegistry


@pytest.mark.asyncio
@respx.mock
async def test_health_checker_healthy_and_unhealthy(mock_settings, sample_catalog_payload):
    client = MelodiAsyncClient(mock_settings)
    registry = DynamicToolRegistry(client, mock_settings)
    health_checker = HealthChecker(registry, client, mock_settings)

    # Cas 1: ERP connecté et catalogue chargé
    respx.get("http://test-melodi:8000/api/melodi_mcp/internal/health").respond(
        status_code=200,
        json={"status": "healthy"},
    )
    await registry.update_from_payload(sample_catalog_payload)

    status_data = await health_checker.check()
    assert status_data["status"] == "HEALTHY"
    assert status_data["melodi_connection"]["status"] in ("CONNECTED", "UP")
    assert status_data["catalog"]["tools_count"] == 2

    # Cas 2: ERP déconnecté
    respx.get("http://test-melodi:8000/api/melodi_mcp/internal/health").respond(status_code=500)
    status_degraded = await health_checker.check()
    assert status_degraded["status"] == "DEGRADED"  # Outils en cache mais ERP down
