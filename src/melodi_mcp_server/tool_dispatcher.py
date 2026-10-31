"""Dispatcher exécutant les outils MCP via MELODI avec contrôles et observabilité."""

import asyncio
import json
import logging
import time
import uuid
from typing import Any, Optional
from mcp.types import CallToolResult, TextContent

from .errors import (
    MelodiAuthenticationError,
    MelodiAuthorizationError,
    MelodiConnectionError,
    MelodiMcpError,
    MelodiTimeoutError,
    ToolExecutionError,
    ToolNotFoundError,
    ToolValidationError,
)
from .melodi_client import MelodiAsyncClient
from .metrics import get_metrics
from .security import SecurityContext, validate_payload_size, validate_tool_arguments
from .settings import Settings, get_settings
from .tool_registry import DynamicToolRegistry

logger = logging.getLogger(__name__)


class ToolDispatcher:
    """Valide les requêtes, appelle l'API interne de MELODI et formate les réponses MCP."""

    def __init__(
        self,
        registry: DynamicToolRegistry,
        client: MelodiAsyncClient,
        settings: Optional[Settings] = None,
    ):
        self.registry = registry
        self.client = client
        self.settings = settings or get_settings()
        self.metrics = get_metrics()

    async def execute_tool(
        self,
        tool_name: str,
        arguments: Optional[dict[str, Any]] = None,
        context: Optional[SecurityContext] = None,
    ) -> CallToolResult:
        """Point d'entrée principal pour l'exécution d'un outil MCP."""
        call_id = str(uuid.uuid4())
        start_time = time.time()
        args = arguments or {}
        sec_context = context or SecurityContext()

        logger.info("Réception appel outil '%s' [ID: %s] (client: %s)", tool_name, call_id, sec_context.client_id)

        try:
            # 1. Vérification de l'existence de l'outil
            tool = self.registry.get_tool(tool_name)
            if not tool:
                raise ToolNotFoundError(f"L'outil '{tool_name}' n'est pas disponible dans le catalogue actuel")

            # 2. Validation de la taille des arguments
            validate_payload_size(args, self.settings.max_request_size_bytes)

            # 3. Validation locale des arguments selon le schéma JSON
            validate_tool_arguments(tool.input_schema, args)

            # 4. Vérification de la politique de confirmation pour les outils sensibles
            if tool.requires_confirmation and not args.get("_confirmed", False):
                return CallToolResult(
                    content=[
                        TextContent(
                            type="text",
                            text=(
                                f"L'outil '{tool_name}' effectue une opération sensible et requiert une confirmation "
                                f"explicite. Veuillez relancer l'appel avec le paramètre '_confirmed': true."
                            ),
                        )
                    ],
                    isError=True,
                )

            # 5. Appel asynchrone à MELODI avec timeout dédié à l'outil
            timeout = tool.timeout_seconds or self.settings.melodi_timeout_seconds
            user_key = getattr(sec_context, "user_key", None)
            try:
                raw_response = await asyncio.wait_for(
                    self.client.execute_tool(
                        tool_name=tool_name,
                        arguments=args,
                        correlation_id=call_id,
                        user_id=sec_context.user_id,
                        user_key=user_key,
                        call_id=call_id,
                    ),
                    timeout=timeout,
                )
            except asyncio.TimeoutError:
                raise MelodiTimeoutError(
                    f"Dépassement du délai d'exécution alloué ({timeout}s) pour l'outil '{tool_name}'"
                )

            duration_ms = (time.time() - start_time) * 1000
            self.metrics.record_call(
                call_id=call_id,
                tool_name=tool_name,
                duration_ms=duration_ms,
                success=True,
            )

            # Formatage de la réponse
            output_text = json.dumps(raw_response, ensure_ascii=False, indent=2)
            logger.info("Outil '%s' [ID: %s] exécuté avec succès en %.1fms", tool_name, call_id, duration_ms)

            return CallToolResult(
                content=[TextContent(type="text", text=output_text)],
                isError=False,
            )

        except MelodiAuthenticationError as e:
            duration_ms = (time.time() - start_time) * 1000
            self.metrics.record_call(
                call_id=call_id,
                tool_name=tool_name,
                duration_ms=duration_ms,
                success=False,
                error_code="MelodiAuthenticationError",
                error_message=e.message,
            )
            logger.warning("Échec d'authentification pour l'outil '%s' : %s", tool_name, e.message)
            msg = (
                "[MelodiAuthenticationError] Action refusée par MELODI : Votre jeton personnel MCP est manquant, invalide ou a expiré. "
                "Veuillez vous rendre sur MELODI ERP dans 'Mes Tokens MCP' (/admin/melodi_mcp/keys) "
                "pour générer un nouveau token et mettre à jour la configuration de votre agent."
            )
            return CallToolResult(
                content=[TextContent(type="text", text=msg)],
                isError=True,
            )

        except MelodiAuthorizationError as e:
            duration_ms = (time.time() - start_time) * 1000
            self.metrics.record_call(
                call_id=call_id,
                tool_name=tool_name,
                duration_ms=duration_ms,
                success=False,
                error_code="MelodiAuthorizationError",
                error_message=e.message,
            )
            logger.warning("Droits insuffisants pour l'outil '%s' : %s", tool_name, e.message)
            msg = (
                f"[MelodiAuthorizationError] Droits insuffisants : Votre compte utilisateur ne possède pas les permissions nécessaires "
                f"dans MELODI ERP pour exécuter l'outil '{tool_name}'."
            )
            return CallToolResult(
                content=[TextContent(type="text", text=msg)],
                isError=True,
            )

        except MelodiMcpError as e:
            duration_ms = (time.time() - start_time) * 1000
            error_code = e.__class__.__name__
            self.metrics.record_call(
                call_id=call_id,
                tool_name=tool_name,
                duration_ms=duration_ms,
                success=False,
                error_code=error_code,
                error_message=e.message,
            )
            logger.warning("Échec exécution outil '%s' [ID: %s]: %s", tool_name, call_id, e.message)
            return CallToolResult(
                content=[TextContent(type="text", text=f"Erreur [{error_code}] : {e.message}")],
                isError=True,
            )

        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000
            self.metrics.record_call(
                call_id=call_id,
                tool_name=tool_name,
                duration_ms=duration_ms,
                success=False,
                error_code="InternalServerError",
                error_message=str(e),
            )
            logger.exception("Erreur inattendue lors de l'exécution de l'outil '%s' [ID: %s]", tool_name, call_id)
            return CallToolResult(
                content=[TextContent(type="text", text="Erreur interne du serveur lors du traitement de l'outil.")],
                isError=True,
            )
