#!/usr/bin/env python3
"""Script de lancement direct de l'agent IA interactif MELODI."""

import sys
import os

# S'assurer que le package src est dans le sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from melodi_mcp_server.agent import main

if __name__ == "__main__":
    main()
