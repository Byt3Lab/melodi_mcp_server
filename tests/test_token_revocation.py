"""Tests complets pour la révocation des jetons JWT, la limite 72h et la purge."""

import time
from datetime import datetime, timezone, timedelta
import jwt
import pytest
import respx
from httpx import ASGITransport, AsyncClient, Response as HttpxResponse
from starlette.testclient import TestClient

from melodi_mcp_server.admin.db import AdminDatabase
from melodi_mcp_server.mcp_server import MelodiMcpServerApp
from melodi_mcp_server.settings import Settings

JWT_SECRET = "jwt-secret-key-32-characters-token-revocation"
MCP_API_KEY = "mcp-infra-key-12345"


@pytest.fixture
def revocation_settings(mock_settings):
    data = mock_settings.model_dump()
    data["mcp_transport"] = "http"
    data["jwt_secret"] = JWT_SECRET
    data["mcp_api_key"] = MCP_API_KEY
    return Settings(**data)


def make_jwt(payload: dict, secret: str = JWT_SECRET) -> str:
    return jwt.encode(payload, secret, algorithm="HS256")


@pytest.mark.asyncio
async def test_admin_db_revocation_and_cleanup():
    """Vérifie les méthodes de révocation, vérification et purge de AdminDatabase."""
    db = AdminDatabase(":memory:")

    # Aucun token révoqué au départ
    assert not db.is_token_revoked("tok_1")

    # Révocation d'un token
    db.revoke_token("tok_1", user_id="42", reason="Test révocation")
    assert db.is_token_revoked("tok_1")
    assert not db.is_token_revoked("tok_2")

    tokens = db.list_revoked_tokens()
    assert len(tokens) == 1
    assert tokens[0]["token_id"] == "tok_1"
    assert tokens[0]["user_id"] == "42"

    # Insertion artificielle d'un token révoqué il y a 80 heures
    old_time = (datetime.now(timezone.utc) - timedelta(hours=80)).isoformat()
    with db._get_connection() as conn:
        conn.execute(
            "INSERT INTO revoked_tokens (token_id, user_id, revoked_at) VALUES (?, ?, ?);",
            ("tok_old", "99", old_time),
        )
        conn.commit()
    db._revoked_tokens_cache.add("tok_old")
    assert db.is_token_revoked("tok_old")

    # Purge des tokens de plus de 72h
    deleted = db.cleanup_expired_revoked_tokens(max_age_hours=72)
    assert deleted == 1  # tok_old supprimé
    assert db.is_token_revoked("tok_1")  # tok_1 conservé (< 72h)
    assert not db.is_token_revoked("tok_old")

    # Déblocage manuel
    db.delete_revoked_token("tok_1")
    assert not db.is_token_revoked("tok_1")


@pytest.mark.asyncio
@respx.mock
async def test_mcp_jwt_requires_token_id_and_max_72h(revocation_settings, sample_catalog_payload):
    """Vérifie le rejet des jetons sans token_id ou avec expiration > 72h."""
    respx.get("http://test-melodi:8000/api/melodi_mcp/internal/tools").respond(
        status_code=200, json=sample_catalog_payload
    )
    admin_db = AdminDatabase(":memory:")
    app = MelodiMcpServerApp(settings=revocation_settings)
    http_app = app.create_http_app(admin_db=admin_db)

    now = int(time.time())
    init_json = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "1.0"},
        },
    }

    with TestClient(http_app, base_url="http://127.0.0.1:8765") as client:
        # 1. Jeton sans token_id -> 401
        token_no_id = make_jwt({
            "mcp_api_key": MCP_API_KEY,
            "user_id": 10,
            "exp": now + 3600,
        })
        r_no_id = client.post("/mcp", headers={"Authorization": f"Bearer {token_no_id}"}, json=init_json)
        assert r_no_id.status_code == 401
        assert "token_id" in r_no_id.json()["error"]["message"]

        # 2. Jeton avec durée supérieure à 72h -> 401
        token_too_long = make_jwt({
            "mcp_api_key": MCP_API_KEY,
            "user_id": 10,
            "token_id": "tok_long",
            "exp": now + (75 * 3600),  # 75 heures
        })
        r_too_long = client.post("/mcp", headers={"Authorization": f"Bearer {token_too_long}"}, json=init_json)
        assert r_too_long.status_code == 401
        assert "72 heures" in r_too_long.json()["error"]["message"]

        # 3. Jeton valide (24h de durée, token_id présent) -> 200
        token_valid = make_jwt({
            "mcp_api_key": MCP_API_KEY,
            "user_id": 10,
            "token_id": "tok_valid_1",
            "exp": now + (24 * 3600),
        })
        r_valid = client.post("/mcp", headers={"Authorization": f"Bearer {token_valid}"}, json=init_json)
        assert r_valid.status_code == 200


