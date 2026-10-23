"""Tests unitaires pour la configuration et la validation des paramètres."""

import os
import pytest
from pydantic import ValidationError
from melodi_mcp_server.settings import Settings, normalize_url_path


def test_01_settings_load_from_env(monkeypatch):
    """Test 1: Chargement de la configuration depuis les variables d'environnement."""
    monkeypatch.setenv("MELODI_BASE_URL", "https://erp.mycompany.com")
    monkeypatch.setenv("MELODI_API_PREFIX", "/api/v2/mcp")
    monkeypatch.setenv("MELODI_API_KEY", "env_secret_key_999")
    monkeypatch.setenv("MELODI_TIMEOUT_SECONDS", "45.5")
    monkeypatch.setenv("MCP_TRANSPORT", "http")
    monkeypatch.setenv("MCP_SERVER_PORT", "9090")
    monkeypatch.setenv("ADMIN_PREFIX", "/superadmin")

    s = Settings()
    assert s.melodi_base_url == "https://erp.mycompany.com"
    assert s.melodi_api_prefix == "/api/v2/mcp"
    assert s.melodi_api_key.get_secret_value() == "env_secret_key_999"
    assert s.melodi_timeout_seconds == 45.5
    assert s.mcp_transport == "http"
    assert s.mcp_server_port == 9090
    assert s.admin_prefix == "/superadmin"


def test_02_settings_validation_invalid_values():
    """Test 2: Validation stricte et rejet des valeurs invalides."""
    # URL invalide (sans http/https)
    with pytest.raises(ValidationError):
        Settings(melodi_base_url="ftp://invalid-url.com")

    # Timeout négatif ou nul
    with pytest.raises(ValidationError):
        Settings(melodi_timeout_seconds=-10)

    # Port hors intervalle
    with pytest.raises(ValidationError):
        Settings(mcp_server_port=70000)

    # Transport non reconnu
    with pytest.raises(ValidationError):
        Settings(mcp_transport="invalid_transport")


def test_02b_configurable_api_prefixes():
    """Test 2b: Normalisation et résolution correcte des préfixes d'API configurables."""
    assert normalize_url_path("internal/mcp/") == "/internal/mcp"
    assert normalize_url_path("/custom/prefix///") == "/custom/prefix"
    assert normalize_url_path("") == ""

    s = Settings(
        melodi_base_url="http://localhost:8000/",
        melodi_api_prefix="v1/mcp_endpoint/",
    )
    assert s.melodi_base_url == "http://localhost:8000"
    assert s.melodi_api_prefix == "/v1/mcp_endpoint"
    assert s.get_melodi_endpoint("/tools") == "http://localhost:8000/v1/mcp_endpoint/tools"
    assert s.get_melodi_endpoint("manifest") == "http://localhost:8000/v1/mcp_endpoint/manifest"
