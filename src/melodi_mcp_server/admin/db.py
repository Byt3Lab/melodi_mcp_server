"""Gestion de la base de données SQLite pour l'authentification et les utilisateurs de la console admin."""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import secrets
import sqlite3
import threading
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class SQLiteStorageBackend:
    """Gestionnaire de persistance SQLite pour les utilisateurs administrateurs et sessions (mode autonome)."""

    def __init__(self, db_path: str = "data/admin.db"):
        self.db_path = db_path
        self._lock = threading.Lock()
        self._mem_conn: Optional[sqlite3.Connection] = None
        self._revoked_tokens_cache: set[str] = set()

        # Si base en mémoire, on garde une connexion persistante
        if db_path == ":memory:":
            self._mem_conn = sqlite3.connect(":memory:", check_same_thread=False, timeout=10.0)
            self._mem_conn.row_factory = sqlite3.Row
            self._mem_conn.execute("PRAGMA foreign_keys = ON;")
        else:
            # Création automatique du répertoire parent si nécessaire
            dir_name = os.path.dirname(db_path)
            if dir_name:
                os.makedirs(dir_name, exist_ok=True)

        self.init_db()

    def close(self) -> None:
        """Ferme la connexion si en mémoire."""
        if self._mem_conn is not None:
            try:
                self._mem_conn.close()
            except Exception:
                pass

    def _get_connection(self) -> sqlite3.Connection:
        """Crée ou réutilise une connexion SQLite avec support des clés étrangères et factory Row."""
        if self._mem_conn is not None:
            return self._mem_conn
        conn = sqlite3.connect(self.db_path, check_same_thread=False, timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        return conn

    def init_db(self) -> None:
        """Initialise le schéma de base de données (tables des utilisateurs et sessions)."""
        with self._lock, self._get_connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS admin_users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT UNIQUE NOT NULL COLLATE NOCASE,
                    password_hash TEXT NOT NULL,
                    salt TEXT NOT NULL,
                    role TEXT NOT NULL DEFAULT 'admin',
                    is_active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS admin_sessions (
                    session_id TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES admin_users(id) ON DELETE CASCADE,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL
                );
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS system_config (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS revoked_tokens (
                    token_id TEXT PRIMARY KEY,
                    user_id TEXT,
                    revoked_at TEXT NOT NULL,
                    expires_at TEXT,
                    reason TEXT
                );
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_revoked_tokens_revoked_at ON revoked_tokens(revoked_at);
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS oauth_codes (
                    code TEXT PRIMARY KEY,
                    client_id TEXT NOT NULL,
                    redirect_uri TEXT NOT NULL,
                    jwt_token TEXT NOT NULL,
                    token_id TEXT,
                    user_id TEXT,
                    code_challenge TEXT,
                    code_challenge_method TEXT,
                    expires_at TEXT NOT NULL,
                    used INTEGER NOT NULL DEFAULT 0
                );
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS oauth_clients (
                    client_id TEXT PRIMARY KEY,
                    client_secret TEXT,
                    client_name TEXT,
                    redirect_uris TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )
            cursor = conn.execute("SELECT token_id FROM revoked_tokens;")
            self._revoked_tokens_cache = {row["token_id"] for row in cursor.fetchall()}
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
            iterations=100_000,
        ).hex()
        return pw_hash, salt

    @staticmethod
    def verify_password(password: str, password_hash: str, salt: str) -> bool:
        """Compare un mot de passe en temps constant pour éviter les attaques temporelles."""
        computed_hash, _ = AdminDatabase.hash_password(password, salt)
        return secrets.compare_digest(computed_hash, password_hash)

    def has_users(self) -> bool:
        """Vérifie si au moins un compte administrateur est déjà enregistré."""
        with self._lock, self._get_connection() as conn:
            cursor = conn.execute("SELECT COUNT(*) AS total FROM admin_users WHERE is_active = 1;")
            row = cursor.fetchone()
            return (row["total"] if row else 0) > 0

    def count_active_admins(self) -> int:
        """Compte le nombre d'administrateurs actifs."""
        with self._lock, self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT COUNT(*) AS total FROM admin_users WHERE role = 'admin' AND is_active = 1;"
            )
            row = cursor.fetchone()
            return row["total"] if row else 0

    def setup_first_admin(self, username: str, password: str) -> Dict[str, Any]:
        """Crée le tout premier compte administrateur lors de l'amorçage.

        Échoue si au moins un utilisateur existe déjà.
        """
        clean_user = username.strip()
        if not clean_user or len(clean_user) < 3:
            raise ValueError("Le nom d'utilisateur doit contenir au moins 3 caractères.")
        if not password or len(password) < 4:
            raise ValueError("Le mot de passe doit contenir au moins 4 caractères.")

        with self._lock, self._get_connection() as conn:
            cursor = conn.execute("SELECT COUNT(*) AS total FROM admin_users;")
            if cursor.fetchone()["total"] > 0:
                raise RuntimeError("L'initialisation a déjà été effectuée. Des comptes existent déjà.")

            now = datetime.now(timezone.utc).isoformat()
            pw_hash, salt = self.hash_password(password)

            cursor = conn.execute(
                """
                INSERT INTO admin_users (username, password_hash, salt, role, is_active, created_at, updated_at)
                VALUES (?, ?, ?, 'admin', 1, ?, ?);
                """,
                (clean_user, pw_hash, salt, now, now),
            )
            conn.commit()
            user_id = cursor.lastrowid
            logger.info("Premier compte administrateur initialisé avec succès : '%s'", clean_user)
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
            cursor = conn.execute(
                "SELECT * FROM admin_users WHERE username = ? AND is_active = 1;",
                (clean_user,),
            )
            row = cursor.fetchone()
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
            conn.execute(
                """
                INSERT INTO admin_sessions (session_id, user_id, created_at, expires_at)
                VALUES (?, ?, ?, ?);
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
            cursor = conn.execute(
                """
                SELECT u.id, u.username, u.role, u.is_active, s.expires_at
                FROM admin_sessions s
                JOIN admin_users u ON s.user_id = u.id
                WHERE s.session_id = ? AND s.expires_at > ? AND u.is_active = 1;
                """,
                (session_id, now_iso),
            )
            row = cursor.fetchone()
            if row:
                return {
                    "id": row["id"],
                    "username": row["username"],
                    "role": row["role"],
                    "is_active": bool(row["is_active"]),
                }
        return None

    def delete_session(self, session_id: str) -> bool:
        """Supprime une session active (déconnexion)."""
        if not session_id:
            return False
        with self._lock, self._get_connection() as conn:
            cursor = conn.execute("DELETE FROM admin_sessions WHERE session_id = ?;", (session_id,))
            conn.commit()
            return cursor.rowcount > 0

    def cleanup_expired_sessions(self) -> int:
        """Nettoie les sessions expirées."""
        now_iso = datetime.now(timezone.utc).isoformat()
        with self._lock, self._get_connection() as conn:
            cursor = conn.execute("DELETE FROM admin_sessions WHERE expires_at <= ?;", (now_iso,))
            conn.commit()
            return cursor.rowcount

    def list_users(self) -> List[Dict[str, Any]]:
        """Retourne la liste de tous les utilisateurs (sans exposer les hashs de mot de passe)."""
        with self._lock, self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT id, username, role, is_active, created_at, updated_at
                FROM admin_users
                ORDER BY id ASC;
                """
            )
            return [
                {
                    "id": r["id"],
                    "username": r["username"],
                    "role": r["role"],
                    "is_active": bool(r["is_active"]),
                    "created_at": r["created_at"],
                    "updated_at": r["updated_at"],
                }
                for r in cursor.fetchall()
            ]

    def create_user(self, username: str, password: str, role: str = "admin") -> Dict[str, Any]:
        """Crée un nouvel utilisateur."""
        clean_user = username.strip()
        if not clean_user or len(clean_user) < 3:
            raise ValueError("Le nom d'utilisateur doit contenir au moins 3 caractères.")
        if not password or len(password) < 4:
            raise ValueError("Le mot de passe doit contenir au moins 4 caractères.")
        if role not in ("admin", "viewer"):
            role = "admin"

        pw_hash, salt = self.hash_password(password)
        now = datetime.now(timezone.utc).isoformat()

        with self._lock, self._get_connection() as conn:
            try:
                cursor = conn.execute(
                    """
                    INSERT INTO admin_users (username, password_hash, salt, role, is_active, created_at, updated_at)
                    VALUES (?, ?, ?, ?, 1, ?, ?);
                    """,
                    (clean_user, pw_hash, salt, role, now, now),
                )
                conn.commit()
                return {
                    "id": cursor.lastrowid,
                    "username": clean_user,
                    "role": role,
                    "is_active": True,
                    "created_at": now,
                    "updated_at": now,
                }
            except sqlite3.IntegrityError:
                raise ValueError(f"Le nom d'utilisateur '{clean_user}' est déjà utilisé.")

    def update_password(self, user_id: int, new_password: str) -> bool:
        """Modifie le mot de passe d'un utilisateur."""
        if not new_password or len(new_password) < 4:
            raise ValueError("Le nouveau mot de passe doit contenir au moins 4 caractères.")

        pw_hash, salt = self.hash_password(new_password)
        now = datetime.now(timezone.utc).isoformat()

        with self._lock, self._get_connection() as conn:
            cursor = conn.execute(
                """
                UPDATE admin_users
                SET password_hash = ?, salt = ?, updated_at = ?
                WHERE id = ?;
                """,
                (pw_hash, salt, now, user_id),
            )
            # Invalider toutes les sessions actives de cet utilisateur pour forcer la reconnexion
            conn.execute("DELETE FROM admin_sessions WHERE user_id = ?;", (user_id,))
            conn.commit()
            return cursor.rowcount > 0

    def toggle_user_status(self, user_id: int, current_user_id: Optional[int] = None) -> bool:
        """Active ou désactive un utilisateur, avec protection du dernier admin."""
        if current_user_id is not None and user_id == current_user_id:
            raise ValueError("Vous ne pouvez pas désactiver votre propre compte actuellement connecté.")

        with self._lock, self._get_connection() as conn:
            cursor = conn.execute("SELECT id, role, is_active FROM admin_users WHERE id = ?;", (user_id,))
            user = cursor.fetchone()
            if not user:
                raise ValueError("Utilisateur introuvable.")

            new_status = 0 if user["is_active"] else 1

            # Si on tente de désactiver un admin, s'assurer qu'il en reste au moins un autre actif
            if new_status == 0 and user["role"] == "admin":
                cur_admins = conn.execute(
                    "SELECT COUNT(*) AS total FROM admin_users WHERE role = 'admin' AND is_active = 1;"
                ).fetchone()["total"]
                if cur_admins <= 1:
                    raise ValueError("Impossible de désactiver le dernier administrateur actif.")

            now = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "UPDATE admin_users SET is_active = ?, updated_at = ? WHERE id = ?;",
                (new_status, now, user_id),
            )
            if new_status == 0:
                conn.execute("DELETE FROM admin_sessions WHERE user_id = ?;", (user_id,))
            conn.commit()
            return True

    def delete_user(self, user_id: int, current_user_id: Optional[int] = None) -> bool:
        """Supprime définitivement un utilisateur, avec vérifications de sécurité."""
        if current_user_id is not None and user_id == current_user_id:
            raise ValueError("Vous ne pouvez pas supprimer votre propre compte actuellement connecté.")

        with self._lock, self._get_connection() as conn:
            cursor = conn.execute("SELECT id, role, is_active FROM admin_users WHERE id = ?;", (user_id,))
            user = cursor.fetchone()
            if not user:
                raise ValueError("Utilisateur introuvable.")

            if user["role"] == "admin" and user["is_active"]:
                cur_admins = conn.execute(
                    "SELECT COUNT(*) AS total FROM admin_users WHERE role = 'admin' AND is_active = 1;"
                ).fetchone()["total"]
                if cur_admins <= 1:
                    raise ValueError("Impossible de supprimer le dernier administrateur actif.")

            conn.execute("DELETE FROM admin_users WHERE id = ?;", (user_id,))
            conn.commit()
            return True

    # --- Gestion de la Configuration Système Persistée ---

    def get_config(self, key: str, default: Optional[str] = None) -> Optional[str]:
        """Récupère une valeur de configuration système persistée."""
        with self._lock, self._get_connection() as conn:
            cursor = conn.execute("SELECT value FROM system_config WHERE key = ?;", (key,))
            row = cursor.fetchone()
            return row["value"] if row else default

    def set_config(self, key: str, value: str) -> None:
        """Enregistre ou met à jour une valeur de configuration système."""
        now = datetime.now(timezone.utc).isoformat()
        with self._lock, self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO system_config (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at;
                """,
                (key, str(value).strip(), now),
            )
            conn.commit()

    def get_all_configs(self) -> Dict[str, str]:
        """Retourne l'ensemble des clés/valeurs de configuration."""
        with self._lock, self._get_connection() as conn:
            cursor = conn.execute("SELECT key, value FROM system_config;")
            return {row["key"]: row["value"] for row in cursor.fetchall()}

    # --- Gestion des Tokens Révoqués & Purge ---

    def revoke_token(
        self,
        token_id: str,
        user_id: Optional[str] = None,
        expires_at: Optional[str] = None,
        reason: Optional[str] = None,
    ) -> bool:
        """Enregistre un jeton JWT dans la liste des tokens révoqués."""
        clean_id = token_id.strip()
        if not clean_id:
            raise ValueError("L'identifiant du token (token_id) ne peut pas être vide.")

        now = datetime.now(timezone.utc).isoformat()
        with self._lock, self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO revoked_tokens (token_id, user_id, revoked_at, expires_at, reason)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(token_id) DO UPDATE SET
                    revoked_at = excluded.revoked_at,
                    reason = excluded.reason;
                """,
                (clean_id, str(user_id) if user_id is not None else None, now, expires_at, reason),
            )
            conn.commit()
            self._revoked_tokens_cache.add(clean_id)
            logger.info("Token JWT '%s' révoqué avec succès (user: %s).", clean_id, user_id)
            return True

    def is_token_revoked(self, token_id: str) -> bool:
        """Vérifie instantanément si un jeton est révoqué via le cache mémoire."""
        if not token_id:
            return False
        return token_id.strip() in self._revoked_tokens_cache

    def list_revoked_tokens(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Liste les jetons révoqués ordonnés par date décroissante."""
        with self._lock, self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT token_id, user_id, revoked_at, expires_at, reason
                FROM revoked_tokens
                ORDER BY revoked_at DESC
                LIMIT ?;
                """,
                (limit,),
            )
            return [dict(row) for row in cursor.fetchall()]

    def delete_revoked_token(self, token_id: str) -> bool:
        """Supprime un jeton de la liste des révocations (déblocage manuel)."""
        clean_id = token_id.strip()
        with self._lock, self._get_connection() as conn:
            conn.execute("DELETE FROM revoked_tokens WHERE token_id = ?;", (clean_id,))
            conn.commit()
            self._revoked_tokens_cache.discard(clean_id)
            return True

    def cleanup_expired_revoked_tokens(self, max_age_hours: int = 72) -> int:
        """Supprime de la base les tokens révoqués de plus de max_age_hours (ou expirés)."""
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=max_age_hours)).isoformat()
        now_str = datetime.now(timezone.utc).isoformat()

        with self._lock, self._get_connection() as conn:
            # Supprimer les tokens révoqués depuis plus de max_age_hours OU dont expires_at est dans le passé
            cursor = conn.execute(
                """
                DELETE FROM revoked_tokens
                WHERE revoked_at < ?
                   OR (expires_at IS NOT NULL AND expires_at < ?);
                """,
                (cutoff, now_str),
            )
            deleted_count = cursor.rowcount
            # Synchroniser le cache mémoire
            cur = conn.execute("SELECT token_id FROM revoked_tokens;")
            self._revoked_tokens_cache = {row["token_id"] for row in cur.fetchall()}
            conn.commit()

            if deleted_count > 0:
                logger.info(
                    "Purge des tokens révoqués terminée : %d token(s) de plus de %d heures supprimé(s).",
                    deleted_count,
                    max_age_hours,
                )
            return deleted_count

    # --- Gestion OAuth 2.0 (Codes & Clients) ---

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
        """Génère et stocke un code d'autorisation OAuth 2.0 temporaire (5 min par défaut)."""
        code = secrets.token_urlsafe(32)
        now = datetime.now(timezone.utc)
        expires = now + timedelta(seconds=ttl_seconds)

        with self._lock, self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO oauth_codes (
                    code, client_id, redirect_uri, jwt_token, token_id, user_id,
                    code_challenge, code_challenge_method, expires_at, used
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0);
                """,
                (
                    code,
                    client_id,
                    redirect_uri,
                    jwt_token,
                    token_id,
                    str(user_id) if user_id is not None else None,
                    code_challenge,
                    code_challenge_method,
                    expires.isoformat(),
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
        """Consomme un code d'autorisation OAuth 2.0 de manière atomique (usage unique).

        Vérifie la validité, l'expiration, PKCE et si le token_id associé n'a pas été révoqué.
        """
        if not code:
            return None

        now_iso = datetime.now(timezone.utc).isoformat()
        with self._lock, self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM oauth_codes WHERE code = ?;",
                (code.strip(),),
            )
            row = cursor.fetchone()
            if not row:
                return None

            # Code déjà utilisé (protection anti-rejeu)
            if row["used"]:
                logger.warning("Tentative de réutilisation d'un code d'autorisation OAuth : %s", code)
                return None

            # Vérification expiration
            if row["expires_at"] <= now_iso:
                logger.warning("Code d'autorisation OAuth expiré : %s", code)
                return None

            # Vérification client_id
            if row["client_id"] != client_id:
                logger.warning(
                    "client_id non concordant pour le code OAuth (attendu: %s, reçu: %s)",
                    row["client_id"],
                    client_id,
                )
                return None

            # Vérification redirect_uri si fourni
            if redirect_uri and row["redirect_uri"] != redirect_uri:
                logger.warning(
                    "redirect_uri non concordant pour le code OAuth (attendu: %s, reçu: %s)",
                    row["redirect_uri"],
                    redirect_uri,
                )
                return None

            # Vérification PKCE si code_challenge était présent
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
                else:
                    logger.warning("Méthode de code_challenge_method inconnue : %s", method)
                    return None

            # Vérifier si le token associé n'a pas été révoqué entre-temps
            token_id = row["token_id"]
            if token_id and self.is_token_revoked(token_id):
                logger.warning("Le token_id associé au code OAuth est déjà révoqué : %s", token_id)
                return None

            # Marquer le code comme consommé
            conn.execute(
                "UPDATE oauth_codes SET used = 1 WHERE code = ?;",
                (code.strip(),),
            )
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
            conn.execute(
                """
                INSERT INTO oauth_clients (client_id, client_secret, client_name, redirect_uris, created_at)
                VALUES (?, ?, ?, ?, ?)
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
            cursor = conn.execute("SELECT * FROM oauth_clients WHERE client_id = ?;", (client_id.strip(),))
            row = cursor.fetchone()
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
            cursor = conn.execute(
                "DELETE FROM oauth_codes WHERE used = 1 OR expires_at <= ?;",
                (now_str,),
            )
            conn.commit()
            return cursor.rowcount


class AdminDatabase:
    """Façade unifiée pour la persistance : SQLite autonome par défaut ou PostgreSQL en cluster."""

    def __init__(self, db_path: str = "data/admin.db", database_url: Optional[str] = None):
        self.db_path = db_path
        self.database_url = database_url
        clean_db_url = str(database_url).strip() if database_url else ""

        if clean_db_url.startswith(("postgresql://", "postgres://")):
            from ..storage.postgres import PostgresStorageBackend
            logger.info("AdminDatabase : activation du backend PostgreSQL partagé (mode cluster).")
            self._backend = PostgresStorageBackend(clean_db_url)
        else:
            self._backend = SQLiteStorageBackend(db_path)

    @property
    def is_cluster(self) -> bool:
        """Retourne True si le stockage actif est PostgreSQL (cluster), False si SQLite."""
        return not isinstance(self._backend, SQLiteStorageBackend)

    @property
    def _revoked_tokens_cache(self) -> set[str]:
        return getattr(self._backend, "_revoked_tokens_cache", set())

    @_revoked_tokens_cache.setter
    def _revoked_tokens_cache(self, value: set[str]) -> None:
        if hasattr(self._backend, "_revoked_tokens_cache"):
            self._backend._revoked_tokens_cache = value

    @staticmethod
    def hash_password(password: str, salt: Optional[str] = None) -> Tuple[str, str]:
        return SQLiteStorageBackend.hash_password(password, salt)

    @staticmethod
    def verify_password(password: str, password_hash: str, salt: str) -> bool:
        return SQLiteStorageBackend.verify_password(password, password_hash, salt)

    def init_db(self) -> None:
        return self._backend.init_db()

    def has_users(self) -> bool:
        return self._backend.has_users()

    def count_active_admins(self) -> int:
        return self._backend.count_active_admins()

    def setup_first_admin(self, username: str, password: str) -> Dict[str, Any]:
        return self._backend.setup_first_admin(username, password)

    def authenticate(self, username: str, password: str) -> Optional[Dict[str, Any]]:
        return self._backend.authenticate(username, password)

    def create_session(self, user_id: int, ttl_hours: int = 24) -> str:
        return self._backend.create_session(user_id, ttl_hours)

    def get_user_by_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        return self._backend.get_user_by_session(session_id)

    def delete_session(self, session_id: str) -> bool:
        return self._backend.delete_session(session_id)

    def cleanup_expired_sessions(self) -> int:
        return self._backend.cleanup_expired_sessions()

    def list_users(self) -> List[Dict[str, Any]]:
        return self._backend.list_users()

    def create_user(self, username: str, password: str, role: str = "admin") -> Dict[str, Any]:
        return self._backend.create_user(username, password, role)

    def update_password(self, user_id: int, new_password: str) -> bool:
        return self._backend.update_password(user_id, new_password)

    def toggle_user_status(self, user_id: int, current_user_id: Optional[int] = None) -> bool:
        return self._backend.toggle_user_status(user_id, current_user_id)

    def delete_user(self, user_id: int, current_user_id: Optional[int] = None) -> bool:
        return self._backend.delete_user(user_id, current_user_id)

    def get_config(self, key: str, default: Optional[str] = None) -> Optional[str]:
        return self._backend.get_config(key, default)

    def set_config(self, key: str, value: str) -> None:
        return self._backend.set_config(key, value)

    def get_all_configs(self) -> Dict[str, str]:
        return self._backend.get_all_configs()

    def revoke_token(
        self,
        token_id: str,
        user_id: Optional[str] = None,
        expires_at: Optional[str] = None,
        reason: Optional[str] = None,
    ) -> None:
        return self._backend.revoke_token(token_id, user_id, expires_at, reason)

    def is_token_revoked(self, token_id: str) -> bool:
        return self._backend.is_token_revoked(token_id)

    def list_revoked_tokens(self, limit: int = 100) -> List[Dict[str, Any]]:
        return self._backend.list_revoked_tokens(limit)

    def delete_revoked_token(self, token_id: str) -> bool:
        return self._backend.delete_revoked_token(token_id)

    def cleanup_expired_revoked_tokens(self, max_age_hours: int = 72) -> int:
        return self._backend.cleanup_expired_revoked_tokens(max_age_hours)

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
        return self._backend.create_oauth_code(
            client_id, redirect_uri, jwt_token, token_id, user_id,
            code_challenge, code_challenge_method, ttl_seconds
        )

    def consume_oauth_code(
        self,
        code: str,
        client_id: str,
        redirect_uri: Optional[str] = None,
        code_verifier: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        return self._backend.consume_oauth_code(code, client_id, redirect_uri, code_verifier)

    def register_oauth_client(
        self,
        client_name: str,
        redirect_uris: List[str],
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
    ) -> Dict[str, Any]:
        return self._backend.register_oauth_client(client_name, redirect_uris, client_id, client_secret)

    def get_oauth_client(self, client_id: str) -> Optional[Dict[str, Any]]:
        return self._backend.get_oauth_client(client_id)

    def cleanup_expired_oauth_codes(self) -> int:
        return self._backend.cleanup_expired_oauth_codes()

    def close(self) -> None:
        if hasattr(self._backend, "close"):
            self._backend.close()

    def __getattr__(self, item: str) -> Any:
        return getattr(self._backend, item)

