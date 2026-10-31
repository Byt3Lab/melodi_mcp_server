"""Sous-package OAuth 2.0 pour melodi_mcp_server."""

from .routes import create_oauth_routes
from .templates import render_authorize_page

__all__ = ["create_oauth_routes", "render_authorize_page"]
