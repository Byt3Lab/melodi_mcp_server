"""Registre dynamique des outils MELODI et conversion vers le protocole MCP."""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional
import jsonschema
from mcp.types import Tool

from .errors import CatalogSyncError, ToolValidationError
from .melodi_client import MelodiAsyncClient
from .settings import Settings, get_settings

logger = logging.getLogger(__name__)


@dataclass
class InternalTool:
    """Représentation interne d'un outil métier découvert depuis MELODI."""

    name: str
    description: str
    input_schema: dict[str, Any]
    read_only: bool = True
    requires_confirmation: bool = False
    timeout_seconds: float = 30.0
    required_permissions: list[str] = field(default_factory=list)
    raw_definition: dict[str, Any] = field(default_factory=dict)

    def to_mcp_tool(self) -> Tool:
        """Convertit la définition interne en outil MCP conforme au SDK."""
        return Tool(
            name=self.name,
            description=self.description,
            inputSchema=self.input_schema,
        )


class DynamicToolRegistry:
    """Gère le catalogue d'outils, les révisions et la synchronisation avec MELODI ERP."""

    def __init__(self, client: MelodiAsyncClient, settings: Optional[Settings] = None):
        self.client = client
        self.settings = settings or get_settings()
        self._tools: dict[str, InternalTool] = {}
        self._lock = asyncio.Lock()
        self.catalog_revision: Optional[str] = None
        self.last_sync_time: Optional[float] = None
        self.last_sync_success: bool = False
        self.last_sync_error: Optional[str] = None
        self._sync_task: Optional[asyncio.Task] = None
        self._running: bool = False

    @property
    def tools_count(self) -> int:
        return len(self._tools)

    def get_tool(self, name: str) -> Optional[InternalTool]:
        """Retourne un outil par son nom."""
        return self._tools.get(name)

    def list_internal_tools(self) -> list[InternalTool]:
        """Retourne la liste des outils internes."""
        return list(self._tools.values())

    def list_mcp_tools(self) -> list[Tool]:
        """Retourne la liste des outils au format MCP."""
        return [tool.to_mcp_tool() for tool in self._tools.values()]

    def validate_tool_definition(self, raw: dict[str, Any]) -> InternalTool:
        """Valide et convertit une définition d'outil issue de l'API MELODI."""
        if not isinstance(raw, dict):
            raise ToolValidationError("La définition de l'outil doit être un dictionnaire")

        name = raw.get("name")
        if not name or not isinstance(name, str) or not name.strip():
            raise ToolValidationError("Le nom de l'outil est obligatoire et doit être une chaîne non vide")

        name = name.strip()
        description = raw.get("description", "") or f"Outil MELODI {name}"

        # Extraction et validation du schéma JSON des arguments
        input_schema = raw.get("input_schema") or raw.get("inputSchema") or {}
        if not isinstance(input_schema, dict):
            raise ToolValidationError(f"Le schéma input_schema pour '{name}' doit être un objet JSON")

        if "type" not in input_schema:
            input_schema["type"] = "object"
        if "properties" not in input_schema:
            input_schema["properties"] = {}

        # Validation de la validité du schéma via Draft7 / jsonschema
        try:
            jsonschema.Draft7Validator.check_schema(input_schema)
        except jsonschema.SchemaError as e:
            raise ToolValidationError(f"Schéma JSON invalide pour l'outil '{name}': {e.message}")

        read_only = bool(raw.get("read_only", True))
        requires_confirmation = bool(
            raw.get("requires_confirmation", raw.get("confirmation_required", False))
        )
        timeout = float(raw.get("timeout_seconds", self.settings.melodi_timeout_seconds))
        permissions = raw.get("required_permissions", [])
        if not isinstance(permissions, list):
            permissions = []

        return InternalTool(
            name=name,
            description=description,
            input_schema=input_schema,
            read_only=read_only,
            requires_confirmation=requires_confirmation,
            timeout_seconds=timeout,
            required_permissions=permissions,
            raw_definition=raw,
        )

    async def update_from_payload(self, catalog_data: dict[str, Any], force: bool = False) -> int:
        """Met à jour le registre atomiquement à partir de la charge utile de l'API."""
        revision = catalog_data.get("catalog_revision") or catalog_data.get("revision")
        if not revision and isinstance(catalog_data.get("data"), dict):
            revision = catalog_data["data"].get("catalog_revision") or catalog_data["data"].get("revision")

        if not force and revision and revision == self.catalog_revision:
            logger.debug("Catalogue inchangé (révision '%s'). Mise à jour ignorée.", revision)
            self.last_sync_success = True
            self.last_sync_time = time.time()
            self.last_sync_error = None
            return len(self._tools)

        # Extraction des outils (supporte le format plat ou imbriqué sous 'data')
        raw_tools = None
        if "tools" in catalog_data and isinstance(catalog_data["tools"], list):
            raw_tools = catalog_data["tools"]
        elif isinstance(catalog_data.get("data"), dict) and isinstance(catalog_data["data"].get("tools"), list):
            raw_tools = catalog_data["data"]["tools"]
        else:
            raw_tools = []

        if not isinstance(raw_tools, list):
            raise ToolValidationError("Le champ 'tools' doit être une liste")

        new_tools: dict[str, InternalTool] = {}
        for raw in raw_tools:
            tool = self.validate_tool_definition(raw)
            if tool.name in new_tools:
                raise ToolValidationError(f"Collision de nom détectée : l'outil '{tool.name}' est dupliqué")
            new_tools[tool.name] = tool

        # Remplacement atomique du registre
        async with self._lock:
            self._tools = new_tools
            self.catalog_revision = revision
            self.last_sync_time = time.time()
            self.last_sync_success = True
            self.last_sync_error = None

        logger.info(
            "Registre mis à jour avec succès : %d outils enregistrés (révision: %s)",
            len(new_tools),
            revision,
        )
        return len(new_tools)

    async def sync_now(self, force: bool = False) -> int:
        """Effectue une synchronisation immédiate auprès de MELODI ERP."""
        logger.info("Déclenchement d'une synchronisation du catalogue MELODI...")
        try:
            catalog_data = await self.client.get_tools()
            count = await self.update_from_payload(catalog_data, force=force)
            return count
        except Exception as e:
            self.last_sync_success = False
            self.last_sync_time = time.time()
            self.last_sync_error = str(e)
            logger.error("Échec de la synchronisation du catalogue MELODI : %s", e)
            raise CatalogSyncError(f"Échec de la synchronisation du catalogue : {e}") from e

    async def start_sync_loop(self) -> None:
        """Démarre la boucle asynchrone de synchronisation périodique."""
        if self._running:
            return
        self._running = True

        async def _loop():
            # Première tentative au démarrage
            try:
                await self.sync_now()
            except Exception as e:
                logger.warning("Échec de synchronisation initiale du catalogue : %s", e)

            while self._running:
                try:
                    await asyncio.sleep(self.settings.catalog_sync_interval_seconds)
                    if not self._running:
                        break
                    await self.sync_now()
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.warning("Erreur périodique lors de la synchronisation du catalogue : %s", e)

        self._sync_task = asyncio.create_task(_loop())
        logger.info(
            "Boucle de synchronisation périodique démarrée (intervalle: %ds)",
            self.settings.catalog_sync_interval_seconds,
        )

    async def stop_sync_loop(self) -> None:
        """Arrête la tâche asynchrone de synchronisation."""
        self._running = False
        if self._sync_task:
            self._sync_task.cancel()
            try:
                await self._sync_task
            except asyncio.CancelledError:
                pass
            self._sync_task = None
            logger.info("Boucle de synchronisation périodique arrêtée.")
