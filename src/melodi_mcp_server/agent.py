"""Agent IA interactif pour MELODI ERP via le protocole MCP.

Ce module permet à un modèle de langage (OpenAI, Ollama, Groq, LM Studio ou mode simulé local)
de découvrir dynamiquement les outils exposés par melodi_mcp_server et de les exécuter.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any, Dict, List, Optional, Tuple

import httpx
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamable_http_client
import mcp.types as mcp_types

# Tentative d'import du client OpenAI officiel si disponible
try:
    from openai import AsyncOpenAI
    HAS_OPENAI_SDK = True
except ImportError:
    HAS_OPENAI_SDK = False


class MelodiMCPAgent:
    """Agent interactif connecté au serveur MELODI MCP."""

    def __init__(
        self,
        transport: str = "http",
        server_url: str = "http://127.0.0.1:8765/mcp",
        stdio_cmd: str = "./venv/bin/melodi-mcp-server",
        provider: str = "auto",
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        mcp_api_key: Optional[str] = None,
    ):
        self.transport = transport
        self.server_url = server_url
        self.stdio_cmd = stdio_cmd
        self.mcp_api_key = mcp_api_key or os.getenv("MCP_API_KEY")

        # Configuration LLM
        self.api_key = api_key or os.getenv("OPENAI_API_KEY") or os.getenv("LLM_API_KEY")
        self.base_url = base_url or os.getenv("OPENAI_BASE_URL") or os.getenv("OLLAMA_BASE_URL")

        if provider == "auto":
            if self.api_key:
                self.provider = "openai"
                self.model = model or "gpt-4o-mini"
            elif self.base_url and "11434" in self.base_url:
                self.provider = "ollama"
                self.model = model or "llama3.1"
                self.api_key = self.api_key or "ollama"
            else:
                self.provider = "simulated"
                self.model = model or "builtin-intent-agent"
        else:
            self.provider = provider
            self.model = model or ("gpt-4o-mini" if provider == "openai" else "llama3.1")
            if provider == "ollama" and not self.base_url:
                self.base_url = "http://localhost:11434/v1"
                self.api_key = self.api_key or "ollama"

        self.tools: List[mcp_types.Tool] = []
        self.openai_tools: List[Dict[str, Any]] = []
        self.messages: List[Dict[str, Any]] = [
            {
                "role": "system",
                "content": (
                    "Tu es un assistant IA d'entreprise pour MELODI ERP. "
                    "Tu disposes d'outils connectés en direct via le protocole MCP pour consulter "
                    "et gérer les données de l'ERP (notamment le blog économique et les statistiques). "
                    "Utilise toujours les outils à ta disposition lorsque l'utilisateur pose une question "
                    "relative aux données ou demande une action."
                ),
            }
        ]

    def _convert_tools_to_openai(self) -> List[Dict[str, Any]]:
        """Convertit les outils MCP en spécifications d'outils compatibles OpenAI."""
        converted = []
        for t in self.tools:
            parameters = t.input_schema or {"type": "object", "properties": {}}
            converted.append(
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description or "",
                        "parameters": parameters,
                    },
                }
            )
        return converted

    async def execute_mcp_tool(
        self, session: ClientSession, tool_name: str, arguments: Dict[str, Any]
    ) -> Tuple[bool, str]:
        """Exécute un outil via la session MCP et retourne (is_error, content_str)."""
        try:
            res = await session.call_tool(tool_name, arguments)
            text_parts = [c.text for c in res.content if hasattr(c, "text")]
            content_str = "\n".join(text_parts) if text_parts else json.dumps(res.model_dump())
            return res.is_error, content_str
        except Exception as e:
            return True, f"Erreur d'exécution de l'outil MCP '{tool_name}': {e}"

    async def handle_user_prompt(
        self, session: ClientSession, user_text: str
    ) -> str:
        """Traite une requête utilisateur avec l'agent et retourne la réponse finale."""
        if self.provider == "simulated":
            return await self._handle_simulated(session, user_text)
        else:
            return await self._handle_llm(session, user_text)

    async def _handle_simulated(
        self, session: ClientSession, user_text: str
    ) -> str:
        """Mode simulé autonome sans clé API : analyse l'intention, appelle les vrais outils MCP."""
        lower = user_text.lower().strip()

        target_tool = None
        args: Dict[str, Any] = {}

        if any(w in lower for w in ["stat", "chiffre", "métrique", "vue"]):
            target_tool = "ecoblog_get_stats"
            args = {}
        elif any(w in lower for w in ["liste", "affiche", "tous les articles", "derniers articles"]):
            target_tool = "ecoblog_list_articles"
            args = {"limit": 5}
        elif "cherche" in lower or "trouve" in lower or "recherche" in lower:
            target_tool = "ecoblog_search_articles"
            # Extrait les mots clés simples
            clean = lower.replace("cherche", "").replace("trouve", "").replace("recherche", "").strip()
            args = {"query": clean or "économie"}
        elif "détail" in lower or "article" in lower and any(char.isdigit() for char in lower):
            # Cherche un ID ou slug
            target_tool = "ecoblog_get_article"
            args = {"id": 1}
        else:
            # Par défaut, liste les articles
            target_tool = "ecoblog_list_articles"
            args = {"limit": 5}

        # Vérifier si l'outil cible existe dans le catalogue découvert
        available_names = [t.name for t in self.tools]
        if target_tool not in available_names:
            return (
                f"[Mode Simulé] Outil déduit '{target_tool}' non disponible dans le catalogue actuel. "
                f"Outils disponibles : {', '.join(available_names)}"
            )

        print(f"\n[Agent IA - Intention détectée] -> Appel de l'outil MCP '{target_tool}' avec {args}...")
        is_error, result_str = await self.execute_mcp_tool(session, target_tool, args)

        if is_error:
            return f"❌ L'outil '{target_tool}' a retourné une erreur :\n{result_str}"

        try:
            parsed = json.loads(result_str)
            data = parsed.get("data", parsed)
            pretty_json = json.dumps(data, indent=2, ensure_ascii=False)
            return (
                f"✅ [Résultat reçu depuis MELODI ERP via MCP]\n"
                f"Outil exécuté : {target_tool}\n\n"
                f"{pretty_json}"
            )
        except Exception:
            return f"✅ [Résultat reçu depuis MELODI ERP via MCP] :\n{result_str}"

    async def _handle_llm(
        self, session: ClientSession, user_text: str
    ) -> str:
        """Mode LLM (OpenAI, Ollama, ou compatible) avec boucle d'appel d'outils récursive."""
        if not HAS_OPENAI_SDK:
            return "❌ Le package 'openai' n'est pas installé. Lancez : pip install openai"

        client_kwargs: Dict[str, Any] = {}
        if self.api_key:
            client_kwargs["api_key"] = self.api_key
        if self.base_url:
            client_kwargs["base_url"] = self.base_url

        client = AsyncOpenAI(**client_kwargs)

        # Ajouter le message utilisateur à l'historique
        self.messages.append({"role": "user", "content": user_text})

        # Boucle de réflexion et d'exécution d'outils (jusqu'à 5 itérations max)
        for _ in range(5):
            print("\n[Agent IA] Réflexion du modèle en cours...")
            try:
                response = await client.chat.completions.create(
                    model=self.model,
                    messages=self.messages,
                    tools=self.openai_tools if self.openai_tools else None,
                    tool_choice="auto" if self.openai_tools else None,
                )
            except Exception as e:
                return f"❌ Erreur lors de l'appel au modèle ({self.provider}/{self.model}) : {e}"

            choice = response.choices[0]
            message = choice.message

            # Si le modèle ne demande aucun outil, on retourne sa réponse finale
            if not message.tool_calls:
                final_content = message.content or ""
                self.messages.append({"role": "assistant", "content": final_content})
                return final_content

            # Ajouter l'intention du modèle à l'historique
            self.messages.append(message.model_dump())

            # Exécuter chaque outil demandé
            for tool_call in message.tool_calls:
                func_name = tool_call.function.name
                raw_args = tool_call.function.arguments
                try:
                    args = json.loads(raw_args) if isinstance(raw_args, str) else (raw_args or {})
                except Exception:
                    args = {}

                print(f"[Agent IA] 🔧 Déclenchement de l'outil MCP : {func_name}")
                print(f"           Paramètres : {json.dumps(args, ensure_ascii=False)}")

                is_error, tool_output = await self.execute_mcp_tool(session, func_name, args)

                status_emoji = "❌" if is_error else "📦"
                print(f"[Serveur MCP] {status_emoji} Réponse obtenue pour {func_name} (taille: {len(tool_output)} car.)")

                # Renvoyer le résultat de l'outil dans l'historique de conversation
                self.messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "name": func_name,
                        "content": tool_output,
                    }
                )

        return "⚠️ Nombre maximum d'itérations d'outils atteint sans réponse finale."

    async def run_session(self) -> None:
        """Démarre la session MCP selon le transport choisi et lance la boucle interactive."""
        if self.transport == "stdio":
            print(f"🔗 Connexion au serveur MCP via STDIO ({self.stdio_cmd})...")
            server_params = StdioServerParameters(
                command=self.stdio_cmd,
                args=["--transport", "stdio"],
            )
            async with stdio_client(server_params) as (read_stream, write_stream):
                async with ClientSession(read_stream, write_stream) as session:
                    await self._interactive_repl(session)
        else:
            print(f"🔗 Connexion au serveur MCP via Streamable HTTP ({self.server_url})...")
            headers = {}
            if self.mcp_api_key:
                headers["X-MCP-API-KEY"] = self.mcp_api_key
            http_client = httpx.AsyncClient(headers=headers) if headers else None
            async with streamable_http_client(self.server_url, http_client=http_client) as (read_stream, write_stream):
                async with ClientSession(read_stream, write_stream) as session:
                    await self._interactive_repl(session)

    async def _interactive_repl(self, session: ClientSession) -> None:
        """Boucle REPL interactive pour converser avec l'agent."""
        print("⚡ Initialisation du protocole MCP...")
        await session.initialize()

        print("🔍 Découverte des outils disponibles sur MELODI ERP...")
        tools_res = await session.list_tools()
        self.tools = tools_res.tools
        self.openai_tools = self._convert_tools_to_openai()

        print("\n" + "=" * 65)
        print(" 🤖 AGENT IA INTERACTIF CONNECTÉ À MELODI MCP")
        print("=" * 65)
        print(f" • Transport MCP   : {self.transport.upper()}")
        print(f" • Moteur IA       : {self.provider.upper()} ({self.model})")
        print(f" • Outils découverts : {len(self.tools)} outils enregistrés")
        for t in self.tools:
            first_line = (t.description or "").split("\n")[0]
            print(f"    - \033[1;36m{t.name}\033[0m : {first_line}")
        print("=" * 65)
        print("💡 Astuce : Tapez votre demande en langage naturel.")
        print("   Commandes disponibles : /tools, /clear, /provider, /exit\n")

        loop = asyncio.get_running_loop()

        while True:
            try:
                # Lecture asynchrone depuis stdin sans bloquer la boucle d'événements
                user_input = await loop.run_in_executor(None, input, "\033[1;32mmelodi-agent>\033[0m ")
                user_input = user_input.strip()

                if not user_input:
                    continue

                if user_input.lower() in ("exit", "quit", "q", "/exit"):
                    print("👋 Déconnexion de l'agent. À bientôt !")
                    break

                if user_input == "/tools":
                    print(f"\n📋 Outils MCP disponibles ({len(self.tools)}) :")
                    for t in self.tools:
                        print(f"\n  • \033[1;33m{t.name}\033[0m")
                        print(f"    Description : {t.description}")
                        print(f"    Schéma : {json.dumps(t.input_schema, indent=2)}")
                    print()
                    continue

                if user_input == "/clear":
                    self.messages = [self.messages[0]]
                    print("🧹 Historique de conversation effacé.")
                    continue

                if user_input.startswith("/provider"):
                    parts = user_input.split()
                    if len(parts) > 1:
                        self.provider = parts[1]
                        if len(parts) > 2:
                            self.model = parts[2]
                        print(f"🔄 Fournisseur changé pour : {self.provider} ({self.model})")
                    else:
                        print(f"Fournisseur actuel : {self.provider} ({self.model})")
                    continue

                # Traitement de la requête utilisateur
                answer = await self.handle_user_prompt(session, user_input)
                print(f"\n\033[1;34m[Assistant MELODI]\033[0m\n{answer}\n")

            except (EOFError, KeyboardInterrupt):
                print("\n👋 Fermeture demandée. Déconnexion...")
                break
            except Exception as e:
                print(f"\n❌ Erreur : {e}\n")


