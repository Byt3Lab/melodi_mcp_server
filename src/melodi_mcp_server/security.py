"""Contrôles de sécurité locaux, gestion du contexte et sanitisation."""

import json
import logging
from typing import Any, Optional
import jsonschema

from .errors import ToolValidationError
from .settings import Settings, get_settings

logger = logging.getLogger(__name__)


class SecurityContext:
    """Représente le contexte de sécurité de l'appelant MCP."""

    def __init__(
        self,
        client_id: Optional[str] = None,
        user_id: Optional[str] = None,
        org_id: Optional[str] = None,
        is_authenticated: bool = False,
    ):
        self.client_id = client_id or "mcp_client"
        self.user_id = user_id if is_authenticated else None
        self.org_id = org_id if is_authenticated else None
        self.is_authenticated = is_authenticated


def validate_payload_size(data: Any, max_bytes: int) -> None:
    """Vérifie que la charge utile sérialisée en JSON ne dépasse pas la limite configurée."""
    try:
        serialized = json.dumps(data)
        size = len(serialized.encode("utf-8"))
        if size > max_bytes:
            raise ToolValidationError(
                f"La taille des arguments ({size} octets) dépasse le plafond autorisé ({max_bytes} octets)"
            )
    except (TypeError, ValueError) as e:
        raise ToolValidationError(f"Données non sérialisables en JSON : {e}")


def validate_tool_arguments(schema: dict[str, Any], arguments: dict[str, Any]) -> None:
    """Valide les arguments d'un outil par rapport à son schéma JSON Schema."""
    if not isinstance(arguments, dict):
        raise ToolValidationError("Les arguments de l'outil doivent être fournis sous la forme d'un dictionnaire")

    try:
        validator = jsonschema.Draft7Validator(schema)
        errors = list(validator.iter_errors(arguments))
        if errors:
            details = [f"{e.path}: {e.message}" if e.path else e.message for e in errors]
            raise ToolValidationError(
                f"Arguments invalides : {'; '.join(details)}",
                details={"errors": details},
            )
    except jsonschema.SchemaError as e:
        logger.error("Schéma JSON corrompu : %s", e)
        raise ToolValidationError(f"Erreur de validation de schéma : {e.message}")


def sanitize_result_for_logging(data: Any) -> Any:
    """Masque les champs potentiellement sensibles dans les dictionnaires pour la traçabilité."""
    sensitive_keys = {"password", "secret", "token", "api_key", "authorization", "credit_card"}
    if isinstance(data, dict):
        sanitized = {}
        for k, v in data.items():
            if any(s in k.lower() for s in sensitive_keys):
                sanitized[k] = "[MASQUÉ]"
            else:
                sanitized[k] = sanitize_result_for_logging(v)
        return sanitized
    if isinstance(data, list):
        return [sanitize_result_for_logging(item) for item in data]
    return data
