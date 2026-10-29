"""Gestion de la base de données SQLite pour l'authentification et les utilisateurs de la console admin."""

from __future__ import annotations

import hashlib
import logging
import os
import secrets
import sqlite3
import threading
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class AdminDatabase:
    """Gestionnaire de persistance SQLite pour les utilisateurs administrateurs et sessions."""

    def __init__(self, db_path: str = "data/admin.db"):
        self.db_path = db_path
        self._lock = threading.Lock()
        self._mem_conn: Optional[sqlite3.Connection] = None

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
