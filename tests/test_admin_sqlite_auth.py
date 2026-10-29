"""Tests unitaires et d'intégration pour l'authentification SQLite et la gestion multi-utilisateurs."""

import pytest
import respx
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from melodi_mcp_server.admin.db import AdminDatabase
from melodi_mcp_server.mcp_server import MelodiMcpServerApp
from melodi_mcp_server.settings import Settings


@pytest.fixture
def memory_db():
    """Fournit une base SQLite isolée en mémoire."""
    return AdminDatabase(":memory:")


def test_sqlite_db_init_and_setup(memory_db):
    """Vérifie l'état initial, le hachage et l'amorçage du premier administrateur."""
    assert memory_db.has_users() is False
    assert memory_db.count_active_admins() == 0

    # Validation des contraintes de saisie
    with pytest.raises(ValueError, match="au moins 3 caractères"):
        memory_db.setup_first_admin("a", "validpassword")
    with pytest.raises(ValueError, match="au moins 4 caractères"):
        memory_db.setup_first_admin("admin", "123")

    # Amorçage réussi
    admin = memory_db.setup_first_admin("superadmin", "motdepasse123")
    assert admin["username"] == "superadmin"
    assert admin["role"] == "admin"
    assert admin["is_active"] is True
    assert memory_db.has_users() is True
    assert memory_db.count_active_admins() == 1

    # Impossible de relancer l'amorçage initial
    with pytest.raises(RuntimeError, match="déjà été effectuée"):
        memory_db.setup_first_admin("autre", "motdepasse123")


def test_sqlite_authentication_and_sessions(memory_db):
    """Vérifie l'authentification et le cycle de vie des sessions."""
    user = memory_db.setup_first_admin("admin", "secret123")

    # Échec d'authentification
    assert memory_db.authenticate("admin", "mauvaismdp") is None
    assert memory_db.authenticate("inconnu", "secret123") is None

    # Succès d'authentification
    auth_user = memory_db.authenticate("admin", "secret123")
    assert auth_user is not None
    assert auth_user["id"] == user["id"]

    # Création et validation de session
    session_id = memory_db.create_session(user["id"], ttl_hours=2)
    session_user = memory_db.get_user_by_session(session_id)
    assert session_user is not None
    assert session_user["username"] == "admin"

    # Déconnexion / suppression de session
    assert memory_db.delete_session(session_id) is True
    assert memory_db.get_user_by_session(session_id) is None


def test_sqlite_user_management_crud_and_guards(memory_db):
    """Vérifie les opérations CRUD et les garde-fous de sécurité sur les utilisateurs."""
    admin1 = memory_db.setup_first_admin("admin1", "pass1234")

    # Création d'un second utilisateur
    user2 = memory_db.create_user("operateur", "pass5678", role="viewer")
    assert user2["username"] == "operateur"
    assert user2["role"] == "viewer"
    assert len(memory_db.list_users()) == 2

    # Nom d'utilisateur dupliqué
    with pytest.raises(ValueError, match="déjà utilisé"):
        memory_db.create_user("operateur", "pass5678")

    # Modification de mot de passe
    memory_db.update_password(user2["id"], "nouveaupassword")
    assert memory_db.authenticate("operateur", "nouveaupassword") is not None

    # Garde-fous : interdiction de se désactiver ou supprimer soi-même
    with pytest.raises(ValueError, match="votre propre compte"):
        memory_db.toggle_user_status(admin1["id"], current_user_id=admin1["id"])
    with pytest.raises(ValueError, match="votre propre compte"):
        memory_db.delete_user(admin1["id"], current_user_id=admin1["id"])

    # Désactivation et suppression de l'utilisateur secondaire
    memory_db.toggle_user_status(user2["id"], current_user_id=admin1["id"])
    assert memory_db.authenticate("operateur", "nouveaupassword") is None

    memory_db.delete_user(user2["id"], current_user_id=admin1["id"])
    assert len(memory_db.list_users()) == 1

    # Garde-fou : interdiction de supprimer le dernier administrateur actif
    with pytest.raises(ValueError, match="dernier administrateur actif"):
        memory_db.delete_user(admin1["id"], current_user_id=None)