@pytest.mark.asyncio
@respx.mock
async def test_mcp_token_revocation_webhook_and_blocking(revocation_settings, sample_catalog_payload):
    """Vérifie le flux : token actif -> appel webhook de révocation par ERP -> token bloqué."""
    respx.get("http://test-melodi:8000/api/melodi_mcp/internal/tools").respond(
        status_code=200, json=sample_catalog_payload
    )
    admin_db = AdminDatabase(":memory:")
    app = MelodiMcpServerApp(settings=revocation_settings)
    http_app = app.create_http_app(admin_db=admin_db)

    now = int(time.time())
    jwt_token = make_jwt({
        "mcp_api_key": MCP_API_KEY,
        "user_id": 42,
        "token_id": "tok_alice_123",
        "exp": now + 3600,
    })

    init_json = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "1.0"},
        },
    }

    with TestClient(http_app, base_url="http://127.0.0.1:8765") as client:
        # Étape 1 : Le token est valide au départ -> 200
        r_before = client.post("/mcp", headers={"Authorization": f"Bearer {jwt_token}"}, json=init_json)
        assert r_before.status_code == 200

        # Étape 2 : Appel webhook de révocation par MELODI ERP (/api/mcp/tokens/revoke)
        # Authentifié par melodi_api_key
        service_key = revocation_settings.melodi_api_key.get_secret_value()
        revoke_resp = client.post(
            "/api/mcp/tokens/revoke",
            headers={"Authorization": f"Bearer {service_key}"},
            json={
                "token_id": "tok_alice_123",
                "user_id": 42,
                "reason": "Token révoqué par l'utilisateur",
            },
        )
        assert revoke_resp.status_code == 200
        assert revoke_resp.json()["success"] is True

        # Étape 3 : Le token est maintenant révoqué -> 401
        r_after = client.post("/mcp", headers={"Authorization": f"Bearer {jwt_token}"}, json=init_json)
        assert r_after.status_code == 401
        assert "révoqué" in r_after.json()["error"]["message"]


@pytest.mark.asyncio
@respx.mock
async def test_admin_api_tokens_cleanup_flow(revocation_settings, sample_catalog_payload):
    """Vérifie la consultation des tokens révoqués et la purge manuelle depuis l'API Admin."""
    respx.get("http://test-melodi:8000/api/melodi_mcp/internal/tools").respond(
        status_code=200, json=sample_catalog_payload
    )
    admin_db = AdminDatabase(":memory:")
    app = MelodiMcpServerApp(settings=revocation_settings)
    http_app = app.create_http_app(admin_db=admin_db)

    transport = ASGITransport(app=http_app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        # 1. Setup session admin
        setup_resp = await client.post(
            "/admin/api/setup",
            json={"setup_key": "admin", "username": "admin", "password": "password123"},
        )
        token = setup_resp.json()["session_token"]
        headers = {"Authorization": f"Bearer {token}"}

        # 2. Révocation manuelle d'un token via l'API Admin
        rev_resp = await client.post(
            "/admin/api/tokens/revoke",
            headers=headers,
            json={"token_id": "tok_manual_99", "user_id": 14, "reason": "Compte suspendu"},
        )
        assert rev_resp.status_code == 200
        assert rev_resp.json()["success"] is True

        # 3. Liste des tokens révoqués
        list_resp = await client.get("/admin/api/tokens/revoked", headers=headers)
        assert list_resp.status_code == 200
        revoked_list = list_resp.json()["revoked_tokens"]
        assert len(revoked_list) == 1
        assert revoked_list[0]["token_id"] == "tok_manual_99"

        # 4. Insertion d'un token vieux de 80h pour tester la purge manuelle
        old_time = (datetime.now(timezone.utc) - timedelta(hours=80)).isoformat()
        with admin_db._get_connection() as conn:
            conn.execute(
                "INSERT INTO revoked_tokens (token_id, user_id, revoked_at) VALUES (?, ?, ?);",
                ("tok_old_manual", "20", old_time),
            )
            conn.commit()
        admin_db._revoked_tokens_cache.add("tok_old_manual")

        # 5. Déclenchement de la purge via POST /admin/api/tokens/cleanup
        cleanup_resp = await client.post("/admin/api/tokens/cleanup", headers=headers)
        assert cleanup_resp.status_code == 200
        assert cleanup_resp.json()["success"] is True
        assert cleanup_resp.json()["deleted_count"] == 1

        # 6. Vérification que tok_manual_99 est toujours révoqué
        assert admin_db.is_token_revoked("tok_manual_99")
        assert not admin_db.is_token_revoked("tok_old_manual")
