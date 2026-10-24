# MELODI MCP Server (`melodi_mcp_server`)

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![MCP Standard](https://img.shields.io/badge/MCP-Standard%202.x-green.svg)](https://modelcontextprotocol.io/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Serveur indépendant implémentant le protocole standardisé **Model Context Protocol (MCP)** pour **MELODI ERP**.  
Cette application agit comme une passerelle d'adaptation sécurisée et dynamique entre les clients MCP (assistants IA, Claude Desktop, Cursor, agents autonomes) et l'API interne du module `melodi_mcp` de l'ERP.

---

## 📑 Sommaire
1. [Architecture & Principes d'Isolation](#1-architecture--principes-disolation)
2. [Spécification du Contrat Interne MELODI (v1)](#2-spécification-du-contrat-interne-melodi-v1)
3. [Prérequis & Installation](#3-prérequis--installation)
4. [Configuration](#4-configuration)
5. [Démarrage & Transports (STDIO & HTTP)](#5-démarrage--transports-stdio--http)
6. [Console d'Administration Web (`/admin`)](#6-console-dadministration-web-admin)
7. [Cycle de Vie & Synchronisation Dynamique](#7-cycle-de-vie--synchronisation-dynamique)
8. [Sécurité & Confidentialité](#8-sécurité--confidentialité)
9. [Connexion avec un Client MCP (ex: Claude Desktop)](#9-connexion-avec-un-client-mcp-ex-claude-desktop)
10. [Tests Automatisés](#10-tests-automatisés)
11. [Limites Connues & Perspectives](#11-limites-connues--perspectives)

---

## 1. Architecture & Principes d'Isolation

`melodi_mcp_server` est entièrement découplé du code applicatif interne de MELODI :
- **Aucun import des modules métier** de MELODI ERP.
- **Aucun accès direct à la base de données** ERP.
- **Rôle d'adaptateur pur** : découverte de catalogue, conversion de schémas vers le format MCP, validation locale et dispatch des requêtes sécurisées avec injection d'identifiant de corrélation (`X-Correlation-ID`).

```text
┌───────────────────────────────────────┐
│     Client MCP (Claude, Agent IA)     │
└───────────────────┬───────────────────┘
                    │  STDIO ou HTTP/SSE (JSON-RPC)
┌───────────────────▼───────────────────┐
│           melodi_mcp_server           │
│  - mcp_server.py (SDK MCP 2.x)        │
│  - tool_registry.py (Catalogue dyn.)  │
│  - tool_dispatcher.py (Validation)    │
│  - admin/ (Console Web autonome)      │
└───────────────────┬───────────────────┘
                    │  HTTP/REST sécurisé (Bearer / Correlation ID)
┌───────────────────▼───────────────────┐
│          MELODI ERP (melodi_mcp)      │
│  - /api/melodi_mcp/internal/health    │
│  - /api/melodi_mcp/internal/manifest  │
│  - /api/melodi_mcp/internal/tools     │
│  - /api/melodi_mcp/internal/tools/... │
└───────────────────────────────────────┘
```

---

## 2. Spécification du Contrat Interne MELODI (v1)

Le serveur communique avec l'instance MELODI ERP via une API REST sécurisée. Le préfixe d'accès (`MELODI_API_PREFIX`) est **configurable** (par défaut `/api/melodi_mcp/internal`, mais peut être configuré par exemple sur `/internal/mcp` ou `/api/v1/mcp`).

### En-têtes obligatoires & contextuels
- `Authorization: Bearer <MELODI_API_KEY>` (ou `X-Melodi-Service-Token: <TOKEN>`)
- `X-Correlation-ID: <UUID>` : Identifiant de traçabilité unique généré par appel.
- `X-Melodi-User-ID: <USER_ID>` *(facultatif)* : Identité de l'utilisateur délégué si authentifié.
- `X-Melodi-Org-ID: <ORG_ID>` *(facultatif)* : Contexte de l'organisation.

### Routes requises côté MELODI
| Méthode | Route dynamique | Rôle |
|---|---|---|
| `GET` | `{MELODI_API_PREFIX}/health` | État de santé du module ERP et connectivité BD |
| `GET` | `{MELODI_API_PREFIX}/manifest` | Manifeste du module (version, révision, capacités) |
| `GET` | `{MELODI_API_PREFIX}/tools` | Catalogue complet des outils autorisés avec schémas JSON Schema |
| `POST` | `{MELODI_API_PREFIX}/tools/{name}/execute` | Exécution synchrone d'un outil métier |

---

## 3. Prérequis & Installation

### Prérequis
- **Python 3.10 ou supérieur** (testé et validé sous Python 3.12).
- `pip` ou un gestionnaire d'environnement virtuel standard.

### Installation

```bash
# 1. Naviguer dans le dossier du projet
cd melodi_mcp_server

# 2. Créer l'environnement virtuel
python3 -m venv venv
source venv/bin/activate

# 3. Installer le paquet en mode éditable avec ses dépendances
pip install --upgrade pip
pip install -e ".[dev]"
```

---

## 4. Configuration

Copiez le fichier `.env.example` vers `.env` et adaptez les variables :

```bash
cp .env.example .env
```

| Variable | Défaut | Description |
|---|---|---|
| `MELODI_BASE_URL` | `http://localhost:8000` | URL de base de votre instance MELODI ERP |
| `MELODI_API_PREFIX` | `/api/melodi_mcp/internal` | **Préfixe configurable** des routes de l'API MCP MELODI |
| `MELODI_API_KEY` | *(vide)* | Clé secrète de service pour s'authentifier auprès de l'ERP |
| `MELODI_TIMEOUT_SECONDS` | `30.0` | Timeout des requêtes HTTP vers MELODI (en secondes) |
| `MELODI_VERIFY_SSL` | `true` | Vérification TLS des certificats HTTPS |
| `MCP_TRANSPORT` | `stdio` | Mode de transport : `stdio` (local) ou `http`/`sse` (réseau) |
| `MCP_SERVER_HOST` | `127.0.0.1` | Adresse d'écoute en mode HTTP |
| `MCP_SERVER_PORT` | `8765` | Port d'écoute en mode HTTP |
| `ADMIN_DASHBOARD_ENABLED` | `true` | Activer la console d'administration web |
| `ADMIN_PREFIX` | `/admin` | Préfixe d'accès à la console d'administration |
| `ADMIN_API_KEY` | `admin` | Clé d'API d'authentification pour la console d'administration |
| `CATALOG_SYNC_INTERVAL_SECONDS` | `60` | Fréquence de resynchronisation automatique du catalogue |
| `MAX_REQUEST_SIZE_BYTES` | `1048576` (1 Mo) | Plafond de taille des arguments reçus |
| `MAX_RESPONSE_SIZE_BYTES` | `5242880` (5 Mo) | Plafond de taille des réponses acceptées |
| `LOG_LEVEL` | `INFO` | Niveau de journalisation (`DEBUG`, `INFO`, `WARNING`, etc.) |

---

## 5. Démarrage & Transports (STDIO & HTTP)

### Mode 1 : STDIO (Usage local avec Claude Desktop / CLI)
En mode `stdio`, le flux JSON-RPC transite exclusivement sur l'entrée/sortie standard (`stdin`/`stdout`). Tous les logs de diagnostic sont strictement redirigés vers `stderr` pour ne jamais corrompre le protocole.

```bash
# Démarrage direct via la commande de script
melodi-mcp-server --transport stdio

# Ou via le binaire Python :
./venv/bin/python -m melodi_mcp_server.main --transport stdio
```

### Mode 2 : HTTP / SSE & Console d'Administration
Idéal pour les déploiements conteneurisés, micro-services ou accès réseau partagé.

```bash
melodi-mcp-server --transport http --host 0.0.0.0 --port 8765
```

Endpoints exposés en mode HTTP :
- `/sse` ou `/mcp` : Endpoint de streaming MCP pour les clients distants.
- `/health` : Bilan de santé opérationnel (JSON).
- `/admin` : Console d'administration web responsive.

---

## 6. Console d'Administration Web (`/admin`)

La console d'administration web intégrée est **100% autonome** (HTML, CSS et JS embarqués sans aucune dépendance externe CDN, opérationnelle en réseau isolé ou air-gapped).

Accessible par défaut sur : **`http://localhost:8765/admin`**

### Fonctionnalités disponibles
1. **Statut & Métriques temps réel** : Uptime, état de liaison avec MELODI ERP, latence mesurée en millisecondes, total des requêtes, taux de succès.
2. **Explorateur d'Outils dynamique** : Tableau interactif avec recherche temps réel, distinction lecture/écriture, timeout et inspecteur de schéma JSON Schema.
3. **Synchronisation à chaud (Force Sync)** : Bouton d'action pour rafraîchir immédiatement le catalogue depuis MELODI sans redémarrage.
4. **Historique des appels récents** : Journal d'activité avec durée d'exécution, statut et identifiants de corrélation (avec masquage des secrets).
5. **Protection optionnelle** : Verrouillage possible par clé d'API via `ADMIN_API_KEY`.

---

## 7. Cycle de Vie & Synchronisation Dynamique

- **Démarrage tolérant aux pannes** : Si MELODI ERP est temporairement indisponible au boot, le serveur démarre en état `DEGRADED` et tente une reconnexion périodique sans planter.
- **Suivi des révisions (`catalog_revision`)** : Le serveur compare la révision renvoyée par MELODI pour éviter tout parsing superflu si les définitions n'ont pas changé.
- **Remplacement atomique** : Lors d'une mise à jour de catalogue, le registre en mémoire est remplacé atomiquement sous verrou asynchrone (`asyncio.Lock`), garantissant qu'aucun client ne voie un catalogue partiel.
- **Fermeture propre (Graceful Shutdown)** : Libération immédiate des tâches de fond et fermeture du pool de connexions HTTP (`MelodiAsyncClient.aclose()`).

---

## 8. Sécurité & Confidentialité

- **Sanitisation des logs** : Un filtre de journalisation (`SecretMaskingFormatter`) expurge automatiquement tout jeton Bearer, mot de passe ou clé d'API des journaux.
- **Validation stricte des schémas** : Les arguments envoyés par les clients sont validés localement via `jsonschema.Draft7Validator` avant toute transmission à l'ERP.
- **Garde-fous sur les outils sensibles** : Les outils marqués `requires_confirmation: true` exigent explicitement l'argument `_confirmed: true` pour s'exécuter.
- **Aucune exécution de code arbitraire** : Seuls les outils déclarés et autorisés par MELODI sont acceptés. L'ERP conserve l'autorité absolue sur les permissions finales.

---

## 9. Agent IA Interactif (Client MCP Intégré)

Le projet inclut un agent conversationnel en ligne de commande directement opérationnel, capable de se connecter au serveur MCP (en HTTP Streamable ou STDIO), de découvrir dynamiquement l'intégralité des outils de MELODI ERP et de les invoquer selon les instructions de l'utilisateur.

### Lancement direct :

```bash
# 1. Mode Simulé Autonome (Fonctionne immédiatement SANS clé API payante) :
./agent_runner.py
# ou : ./venv/bin/melodi-agent

# 2. Mode OpenAI (avec function calling réel) :
OPENAI_API_KEY="sk-..." ./agent_runner.py --provider openai --model gpt-4o-mini

# 3. Mode Ollama (100% Local et Gratuit) :
./agent_runner.py --provider ollama --model llama3.1

# 4. Connexion via STDIO (lance le serveur à la volée sans daemon HTTP) :
./agent_runner.py --transport stdio
```

### Exemple d'interaction dans le terminal :

```text
=================================================================
 🤖 AGENT IA INTERACTIF CONNECTÉ À MELODI MCP
=================================================================
 • Transport MCP     : HTTP
 • Moteur IA         : SIMULATED (builtin-intent-agent)
 • Outils découverts : 7 outils enregistrés
    - ecoblog_create_article : Rédige et publie un nouvel article...
    - ecoblog_get_stats : Tableau de bord analytique du blog...
    - ecoblog_list_articles : Liste paginée des articles...
=================================================================

melodi-agent> Donne-moi les statistiques du blog
[Agent IA - Intention détectée] -> Appel de l'outil MCP 'ecoblog_get_stats' avec {}...
✅ [Résultat reçu depuis MELODI ERP via MCP]
Outil exécuté : ecoblog_get_stats
{
  "total_articles": 5,
  "total_views": 0,
  "by_category": { "fintech": 1, "macroéconomie": 1, ... }
}

melodi-agent> /tools
📋 Outils MCP disponibles (7) : ...
```

---

## 10. Connexion avec un Client MCP Tiers (ex: Claude Desktop)

Pour connecter Claude Desktop à `melodi_mcp_server`, ajoutez la configuration suivante dans votre fichier de configuration (`claude_desktop_config.json`) :

### Sous Linux / macOS :
`~/.config/Claude/claude_desktop_config.json`

```json
{
  "mcpServers": {
    "melodi_erp": {
      "command": "/home/gomsugaetant/Documents/byt3lab/projets/melodi_mcp_server/venv/bin/melodi-mcp-server",
      "args": ["--transport", "stdio"],
      "env": {
        "MELODI_BASE_URL": "http://localhost:8000",
        "MELODI_API_PREFIX": "/api/melodi_mcp/internal",
        "MELODI_API_KEY": "votre_cle_secrete_melodi"
      }
    }
  }
}
```

---

## 10. Tests Automatisés

La suite de tests automatisés utilise `pytest`, `pytest-asyncio` et `respx` pour simuler l'ensemble des scénarios sans nécessiter de serveur MELODI réel.

```bash
# Exécution de l'intégralité des tests unitaires et d'intégration
./venv/bin/pytest -v tests/
```

### Couverture des 24 exigences formelles :
- Tests 1 à 2b : Chargement config, validation des rejets, préfixes d'API personnalisables.
- Tests 3 à 6 : Client HTTP asynchrone, authentification, timeouts, manifest et catalogue.
- Tests 7 à 11 & 18 : Registre dynamique, conversion `Tool`, collisions de noms, suppression d'outils obsolètes, suivi de révision.
- Tests 12 à 17 & 22 : Exécution lecture/écriture, confirmation requise, timeouts, erreurs métier et revalidation ERP.
- Tests 19 & 21 : Non-divulgation de secrets dans les logs, fermeture gracieuse.
- Test 20 : Cycle de vie du serveur et transport.
- Tests 23 & 24 : Dashboard d'administration (rendu HTML, APIs JSON de statut, synchronisation forcée).

---

## 11. Limites Connues & Perspectives

- **Module ERP en cours d'implémentation** : Le serveur est prêt et aligné sur le contrat `v1`. Dès que le module `melodi_mcp` sera déployé dans l'ERP, aucune modification de code ne sera requise côté serveur.
- **Persistance locale des métriques** : Les métriques de la console d'administration sont conservées en mémoire vive (anneau circulaire des 50 derniers appels). Une extension SQLite pourra être ajoutée si un archivage d'audit persistant sur disque est nécessaire.