@pytest.mark.asyncio
@respx.mock
async def test_admin_onboarding_and_login_flow():
    """Test d'intégration du flux complet : Amorçage initial -> Connexion nominale -> Gestion."""
    respx.get("http://test-melodi:8000/api/melodi_mcp/internal/tools").respond(
        status_code=200,
        json={"success": True, "data": {"tools": []}},
    )
    respx.get("http://test-melodi:8000/api/melodi_mcp/internal/health").respond(
        status_code=200,
        json={"status": "healthy"},
    )

    settings = Settings(
        melodi_base_url="http://test-melodi:8000",
        admin_api_key=SecretStr("custom_setup_token"),
    )
    mem_db = AdminDatabase(":memory:")

    app = MelodiMcpServerApp(settings=settings)
    http_app = app.create_http_app(admin_db=mem_db)

    transport = ASGITransport(app=http_app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        # 1. Avant configuration : /admin affiche la page de Setup (amorçage)
        res_setup_page = await client.get("/admin")
        assert res_setup_page.status_code == 200
        assert "Configuration Initiale" in res_setup_page.text

        # 2. Accès aux APIs sans être configuré -> 401
        res_unauth = await client.get("/admin/api/status")
        assert res_unauth.status_code == 401

        # 3. Mauvaise clé d'amorçage
        res_bad_setup = await client.post(
            "/admin/api/setup",
            json={"setup_key": "wrong_key", "username": "admin", "password": "securepassword"},
        )
        assert res_bad_setup.status_code == 401

        # 4. Amorçage réussi avec la bonne clé
        res_good_setup = await client.post(
            "/admin/api/setup",
            json={"setup_key": "custom_setup_token", "username": "boss", "password": "securepassword"},
        )
        assert res_good_setup.status_code == 200
        data = res_good_setup.json()
        assert data["success"] is True
        assert data["user"]["username"] == "boss"
        assert "admin_session" in res_good_setup.cookies

        # 5. Deuxième tentative d'amorçage rejetée (le setup est désormais verrouillé)
        res_repeat_setup = await client.post(
            "/admin/api/setup",
            json={"setup_key": "custom_setup_token", "username": "autre", "password": "securepassword"},
        )
        assert res_repeat_setup.status_code == 400

        # 6. Maintenant configuré : accès avec cookie de session au Dashboard
        session_cookie = res_good_setup.cookies["admin_session"]
        res_dash = await client.get("/admin", cookies={"admin_session": session_cookie})
        assert res_dash.status_code == 200
        assert "MELODI MCP Server" in res_dash.text
        assert "Catalogue des Outils Découverts" in res_dash.text

        # 7. Endpoint /admin/api/me
        res_me = await client.get("/admin/api/me", cookies={"admin_session": session_cookie})
        assert res_me.status_code == 200
        assert res_me.json()["user"]["username"] == "boss"

        # 8. Création d'un second utilisateur par l'admin
        res_create_user = await client.post(
            "/admin/api/users",
            json={"username": "alice", "password": "alicepass123", "role": "viewer"},
            cookies={"admin_session": session_cookie},
        )
        assert res_create_user.status_code == 201

        # 9. Déconnexion
        res_logout = await client.post("/admin/api/logout", cookies={"admin_session": session_cookie})
        assert res_logout.status_code == 200

        # 10. Visite sans cookie -> renvoie la page de Login standard
        res_login_page = await client.get("/admin")
        assert res_login_page.status_code == 200
        assert "Connexion" in res_login_page.text

        # 11. Connexion avec le nouvel utilisateur créé (alice)
        res_alice_login = await client.post(
            "/admin/api/login",
            json={"username": "alice", "password": "alicepass123"},
        )
        assert res_alice_login.status_code == 200
        alice_cookie = res_alice_login.cookies["admin_session"]

        # Alice est "viewer" : elle ne peut pas créer d'utilisateurs (403 Forbidden)
        res_forbidden = await client.post(
            "/admin/api/users",
            json={"username": "bob", "password": "bobpass123"},
            cookies={"admin_session": alice_cookie},
        )
        assert res_forbidden.status_code == 403
