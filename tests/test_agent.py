"""Tests unitaires pour l'Agent IA interactif connecté à MELODI MCP."""

import pytest
import json
from unittest.mock import AsyncMock, MagicMock, patch

import mcp.types as mcp_types
from melodi_mcp_server.agent import MelodiMCPAgent


@pytest.fixture
def mock_mcp_tools():
    return [
        mcp_types.Tool(
            name="ecoblog_get_stats",
            description="Retourne les statistiques globales du blog.",
            input_schema={"type": "object", "properties": {}},
        ),
        mcp_types.Tool(
            name="ecoblog_list_articles",
            description="Liste les articles économiques récents.",
            input_schema={
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "default": 5},
                    "category": {"type": "string"},
                },
            },
        ),
        mcp_types.Tool(
            name="ecoblog_search_articles",
            description="Recherche d'articles par mots-clés.",
            input_schema={
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        ),
    ]


def test_agent_initialization():
    """Vérifie l'initialisation de l'agent et la sélection automatique de provider."""
    # Par défaut sans clé -> simulated
    agent = MelodiMCPAgent(api_key=None, base_url=None)
    assert agent.provider == "simulated"
    assert agent.transport == "http"

    # Avec clé API -> openai
    agent_openai = MelodiMCPAgent(api_key="sk-test-fake-key")
    assert agent_openai.provider == "openai"
    assert agent_openai.model == "gpt-4o-mini"

    # Provider forcé Ollama
    agent_ollama = MelodiMCPAgent(provider="ollama", model="llama3.1")
    assert agent_ollama.provider == "ollama"
    assert agent_ollama.model == "llama3.1"
    assert agent_ollama.base_url == "http://localhost:11434/v1"


def test_tools_conversion_to_openai(mock_mcp_tools):
    """Vérifie la transformation correcte des outils MCP en schémas OpenAI."""
    agent = MelodiMCPAgent()
    agent.tools = mock_mcp_tools
    openai_tools = agent._convert_tools_to_openai()

    assert len(openai_tools) == 3
    t1 = openai_tools[0]
    assert t1["type"] == "function"
    assert t1["function"]["name"] == "ecoblog_get_stats"
    assert t1["function"]["description"] == "Retourne les statistiques globales du blog."
    assert t1["function"]["parameters"] == {"type": "object", "properties": {}}

    t2 = openai_tools[1]
    assert t2["function"]["name"] == "ecoblog_list_articles"
    assert "limit" in t2["function"]["parameters"]["properties"]


@pytest.mark.asyncio
async def test_execute_mcp_tool_success():
    """Vérifie l'exécution d'un outil MCP avec succès."""
    agent = MelodiMCPAgent()
    session = AsyncMock()
    mock_result = MagicMock()
    mock_result.is_error = False
    mock_content = MagicMock()
    mock_content.text = json.dumps({"success": True, "total": 5})
    mock_result.content = [mock_content]
    session.call_tool.return_value = mock_result

    is_error, content = await agent.execute_mcp_tool(session, "ecoblog_get_stats", {})
    assert is_error is False
    assert "total" in content
    session.call_tool.assert_awaited_once_with("ecoblog_get_stats", {})


@pytest.mark.asyncio
async def test_execute_mcp_tool_error():
    """Vérifie le traitement d'une erreur lors de l'appel d'outil MCP."""
    agent = MelodiMCPAgent()
    session = AsyncMock()
    session.call_tool.side_effect = RuntimeError("Erreur réseau de test")

    is_error, content = await agent.execute_mcp_tool(session, "ecoblog_get_stats", {})
    assert is_error is True
    assert "Erreur réseau de test" in content


@pytest.mark.asyncio
async def test_simulated_intent_recognition(mock_mcp_tools):
    """Vérifie la détection d'intention en mode simulé et l'exécution de l'outil approprié."""
    agent = MelodiMCPAgent(provider="simulated")
    agent.tools = mock_mcp_tools

    session = AsyncMock()
    mock_result = MagicMock()
    mock_result.is_error = False
    mock_content = MagicMock()
    mock_content.text = json.dumps({"data": {"total_articles": 12}})
    mock_result.content = [mock_content]
    session.call_tool.return_value = mock_result

    # 1. Intention statistiques
    resp = await agent.handle_user_prompt(session, "Donne-moi les stats du blog")
    assert "ecoblog_get_stats" in resp
    assert "total_articles" in resp

    # 2. Intention recherche
    resp_search = await agent.handle_user_prompt(session, "Cherche l'article sur la zlecaf")
    assert "ecoblog_search_articles" in resp_search
