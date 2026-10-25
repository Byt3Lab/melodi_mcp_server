"""Contrôle d'état de santé opérationnel pour melodi_mcp_server."""

import time
from typing import Any, Optional

from .melodi_client import MelodiAsyncClient
from .settings import Settings, get_settings
from .tool_registry import DynamicToolRegistry


class HealthChecker:
    """Évalue et synthétise l'état de santé du serveur MCP et de sa liaison avec MELODI ERP."""

    def __init__(
        self,
        registry: DynamicToolRegistry,
        client: MelodiAsyncClient,
        settings: Optional[Settings] = None,
    ):
        self.registry = registry
        self.client = client
        self.settings = settings or get_settings()
        self.start_time = time.time()

    async def check(self) -> dict[str, Any]:
        """Exécute un diagnostic complet de l'infrastructure."""
        now = time.time()
        uptime_seconds = int(now - self.start_time)

        # Vérification de la connectivité MELODI
        melodi_status = "UNKNOWN"
        melodi_latency_ms: Optional[float] = None
        melodi_error: Optional[str] = None
        contract_compatible = True

        t0 = time.time()
        try:
            health_res = await self.client.get_health()
            melodi_latency_ms = round((time.time() - t0) * 1000, 1)
            melodi_status = "CONNECTED" if health_res.get("status") in ("OK", "healthy", "up", True) else "UP"
        except Exception as e:
            melodi_latency_ms = round((time.time() - t0) * 1000, 1)
            melodi_status = "DISCONNECTED"
            melodi_error = str(e)
            contract_compatible = False

        # Évaluation globale
        is_healthy = (
            melodi_status in ("CONNECTED", "UP")
            and (self.registry.last_sync_success or self.registry.tools_count > 0)
        )

        overall_status = "HEALTHY" if is_healthy else ("DEGRADED" if self.registry.tools_count > 0 else "UNHEALTHY")

        return {
            "status": overall_status,
            "server": {
                "name": "melodi_mcp_server",
                "version": "0.1.0",
                "uptime_seconds": uptime_seconds,
                "transport": self.settings.mcp_transport,
                "auth_required": bool(self.settings.mcp_api_key and self.settings.mcp_api_key.get_secret_value()),
            },
            "melodi_connection": {
                "base_url": self.settings.melodi_base_url,
                "api_prefix": self.settings.melodi_api_prefix,
                "status": melodi_status,
                "latency_ms": melodi_latency_ms,
                "error": melodi_error,
                "contract_compatible": contract_compatible,
            },
            "catalog": {
                "revision": self.registry.catalog_revision,
                "tools_count": self.registry.tools_count,
                "last_sync_time": (
                    time.strftime("%Y-%m-%d %H:%M:%SZ", time.gmtime(self.registry.last_sync_time))
                    if self.registry.last_sync_time
                    else None
                ),
                "last_sync_success": self.registry.last_sync_success,
                "last_sync_error": self.registry.last_sync_error,
            },
        }
