"""Tests unitaires pour la gestion des codes et clients OAuth 2.0 dans AdminDatabase."""

import base64
import hashlib
import time
from datetime import datetime, timezone, timedelta
import pytest
from melodi_mcp_server.admin.db import AdminDatabase


def test_oauth_db_create_and_consume_code():
    db = AdminDatabase(":memory:")

    code = db.create_oauth_code(
        client_id="chatgpt_client",
        redirect_uri="https://chat.openai.com/aip/callback",
        jwt_token="fake.jwt.token",
        token_id="tok_123",
        user_id="usr_456",
        ttl_seconds=300,
    )
    assert code is not None
    assert len(code) > 20

    # 1. Consommation réussie
    consumed = db.consume_oauth_code(
        code=code,
        client_id="chatgpt_client",
        redirect_uri="https://chat.openai.com/aip/callback",
    )
    assert consumed is not None
    assert consumed["code"] == code
    assert consumed["jwt_token"] == "fake.jwt.token"
    assert consumed["token_id"] == "tok_123"
    assert consumed["user_id"] == "usr_456"

    # 2. Protection anti-rejeu : le code ne peut plus être consommé
    second_attempt = db.consume_oauth_code(
        code=code,
        client_id="chatgpt_client",
        redirect_uri="https://chat.openai.com/aip/callback",
    )
    assert second_attempt is None


def test_oauth_db_pkce_s256_validation():
    db = AdminDatabase(":memory:")

    # Création d'un verifier et challenge S256
    verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")

    code = db.create_oauth_code(
        client_id="claude_client",
        redirect_uri="http://localhost:8000/callback",
        jwt_token="valid.jwt.token",
        code_challenge=challenge,
        code_challenge_method="S256",
    )

    # Rejet avec mauvais verifier
    bad_res = db.consume_oauth_code(
        code=code,
        client_id="claude_client",
        code_verifier="wrong_verifier",
    )
    assert bad_res is None

    # Succès avec bon verifier
    good_res = db.consume_oauth_code(
        code=code,
        client_id="claude_client",
        code_verifier=verifier,
    )
    assert good_res is not None
    assert good_res["jwt_token"] == "valid.jwt.token"


def test_oauth_db_pkce_plain_validation():
    db = AdminDatabase(":memory:")

    verifier = "my-plain-verifier-string"
    code = db.create_oauth_code(
        client_id="simple_client",
        redirect_uri="http://localhost/cb",
        jwt_token="plain.jwt",
        code_challenge=verifier,
        code_challenge_method="plain",
    )

    # Rejet mauvais verifier
    assert db.consume_oauth_code(code=code, client_id="simple_client", code_verifier="bad") is None

    # Succès
    res = db.consume_oauth_code(code=code, client_id="simple_client", code_verifier=verifier)
    assert res is not None
    assert res["jwt_token"] == "plain.jwt"


def test_oauth_db_rejects_revoked_token_id():
    db = AdminDatabase(":memory:")

    code = db.create_oauth_code(
        client_id="chatgpt",
        redirect_uri="https://chat.openai.com/cb",
        jwt_token="some.jwt.token",
        token_id="tok_to_revoke",
        user_id="usr_bob",
    )

    # Révoquer le token avant consommation
    db.revoke_token("tok_to_revoke", user_id="usr_bob", reason="Compte suspendu")

    # Tentative d'échange : rejetée
    assert db.consume_oauth_code(code=code, client_id="chatgpt") is None


def test_oauth_db_client_registration_and_cleanup():
    db = AdminDatabase(":memory:")

    # Enregistrement dynamique
    client_info = db.register_oauth_client(
        client_name="Mon Assistant Custom",
        redirect_uris=["https://ai.example.com/oauth/callback"],
    )
    cid = client_info["client_id"]
    assert cid.startswith("client_")

    fetched = db.get_oauth_client(cid)
    assert fetched is not None
    assert fetched["client_name"] == "Mon Assistant Custom"
    assert "https://ai.example.com/oauth/callback" in fetched["redirect_uris"]

    # Codes expirés
    db.create_oauth_code(
        client_id="expired_client",
        redirect_uri="http://cb",
        jwt_token="expired.jwt",
        ttl_seconds=-10,  # Déjà expiré
    )
    deleted = db.cleanup_expired_oauth_codes()
    assert deleted >= 1
