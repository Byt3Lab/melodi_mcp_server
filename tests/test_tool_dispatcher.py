"""Tests d'exécution des outils, validation locale et transmission à MELODI."""

import asyncio
import pytest
import respx
from melodi_mcp_server.melodi_client import MelodiAsyncClient
from melodi_mcp_server.security import SecurityContext
from melodi_mcp_server.tool_dispatcher import ToolDispatcher
from melodi_mcp_server.tool_registry import DynamicToolRegistry


@pytest.fixture
async def setup_dispatcher(mock_settings, sample_catalog_payload):
    client = MelodiAsyncClient(mock_settings)
    registry = DynamicToolRegistry(client, mock_settings)
    await registry.update_from_payload(sample_catalog_payload)
    dispatcher = ToolDispatcher(registry, client, mock_settings)
    return dispatcher, client, registry


@pytest.mark.asyncio
@respx.mock
async def test_12_execute_read_tool(setup_dispatcher):
    """Test 12: Exécution réussie d'un outil en lecture."""
    dispatcher, _, _ = setup_dispatcher

    respx.post("http://test-melodi:8000/internal/mcp/tools/accounting_get_balance/execute").respond(
        status_code=200,
        json={"balance": 54200.0, "currency": "EUR"},
    )

    result = await dispatcher.execute_tool(
        tool_name="accounting_get_balance",
        arguments={"account_id": "ACC-512"},
    )

    assert result.is_error is False or result.isError is False
    assert len(result.content) == 1
    assert "54200" in result.content[0].text


@pytest.mark.asyncio
@respx.mock
async def test_13_execute_write_tool_with_confirmation(setup_dispatcher):
    """Test 13: Exécution d'un outil en écriture avec vérification de la confirmation."""
    dispatcher, _, _ = setup_dispatcher

    # 1. Tentative sans le flag de confirmation requis pour invoice_create
    res_unconfirmed = await dispatcher.execute_tool(
        tool_name="invoice_create",
        arguments={"customer_id": "CUST-1", "amount": 1500.0},
    )
    assert res_unconfirmed.is_error is True or res_unconfirmed.isError is True
    assert "requiert une confirmation explicite" in res_unconfirmed.content[0].text

    # 2. Appel avec confirmation
    respx.post("http://test-melodi:8000/internal/mcp/tools/invoice_create/execute").respond(
        status_code=200,
        json={"invoice_id": "INV-2026-001", "status": "draft"},
    )
    res_confirmed = await dispatcher.execute_tool(
        tool_name="invoice_create",
        arguments={"customer_id": "CUST-1", "amount": 1500.0, "_confirmed": True},
    )
    assert res_confirmed.is_error is False or res_confirmed.isError is False
    assert "INV-2026-001" in res_confirmed.content[0].text


@pytest.mark.asyncio
@respx.mock
async def test_14_melodi_business_error_propagation(setup_dispatcher):
    """Test 14: Transmission et formatage propre des erreurs métier MELODI."""
    dispatcher, _, _ = setup_dispatcher

    respx.post("http://test-melodi:8000/internal/mcp/tools/accounting_get_balance/execute").respond(
        status_code=422,
        json={"error": "COMPTE_CLOTURE", "message": "Ce compte comptable est clôturé."},
    )

    result = await dispatcher.execute_tool(
        tool_name="accounting_get_balance",
        arguments={"account_id": "ACC-999"},
    )
    assert result.is_error is True or result.isError is True
    assert "Erreur [ToolExecutionError]" in result.content[0].text
    assert "422" in result.content[0].text


@pytest.mark.asyncio
async def test_15_local_argument_validation_rejection(setup_dispatcher):
    """Test 15: Rejet local des arguments invalides selon le schéma JSON."""
    dispatcher, _, _ = setup_dispatcher

    # 'account_id' manquant pour accounting_get_balance
    result = await dispatcher.execute_tool(
        tool_name="accounting_get_balance",
        arguments={},
    )
    assert result.is_error is True or result.isError is True
    assert "ToolValidationError" in result.content[0].text
    assert "account_id" in result.content[0].text


@pytest.mark.asyncio
@respx.mock
async def test_16_tool_execution_timeout(setup_dispatcher):
    """Test 16: Gestion d'un délai d'exécution dépassé (timeout)."""
    dispatcher, _, _ = setup_dispatcher

    async def slow_response(request):
        await asyncio.sleep(10.0)
        return respx.MockResponse(200)

    respx.post("http://test-melodi:8000/internal/mcp/tools/accounting_get_balance/execute").mock(
        side_effect=slow_response
    )

    # L'outil a un timeout de 5.0s, on force un timeout court
    dispatcher.registry.get_tool("accounting_get_balance").timeout_seconds = 0.1

    result = await dispatcher.execute_tool(
        tool_name="accounting_get_balance",
        arguments={"account_id": "ACC-512"},
    )
    assert result.is_error is True or result.isError is True
    assert "Dépassement du délai d'exécution alloué" in result.content[0].text


@pytest.mark.asyncio
async def test_17_unknown_tool_rejection(setup_dispatcher):
    """Test 17: Refus d'un outil inconnu."""
    dispatcher, _, _ = setup_dispatcher

    result = await dispatcher.execute_tool(
        tool_name="non_existent_tool",
        arguments={},
    )
    assert result.is_error is True or result.isError is True
    assert "ToolNotFoundError" in result.content[0].text


@pytest.mark.asyncio
@respx.mock
async def test_22_authorization_revalidation_by_melodi(setup_dispatcher):
    """Test 22: Revalidation systématique de l'autorisation par MELODI (403 Forbidden)."""
    dispatcher, _, _ = setup_dispatcher

    respx.post("http://test-melodi:8000/internal/mcp/tools/accounting_get_balance/execute").respond(
        status_code=403,
        json={"error": "FORBIDDEN", "message": "Permission comptable révoquée."},
    )

    result = await dispatcher.execute_tool(
        tool_name="accounting_get_balance",
        arguments={"account_id": "ACC-512"},
        context=SecurityContext(client_id="test_client", user_id="user_1", is_authenticated=True),
    )
    assert result.is_error is True or result.isError is True
    assert "MelodiAuthorizationError" in result.content[0].text
