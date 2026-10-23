"""Collecteur de métriques d'exécution et de santé pour melodi_mcp_server."""

import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional


@dataclass
class ToolExecutionRecord:
    """Enregistrement d'un appel d'outil pour l'observabilité."""
    call_id: str
    tool_name: str
    timestamp: str
    duration_ms: float
    success: bool
    error_code: Optional[str] = None
    error_message: Optional[str] = None


class MetricsCollector:
    """Collecteur centralisé des statistiques d'exécution et d'activité."""

    def __init__(self, max_history: int = 50):
        self.start_time: float = time.time()
        self.total_requests: int = 0
        self.successful_requests: int = 0
        self.failed_requests: int = 0
        self.total_duration_ms: float = 0.0
        self.tool_stats: dict[str, dict[str, Any]] = {}
        self.recent_calls: deque[ToolExecutionRecord] = deque(maxlen=max_history)

    def record_call(
        self,
        call_id: str,
        tool_name: str,
        duration_ms: float,
        success: bool,
        error_code: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> None:
        """Enregistre un appel d'outil."""
        self.total_requests += 1
        self.total_duration_ms += duration_ms
        if success:
            self.successful_requests += 1
        else:
            self.failed_requests += 1

        if tool_name not in self.tool_stats:
            self.tool_stats[tool_name] = {
                "total": 0,
                "success": 0,
                "failure": 0,
                "total_duration_ms": 0.0,
            }

        stats = self.tool_stats[tool_name]
        stats["total"] += 1
        stats["total_duration_ms"] += duration_ms
        if success:
            stats["success"] += 1
        else:
            stats["failure"] += 1

        now_iso = datetime.now(timezone.utc).isoformat()
        record = ToolExecutionRecord(
            call_id=call_id,
            tool_name=tool_name,
            timestamp=now_iso,
            duration_ms=round(duration_ms, 2),
            success=success,
            error_code=error_code,
            error_message=error_message[:200] if error_message else None,
        )
        self.recent_calls.appendleft(record)

    def get_summary(self) -> dict[str, Any]:
        """Retourne les métriques synthétiques."""
        uptime_seconds = int(time.time() - self.start_time)
        avg_duration = (
            round(self.total_duration_ms / self.total_requests, 2)
            if self.total_requests > 0
            else 0.0
        )
        return {
            "uptime_seconds": uptime_seconds,
            "total_requests": self.total_requests,
            "successful_requests": self.successful_requests,
            "failed_requests": self.failed_requests,
            "success_rate_percent": (
                round((self.successful_requests / self.total_requests) * 100, 1)
                if self.total_requests > 0
                else 100.0
            ),
            "avg_duration_ms": avg_duration,
            "tools_breakdown": self.tool_stats,
            "recent_calls": [
                {
                    "call_id": r.call_id,
                    "tool_name": r.tool_name,
                    "timestamp": r.timestamp,
                    "duration_ms": r.duration_ms,
                    "success": r.success,
                    "error_code": r.error_code,
                    "error_message": r.error_message,
                }
                for r in self.recent_calls
            ],
        }


# Singleton métriques
_metrics_instance = MetricsCollector()


def get_metrics() -> MetricsCollector:
    return _metrics_instance
