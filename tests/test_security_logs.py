"""Tests de sécurité des logs et de fermeture propre des ressources."""

import io
import logging
import pytest
from melodi_mcp_server.logging_config import configure_logging
from melodi_mcp_server.melodi_client import MelodiAsyncClient
from melodi_mcp_server.settings import Settings


def test_19_no_secret_leaks_in_logs():
    """Test 19: Absence absolue de fuite de tokens/secrets dans les journaux."""
    log_stream = io.StringIO()
    logger = configure_logging(level="DEBUG", stream=log_stream)

    secret_token = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.sometokendata"
    logger.info("Connexion avec Bearer %s", secret_token)
    logger.info("Paramètres : api_key=%s", "secret_api_key_value_9999")
    logger.info("Authentification : password=SuperSecretPassword123!")

    output = log_stream.getvalue()
    assert secret_token not in output
    assert "secret_api_key_value_9999" not in output
    assert "SuperSecretPassword123!" not in output
    assert "[MASQUÉ]" in output


@pytest.mark.asyncio
async def test_21_graceful_shutdown_of_resources(mock_settings):
    """Test 21: Fermeture propre des connexions et des ressources HTTP."""
    client = MelodiAsyncClient(mock_settings)
    assert not client._client.is_closed

    await client.aclose()
    assert client._client.is_closed
