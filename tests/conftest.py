"""Fixtures et mocks globaux pour la suite de tests."""

import pytest
from pydantic import SecretStr
import respx
from httpx import Response

from melodi_mcp_server.settings import Settings, set_settings
from melodi_mcp_server.melodi_client import MelodiAsyncClient
from melodi_mcp_server.tool_registry import DynamicToolRegistry
from melodi_mcp_server.tool_dispatcher import ToolDispatcher
from melodi_mcp_server.health import HealthChecker


@pytest.fixture
def mock_settings() -> Settings:
    """Paramètres isolés pour les tests."""
    s = Settings(
        melodi_base_url="http://test-melodi:8000",
        melodi_api_prefix="/api/melodi_mcp/internal",
        melodi_api_key=SecretStr("super_secret_test_token_12345"),
        melodi_timeout_seconds=2.0,
        mcp_transport="stdio",
        admin_dashboard_enabled=True,
        admin_prefix="/admin",
        admin_api_key=None,
        catalog_sync_interval_seconds=10,
    )
    set_settings(s)
    return s


@pytest.fixture
def sample_catalog_payload() -> dict:
    """Exemple de catalogue retourné par MELODI."""
    return {
        "catalog_revision": "rev-test-1",
        "tools": [
            {
                "name": "accounting_get_balance",
                "description": "Retourne le solde du compte",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "account_id": {"type": "string"}
                    },
                    "required": ["account_id"]
                },
                "read_only": True,
                "requires_confirmation": False,
                "timeout_seconds": 5.0,
                "required_permissions": ["accounting.view"]
            },
            {
                "name": "invoice_create",
                "description": "Crée une facture dans MELODI",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "customer_id": {"type": "string"},
                        "amount": {"type": "number"},
                        "_confirmed": {"type": "boolean"}
                    },
                    "required": ["customer_id", "amount"]
                },
                "read_only": False,
                "requires_confirmation": True,
                "timeout_seconds": 10.0,
                "required_permissions": ["invoicing.create"]
            }
        ]
    }


@pytest.fixture
def sample_manifest_payload() -> dict:
    return {
        "module": "melodi_mcp",
        "version": "1.0.0",
        "catalog_revision": "rev-test-1",
        "capabilities": {
            "dynamic_tools": True,
            "user_delegation": True,
            "confirmation_flow": True
        }
    }


@pytest.fixture
def sample_health_payload() -> dict:
    return {
        "status": "healthy",
        "database": "connected",
        "timestamp": "2026-10-23T07:00:00Z"
    }
