"""Tests de la sécurité et de l'authentification unifiée par jeton JWT sur les endpoints MCP."""

import time
import jwt
import pytest
import respx
from pydantic import SecretStr
from starlette.testclient import TestClient

from melodi_mcp_server.mcp_server import MelodiMcpServerApp
from melodi_mcp_server.settings import Settings

JWT_TEST_SECRET = "super-secret-key-for-mcp-jwt-testing-32-chars"
MCP_INFRA_KEY = "test-secret-token-xyz"


@pytest.fixture
def jwt_settings(mock_settings):
    """Configuration avec une clé secrète JWT et une clé MCP API définies."""
    data = mock_settings.model_dump()
    data["mcp_transport"] = "http"
    data["jwt_secret"] = JWT_TEST_SECRET
    data["mcp_api_key"] = MCP_INFRA_KEY
    return Settings(**data)


def create_test_jwt(
    payload: dict,
    secret: str = JWT_TEST_SECRET,
    algorithm: str = "HS256",
) -> str:
    """Utilitaire pour créer des jetons JWT valides ou personnalisés pour les tests."""
    return jwt.encode(payload, secret, algorithm=algorithm)


@pytest.mark.asyncio
@respx.mock
async def test_mcp_auth_requires_jwt_secret_configured(mock_settings, sample_catalog_payload):
    """Sans jwt_secret configuré, les requêtes MCP sont rejetées avec 401 informant de configurer le secret."""
    respx.get("http://test-melodi:8000/api/melodi_mcp/internal/tools").respond(
        status_code=200, json=sample_catalog_payload
    )
    app = MelodiMcpServerApp(settings=mock_settings)
    http_app = app.create_http_app()

    init_payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "test-client", "version": "1.0"},
        },
    }

    with TestClient(http_app, base_url="http://127.0.0.1:8765") as client:
        # Sans Authorization header -> 401
        r = client.post("/mcp", json=init_payload)
        assert r.status_code == 401
        assert "Authorization" in r.json()["error"]["message"]


@pytest.mark.asyncio
@respx.mock
async def test_mcp_jwt_auth_full_matrix(jwt_settings, sample_catalog_payload):
    """Test complet de l'authentification unifiée par jeton JWT via l'en-tête unique Authorization."""
    respx.get("http://test-melodi:8000/api/melodi_mcp/internal/tools").respond(
        status_code=200, json=sample_catalog_payload
    )
    app = MelodiMcpServerApp(settings=jwt_settings)
    http_app = app.create_http_app()

    init_payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "test-client", "version": "1.0"},
        },
    }

    valid_payload = {
        "token_id": "tok_matrix_1",
        "mcp_api_key": MCP_INFRA_KEY,
        "user_id": 42,
        "exp": int(time.time()) + 3600,
    }
    valid_jwt = create_test_jwt(valid_payload)

    with TestClient(http_app, base_url="http://127.0.0.1:8765") as client:
        # 1. /health reste accessible publiquement
        r_health = client.get("/health")
        assert r_health.status_code == 200
        health_json = r_health.json()
        assert health_json["server"]["auth_required"] is True
        assert health_json["server"]["jwt_configured"] is True

        # 2. Requête sans en-tête Authorization sur /mcp -> 401
        r_unauth = client.post("/mcp", json=init_payload)
        assert r_unauth.status_code == 401
        assert "WWW-Authenticate" in r_unauth.headers
        assert r_unauth.json()["error"]["code"] == -32000
        assert "Authorization" in r_unauth.json()["error"]["message"]

        # 3. Requête sans en-tête Authorization sur / -> 401
        r_root_unauth = client.post("/", json=init_payload)
        assert r_root_unauth.status_code == 401

        # 4. Requête avec token non-JWT / corrompu -> 401
        r_bad_jwt = client.post(
            "/mcp",
            headers={"Authorization": "Bearer not-a-valid-jwt-token"},
            json=init_payload,
        )
        assert r_bad_jwt.status_code == 401
        assert "Jeton JWT invalide" in r_bad_jwt.json()["error"]["message"]

        # 5. Requête avec JWT signé avec une mauvaise clé secrète -> 401
        wrong_secret_jwt = create_test_jwt(valid_payload, secret="wrong-secret-key-that-does-not-match")
        r_bad_sig = client.post(
            "/mcp",
            headers={"Authorization": f"Bearer {wrong_secret_jwt}"},
            json=init_payload,
        )
        assert r_bad_sig.status_code == 401
        assert "Signature du jeton JWT invalide" in r_bad_sig.json()["error"]["message"]

        # 6. Requête avec JWT expiré -> 401
        expired_payload = {**valid_payload, "exp": int(time.time()) - 100}
        expired_jwt = create_test_jwt(expired_payload)
        r_expired = client.post(
            "/mcp",
            headers={"Authorization": f"Bearer {expired_jwt}"},
            json=init_payload,
        )
        assert r_expired.status_code == 401
        assert "Jeton JWT expiré" in r_expired.json()["error"]["message"]

        # 7. Requête avec JWT contenant une mauvaise mcp_api_key -> 401
        bad_infra_payload = {**valid_payload, "mcp_api_key": "wrong-mcp-key"}
        bad_infra_jwt = create_test_jwt(bad_infra_payload)
        r_bad_infra = client.post(
            "/mcp",
            headers={"Authorization": f"Bearer {bad_infra_jwt}"},
            json=init_payload,
        )
        assert r_bad_infra.status_code == 401
        assert "mcp_api_key" in r_bad_infra.json()["error"]["message"]

        # 8. Requête avec JWT sans identifiant utilisateur -> 401
        no_user_payload = {
            "token_id": "tok_no_user",
            "mcp_api_key": MCP_INFRA_KEY,
            "exp": int(time.time()) + 3600,
        }
        no_user_jwt = create_test_jwt(no_user_payload)
        r_no_user = client.post(
            "/mcp",
            headers={"Authorization": f"Bearer {no_user_jwt}"},
            json=init_payload,
        )
        assert r_no_user.status_code == 401
        assert "user_id" in r_no_user.json()["error"]["message"]

        # 9. Requête avec JWT valide sur /mcp -> 200
        r_ok_mcp = client.post(
            "/mcp",
            headers={"Authorization": f"Bearer {valid_jwt}"},
            json=init_payload,
        )
        assert r_ok_mcp.status_code == 200

        # 10. Requête avec JWT valide sur / -> 200
        r_ok_root = client.post(
            "/",
            headers={"Authorization": f"Bearer {valid_jwt}"},
            json=init_payload,
        )
        assert r_ok_root.status_code == 200

        # 11. Requête avec JWT valide sur /sse -> 200
        r_ok_sse = client.post(
            "/sse",
            headers={"Authorization": f"Bearer {valid_jwt}"},
            json=init_payload,
        )
        assert r_ok_sse.status_code == 200

        # 12. Requête avec JWT valide passé en paramètre d'URL (?token=...) -> 200
        r_ok_query = client.post(
            f"/mcp?token={valid_jwt}",
            json=init_payload,
        )
        assert r_ok_query.status_code == 200
