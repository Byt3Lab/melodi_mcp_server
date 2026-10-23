"""Tests unitaires pour DynamicToolRegistry et la conversion de schéma."""

import pytest
import respx
from melodi_mcp_server.errors import ToolValidationError
from melodi_mcp_server.melodi_client import MelodiAsyncClient
from melodi_mcp_server.tool_registry import DynamicToolRegistry
from mcp.types import Tool


def test_07_tool_conversion_to_mcp(mock_settings):
    """Test 7: Conversion d'un outil MELODI en définition MCP (types.Tool)."""
    client = MelodiAsyncClient(mock_settings)
    registry = DynamicToolRegistry(client, mock_settings)

    raw_tool = {
        "name": "accounting_get_balance",
        "description": "Retourne le solde",
        "input_schema": {
            "type": "object",
            "properties": {"account_id": {"type": "string"}},
            "required": ["account_id"]
        },
        "read_only": True,
        "timeout_seconds": 15.0
    }

    internal = registry.validate_tool_definition(raw_tool)
    mcp_tool = internal.to_mcp_tool()

    assert isinstance(mcp_tool, Tool)
    assert mcp_tool.name == "accounting_get_balance"
    assert mcp_tool.description == "Retourne le solde"
    assert mcp_tool.input_schema == raw_tool["input_schema"]


def test_08_invalid_schema_rejected(mock_settings):
    """Test 8: Gestion et rejet d'un schéma JSON invalide ou incompatible."""
    client = MelodiAsyncClient(mock_settings)
    registry = DynamicToolRegistry(client, mock_settings)

    # Nom d'outil manquant
    with pytest.raises(ToolValidationError):
        registry.validate_tool_definition({"description": "sans nom"})

    # Schéma JSON invalide selon la spécification JSON Schema
    with pytest.raises(ToolValidationError):
        registry.validate_tool_definition({
            "name": "invalid_tool",
            "input_schema": {"type": "inconnu_non_valide"}
        })


def test_09_name_collision_detected(mock_settings):
    """Test 9: Détection et rejet des collisions de noms d'outils."""
    client = MelodiAsyncClient(mock_settings)
    registry = DynamicToolRegistry(client, mock_settings)

    duplicated_payload = {
        "catalog_revision": "rev-dup",
        "tools": [
            {"name": "tool_alpha", "input_schema": {"type": "object"}},
            {"name": "tool_alpha", "input_schema": {"type": "object"}},
        ]
    }

    with pytest.raises(ToolValidationError, match="Collision de nom détectée"):
        import asyncio
        asyncio.run(registry.update_from_payload(duplicated_payload))


@pytest.mark.asyncio
async def test_10_dynamic_registration(mock_settings, sample_catalog_payload):
    """Test 10: Enregistrement dynamique des outils dans le registre."""
    client = MelodiAsyncClient(mock_settings)
    registry = DynamicToolRegistry(client, mock_settings)

    count = await registry.update_from_payload(sample_catalog_payload)
    assert count == 2
    assert registry.tools_count == 2
    assert registry.get_tool("accounting_get_balance") is not None
    assert registry.get_tool("invoice_create") is not None


@pytest.mark.asyncio
async def test_11_removal_of_unavailable_tool_on_resync(mock_settings, sample_catalog_payload):
    """Test 11: Suppression d'un outil devenu indisponible après resynchronisation."""
    client = MelodiAsyncClient(mock_settings)
    registry = DynamicToolRegistry(client, mock_settings)

    # Initialisation avec 2 outils
    await registry.update_from_payload(sample_catalog_payload)
    assert registry.tools_count == 2

    # Nouveau catalogue où invoice_create a été désactivé/retiré côté MELODI
    new_payload = {
        "catalog_revision": "rev-test-2",
        "tools": [
            {
                "name": "accounting_get_balance",
                "description": "Retourne le solde du compte",
                "input_schema": {"type": "object"}
            }
        ]
    }
    await registry.update_from_payload(new_payload)

    assert registry.tools_count == 1
    assert registry.get_tool("invoice_create") is None
    assert registry.get_tool("accounting_get_balance") is not None


@pytest.mark.asyncio
async def test_18_catalog_revision_tracking(mock_settings, sample_catalog_payload):
    """Test 18: Gestion et détection des changements de révision de catalogue."""
    client = MelodiAsyncClient(mock_settings)
    registry = DynamicToolRegistry(client, mock_settings)

    await registry.update_from_payload(sample_catalog_payload)
    assert registry.catalog_revision == "rev-test-1"

    # Même révision fournie : pas de réanalyse superflue
    same_revision_payload = {
        "catalog_revision": "rev-test-1",
        "tools": []  # Si la révision n'a pas changé, le catalogue en cache est conservé
    }
    count = await registry.update_from_payload(same_revision_payload, force=False)
    assert count == 2
    assert registry.tools_count == 2
