"""Implémentation du stockage persistant PostgreSQL pour le mode cluster de melodi_mcp_server."""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import secrets
import threading
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

import psycopg
from psycopg.rows import dict_row

logger = logging.getLogger(__name__)


class PostgresStorageBackend:
    """Gestionnaire de persistance PostgreSQL avec pool de connexions pour cluster multi-instances."""

    def __init__(self, database_url: str, min_size: int = 1, max_size: int = 10):
        self.database_url = database_url
        self._lock = threading.Lock()
        self._pool = None
        self._revoked_tokens_cache: set[str] = set()

        try:
            from psycopg_pool import ConnectionPool
            self._pool = ConnectionPool(
                conninfo=self.database_url,
                min_size=min_size,
                max_size=max_size,
                timeout=10.0,
                kwargs={"row_factory": dict_row},
            )
        except Exception as e:
            logger.warning("Impossible d'initialiser le pool psycopg_pool, fallback sur connexions directes : %s", e)
            self._pool = None

        self.init_db()

    @contextmanager
    def _get_connection(self):
        """Fournit une connexion PostgreSQL depuis le pool ou crée une connexion éphémère."""
        if self._pool is not None:
            with self._pool.connection() as conn:
                yield conn
        else:
            with psycopg.connect(self.database_url, row_factory=dict_row) as conn:
                yield conn

    def close(self) -> None:
        """Ferme proprement le pool de connexions s'il existe."""
        if self._pool is not None:
            try:
                self._pool.close()
            except Exception:
                pass

    def init_db(self) -> None:
        """Initialise le schéma PostgreSQL si les tables n'existent pas encore."""
        with self._lock, self._get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS admin_users (
                        id SERIAL PRIMARY KEY,
                        username VARCHAR(150) UNIQUE NOT NULL,
                        password_hash TEXT NOT NULL,
                        salt TEXT NOT NULL,
                        role VARCHAR(50) NOT NULL DEFAULT 'admin',
                        is_active INTEGER NOT NULL DEFAULT 1,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS admin_sessions (
                        session_id VARCHAR(128) PRIMARY KEY,
                        user_id INTEGER NOT NULL REFERENCES admin_users(id) ON DELETE CASCADE,
                        created_at TEXT NOT NULL,
                        expires_at TEXT NOT NULL
                    );
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS system_config (
                        key VARCHAR(128) PRIMARY KEY,
                        value TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS revoked_tokens (
                        token_id VARCHAR(256) PRIMARY KEY,
                        user_id TEXT,
                        revoked_at TEXT NOT NULL,
                        expires_at TEXT,
                        reason TEXT
                    );
                    """
                )
                cur.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_pg_revoked_tokens_revoked_at ON revoked_tokens(revoked_at);
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS oauth_codes (
                        code VARCHAR(128) PRIMARY KEY,
                        client_id VARCHAR(256) NOT NULL,
                        redirect_uri TEXT NOT NULL,
                        jwt_token TEXT NOT NULL,
                        token_id VARCHAR(256),
                        user_id TEXT,
                        code_challenge TEXT,
                        code_challenge_method VARCHAR(32),
                        expires_at TEXT NOT NULL,
                        used INTEGER NOT NULL DEFAULT 0
                    );
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS oauth_clients (
                        client_id VARCHAR(128) PRIMARY KEY,
                        client_secret TEXT,
                        client_name TEXT,
                        redirect_uris TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    );
                    """
                )
                cur.execute("SELECT token_id FROM revoked_tokens;")
                rows = cur.fetchall()
                self._revoked_tokens_cache = {r["token_id"] for r in rows}
            conn.commit()

    @staticmethod
    def hash_password(password: str, salt: Optional[str] = None) -> Tuple[str, str]:
        """Génère un hash fort PBKDF2-HMAC-SHA256 avec sel aléatoire (100 000 itérations)."""
        if not salt:
            salt = secrets.token_hex(16)
        pw_hash = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt.encode("utf-8"),
            100_000,
        ).hex()
        return pw_hash, salt

    @staticmethod
    def verify_password(password: str, password_hash: str, salt: str) -> bool:
        """Compare en temps constant le mot de passe fourni avec le hash stocké."""
        computed_hash, _ = PostgresStorageBackend.hash_password(password, salt)
        return secrets.compare_digest(computed_hash, password_hash)

    def has_users(self) -> bool:
        """Vérifie si au moins un compte administrateur est déjà enregistré."""
        with self._lock, self._get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) AS total FROM admin_users WHERE is_active = 1;")
                row = cur.fetchone()
                return (row["total"] if row else 0) > 0

    def count_active_admins(self) -> int:
        """Compte le nombre d'administrateurs actifs."""
        with self._lock, self._get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) AS total FROM admin_users WHERE role = 'admin' AND is_active = 1;"
                )
                row = cur.fetchone()
                return row["total"] if row else 0

    def setup_first_admin(self, username: str, password: str) -> Dict[str, Any]:
        """Crée le tout premier compte administrateur lors de l'amorçage."""
        clean_user = username.strip()
        if not clean_user or len(clean_user) < 3:
            raise ValueError("Le nom d'utilisateur doit contenir au moins 3 caractères.")
        if not password or len(password) < 4:
            raise ValueError("Le mot de passe doit contenir au moins 4 caractères.")

        with self._lock, self._get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) AS total FROM admin_users;")
                row = cur.fetchone()
                if row and row["total"] > 0:
                    raise RuntimeError("L'initialisation a déjà été effectuée. Des comptes existent déjà.")

                now = datetime.now(timezone.utc).isoformat()
                pw_hash, salt = self.hash_password(password)

                cur.execute(
                    """
                    INSERT INTO admin_users (username, password_hash, salt, role, is_active, created_at, updated_at)
                    VALUES (%s, %s, %s, 'admin', 1, %s, %s)
                    RETURNING id;
                    """,
                    (clean_user, pw_hash, salt, now, now),
                )
                user_id = cur.fetchone()["id"]
            conn.commit()
            logger.info("Premier compte administrateur PostgreSQL initialisé avec succès : '%s'", clean_user)
            return {
                "id": user_id,
                "username": clean_user,
                "role": "admin",
                "is_active": True,
                "created_at": now,
            }

    def authenticate(self, username: str, password: str) -> Optional[Dict[str, Any]]:
        """Valide les identifiants d'un utilisateur et retourne son profil si valide."""
        clean_user = username.strip()
        if not clean_user or not password:
            return None

        with self._lock, self._get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM admin_users WHERE LOWER(username) = LOWER(%s) AND is_active = 1;",
                    (clean_user,),
                )
                row = cur.fetchone()
                if not row:
                    return None

                if self.verify_password(password, row["password_hash"], row["salt"]):
                    return {
                        "id": row["id"],
                        "username": row["username"],
                        "role": row["role"],
                        "is_active": bool(row["is_active"]),
                        "created_at": row["created_at"],
                    }
        return None

    def create_session(self, user_id: int, ttl_hours: int = 24) -> str:
        """Génère et persiste une session pour un utilisateur avec date d'expiration."""
        session_id = secrets.token_urlsafe(32)
        now = datetime.now(timezone.utc)
        expires = now + timedelta(hours=ttl_hours)

        with self._lock, self._get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO admin_sessions (session_id, user_id, created_at, expires_at)
                    VALUES (%s, %s, %s, %s);
                    """,
                    (session_id, user_id, now.isoformat(), expires.isoformat()),
                )
            conn.commit()
        return session_id

    def get_user_by_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Récupère l'utilisateur associé à une session active et non expirée."""
        if not session_id:
            return None

        now_iso = datetime.now(timezone.utc).isoformat()
        with self._lock, self._get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT u.id, u.username, u.role, u.is_active, u.created_at, s.expires_at
                    FROM admin_sessions s
                    JOIN admin_users u ON s.user_id = u.id
                    WHERE s.session_id = %s AND s.expires_at > %s AND u.is_active = 1;
                    """,
                    (session_id.strip(), now_iso),
                )
                row = cur.fetchone()
                if not row:
                    return None
                return {
                    "id": row["id"],
                    "username": row["username"],
                    "role": row["role"],
                    "is_active": bool(row["is_active"]),
                    "created_at": row["created_at"],
                }

    def delete_session(self, session_id: str) -> bool:
        """Supprime une session administrateur."""
        if not session_id:
            return False
        with self._lock, self._get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM admin_sessions WHERE session_id = %s;", (session_id.strip(),))
                rc = cur.rowcount
            conn.commit()
            return rc > 0

    def cleanup_expired_sessions(self) -> int:
        """Supprime les sessions dont la date d'expiration est dépassée."""
        now_iso = datetime.now(timezone.utc).isoformat()
        with self._lock, self._get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM admin_sessions WHERE expires_at <= %s;", (now_iso,))
                rc = cur.rowcount
            conn.commit()
            return rc

    def list_users(self) -> List[Dict[str, Any]]:
        """Liste tous les comptes administrateurs enregistrés."""
        with self._lock, self._get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id, username, role, is_active, created_at, updated_at FROM admin_users ORDER BY id ASC;")
                rows = cur.fetchall()
                return [
                    {
                        "id": r["id"],
                        "username": r["username"],
                        "role": r["role"],
                        "is_active": bool(r["is_active"]),
                        "created_at": r["created_at"],
                        "updated_at": r["updated_at"],
                    }
                    for r in rows
                ]

    def create_user(self, username: str, password: str, role: str = "admin") -> Dict[str, Any]:
        """Crée un nouvel utilisateur administrateur."""
        clean_user = username.strip()
        if not clean_user or len(clean_user) < 3:
            raise ValueError("Le nom d'utilisateur doit contenir au moins 3 caractères.")
        if not password or len(password) < 4:
            raise ValueError("Le mot de passe doit contenir au moins 4 caractères.")
        if role not in ("admin", "viewer"):
            raise ValueError("Rôle invalide (doit être 'admin' ou 'viewer').")

        pw_hash, salt = self.hash_password(password)
        now = datetime.now(timezone.utc).isoformat()

        with self._lock, self._get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO admin_users (username, password_hash, salt, role, is_active, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, 1, %s, %s)
                    RETURNING id;
                    """,
                    (clean_user, pw_hash, salt, role, now, now),
                )
                user_id = cur.fetchone()["id"]
            conn.commit()

        return {
            "id": user_id,
            "username": clean_user,
            "role": role,
            "is_active": True,
            "created_at": now,
        }

    def update_password(self, user_id: int, new_password: str) -> bool:
        """Modifie le mot de passe d'un utilisateur et révoque ses sessions en cours."""
        if not new_password or len(new_password) < 4:
            raise ValueError("Le mot de passe doit contenir au moins 4 caractères.")

        pw_hash, salt = self.hash_password(new_password)
        now = datetime.now(timezone.utc).isoformat()

        with self._lock, self._get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE admin_users
                    SET password_hash = %s, salt = %s, updated_at = %s
                    WHERE id = %s;
                    """,
                    (pw_hash, salt, now, user_id),
                )
                if cur.rowcount == 0:
                    return False
                # Révoquer les sessions actives
                cur.execute("DELETE FROM admin_sessions WHERE user_id = %s;", (user_id,))
            conn.commit()
            return True

    def toggle_user_status(self, user_id: int, current_user_id: Optional[int] = None) -> bool:
        """Active ou désactive un utilisateur, avec protection du dernier admin."""
        if current_user_id is not None and user_id == current_user_id:
            raise ValueError("Vous ne pouvez pas désactiver votre propre compte actuellement connecté.")

        with self._lock, self._get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id, role, is_active FROM admin_users WHERE id = %s;", (user_id,))
                user = cur.fetchone()
                if not user:
                    raise ValueError("Utilisateur introuvable.")

                new_status = 0 if user["is_active"] else 1

                # Si on tente de désactiver un admin, s'assurer qu'il en reste au moins un autre actif
                if new_status == 0 and user["role"] == "admin":
                    cur.execute(
                        "SELECT COUNT(*) AS total FROM admin_users WHERE role = 'admin' AND is_active = 1;"
                    )
                    cur_admins = cur.fetchone()["total"]
                    if cur_admins <= 1:
                        raise ValueError("Impossible de désactiver le dernier administrateur actif.")

                now = datetime.now(timezone.utc).isoformat()
                cur.execute(
                    "UPDATE admin_users SET is_active = %s, updated_at = %s WHERE id = %s;",
                    (new_status, now, user_id),
                )
                if new_status == 0:
                    cur.execute("DELETE FROM admin_sessions WHERE user_id = %s;", (user_id,))
            conn.commit()
            return True

    def delete_user(self, user_id: int, current_user_id: Optional[int] = None) -> bool:
        """Supprime définitivement un utilisateur, avec vérifications de sécurité."""
        if current_user_id is not None and user_id == current_user_id:
            raise ValueError("Vous ne pouvez pas supprimer votre propre compte actuellement connecté.")

        with self._lock, self._get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id, role, is_active FROM admin_users WHERE id = %s;", (user_id,))
                user = cur.fetchone()
                if not user:
                    raise ValueError("Utilisateur introuvable.")

                if user["role"] == "admin" and user["is_active"]:
                    cur.execute(
                        "SELECT COUNT(*) AS total FROM admin_users WHERE role = 'admin' AND is_active = 1;"
                    )
                    cur_admins = cur.fetchone()["total"]
                    if cur_admins <= 1:
                        raise ValueError("Impossible de supprimer le dernier administrateur actif.")

                cur.execute("DELETE FROM admin_users WHERE id = %s;", (user_id,))
                rc = cur.rowcount
            conn.commit()
            return rc > 0

    def get_config(self, key: str, default: Optional[str] = None) -> Optional[str]:
        """Récupère une valeur de configuration persistée."""
        with self._lock, self._get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT value FROM system_config WHERE key = %s;", (key,))
                row = cur.fetchone()
                if row:
                    return row["value"]
                return default

    def set_config(self, key: str, value: str) -> None:
        """Enregistre ou met à jour une valeur de configuration."""
        now = datetime.now(timezone.utc).isoformat()
        with self._lock, self._get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO system_config (key, value, updated_at)
                    VALUES (%s, %s, %s)
                    ON CONFLICT(key) DO UPDATE SET
                        value = excluded.value,
                        updated_at = excluded.updated_at;
                    """,
                    (key, str(value), now),
                )
            conn.commit()

    def get_all_configs(self) -> Dict[str, str]:
        """Récupère l'ensemble des clés de configuration persistées."""
        with self._lock, self._get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT key, value FROM system_config;")
                rows = cur.fetchall()
                return {r["key"]: r["value"] for r in rows}

    def revoke_token(
        self,
        token_id: str,
        user_id: Optional[str] = None,
        expires_at: Optional[str] = None,
        reason: Optional[str] = None,
    ) -> None:
        """Révoque un token JWT en enregistrant son token_id."""
        clean_id = token_id.strip()
        now = datetime.now(timezone.utc).isoformat()

        with self._lock, self._get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO revoked_tokens (token_id, user_id, revoked_at, expires_at, reason)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT(token_id) DO UPDATE SET
                        revoked_at = excluded.revoked_at,
                        reason = excluded.reason;
                    """,
                    (clean_id, str(user_id) if user_id else None, now, expires_at, reason),
                )
            conn.commit()
            self._revoked_tokens_cache.add(clean_id)
            logger.info("Token révoqué dans PostgreSQL : '%s' (user_id: %s)", clean_id, user_id)

    def is_token_revoked(self, token_id: str) -> bool:
        """Vérifie si le token_id est révoqué."""
        clean_id = token_id.strip()
        if clean_id in self._revoked_tokens_cache:
            return True

        with self._lock, self._get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT token_id FROM revoked_tokens WHERE token_id = %s;", (clean_id,))
                row = cur.fetchone()
                if row:
                    self._revoked_tokens_cache.add(clean_id)
                    return True
        return False

    def list_revoked_tokens(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Liste les tokens révoqués récents."""
        with self._lock, self._get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT token_id, user_id, revoked_at, expires_at, reason
                    FROM revoked_tokens
                    ORDER BY revoked_at DESC
                    LIMIT %s;
                    """,
                    (limit,),
                )
                rows = cur.fetchall()
                return [dict(r) for r in rows]

    def delete_revoked_token(self, token_id: str) -> bool:
        """Supprime un token de la liste de révocation (le réactive)."""
        clean_id = token_id.strip()
        with self._lock, self._get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM revoked_tokens WHERE token_id = %s;", (clean_id,))
                rc = cur.rowcount
            conn.commit()
            self._revoked_tokens_cache.discard(clean_id)
            return rc > 0

    def cleanup_expired_revoked_tokens(self, max_age_hours: int = 72) -> int:
        """Purge les entrées de révocation trop anciennes (> 72h)."""
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=max_age_hours)).isoformat()
        with self._lock, self._get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    DELETE FROM revoked_tokens
                    WHERE (expires_at IS NOT NULL AND expires_at < %s)
                       OR (expires_at IS NULL AND revoked_at < %s);
                    """,
                    (cutoff, cutoff),
                )
                deleted = cur.rowcount
                cur.execute("SELECT token_id FROM revoked_tokens;")
                rows = cur.fetchall()
                self._revoked_tokens_cache = {r["token_id"] for r in rows}
            conn.commit()
            return deleted

    def create_oauth_code(
        self,
        client_id: str,
        redirect_uri: str,
        jwt_token: str,
        token_id: Optional[str] = None,
        user_id: Optional[str] = None,
        code_challenge: Optional[str] = None,
        code_challenge_method: Optional[str] = None,
        ttl_seconds: int = 300,
    ) -> str:
        """Génère et stocke un code d'autorisation éphémère OAuth 2.0."""
        code = secrets.token_urlsafe(32)
        now = datetime.now(timezone.utc)
        expires = (now + timedelta(seconds=ttl_seconds)).isoformat()

        with self._lock, self._get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO oauth_codes (
                        code, client_id, redirect_uri, jwt_token, token_id, user_id,
                        code_challenge, code_challenge_method, expires_at, used
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 0);
                    """,
                    (
                        code,
                        client_id.strip(),
                        redirect_uri.strip(),
                        jwt_token.strip(),
                        str(token_id) if token_id else None,
                        str(user_id) if user_id else None,
                        code_challenge,
                        code_challenge_method,
                        expires,
                    ),
                )
            conn.commit()
        return code

    def consume_oauth_code(
        self,
        code: str,
        client_id: str,
        redirect_uri: Optional[str] = None,
        code_verifier: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Valide et consomme un code d'autorisation OAuth 2.0 (usage unique anti-rejeu)."""
        if not code or not client_id:
            return None

        now_iso = datetime.now(timezone.utc).isoformat()
        with self._lock, self._get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM oauth_codes WHERE code = %s;",
                    (code.strip(),),
                )
                row = cur.fetchone()
                if not row:
                    return None

                if row["used"]:
                    logger.warning("Tentative de réutilisation d'un code d'autorisation OAuth : %s", code)
                    return None

                if row["expires_at"] <= now_iso:
                    logger.warning("Code d'autorisation OAuth expiré : %s", code)
                    return None

                if row["client_id"] != client_id:
                    logger.warning(
                        "client_id non concordant pour le code OAuth (attendu: %s, reçu: %s)",
                        row["client_id"],
                        client_id,
                    )
                    return None

                if redirect_uri and row["redirect_uri"] != redirect_uri:
                    logger.warning(
                        "redirect_uri non concordant pour le code OAuth (attendu: %s, reçu: %s)",
                        row["redirect_uri"],
                        redirect_uri,
                    )
                    return None

                challenge = row["code_challenge"]
                if challenge:
                    if not code_verifier:
                        logger.warning("code_verifier manquant alors qu'un code_challenge était enregistré.")
                        return None
                    method = row["code_challenge_method"] or "S256"
                    if method == "S256":
                        digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
                        computed = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
                        if not secrets.compare_digest(computed, challenge.rstrip("=")):
                            logger.warning("Échec de la validation PKCE S256.")
                            return None
                    elif method == "plain":
                        if not secrets.compare_digest(code_verifier, challenge):
                            logger.warning("Échec de la validation PKCE plain.")
                            return None

                token_id = row["token_id"]
                if token_id and self.is_token_revoked(token_id):
                    logger.warning("Le token_id associé au code OAuth est déjà révoqué : %s", token_id)
                    return None

                cur.execute("UPDATE oauth_codes SET used = 1 WHERE code = %s;", (code.strip(),))
            conn.commit()

            return {
                "code": row["code"],
                "client_id": row["client_id"],
                "redirect_uri": row["redirect_uri"],
                "jwt_token": row["jwt_token"],
                "token_id": row["token_id"],
                "user_id": row["user_id"],
            }

    def register_oauth_client(
        self,
        client_name: str,
        redirect_uris: List[str],
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Enregistre dynamiquement un client OAuth 2.0 (RFC 7591)."""
        cid = client_id or f"client_{secrets.token_hex(8)}"
        secret = client_secret or secrets.token_urlsafe(32)
        uris_str = json.dumps(redirect_uris)
        now = datetime.now(timezone.utc).isoformat()

        with self._lock, self._get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO oauth_clients (client_id, client_secret, client_name, redirect_uris, created_at)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT(client_id) DO UPDATE SET
                        client_secret = excluded.client_secret,
                        client_name = excluded.client_name,
                        redirect_uris = excluded.redirect_uris;
                    """,
                    (cid, secret, client_name, uris_str, now),
                )
            conn.commit()

        return {
            "client_id": cid,
            "client_secret": secret,
            "client_name": client_name,
            "redirect_uris": redirect_uris,
        }

    def get_oauth_client(self, client_id: str) -> Optional[Dict[str, Any]]:
        """Récupère les informations d'un client OAuth enregistré."""
        with self._lock, self._get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM oauth_clients WHERE client_id = %s;", (client_id.strip(),))
                row = cur.fetchone()
                if not row:
                    return None
                return {
                    "client_id": row["client_id"],
                    "client_secret": row["client_secret"],
                    "client_name": row["client_name"],
                    "redirect_uris": json.loads(row["redirect_uris"]),
                    "created_at": row["created_at"],
                }

    def cleanup_expired_oauth_codes(self) -> int:
        """Purge les codes OAuth expirés ou déjà utilisés."""
        now_str = datetime.now(timezone.utc).isoformat()
        with self._lock, self._get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM oauth_codes WHERE used = 1 OR expires_at <= %s;",
                    (now_str,),
                )
                deleted = cur.rowcount
            conn.commit()
            return deleted
