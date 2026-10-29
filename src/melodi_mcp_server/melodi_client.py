"""Client HTTP asynchrone pour l'API interne de MELODI ERP."""

import logging
import uuid
from typing import Any, Optional
import httpx
from pydantic import SecretStr

from .errors import (
    MelodiAuthenticationError,
    MelodiAuthorizationError,
    MelodiConnectionError,
    MelodiContractError,
    MelodiTimeoutError,
    ToolExecutionError,
    ToolNotFoundError,
)
from .settings import Settings, get_settings

logger = logging.getLogger(__name__)


class MelodiAsyncClient:
    """Client gérant les échanges REST sécurisés avec le module melodi_mcp de MELODI ERP."""

    def __init__(self, settings: Optional[Settings] = None, client: Optional[httpx.AsyncClient] = None):
        self.settings = settings or get_settings()
        self._external_client = client is not None
        self._client = client or httpx.AsyncClient(
            base_url=self.settings.melodi_base_url,
            timeout=self.settings.melodi_timeout_seconds,
            verify=self.settings.melodi_verify_ssl,
        )

    async def aclose(self) -> None:
        """Ferme proprement le pool de connexions HTTP."""
        if not self._external_client and not self._client.is_closed:
            await self._client.aclose()
            logger.info("Connexions HTTP MelodiAsyncClient fermées avec succès.")

    async def __aenter__(self) -> "MelodiAsyncClient":
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.aclose()

    def update_base_url(self, new_url: str) -> str:
        """Met à jour dynamiquement l'URL de base de MELODI ERP."""
        clean_url = new_url.strip().rstrip("/")
        if not clean_url.startswith(("http://", "https://")):
            raise ValueError("L'URL de MELODI ERP doit débuter par http:// ou https://")
        self.settings.melodi_base_url = clean_url
        self._client.base_url = httpx.URL(clean_url)
        logger.info("URL de base de MELODI ERP mise à jour dynamiquement : %s", clean_url)
        return clean_url

    def update_api_key(self, new_api_key: str) -> str:
        """Met à jour dynamiquement la clé secrète de service MELODI."""
        clean_key = new_api_key.strip()
        if not clean_key:
            raise ValueError("La clé API de service MELODI ne peut pas être vide.")
        self.settings.melodi_api_key = SecretStr(clean_key)
        logger.info("Clé API interne de service MELODI mise à jour dynamiquement.")
        return clean_key

    def _build_headers(
        self,
        correlation_id: Optional[str] = None,
        user_id: Optional[str] = None,
        org_id: Optional[str] = None,
        user_key: Optional[str] = None,
    ) -> dict[str, str]:
        """Génère les en-têtes d'authentification et de traçabilité."""
        cid = correlation_id or str(uuid.uuid4())
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-Correlation-ID": cid,
        }
        api_key = self.settings.melodi_api_key.get_secret_value()
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
            headers["X-Internal-MCP-Key"] = api_key
            headers["X-Melodi-Service-Token"] = api_key

        if user_key:
            headers["X-User-MCP-Key"] = str(user_key)
            headers["X-MCP-User-Key"] = str(user_key)
        if user_id:
            headers["X-Melodi-User-ID"] = str(user_id)
        if org_id:
            headers["X-Melodi-Org-ID"] = str(org_id)

        return headers

    def _resolve_url(self, path: str) -> str:
        """Résout le chemin complet en injectant le préfixe d'API configuré."""
        prefix = self.settings.melodi_api_prefix
        clean_path = path if path.startswith("/") else f"/{path}"
        return f"{prefix}{clean_path}"

    async def _request(
        self,
        method: str,
        path: str,
        correlation_id: Optional[str] = None,
        user_id: Optional[str] = None,
        org_id: Optional[str] = None,
        user_key: Optional[str] = None,
        json_data: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """Exécute une requête HTTP avec gestion robuste des erreurs et des délais."""
        url = self._resolve_url(path)
        headers = self._build_headers(correlation_id=correlation_id, user_id=user_id, org_id=org_id, user_key=user_key)
        cid = headers["X-Correlation-ID"]

        try:
            logger.debug("Appel MELODI [%s] %s (CID: %s)", method, url, cid)
            response = await self._client.request(
                method=method,
                url=url,
                headers=headers,
                json=json_data,
            )

            # Vérification de la taille de réponse
            if len(response.content) > self.settings.max_response_size_bytes:
                raise MelodiContractError(
                    f"Réponse MELODI trop volumineuse ({len(response.content)} octets > limite {self.settings.max_response_size_bytes})"
                )

            # Traitement des codes d'erreur HTTP
            if response.status_code == 401:
                raise MelodiAuthenticationError(
                    "Authentification refusée par MELODI (401 Unauthorized)",
                    details={"url": url, "correlation_id": cid},
                )
            if response.status_code == 403:
                raise MelodiAuthorizationError(
                    "Droits insuffisants pour accéder à l'endpoint MELODI (403 Forbidden)",
                    details={"url": url, "correlation_id": cid},
                )
            if response.status_code == 404:
                raise ToolNotFoundError(
                    f"Ressource introuvable sur MELODI (404 Not Found) : {url}",
                    details={"url": url, "correlation_id": cid},
                )
            if response.status_code >= 500:
                raise MelodiConnectionError(
                    f"Erreur interne de MELODI (HTTP {response.status_code})",
                    details={"url": url, "correlation_id": cid, "response": response.text[:200]},
                )

            if response.status_code >= 400:
                # Erreur client ou validation métier
                error_body = {}
                try:
                    error_body = response.json()
                except Exception:
                    error_body = {"raw": response.text[:200]}
                raise ToolExecutionError(
                    f"Erreur MELODI (HTTP {response.status_code})",
                    details={"error": error_body, "correlation_id": cid},
                )

            # Décodage JSON
            try:
                data = response.json()
            except Exception as e:
                raise MelodiContractError(
                    f"Réponse MELODI non conforme (JSON invalide) : {e}",
                    details={"url": url, "raw": response.text[:200]},
                )

            # Vérification si MELODI a répondu avec success: false (même sous HTTP 200)
            if isinstance(data, dict) and data.get("success") is False:
                err_info = data.get("error", {})
                err_code = err_info.get("code") if isinstance(err_info, dict) else "UNKNOWN_ERROR"
                err_msg = err_info.get("message", "Erreur renvoyée par MELODI") if isinstance(err_info, dict) else str(err_info)
                if err_code in ("AUTHENTICATION_REQUIRED", "UNAUTHORIZED"):
                    raise MelodiAuthenticationError(
                        f"Authentification refusée par MELODI : {err_msg}",
                        details={"url": url, "correlation_id": cid, "data": data},
                    )
                elif err_code in ("PERMISSION_DENIED", "FORBIDDEN"):
                    raise MelodiAuthorizationError(
                        f"Droits insuffisants (MELODI) : {err_msg}",
                        details={"url": url, "correlation_id": cid, "data": data},
                    )
                elif err_code == "TOOL_NOT_FOUND":
                    raise ToolNotFoundError(
                        f"Outil introuvable (MELODI) : {err_msg}",
                        details={"url": url, "correlation_id": cid, "data": data},
                    )
                else:
                    raise ToolExecutionError(
                        f"Erreur MELODI [{err_code}] : {err_msg}",
                        details={"url": url, "correlation_id": cid, "data": data},
                    )

            return data

        except httpx.TimeoutException as e:
            logger.warning("Timeout lors de l'appel MELODI %s (CID: %s): %s", url, cid, e)
            raise MelodiTimeoutError(
                f"Délai d'attente dépassé lors de l'appel à MELODI ({self.settings.melodi_timeout_seconds}s)",
                details={"url": url, "correlation_id": cid},
            )
        except httpx.ConnectError as e:
            logger.error("Échec de connexion vers MELODI %s: %s", url, e)
            raise MelodiConnectionError(
                f"Impossible de se connecter à MELODI ({self.settings.melodi_base_url}) : {e}",
                details={"url": url, "correlation_id": cid},
            )
        except httpx.RequestError as e:
            logger.error("Erreur réseau vers MELODI %s: %s", url, e)
            raise MelodiConnectionError(
                f"Erreur réseau lors de la communication avec MELODI : {e}",
                details={"url": url, "correlation_id": cid},
            )

    async def get_health(self) -> dict[str, Any]:
        """Interroge l'état de santé du module melodi_mcp de l'ERP."""
        return await self._request("GET", "/health")

    async def get_manifest(self) -> dict[str, Any]:
        """Récupère le manifeste du module melodi_mcp."""
        return await self._request("GET", "/manifest")

    async def get_tools(self, user_key: Optional[str] = None) -> dict[str, Any]:
        """Récupère le catalogue d'outils disponibles auprès de MELODI."""
        return await self._request("GET", "/tools", user_key=user_key)

    async def execute_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        correlation_id: Optional[str] = None,
        user_id: Optional[str] = None,
        org_id: Optional[str] = None,
        user_key: Optional[str] = None,
        call_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """Transmet l'exécution d'un outil à MELODI."""
        confirmed = bool(arguments.get("_confirmed", arguments.get("confirmed", False)))
        payload = {
            "tool_name": tool_name,
            "call_id": call_id or str(uuid.uuid4()),
            "correlation_id": correlation_id or call_id or str(uuid.uuid4()),
            "confirmed": confirmed,
            "arguments": arguments,
            "context": {
                "client": "melodi_mcp_server",
                "user_id": user_id,
                "org_id": org_id,
                "user_key": user_key,
                "user_token": user_key,
            },
        }
        return await self._request(
            method="POST",
            path=f"/tools/{tool_name}/execute",
            correlation_id=correlation_id,
            user_id=user_id,
            org_id=org_id,
            user_key=user_key,
            json_data=payload,
        )
