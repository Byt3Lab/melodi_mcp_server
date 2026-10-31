"""Tests d'intégration des routes et flux OAuth 2.0 (Découverte, Authorize, Token, Register)."""

import time
import jwt
import pytest
import respx
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from melodi_mcp_server.admin.db import AdminDatabase
from melodi_mcp_server.mcp_server import MelodiMcpServerApp
from melodi_mcp_server.settings import Settings

JWT_SECRET = "oauth-test-jwt-secret-key-32-chars-minimum"
MCP_API_KEY = "infra-mcp-key-123"


@pytest.fixture
def oauth_settings(mock_settings):
    data = mock_settings.model_dump()
    data["mcp_transport"] = "http"
    data["jwt_secret"] = JWT_SECRET
    data["mcp_api_key"] = MCP_API_KEY
    return Settings(**data)


@pytest.mark.asyncio
@respx.mock
async def test_oauth_discovery_and_unauthorized_header(oauth_settings):
    """Vérifie les métadonnées RFC 8414 et l'en-tête WWW-Authenticate sur /mcp."""
    app = MelodiMcpServerApp(settings=oauth_settings)
    http_app = app.create_http_app()

    transport = ASGITransport(app=http_app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        # 1. Découverte RFC 8414
        disc_resp = await client.get("/.well-known/oauth-authorization-server")
        assert disc_resp.status_code == 200
        meta = disc_resp.json()
        assert meta["authorization_endpoint"] == "http://testserver/oauth/authorize"
        assert meta["token_endpoint"] == "http://testserver/oauth/token"
        assert meta["registration_endpoint"] == "http://testserver/oauth/register"
        assert "authorization_code" in meta["grant_types_supported"]
        assert "S256" in meta["code_challenge_methods_supported"]

        # 2. Découverte OpenID Connect
        oidc_resp = await client.get("/.well-known/openid-configuration")
        assert oidc_resp.status_code == 200
        assert oidc_resp.json()["authorization_endpoint"] == "http://testserver/oauth/authorize"

        # 3. Requête anonyme sur /mcp -> 401 avec WWW-Authenticate enrichi
        mcp_resp = await client.get("/mcp")
        assert mcp_resp.status_code == 401
        www_auth = mcp_resp.headers.get("www-authenticate", "")
        assert 'Bearer realm="MELODI MCP"' in www_auth
        assert 'authorization_uri="/oauth/authorize"' in www_auth


@pytest.mark.asyncio
@respx.mock
async def test_oauth_authorize_get_and_post_validations(oauth_settings, sample_health_payload):
    """Vérifie la page GET /oauth/authorize et les validations du POST /oauth/authorize."""
    respx.get("http://test-melodi:8000/api/melodi_mcp/internal/health").respond(
        status_code=200, json=sample_health_payload
    )

    admin_db = AdminDatabase(":memory:")
    app = MelodiMcpServerApp(settings=oauth_settings)
    http_app = app.create_http_app(admin_db=admin_db)

    transport = ASGITransport(app=http_app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        # 1. GET sans redirect_uri -> 400
        bad_get = await client.get("/oauth/authorize?client_id=chatgpt")
        assert bad_get.status_code == 400

        # 2. GET avec paramètres valides -> 200 HTML
        good_get = await client.get(
            "/oauth/authorize?client_id=chatgpt_agent&redirect_uri=https://chat.openai.com/aip/callback&state=xyz123"
        )
        assert good_get.status_code == 200
        assert "text/html" in good_get.headers.get("content-type", "")
        assert "chatgpt_agent" in good_get.text
        assert "https://chat.openai.com/aip/callback" in good_get.text

        # 3. POST sans jeton -> 400 avec message d'erreur
        empty_post = await client.post(
            "/oauth/authorize",
            data={
                "client_id": "chatgpt_agent",
                "redirect_uri": "https://chat.openai.com/aip/callback",
                "jwt_token": "",
            },
        )
        assert empty_post.status_code == 400
        assert "Veuillez coller votre jeton JWT" in empty_post.text

        # 4. POST avec signature invalide -> 400
        bad_sig_jwt = jwt.encode(
            {"token_id": "tok_1", "mcp_api_key": MCP_API_KEY, "user_id": "u1", "user_api_key": "k1"},
            "wrong-secret-key-32-chars-min-len",
            algorithm="HS256",
        )
        bad_sig_post = await client.post(
            "/oauth/authorize",
            data={
                "client_id": "chatgpt_agent",
                "redirect_uri": "https://chat.openai.com/aip/callback",
                "jwt_token": bad_sig_jwt,
            },
        )
        assert bad_sig_post.status_code == 400
        assert "Signature du jeton invalide" in bad_sig_post.text

        # 5. POST avec jeton révoqué -> 400
        admin_db.revoke_token("tok_revoked", reason="Compte compromis")
        revoked_jwt = jwt.encode(
            {"token_id": "tok_revoked", "mcp_api_key": MCP_API_KEY, "user_id": "u1", "user_api_key": "k1"},
            JWT_SECRET,
            algorithm="HS256",
        )
        revoked_post = await client.post(
            "/oauth/authorize",
            data={
                "client_id": "chatgpt_agent",
                "redirect_uri": "https://chat.openai.com/aip/callback",
                "jwt_token": revoked_jwt,
            },
        )
        assert revoked_post.status_code == 400
        assert "a été révoqué" in revoked_post.text

        # 6. POST avec jeton valide -> 302 Redirection vers redirect_uri avec code et state
        valid_jwt = jwt.encode(
            {
                "token_id": "tok_valid_999",
                "mcp_api_key": MCP_API_KEY,
                "user_id": "usr_charlie",
                "user_api_key": "pat_charlie_key_456",
                "exp": int(time.time()) + 7200,
            },
            JWT_SECRET,
            algorithm="HS256",
        )
        success_post = await client.post(
            "/oauth/authorize",
            data={
                "client_id": "chatgpt_agent",
                "redirect_uri": "https://chat.openai.com/aip/callback",
                "state": "state_abc",
                "jwt_token": valid_jwt,
            },
            follow_redirects=False,
        )
        assert success_post.status_code == 302
        location = success_post.headers.get("location", "")
        assert location.startswith("https://chat.openai.com/aip/callback?code=")
        assert "state=state_abc" in location


@pytest.mark.asyncio
@respx.mock
async def test_oauth_token_exchange_and_client_registration(oauth_settings, sample_health_payload):
    """Vérifie l'échange /oauth/token et l'enregistrement dynamique /oauth/register."""
    respx.get("http://test-melodi:8000/api/melodi_mcp/internal/health").respond(
        status_code=200, json=sample_health_payload
    )

    admin_db = AdminDatabase(":memory:")
    app = MelodiMcpServerApp(settings=oauth_settings)
    http_app = app.create_http_app(admin_db=admin_db)

    transport = ASGITransport(app=http_app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        # 1. Enregistrement dynamique RFC 7591
        reg_resp = await client.post(
            "/oauth/register",
            json={
                "client_name": "Claude Desktop",
                "redirect_uris": ["http://localhost:8080/callback"],
            },
        )
        assert reg_resp.status_code == 201
        reg_data = reg_resp.json()
        registered_client_id = reg_data["client_id"]
        assert registered_client_id.startswith("client_")

        # 2. Autorisation d'un JWT valide pour ce client
        valid_jwt = jwt.encode(
            {
                "token_id": "tok_flow_1",
                "mcp_api_key": MCP_API_KEY,
                "user_id": "usr_david",
                "user_api_key": "pat_david_key_789",
                "exp": int(time.time()) + 3600,
            },
            JWT_SECRET,
            algorithm="HS256",
        )
        auth_resp = await client.post(
            "/oauth/authorize",
            data={
                "client_id": registered_client_id,
                "redirect_uri": "http://localhost:8080/callback",
                "jwt_token": valid_jwt,
            },
            follow_redirects=False,
        )
        assert auth_resp.status_code == 302
        loc = auth_resp.headers["location"]
        auth_code = loc.split("code=")[1].split("&")[0]

        # 3. Échange sur /oauth/token
        token_resp = await client.post(
            "/oauth/token",
            data={
                "grant_type": "authorization_code",
                "code": auth_code,
                "client_id": registered_client_id,
                "redirect_uri": "http://localhost:8080/callback",
            },
        )
        assert token_resp.status_code == 200
        token_json = token_resp.json()
        assert token_json["token_type"] == "Bearer"
        assert token_json["access_token"] == valid_jwt
        assert token_json["expires_in"] > 0

        # 4. Rejet en cas de seconde tentative avec le même code (anti-rejeu)
        replay_resp = await client.post(
            "/oauth/token",
            data={
                "grant_type": "authorization_code",
                "code": auth_code,
                "client_id": registered_client_id,
            },
        )
        assert replay_resp.status_code == 400
        assert replay_resp.json()["error"] == "invalid_grant"