def main() -> None:
    """Point d'entrée CLI pour exécuter l'agent."""
    import argparse

    parser = argparse.ArgumentParser(description="Agent IA interactif pour MELODI MCP Server")
    parser.add_argument(
        "--transport",
        choices=["http", "stdio"],
        default="http",
        help="Transport MCP (défaut: http)",
    )
    parser.add_argument(
        "--url",
        default="http://127.0.0.1:8765/mcp",
        help="URL du point de terminaison MCP Streamable HTTP (défaut: http://127.0.0.1:8765/mcp)",
    )
    parser.add_argument(
        "--mcp-api-key",
        default=None,
        help="Clé secrète requise par le serveur MCP (ou variable MCP_API_KEY)",
    )
    parser.add_argument(
        "--provider",
        choices=["auto", "openai", "ollama", "simulated"],
        default="auto",
        help="Moteur d'intelligence artificielle (auto, openai, ollama, simulated)",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Modèle LLM à utiliser (ex: gpt-4o-mini, llama3.1)",
    )
    parser.add_argument(
        "--api-key",
        default=None,
        help="Clé API OpenAI / LLM (ou variable OPENAI_API_KEY)",
    )
    parser.add_argument(
        "--base-url",
        default=None,
        help="Base URL compatible OpenAI (ex: http://localhost:11434/v1 pour Ollama)",
    )

    args = parser.parse_args()

    agent = MelodiMCPAgent(
        transport=args.transport,
        server_url=args.url,
        provider=args.provider,
        model=args.model,
        api_key=args.api_key,
        base_url=args.base_url,
        mcp_api_key=args.mcp_api_key,
    )

    try:
        asyncio.run(agent.run_session())
    except KeyboardInterrupt:
        print("\nSession terminée.")


if __name__ == "__main__":
    main()
