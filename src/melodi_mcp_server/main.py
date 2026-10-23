"""Point d'entrée principal en ligne de commande pour melodi_mcp_server."""

import argparse
import asyncio
import sys
import uvicorn

from .logging_config import configure_logging
from .mcp_server import MelodiMcpServerApp
from .settings import Settings, get_settings, set_settings


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="melodi-mcp-server",
        description="Serveur MCP indépendant pour MELODI ERP",
    )
    parser.add_argument(
        "--transport",
        choices=["stdio", "http", "sse"],
        help="Transport MCP à utiliser (stdio par défaut, ou http/sse pour le mode web)",
    )
    parser.add_argument(
        "--host",
        help="Adresse d'écoute du serveur HTTP (ex: 127.0.0.1 ou 0.0.0.0)",
    )
    parser.add_argument(
        "--port",
        type=int,
        help="Port d'écoute du serveur HTTP (ex: 8765)",
    )
    parser.add_argument(
        "--melodi-url",
        help="URL racine de MELODI ERP (ex: http://localhost:8000)",
    )
    parser.add_argument(
        "--melodi-prefix",
        help="Préfixe des routes de l'API MCP MELODI (ex: /internal/mcp, /api/v1/mcp)",
    )
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Niveau de journalisation",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    settings = get_settings()

    # Remplacement des paramètres par les drapeaux CLI si fournis
    overrides = {}
    if args.transport:
        overrides["mcp_transport"] = args.transport
    if args.host:
        overrides["mcp_server_host"] = args.host
    if args.port:
        overrides["mcp_server_port"] = args.port
    if args.melodi_url:
        overrides["melodi_base_url"] = args.melodi_url
    if args.melodi_prefix:
        overrides["melodi_api_prefix"] = args.melodi_prefix
    if args.log_level:
        overrides["log_level"] = args.log_level

    if overrides:
        settings = Settings(**{**settings.model_dump(), **overrides})
        set_settings(settings)

    # Initialisation du logging (toujours vers stderr pour protéger stdout en mode stdio)
    logger = configure_logging(level=settings.log_level, stream=sys.stderr)
    logger.info(
        "Démarrage de melodi_mcp_server [Transport: %s | Cible MELODI: %s%s]",
        settings.mcp_transport,
        settings.melodi_base_url,
        settings.melodi_api_prefix,
    )

    app = MelodiMcpServerApp(settings=settings)

    if settings.mcp_transport == "stdio":
        try:
            asyncio.run(app.run_stdio())
        except KeyboardInterrupt:
            logger.info("Arrêt du serveur MCP STDIO demandé par l'utilisateur.")
    else:
        # Mode HTTP / SSE
        http_app = app.create_http_app()
        logger.info(
            "Serveur MCP HTTP prêt sur http://%s:%d (Admin sur %s)",
            settings.mcp_server_host,
            settings.mcp_server_port,
            settings.admin_prefix if settings.admin_dashboard_enabled else "désactivé",
        )
        uvicorn.run(
            http_app,
            host=settings.mcp_server_host,
            port=settings.mcp_server_port,
            log_level=settings.log_level.lower(),
        )


def run_cli() -> None:
    """Wrapper pour la commande de script `melodi-mcp-server`."""
    try:
        main()
    except Exception as e:
        sys.stderr.write(f"Erreur fatale au lancement de melodi_mcp_server : {e}\n")
        sys.exit(1)


if __name__ == "__main__":
    run_cli()
