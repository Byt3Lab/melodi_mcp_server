"""Utilitaire de diagnostic et de test de connectivité pour le cluster (PostgreSQL & Redis)."""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)


def test_cluster_connections(
    database_url: Optional[str] = None,
    redis_url: Optional[str] = None,
    timeout_seconds: float = 4.0,
) -> dict[str, Any]:
    """Teste la joignabilité et l'authentification de PostgreSQL et optionnellement Redis.

    Retourne un compte-rendu structuré sans jamais lever d'exception non interceptée.
    """
    result: dict[str, Any] = {
        "success": False,
        "pg_tested": False,
        "pg_success": False,
        "pg_version": None,
        "pg_latency_ms": None,
        "pg_error": None,
        "redis_tested": False,
        "redis_success": False,
        "redis_status": None,
        "redis_latency_ms": None,
        "redis_error": None,
        "message": "",
    }

    # 1. Test PostgreSQL
    clean_db_url = (database_url or "").strip()
    if clean_db_url:
        result["pg_tested"] = True
        if not clean_db_url.startswith(("postgresql://", "postgres://")):
            result["pg_error"] = "L'URL de base de données doit débuter par 'postgresql://' ou 'postgres://'"
        else:
            try:
                import psycopg

                start_t = time.time()
                # Connexion avec timeout d'ouverture strict
                with psycopg.connect(clean_db_url, connect_timeout=int(timeout_seconds)) as conn:
                    with conn.cursor() as cur:
                        cur.execute("SELECT version();")
                        row = cur.fetchone()
                        pg_version = row[0] if row else "PostgreSQL inconnu"
                latency = round((time.time() - start_t) * 1000, 1)
                result["pg_success"] = True
                result["pg_version"] = pg_version
                result["pg_latency_ms"] = latency
            except Exception as e:
                result["pg_error"] = str(e)
                logger.warning("Échec du test de connexion PostgreSQL : %s", e)

    # 2. Test Redis (si fourni)
    clean_redis_url = (redis_url or "").strip()
    if clean_redis_url:
        result["redis_tested"] = True
        if not clean_redis_url.startswith(("redis://", "rediss://")):
            result["redis_error"] = "L'URL Redis doit débuter par 'redis://' ou 'rediss://'"
        else:
            try:
                import redis

                start_r = time.time()
                client = redis.Redis.from_url(
                    clean_redis_url,
                    socket_connect_timeout=timeout_seconds,
                    socket_timeout=timeout_seconds,
                )
                ping_res = client.ping()
                latency_r = round((time.time() - start_r) * 1000, 1)
                client.close()
                if ping_res:
                    result["redis_success"] = True
                    result["redis_status"] = "PONG"
                    result["redis_latency_ms"] = latency_r
                else:
                    result["redis_error"] = "Réponse inattendue de Redis (ping returned False)"
            except Exception as e:
                result["redis_error"] = str(e)
                logger.warning("Échec du test de connexion Redis : %s", e)

    # 3. Synthèse globale
    # Si PostgreSQL a été testé et a réussi (et Redis aussi s'il a été testé)
    all_tests_passed = True
    messages = []

    if result["pg_tested"]:
        if result["pg_success"]:
            messages.append(f"PostgreSQL connecté ({result['pg_latency_ms']}ms)")
        else:
            all_tests_passed = False
            messages.append(f"Erreur PostgreSQL: {result['pg_error']}")
    else:
        all_tests_passed = False
        messages.append("Aucune URL PostgreSQL fournie.")

    if result["redis_tested"]:
        if result["redis_success"]:
            messages.append(f"Redis connecté ({result['redis_latency_ms']}ms)")
        else:
            all_tests_passed = False
            messages.append(f"Erreur Redis: {result['redis_error']}")

    result["success"] = all_tests_passed
    result["message"] = " | ".join(messages)
    return result
