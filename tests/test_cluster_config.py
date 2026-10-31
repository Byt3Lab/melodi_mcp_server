"""Tests unitaires et d'intégration pour la configuration de scalabilité cluster (PostgreSQL/Redis)."""

import pytest
from starlette.testclient import TestClient

from melodi_mcp_server.admin.db import AdminDatabase
from melodi_mcp_server.mcp_server import MelodiMcpServerApp
from melodi_mcp_server.settings import Settings, mask_connection_url
from melodi_mcp_server.storage.tester import test_cluster_connections as check_cluster_connections


def test_mask_connection_url():
    """Vérifie le masquage sécurisé des identifiants dans les URL de connexion."""
    assert mask_connection_url(None) == ""
    assert mask_connection_url("") == ""
    # PostgreSQL avec mot de passe
    pg_url = "postgresql://myuser:supersecret123@db.prod.internal:5432/melodi_mcp"
    masked_pg = mask_connection_url(pg_url)
    assert "supersecret123" not in masked_pg
    assert "••••••••" in masked_pg
    assert "myuser" in masked_pg
    assert "db.prod.internal:5432/melodi_mcp" in masked_pg

    # Redis avec mot de passe
    redis_url = "redis://:p@ssw0rd!@cache.prod.internal:6379/0"
    masked_redis = mask_connection_url(redis_url)
    assert "p@ssw0rd!" not in masked_redis
    assert "••••••••" in masked_redis


def test_cluster_connections_tester_validation():
    """Vérifie que test_cluster_connections valide les schémas et gère les erreurs sans crash."""
    # 1. URL sans schéma valide
    res = check_cluster_connections("http://localhost:5432/db")
    assert res["success"] is False
    assert "postgresql://" in res["pg_error"]

    # 2. Redis avec schéma invalide
    res_redis = check_cluster_connections(
        "postgresql://user:pass@127.0.0.1:5432/db",
        redis_url="ftp://cache:6379",
    )
    assert res_redis["redis_tested"] is True
    assert res_redis["redis_success"] is False
    assert "redis://" in res_redis["redis_error"]

    # 3. Port inaccessible -> géré proprement sans exception
    res_unreachable = check_cluster_connections(
        "postgresql://user:pass@127.0.0.1:59999/test_db",
        timeout_seconds=0.5,
    )
    assert res_unreachable["success"] is False
    assert res_unreachable["pg_success"] is False
    assert res_unreachable["pg_error"] is not None


def test_admin_database_cluster_property(tmp_path):
    """Vérifie la propriété is_cluster sur AdminDatabase selon le backend."""
    sqlite_db_file = str(tmp_path / "test_admin.db")
    db_standalone = AdminDatabase(sqlite_db_file)
    assert db_standalone.is_cluster is False


@pytest.mark.asyncio
async def test_admin_cluster_config_api_flow(mock_settings):
    """Vérifie le cycle de lecture, test et enregistrement des paramètres de scalabilité via l'API Admin."""
    app = MelodiMcpServerApp(settings=mock_settings)
    memory_db = AdminDatabase(":memory:")
    memory_db.setup_first_admin("superadmin", "motdepasse123")
    session_id = memory_db.create_session(1)

    http_app = app.create_http_app(admin_db=memory_db)

    with TestClient(http_app, cookies={"admin_session": session_id}) as client:
        # 1. GET /admin/api/config initial
        resp = client.get("/admin/api/config")
        assert resp.status_code == 200
        cfg = resp.json()
        assert cfg["cluster_mode"] is False
        assert cfg["is_running_cluster"] is False
        assert cfg["has_database_url"] is False
        assert cfg["mcp_stateless_http"] is False

        # 2. POST /admin/api/config/test-cluster avec URL invalide
        resp_test_err = client.post(
            "/admin/api/config/test-cluster",
            json={"database_url": "mysql://root@localhost/db"},
        )
        assert resp_test_err.status_code == 400
        assert resp_test_err.json()["success"] is False

        # 3. POST /admin/api/config pour activer le mode cluster et configurer les URL
        resp_save = client.post(
            "/admin/api/config",
            json={
                "cluster_mode": True,
                "database_url": "postgresql://appuser:secr3tp@ss@db.internal:5432/mcp_prod",
                "redis_url": "redis://:cachepass@redis.internal:6379/0",
                "mcp_stateless_http": True,
            },
        )
        assert resp_save.status_code == 200
        save_data = resp_save.json()
        assert save_data["success"] is True
        assert save_data["requires_restart"] is True
        assert "redémarrer" in save_data["message"]
        assert save_data["cluster_mode"] is True
        assert save_data["mcp_stateless_http"] is True

        # 4. GET /admin/api/config vérifie le masquage des mots de passe
        resp_updated = client.get("/admin/api/config")
        assert resp_updated.status_code == 200
        up_cfg = resp_updated.json()
        assert up_cfg["cluster_mode"] is True
        assert up_cfg["has_database_url"] is True
        assert "secr3tp@ss" not in up_cfg["database_url_preview"]
        assert "••••••••" in up_cfg["database_url_preview"]
        assert up_cfg["has_redis_url"] is True
        assert "cachepass" not in up_cfg["redis_url_preview"]
        assert up_cfg["mcp_stateless_http"] is True
