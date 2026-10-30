"""Tests pour la mise à jour et la persistance dynamique de l'URL de MELODI ERP depuis l'admin."""

import pytest
import respx
from httpx import ASGITransport, AsyncClient, Response
from pydantic import SecretStr

from melodi_mcp_server.admin.db import AdminDatabase
from melodi_mcp_server.mcp_server import MelodiMcpServerApp
from melodi_mcp_server.settings import Settings


@pytest.mark.asyncio
async def test_admin_db_system_config_persistence():
    """Vérifie les méthodes get_config et set_config de la base SQLite."""
    db = AdminDatabase(":memory:")
    assert db.get_config("melodi_base_url") is None
    assert db.get_config("melodi_base_url", "http://default") == "http://default"

    db.set_config("melodi_base_url", "http://melodi-custom:9000")
    assert db.get_config("melodi_base_url") == "http://melodi-custom:9000"

    # Mise à jour
    db.set_config("melodi_base_url", "https://erp.enterprise.com")
    assert db.get_config("melodi_base_url") == "https://erp.enterprise.com"
    assert db.get_all_configs()["melodi_base_url"] == "https://erp.enterprise.com"

    # Clé API
    assert db.get_config("melodi_api_key") is None
    db.set_config("melodi_api_key", "secret-test-key-1234")
    assert db.get_config("melodi_api_key") == "secret-test-key-1234"
    assert db.get_all_configs()["melodi_api_key"] == "secret-test-key-1234"

    # Préfixe API
    assert db.get_config("melodi_api_prefix") is None
    db.set_config("melodi_api_prefix", "/api/v2/custom")
    assert db.get_config("melodi_api_prefix") == "/api/v2/custom"
    assert db.get_all_configs()["melodi_api_prefix"] == "/api/v2/custom"


@pytest.mark.asyncio
@respx.mock
async def test_admin_config_api_get_and_update(mock_settings, sample_catalog_payload):
    """Vérifie les endpoints GET /admin/api/config, POST /admin/api/config et la persistance."""
    respx.get("http://test-melodi:8000/api/melodi_mcp/internal/tools").respond(
        status_code=200, json=sample_catalog_payload
    )
    respx.get("http://test-melodi:8000/api/melodi_mcp/internal/health").respond(
        status_code=200, json={"status": "healthy"}
    )

    # Nouveaux mocks pour la nouvelle URL et le nouveau préfixe
    respx.get("http://new-erp-host:5000/api/melodi_custom/health").respond(
        status_code=200, json={"status": "healthy"}
    )
    respx.get("http://new-erp-host:5000/api/melodi_custom/tools").respond(
        status_code=200, json=sample_catalog_payload
    )

    admin_db = AdminDatabase(":memory:")
    app = MelodiMcpServerApp(settings=mock_settings)
    await app.startup()
    http_app = app.create_http_app(admin_db=admin_db)

    transport = ASGITransport(app=http_app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        # 1. Setup premier admin
        setup_resp = await client.post(
            "/admin/api/setup",
            json={"setup_key": "admin", "username": "admin", "password": "password123"},
        )
        assert setup_resp.status_code == 200
        token = setup_resp.json()["session_token"]
        headers = {"Authorization": f"Bearer {token}"}

        # 2. Consultation de la configuration actuelle
        cfg_resp = await client.get("/admin/api/config", headers=headers)
        assert cfg_resp.status_code == 200
        data = cfg_resp.json()
        assert data["melodi_base_url"] == "http://test-melodi:8000"
        assert data["melodi_api_prefix"] == "/api/melodi_mcp/internal"
        assert data["has_melodi_api_key"] is True
        assert data["melodi_api_key_preview"].startswith("supe")
        assert "••••" in data["melodi_api_key_preview"]

        # 3. Test de connectivité vers la nouvelle URL et avec une nouvelle clé et préfixe personnalisé
        test_resp = await client.post(
            "/admin/api/config/test",
            headers=headers,
            json={
                "melodi_base_url": "http://new-erp-host:5000",
                "melodi_api_key": "new-secret-token-xyz",
                "melodi_api_prefix": "/api/melodi_custom",
            },
        )
        assert test_resp.status_code == 200
        assert test_resp.json()["success"] is True
        assert "Connexion réussie" in test_resp.json()["message"]
        assert "http://new-erp-host:5000/api/melodi_custom" in test_resp.json()["message"]

        # 4. Rejet d'une URL invalide
        bad_resp = await client.post(
            "/admin/api/config",
            headers=headers,
            json={"melodi_base_url": "ftp://invalid-url"},
        )
        assert bad_resp.status_code == 400

        # 5. Application de la nouvelle URL, de la nouvelle clé d'API et du nouveau préfixe
        up_resp = await client.post(
            "/admin/api/config",
            headers=headers,
            json={
                "melodi_base_url": "http://new-erp-host:5000",
                "melodi_api_key": "new-secret-token-xyz",
                "melodi_api_prefix": "api/melodi_custom/",
            },
        )
        assert up_resp.status_code == 200
        assert up_resp.json()["success"] is True
        assert up_resp.json()["melodi_base_url"] == "http://new-erp-host:5000"
        assert up_resp.json()["melodi_api_prefix"] == "/api/melodi_custom"
        assert up_resp.json()["has_melodi_api_key"] is True

        # Vérifier que le client en mémoire utilise bien la nouvelle URL, clé et préfixe
        assert str(app.client._client.base_url).rstrip("/") == "http://new-erp-host:5000"
        assert app.settings.melodi_base_url == "http://new-erp-host:5000"
        assert app.settings.melodi_api_key.get_secret_value() == "new-secret-token-xyz"
        assert app.settings.melodi_api_prefix == "/api/melodi_custom"

        # Vérifier la persistance dans la base SQLite
        assert admin_db.get_config("melodi_base_url") == "http://new-erp-host:5000"
        assert admin_db.get_config("melodi_api_key") == "new-secret-token-xyz"
        assert admin_db.get_config("melodi_api_prefix") == "/api/melodi_custom"

    await app.shutdown()


@pytest.mark.asyncio
@respx.mock
async def test_restoration_of_persisted_url_on_startup(mock_settings, sample_catalog_payload):
    """Vérifie qu'un redémarrage du serveur restaure automatiquement l'URL, la clé et le préfixe configurés en base."""
    respx.get("https://restored-melodi:443/custom_prefix/health").respond(
        status_code=200, json={"status": "healthy"}
    )
    respx.get("https://restored-melodi:443/custom_prefix/tools").respond(
        status_code=200, json=sample_catalog_payload
    )

    admin_db = AdminDatabase(":memory:")
    # Pré-configurer une URL, une clé et un préfixe personnalisés dans la base
    admin_db.set_config("melodi_base_url", "https://restored-melodi")
    admin_db.set_config("melodi_api_key", "restored-super-key")
    admin_db.set_config("melodi_api_prefix", "/custom_prefix")

    new_app = MelodiMcpServerApp(settings=mock_settings)
    new_http_app = new_app.create_http_app(admin_db=admin_db)

    # Vérification que l'URL, la clé et le préfixe ont été immédiatement restaurés
    assert str(new_app.client._client.base_url).rstrip("/") == "https://restored-melodi"
    assert new_app.settings.melodi_base_url == "https://restored-melodi"
    assert new_app.settings.melodi_api_key.get_secret_value() == "restored-super-key"
    assert new_app.settings.melodi_api_prefix == "/custom_prefix"

    await new_app.shutdown()
