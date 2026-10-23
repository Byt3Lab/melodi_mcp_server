"""Configuration du logging sécurisé pour melodi_mcp_server."""

import logging
import re
import sys
from typing import Optional

SECRET_PATTERNS = [
    re.compile(r"(Bearer\s+)[A-Za-z0-9_\-\.]{8,}", re.IGNORECASE),
    re.compile(r"(api[_-]?key[\"'\s:=]+)[A-Za-z0-9_\-\.]{8,}", re.IGNORECASE),
    re.compile(r"(token[\"'\s:=]+)[A-Za-z0-9_\-\.]{8,}", re.IGNORECASE),
    re.compile(r"(password[\"'\s:=]+)[^\s,\"'\&]+", re.IGNORECASE),
]


class SecretMaskingFormatter(logging.Formatter):
    """Formatter garantissant qu'aucun jeton d'authentification ou secret ne fuite dans les logs."""

    def format(self, record: logging.LogRecord) -> str:
        original = super().format(record)
        sanitized = original
        for pattern in SECRET_PATTERNS:
            sanitized = pattern.sub(r"\1[MASQUÉ]", sanitized)
        return sanitized


def configure_logging(level: str = "INFO", stream: Optional[object] = None) -> logging.Logger:
    """Configure la journalisation vers stderr (pour ne jamais polluer stdout utilisé par stdio)."""
    target_stream = stream or sys.stderr
    handler = logging.StreamHandler(target_stream)
    formatter = SecretMaskingFormatter(
        fmt="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    # Nettoyer d'anciens handlers éventuels
    for h in root_logger.handlers[:]:
        root_logger.removeHandler(h)

    numeric_level = getattr(logging, level.upper(), logging.INFO)
    root_logger.setLevel(numeric_level)
    root_logger.addHandler(handler)

    # Réduire le verbiage de bibliothèques tierces
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)

    logger = logging.getLogger("melodi_mcp_server")
    logger.setLevel(numeric_level)
    return logger
