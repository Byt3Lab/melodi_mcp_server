"""Template HTML / CSS / JS embarqué autonome pour la console d'administration."""

SETUP_HTML = r"""<!DOCTYPE html>
<html lang="fr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>MELODI MCP Server - Initialisation</title>
    <style>
        :root {
            --bg: #0f172a;
            --surface: #1e293b;
            --border: #334155;
            --text: #f8fafc;
            --text-muted: #94a3b8;
            --primary: #3b82f6;
            --primary-hover: #2563eb;
            --success: #10b981;
            --danger: #ef4444;
            --radius: 8px;
            --font: system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
        }
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { font-family: var(--font); background: var(--bg); color: var(--text); min-height: 100vh; display: flex; align-items: center; justify-content: center; padding: 20px; }
        .card { background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); padding: 32px; width: 100%; max-width: 460px; box-shadow: 0 10px 25px rgba(0,0,0,0.5); }
        .logo { font-size: 1.5rem; font-weight: 700; margin-bottom: 8px; text-align: center; }
        .badge { display: inline-block; background: rgba(59, 130, 246, 0.15); color: #60a5fa; border: 1px solid #3b82f6; padding: 3px 8px; border-radius: 9999px; font-size: 0.75rem; font-weight: 600; margin-bottom: 12px; }
        .subtitle { font-size: 0.9rem; color: var(--text-muted); margin-bottom: 24px; text-align: center; line-height: 1.4; }
        .form-group { margin-bottom: 16px; }
        label { display: block; font-size: 0.85rem; font-weight: 500; margin-bottom: 6px; color: var(--text); }
        input[type="text"], input[type="password"] { width: 100%; padding: 10px 14px; background: #0f172a; border: 1px solid var(--border); border-radius: var(--radius); color: var(--text); font-size: 0.95rem; outline: none; }
        input[type="text"]:focus, input[type="password"]:focus { border-color: var(--primary); }
        .btn { width: 100%; background: var(--primary); color: white; border: none; padding: 12px; border-radius: var(--radius); font-size: 0.95rem; font-weight: 600; cursor: pointer; transition: background 0.2s; margin-top: 8px; }
        .btn:hover { background: var(--primary-hover); }
        .btn:disabled { opacity: 0.6; cursor: not-allowed; }
        .code { font-family: monospace; background: rgba(0,0,0,0.3); padding: 2px 6px; border-radius: 4px; color: #38bdf8; font-size: 0.85rem; }
        .alert { padding: 10px 14px; border-radius: var(--radius); font-size: 0.85rem; margin-bottom: 16px; display: none; }
        .alert-error { background: rgba(239, 68, 68, 0.2); border: 1px solid var(--danger); color: #fca5a5; }
        .alert-info { background: rgba(59, 130, 246, 0.15); border: 1px solid var(--primary); color: #93c5fd; }
        .hint { font-size: 0.8rem; color: var(--text-muted); margin-top: 16px; text-align: center; }
    </style>
</head>
<body>
    <div class="card">
        <div style="text-align: center;">
            <div class="logo">⚡ MELODI MCP Server</div>
            <span class="badge">Configuration Initiale</span>
            <div class="subtitle">
                Aucun compte administrateur n'est encore enregistré. Saisissez la clé d'amorçage pour créer votre compte administrateur principal.
            </div>
        </div>

        <div id="alert-box" class="alert alert-error"></div>

        <form id="setup-form" onsubmit="handleSetup(event)">
            <div class="form-group">
                <label for="setup-key">Clé d'amorçage (ADMIN_API_KEY)</label>
                <input type="password" id="setup-key" value="admin" placeholder="Clé d'amorçage (par défaut: admin)" required autofocus>
            </div>
            <div class="form-group">
                <label for="username">Identifiant du Premier Administrateur</label>
                <input type="text" id="username" value="admin" placeholder="ex: admin" required>
            </div>
            <div class="form-group">
                <label for="password">Mot de passe</label>
                <input type="password" id="password" placeholder="Choisissez un mot de passe robuste" required minlength="4">
            </div>
            <div class="form-group">
                <label for="password-confirm">Confirmer le mot de passe</label>
                <input type="password" id="password-confirm" placeholder="Confirmez le mot de passe" required minlength="4">
            </div>
            <button type="submit" id="submit-btn" class="btn">Créer le compte & Accéder au Dashboard</button>
        </form>

        <div class="hint">
            💡 Clé d'amorçage par défaut : <span class="code">admin</span>
        </div>
    </div>

    <script>
        async function handleSetup(e) {
            e.preventDefault();
            const alertBox = document.getElementById('alert-box');
            const submitBtn = document.getElementById('submit-btn');
            alertBox.style.display = 'none';

            const setupKey = document.getElementById('setup-key').value.trim();
            const username = document.getElementById('username').value.trim();
            const password = document.getElementById('password').value;
            const passwordConfirm = document.getElementById('password-confirm').value;

            if (password !== passwordConfirm) {
                alertBox.innerText = 'Les deux mots de passe ne correspondent pas.';
                alertBox.style.display = 'block';
                return;
            }

            submitBtn.disabled = true;
            submitBtn.innerText = 'Initialisation en cours...';

            try {
                const res = await fetch(window.location.pathname.replace(/\/$/, '') + '/api/setup', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        setup_key: setupKey,
                        username: username,
                        password: password
                    })
                });

                const data = await res.json();
                if (res.ok && data.success) {
                    window.location.href = window.location.pathname;
                } else {
                    alertBox.innerText = data.error || 'Erreur lors de la configuration initiale.';
                    alertBox.style.display = 'block';
                    submitBtn.disabled = false;
                    submitBtn.innerText = 'Créer le compte & Accéder au Dashboard';
                }
            } catch (err) {
                alertBox.innerText = 'Erreur réseau : ' + err.message;
                alertBox.style.display = 'block';
                submitBtn.disabled = false;
                submitBtn.innerText = 'Créer le compte & Accéder au Dashboard';
            }
        }
    </script>
</body>
</html>
"""

LOGIN_HTML = r"""<!DOCTYPE html>
<html lang="fr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>MELODI MCP Server - Connexion</title>
    <style>
        :root {
            --bg: #0f172a;
            --surface: #1e293b;
            --border: #334155;
            --text: #f8fafc;
            --text-muted: #94a3b8;
            --primary: #3b82f6;
            --primary-hover: #2563eb;
            --danger: #ef4444;
            --radius: 8px;
            --font: system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
        }
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { font-family: var(--font); background: var(--bg); color: var(--text); min-height: 100vh; display: flex; align-items: center; justify-content: center; padding: 20px; }
        .card { background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); padding: 32px; width: 100%; max-width: 400px; box-shadow: 0 10px 25px rgba(0,0,0,0.5); }
        .logo { font-size: 1.5rem; font-weight: 700; margin-bottom: 8px; text-align: center; }
        .subtitle { font-size: 0.9rem; color: var(--text-muted); margin-bottom: 24px; text-align: center; }
        .form-group { text-align: left; margin-bottom: 16px; }
        label { display: block; font-size: 0.85rem; font-weight: 500; margin-bottom: 6px; color: var(--text); }
        input[type="text"], input[type="password"] { width: 100%; padding: 10px 14px; background: #0f172a; border: 1px solid var(--border); border-radius: var(--radius); color: var(--text); font-size: 0.95rem; outline: none; }
        input[type="text"]:focus, input[type="password"]:focus { border-color: var(--primary); }
        .btn { width: 100%; background: var(--primary); color: white; border: none; padding: 12px; border-radius: var(--radius); font-size: 0.95rem; font-weight: 600; cursor: pointer; transition: background 0.2s; margin-top: 8px; }
        .btn:hover { background: var(--primary-hover); }
        .btn:disabled { opacity: 0.6; cursor: not-allowed; }
        .error { color: var(--danger); font-size: 0.85rem; margin-bottom: 14px; padding: 8px 12px; background: rgba(239, 68, 68, 0.15); border: 1px solid var(--danger); border-radius: var(--radius); display: none; }
    </style>
</head>
<body>
    <div class="card">
        <div class="logo">⚡ MELODI MCP Server</div>
        <div class="subtitle">Console d'Administration</div>
        
        <div id="error-msg" class="error"></div>

        <form id="login-form" onsubmit="handleLogin(event)">
            <div class="form-group">
                <label for="username">Identifiant</label>
                <input type="text" id="username" placeholder="Entrez votre identifiant..." required autofocus>
            </div>
            <div class="form-group">
                <label for="password">Mot de passe</label>
                <input type="password" id="password" placeholder="Entrez votre mot de passe..." required>
            </div>
            <button type="submit" id="submit-btn" class="btn">Se connecter</button>
        </form>
    </div>

    <script>
        async function handleLogin(e) {
            e.preventDefault();
            const errorBox = document.getElementById('error-msg');
            const submitBtn = document.getElementById('submit-btn');
            errorBox.style.display = 'none';

            const username = document.getElementById('username').value.trim();
            const password = document.getElementById('password').value;

            submitBtn.disabled = true;
            submitBtn.innerText = 'Connexion...';

            try {
                const res = await fetch(window.location.pathname.replace(/\/$/, '') + '/api/login', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ username: username, password: password })
                });

                const data = await res.json();
                if (res.ok && data.success) {
                    window.location.href = window.location.pathname;
                } else {
                    errorBox.innerText = data.error || 'Identifiant ou mot de passe incorrect.';
                    errorBox.style.display = 'block';
                    submitBtn.disabled = false;
                    submitBtn.innerText = 'Se connecter';
                }
            } catch (err) {
                errorBox.innerText = 'Erreur réseau : ' + err.message;
                errorBox.style.display = 'block';
                submitBtn.disabled = false;
                submitBtn.innerText = 'Se connecter';
            }
        }
    </script>
</body>
</html>
"""

DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="fr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>MELODI MCP Server - Administration</title>
    <style>
        :root {
            --bg: #0f172a;
            --surface: #1e293b;
            --surface-hover: #334155;
            --border: #334155;
            --text: #f8fafc;
            --text-muted: #94a3b8;
            --primary: #3b82f6;
            --primary-hover: #2563eb;
            --success: #10b981;
            --warning: #f59e0b;
            --danger: #ef4444;
            --radius: 8px;
            --font: system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
        }
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { font-family: var(--font); background: var(--bg); color: var(--text); padding: 24px; line-height: 1.5; }
        .container { max-width: 1200px; margin: 0 auto; }
        header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 24px; padding-bottom: 16px; border-bottom: 1px solid var(--border); flex-wrap: wrap; gap: 12px; }
        .logo-group { display: flex; align-items: center; gap: 12px; }
        .badge { padding: 4px 10px; border-radius: 9999px; font-size: 0.75rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.05em; }
        .badge-healthy { background: rgba(16, 185, 129, 0.15); color: #34d399; border: 1px solid #10b981; }
        .badge-degraded { background: rgba(245, 158, 11, 0.15); color: #fbbf24; border: 1px solid #f59e0b; }
        .badge-unhealthy { background: rgba(239, 68, 68, 0.15); color: #f87171; border: 1px solid #ef4444; }
        .badge-read { background: rgba(59, 130, 246, 0.15); color: #60a5fa; border: 1px solid #3b82f6; }
        .badge-write { background: rgba(239, 68, 68, 0.15); color: #f87171; border: 1px solid #ef4444; }
        .badge-admin { background: rgba(139, 92, 246, 0.2); color: #c4b5fd; border: 1px solid #8b5cf6; }
        .badge-viewer { background: rgba(100, 116, 139, 0.2); color: #cbd5e1; border: 1px solid #64748b; }
        .badge-active { background: rgba(16, 185, 129, 0.15); color: #34d399; border: 1px solid #10b981; }
        .badge-inactive { background: rgba(239, 68, 68, 0.15); color: #f87171; border: 1px solid #ef4444; }
        .kpi-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 16px; margin-bottom: 24px; }
        .card { background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); padding: 20px; }
        .kpi-title { font-size: 0.85rem; color: var(--text-muted); text-transform: uppercase; margin-bottom: 8px; font-weight: 500; }
        .kpi-value { font-size: 2rem; font-weight: 700; color: var(--text); }
        .kpi-sub { font-size: 0.8rem; color: var(--text-muted); margin-top: 4px; }
        .section-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; margin-top: 24px; }
        .section-title { font-size: 1.25rem; font-weight: 600; }
        .btn { background: var(--primary); color: white; border: none; padding: 8px 16px; border-radius: var(--radius); cursor: pointer; font-weight: 500; font-size: 0.9rem; transition: background 0.2s; display: inline-flex; align-items: center; gap: 8px; }
        .btn:hover { background: var(--primary-hover); }
        .btn:disabled { opacity: 0.5; cursor: not-allowed; }
        .btn-outline { background: transparent; border: 1px solid var(--border); color: var(--text); }
        .btn-outline:hover { background: var(--surface-hover); }
        .btn-danger { background: rgba(239, 68, 68, 0.15); border: 1px solid var(--danger); color: #f87171; }
        .btn-danger:hover { background: var(--danger); color: white; }
        .btn-sm { padding: 4px 10px; font-size: 0.8rem; }
        .search-box { background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); padding: 8px 14px; color: var(--text); font-size: 0.9rem; width: 260px; outline: none; }
        .search-box:focus { border-color: var(--primary); }
        table { width: 100%; border-collapse: collapse; text-align: left; font-size: 0.9rem; }
        th { padding: 12px 16px; background: rgba(0,0,0,0.2); color: var(--text-muted); font-weight: 600; border-bottom: 1px solid var(--border); }
        td { padding: 12px 16px; border-bottom: 1px solid var(--border); vertical-align: middle; }
        tr:hover td { background: var(--surface-hover); }
        .code { font-family: monospace; font-size: 0.85rem; background: rgba(0,0,0,0.3); padding: 2px 6px; border-radius: 4px; }
        .toast { position: fixed; bottom: 20px; right: 20px; padding: 12px 20px; border-radius: var(--radius); color: white; font-weight: 500; font-size: 0.9rem; box-shadow: 0 4px 12px rgba(0,0,0,0.5); z-index: 1000; transition: transform 0.3s, opacity 0.3s; opacity: 0; pointer-events: none; }
        .toast.show { opacity: 1; transform: translateY(-10px); }
        .toast-success { background: var(--success); }
        .toast-error { background: var(--danger); }
        pre { background: #0b1120; padding: 12px; border-radius: 6px; overflow-x: auto; font-size: 0.85rem; color: #38bdf8; max-height: 250px; }
        .modal { display: none; position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0,0,0,0.7); backdrop-filter: blur(4px); z-index: 2000; justify-content: center; align-items: center; }
        .modal.active { display: flex; }
        .modal-content { background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); width: 90%; max-width: 550px; padding: 24px; position: relative; }
        .modal-close { position: absolute; top: 16px; right: 16px; background: none; border: none; color: var(--text-muted); font-size: 1.5rem; cursor: pointer; }
        .modal-close:hover { color: var(--text); }
        .tabs { display: flex; gap: 8px; margin-bottom: 20px; border-bottom: 1px solid var(--border); }
        .tab-btn { background: none; border: none; padding: 10px 18px; color: var(--text-muted); cursor: pointer; font-weight: 600; font-size: 0.95rem; border-bottom: 2px solid transparent; transition: color 0.2s; }
        .tab-btn:hover { color: var(--text); }
        .tab-btn.active { color: var(--primary); border-bottom-color: var(--primary); }
        .tab-pane { display: none; }
        .tab-pane.active { display: block; }
        .form-group { margin-bottom: 16px; text-align: left; }
        .form-group label { display: block; font-size: 0.85rem; font-weight: 500; margin-bottom: 6px; color: var(--text); }
        .form-group input, .form-group select { width: 100%; padding: 10px 12px; background: #0f172a; border: 1px solid var(--border); border-radius: var(--radius); color: var(--text); font-size: 0.9rem; outline: none; }
        .form-group input:focus, .form-group select:focus { border-color: var(--primary); }
        .actions-cell { display: flex; gap: 8px; align-items: center; }
    </style>
</head>
<body>
    <div class="container">
        <header>
            <div class="logo-group">
                <h1 style="font-size: 1.5rem; font-weight: 700;">⚡ MELODI MCP Server</h1>
                <span id="health-badge" class="badge badge-healthy">Chargement...</span>
            </div>
            <div style="display: flex; gap: 12px; align-items: center; flex-wrap: wrap;">
                <span id="current-user-badge" class="badge badge-admin" style="display: none;">👤 Chargement...</span>
                <label style="font-size: 0.85rem; color: var(--text-muted); display: flex; align-items: center; gap: 6px;">
                    <input type="checkbox" id="auto-refresh" checked> Auto-refresh (5s)
                </label>
                <button id="btn-sync" class="btn" onclick="triggerSync()">🔄 Actualiser Catalogue</button>
                <button class="btn btn-outline" onclick="handleLogout()">🚪 Déconnexion</button>
            </div>
        </header>

        <!-- KPI Cards -->
        <div class="kpi-grid">
            <div class="card">
                <div class="kpi-title">Outils Disponibles</div>
                <div class="kpi-value" id="kpi-tools">0</div>
                <div class="kpi-sub" id="kpi-revision">Révision : -</div>
            </div>
            <div class="card">
                <div class="kpi-title">Requêtes Traitées</div>
                <div class="kpi-value" id="kpi-requests">0</div>
                <div class="kpi-sub" id="kpi-success-rate">Taux de succès : 100%</div>
            </div>
            <div class="card">
                <div class="kpi-title">Latence MELODI ERP</div>
                <div class="kpi-value" id="kpi-latency">- ms</div>
                <div class="kpi-sub" id="kpi-erp-status">Liaison : Inconnue</div>
                <div class="kpi-sub" id="kpi-erp-url" style="word-break: break-all; margin-top: 2px;">URL : Chargement...</div>
            </div>
            <div class="card">
                <div class="kpi-title">Temps de Fonctionnement</div>
                <div class="kpi-value" id="kpi-uptime">0s</div>
                <div class="kpi-sub" id="kpi-last-sync">Dernière sync : Jamais</div>
            </div>
        </div>

        <!-- Navigation Tabs -->
        <div class="tabs">
            <button class="tab-btn active" onclick="switchTab('tools')">🛠️ Catalogue des Outils</button>
            <button class="tab-btn" onclick="switchTab('history')">📜 Historique des Appels</button>
            <button class="tab-btn" id="tab-btn-users" onclick="switchTab('users')">👥 Gestion des Utilisateurs</button>
            <button class="tab-btn" id="tab-btn-config" onclick="switchTab('config')">⚙️ Configuration ERP</button>
        </div>

        <!-- TAB 1 : Outils -->
        <div id="pane-tools" class="tab-pane active">
            <div class="section-header">
                <div class="section-title">Catalogue des Outils Découverts</div>
                <input type="text" id="tool-search" class="search-box" placeholder="Rechercher un outil..." oninput="filterTools()">
            </div>
            <div class="card" style="padding: 0; overflow-x: auto;">
                <table>
                    <thead>
                        <tr>
                            <th>Nom de l'outil</th>
                            <th>Description</th>
                            <th>Type</th>
                            <th>Confirmation</th>
                            <th>Timeout</th>
                            <th>Action</th>
                        </tr>
                    </thead>
                    <tbody id="tools-tbody">
                        <tr><td colspan="6" style="text-align: center; color: var(--text-muted);">Chargement du catalogue...</td></tr>
                    </tbody>
                </table>
            </div>
        </div>

        <!-- TAB 2 : Historique -->
        <div id="pane-history" class="tab-pane">
            <div class="section-header">
                <div class="section-title">Dernières Exécutions d'Outils</div>
            </div>
            <div class="card" style="padding: 0; overflow-x: auto;">
                <table>
                    <thead>
                        <tr>
                            <th>Date / Heure</th>
                            <th>Outil</th>
                            <th>Statut</th>
                            <th>Durée</th>
                            <th>Correlation ID</th>
                        </tr>
                    </thead>
                    <tbody id="history-tbody">
                        <tr><td colspan="5" style="text-align: center; color: var(--text-muted);">Aucune exécution enregistrée.</td></tr>
                    </tbody>
                </table>
            </div>
        </div>

        <!-- TAB 3 : Utilisateurs -->
        <div id="pane-users" class="tab-pane">
            <div class="section-header">
                <div class="section-title">Utilisateurs de la Console Admin</div>
                <button class="btn" onclick="openCreateUserModal()">+ Nouvel Utilisateur</button>
            </div>
            <div class="card" style="padding: 0; overflow-x: auto;">
                <table>
                    <thead>
                        <tr>
                            <th>Identifiant</th>
                            <th>Rôle</th>
                            <th>Statut</th>
                            <th>Date de Création</th>
                            <th style="text-align: right;">Actions</th>
                        </tr>
                    </thead>
                    <tbody id="users-tbody">
                        <tr><td colspan="5" style="text-align: center; color: var(--text-muted);">Chargement des utilisateurs...</td></tr>
                    </tbody>
                </table>
            </div>
        </div>

        <!-- TAB 4 : Configuration ERP -->
        <div id="pane-config" class="tab-pane">
            <div class="section-header">
                <div class="section-title">Liaison avec le Serveur MELODI ERP</div>
            </div>
            <div class="card" style="max-width: 680px;">
                <p style="color: var(--text-muted); font-size: 0.9rem; margin-bottom: 20px;">
                    Configurez l'adresse réseau de votre instance MELODI ERP. Le changement est immédiatement pris en compte par la passerelle MCP et persisté dans la base SQLite.
                </p>

                <div id="config-alert" class="alert" style="display: none; margin-bottom: 16px;"></div>

                <form onsubmit="handleSaveConfig(event)">
                    <div class="form-group">
                        <label for="cfg-melodi-url">URL Racine du Serveur MELODI ERP *</label>
                        <input type="url" id="cfg-melodi-url" required placeholder="ex: http://localhost:5000 ou http://host.docker.internal:5000">
                        <span style="font-size: 0.75rem; color: var(--text-muted); display: block; margin-top: 4px;">
                            Exemple conteneurisé : <code>http://host.docker.internal:5000</code> ou <code>http://melodi_app:5000</code>
                        </span>
                    </div>

                    <div class="form-group">
                        <label for="cfg-melodi-key">Clé d'API Interne (MELODI_API_KEY)</label>
                        <input type="password" id="cfg-melodi-key" autocomplete="new-password" placeholder="•••••••• (Laissez vide pour conserver la clé actuelle)">
                        <span id="cfg-melodi-key-hint" style="font-size: 0.75rem; color: var(--text-muted); display: block; margin-top: 4px;">
                            Jeton secret partagé avec le module melodi_mcp de l'ERP (X-Internal-MCP-Key).
                        </span>
                    </div>

                    <div class="form-group">
                        <label for="cfg-api-prefix">Préfixe API MCP (Lecture seule)</label>
                        <input type="text" id="cfg-api-prefix" readonly style="opacity: 0.7; background: rgba(0,0,0,0.25);">
                    </div>

                    <div style="display: flex; gap: 12px; margin-top: 24px; flex-wrap: wrap;">
                        <button type="button" id="btn-test-config" class="btn btn-outline" onclick="handleTestConfig()">
                            🔍 Tester la Connexion
                        </button>
                        <button type="submit" id="btn-save-config" class="btn">
                            💾 Enregistrer & Appliquer
                        </button>
                    </div>
                </form>
            </div>
        </div>
    </div>

    <!-- Modal Détails d'un outil -->
    <div id="tool-modal" class="modal">
        <div class="modal-content">
            <button class="modal-close" onclick="closeToolModal()">&times;</button>
            <h2 id="modal-tool-name" style="margin-bottom: 12px; font-size: 1.25rem;">-</h2>
            <p id="modal-tool-desc" style="color: var(--text-muted); margin-bottom: 16px; font-size: 0.9rem;">-</p>
            <h4 style="margin-bottom: 8px; font-size: 0.85rem; text-transform: uppercase; color: var(--text-muted);">Schéma d'Entrée (JSON Schema)</h4>
            <pre><code id="modal-tool-schema">{}</code></pre>
        </div>
    </div>

    <!-- Modal Création d'un utilisateur -->
    <div id="modal-create-user" class="modal">
        <div class="modal-content">
            <button class="modal-close" onclick="closeModal('modal-create-user')">&times;</button>
            <h2 style="margin-bottom: 16px; font-size: 1.25rem;">Ajouter un Utilisateur</h2>
            <form onsubmit="handleCreateUser(event)">
                <div class="form-group">
                    <label for="new-username">Identifiant</label>
                    <input type="text" id="new-username" required minlength="3" placeholder="ex: operateur">
                </div>
                <div class="form-group">
                    <label for="new-role">Rôle</label>
                    <select id="new-role">
                        <option value="admin">Administrateur (Complet)</option>
                        <option value="viewer">Observateur (Lecture seule)</option>
                    </select>
                </div>
                <div class="form-group">
                    <label for="new-password">Mot de passe</label>
                    <input type="password" id="new-password" required minlength="4" placeholder="Mot de passe robuste">
                </div>
                <div style="display: flex; justify-content: flex-end; gap: 8px; margin-top: 20px;">
                    <button type="button" class="btn btn-outline" onclick="closeModal('modal-create-user')">Annuler</button>
                    <button type="submit" class="btn">Créer l'utilisateur</button>
                </div>
            </form>
        </div>
    </div>

    <!-- Modal Modification de mot de passe -->
    <div id="modal-password-user" class="modal">
        <div class="modal-content">
            <button class="modal-close" onclick="closeModal('modal-password-user')">&times;</button>
            <h2 style="margin-bottom: 16px; font-size: 1.25rem;">Changer le mot de passe</h2>
            <form onsubmit="handleChangePassword(event)">
                <input type="hidden" id="pwd-user-id">
                <div class="form-group">
                    <label>Utilisateur : <span id="pwd-username" class="code">-</span></label>
                </div>
                <div class="form-group">
                    <label for="edit-new-password">Nouveau mot de passe</label>
                    <input type="password" id="edit-new-password" required minlength="4" placeholder="Entrez le nouveau mot de passe">
                </div>
                <div style="display: flex; justify-content: flex-end; gap: 8px; margin-top: 20px;">
                    <button type="button" class="btn btn-outline" onclick="closeModal('modal-password-user')">Annuler</button>
                    <button type="submit" class="btn">Enregistrer</button>
                </div>
            </form>
        </div>
    </div>

    <div id="toast" class="toast">Action effectuée avec succès</div>

    <script>
        let allTools = [];
        let currentUser = null;
        const apiBase = window.location.pathname.replace(/\/$/, '');

        function showToast(msg, isError = false) {
            const toast = document.getElementById('toast');
            toast.innerText = msg;
            toast.className = 'toast show ' + (isError ? 'toast-error' : 'toast-success');
            setTimeout(() => { toast.className = 'toast'; }, 3000);
        }

        function switchTab(tabId) {
            document.querySelectorAll('.tab-btn').forEach(btn => btn.classList.remove('active'));
            document.querySelectorAll('.tab-pane').forEach(pane => pane.classList.remove('active'));

            if (tabId === 'tools') {
                document.querySelectorAll('.tab-btn')[0].classList.add('active');
                document.getElementById('pane-tools').classList.add('active');
            } else if (tabId === 'history') {
                document.querySelectorAll('.tab-btn')[1].classList.add('active');
                document.getElementById('pane-history').classList.add('active');
            } else if (tabId === 'users') {
                document.getElementById('tab-btn-users').classList.add('active');
                document.getElementById('pane-users').classList.add('active');
                fetchUsers();
            } else if (tabId === 'config') {
                document.getElementById('tab-btn-config').classList.add('active');
                document.getElementById('pane-config').classList.add('active');
                fetchConfig();
            }
        }

        async function fetchMe() {
            try {
                const res = await fetch(apiBase + '/api/me');
                if (res.status === 401) {
                    window.location.reload();
                    return;
                }
                const data = await res.json();
                currentUser = data.user;
                const badge = document.getElementById('current-user-badge');
                badge.innerText = '👤 ' + currentUser.username + ' (' + currentUser.role + ')';
                badge.style.display = 'inline-block';

                if (currentUser.role !== 'admin') {
                    document.getElementById('tab-btn-users').style.display = 'none';
                    document.getElementById('tab-btn-config').style.display = 'none';
                    document.getElementById('btn-sync').disabled = true;
                }
            } catch (e) {
                console.error('Erreur profil :', e);
            }
        }

        async function handleLogout() {
            try {
                await fetch(apiBase + '/api/logout', { method: 'POST' });
            } finally {
                window.location.reload();
            }
        }

        async function fetchStatus() {
            try {
                const res = await fetch(apiBase + '/api/status');
                if (res.status === 401) {
                    window.location.reload();
                    return;
                }
                const data = await res.json();
                updateHealthBadge(data.health.status);
                document.getElementById('kpi-latency').innerText = data.health.melodi.latency_ms ? data.health.melodi.latency_ms + ' ms' : '- ms';
                document.getElementById('kpi-erp-status').innerText = 'Liaison : ' + (data.health.melodi.healthy ? 'Opérationnelle' : 'Dégradée');
                document.getElementById('kpi-requests').innerText = data.metrics.total_requests;
                document.getElementById('kpi-success-rate').innerText = 'Taux de succès : ' + data.metrics.success_rate + '%';
                document.getElementById('kpi-uptime').innerText = formatUptime(data.metrics.uptime_seconds);

                if (data.metrics.last_sync_time) {
                    document.getElementById('kpi-last-sync').innerText = 'Dernière sync : ' + new Date(data.metrics.last_sync_time).toLocaleTimeString();
                }

                renderHistory(data.metrics.recent_calls || []);
            } catch (err) {
                console.error('Erreur chargement statut :', err);
            }
        }

        async function fetchTools() {
            try {
                const res = await fetch(apiBase + '/api/tools');
                if (res.status === 401) {
                    window.location.reload();
                    return;
                }
                const data = await res.json();
                allTools = data.tools || [];
                document.getElementById('kpi-tools').innerText = allTools.length;
                document.getElementById('kpi-revision').innerText = 'Révision : ' + (data.revision || '-');
                renderTools(allTools);
            } catch (err) {
                console.error('Erreur chargement outils :', err);
            }
        }

        function updateHealthBadge(status) {
            const badge = document.getElementById('health-badge');
            badge.innerText = status;
            badge.className = 'badge ' + (status === 'HEALTHY' ? 'badge-healthy' : (status === 'DEGRADED' ? 'badge-degraded' : 'badge-unhealthy'));
        }

        function formatUptime(seconds) {
            const d = Math.floor(seconds / (3600*24));
            const h = Math.floor(seconds % (3600*24) / 3600);
            const m = Math.floor(seconds % 3600 / 60);
            const s = Math.floor(seconds % 60);
            return (d > 0 ? d + 'j ' : '') + (h > 0 ? h + 'h ' : '') + m + 'm ' + s + 's';
        }

        function renderTools(tools) {
            const tbody = document.getElementById('tools-tbody');
            if (!tools || tools.length === 0) {
                tbody.innerHTML = '<tr><td colspan="6" style="text-align: center; color: var(--text-muted);">Aucun outil correspondant.</td></tr>';
                return;
            }

            tbody.innerHTML = tools.map((t, idx) => `
                <tr>
                    <td><strong class="code">${escapeHtml(t.name)}</strong></td>
                    <td style="max-width: 400px; color: var(--text-muted);">${escapeHtml(t.description || '-')}</td>
                    <td><span class="badge ${t.read_only ? 'badge-read' : 'badge-write'}">${t.read_only ? 'Lecture' : 'Écriture'}</span></td>
                    <td>${t.requires_confirmation ? '<span class="badge badge-degraded">Oui</span>' : '<span style="color: var(--text-muted);">Non</span>'}</td>
                    <td>${t.timeout_seconds}s</td>
                    <td><button class="btn btn-outline" style="padding: 4px 10px; font-size: 0.8rem;" onclick="viewTool(${idx})">Détails</button></td>
                </tr>
            `).join('');
        }

        function renderHistory(calls) {
            const tbody = document.getElementById('history-tbody');
            if (!calls || calls.length === 0) {
                tbody.innerHTML = '<tr><td colspan="5" style="text-align: center; color: var(--text-muted);">Aucune exécution enregistrée.</td></tr>';
                return;
            }

            tbody.innerHTML = calls.map(c => `
                <tr>
                    <td>${new Date(c.timestamp).toLocaleTimeString()}</td>
                    <td><span class="code">${escapeHtml(c.tool_name)}</span></td>
                    <td><span class="badge ${c.status === 'SUCCESS' ? 'badge-healthy' : 'badge-unhealthy'}">${c.status}</span></td>
                    <td>${c.duration_ms} ms</td>
                    <td><span class="code" style="font-size: 0.75rem;">${escapeHtml(c.correlation_id || '-')}</span></td>
                </tr>
            `).join('');
        }

        async function fetchUsers() {
            try {
                const res = await fetch(apiBase + '/api/users');
                if (!res.ok) return;
                const data = await res.json();
                renderUsers(data.users || []);
            } catch (err) {
                console.error('Erreur chargement utilisateurs :', err);
            }
        }

        function renderUsers(users) {
            const tbody = document.getElementById('users-tbody');
            if (!users || users.length === 0) {
                tbody.innerHTML = '<tr><td colspan="5" style="text-align: center; color: var(--text-muted);">Aucun utilisateur enregistré.</td></tr>';
                return;
            }

            tbody.innerHTML = users.map(u => `
                <tr>
                    <td><strong>${escapeHtml(u.username)}</strong> ${u.id === currentUser?.id ? '<span class="badge badge-read" style="margin-left: 6px;">Vous</span>' : ''}</td>
                    <td><span class="badge ${u.role === 'admin' ? 'badge-admin' : 'badge-viewer'}">${u.role.toUpperCase()}</span></td>
                    <td><span class="badge ${u.is_active ? 'badge-active' : 'badge-inactive'}">${u.is_active ? 'Actif' : 'Inactif'}</span></td>
                    <td>${new Date(u.created_at).toLocaleDateString()}</td>
                    <td style="text-align: right;">
                        <div class="actions-cell" style="justify-content: flex-end;">
                            <button class="btn btn-outline btn-sm" onclick="openPasswordModal(${u.id}, '${escapeHtml(u.username)}')">Mot de passe</button>
                            ${u.id !== currentUser?.id ? `
                                <button class="btn btn-outline btn-sm" onclick="toggleUser(${u.id})">${u.is_active ? 'Désactiver' : 'Activer'}</button>
                                <button class="btn btn-danger btn-sm" onclick="deleteUser(${u.id}, '${escapeHtml(u.username)}')">Supprimer</button>
                            ` : ''}
                        </div>
                    </td>
                </tr>
            `).join('');
        }

        function openCreateUserModal() {
            document.getElementById('new-username').value = '';
            document.getElementById('new-password').value = '';
            document.getElementById('modal-create-user').classList.add('active');
        }

        function openPasswordModal(id, username) {
            document.getElementById('pwd-user-id').value = id;
            document.getElementById('pwd-username').innerText = username;
            document.getElementById('edit-new-password').value = '';
            document.getElementById('modal-password-user').classList.add('active');
        }

        function closeModal(id) {
            document.getElementById(id).classList.remove('active');
        }

        async function handleCreateUser(e) {
            e.preventDefault();
            const username = document.getElementById('new-username').value.trim();
            const role = document.getElementById('new-role').value;
            const password = document.getElementById('new-password').value;

            try {
                const res = await fetch(apiBase + '/api/users', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ username, role, password })
                });
                const data = await res.json();
                if (res.ok && data.success) {
                    showToast('Utilisateur créé avec succès !');
                    closeModal('modal-create-user');
                    fetchUsers();
                } else {
                    showToast(data.error || 'Erreur lors de la création', true);
                }
            } catch (err) {
                showToast(err.message, true);
            }
        }

        async function handleChangePassword(e) {
            e.preventDefault();
            const id = document.getElementById('pwd-user-id').value;
            const password = document.getElementById('edit-new-password').value;

            try {
                const res = await fetch(apiBase + '/api/users/' + id + '/password', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ password })
                });
                const data = await res.json();
                if (res.ok && data.success) {
                    showToast('Mot de passe mis à jour !');
                    closeModal('modal-password-user');
                } else {
                    showToast(data.error || 'Erreur modification', true);
                }
            } catch (err) {
                showToast(err.message, true);
            }
        }

        async function toggleUser(id) {
            try {
                const res = await fetch(apiBase + '/api/users/' + id + '/toggle-status', { method: 'POST' });
                const data = await res.json();
                if (res.ok && data.success) {
                    showToast('Statut mis à jour');
                    fetchUsers();
                } else {
                    showToast(data.error || 'Erreur modification statut', true);
                }
            } catch (err) {
                showToast(err.message, true);
            }
        }

        async function deleteUser(id, username) {
            if (!confirm('Êtes-vous sûr de vouloir supprimer définitivement l\'utilisateur "' + username + '" ?')) return;

            try {
                const res = await fetch(apiBase + '/api/users/' + id, { method: 'DELETE' });
                const data = await res.json();
                if (res.ok && data.success) {
                    showToast('Utilisateur supprimé');
                    fetchUsers();
                } else {
                    showToast(data.error || 'Erreur suppression', true);
                }
            } catch (err) {
                showToast(err.message, true);
            }
        }

        function filterTools() {
            const query = document.getElementById('tool-search').value.toLowerCase();
            const filtered = allTools.filter(t => t.name.toLowerCase().includes(query) || (t.description || '').toLowerCase().includes(query));
            renderTools(filtered);
        }

        function viewTool(idx) {
            const t = allTools[idx];
            if (!t) return;
            document.getElementById('modal-tool-name').innerText = t.name;
            document.getElementById('modal-tool-desc').innerText = t.description || 'Aucune description disponible.';
            document.getElementById('modal-tool-schema').innerText = JSON.stringify(t.input_schema, null, 2);
            document.getElementById('tool-modal').classList.add('active');
        }

        function closeToolModal() {
            document.getElementById('tool-modal').classList.remove('active');
        }

        async function triggerSync() {
            const btn = document.getElementById('btn-sync');
            btn.disabled = true;
            btn.innerText = '🔄 Synchronisation...';
            try {
                const res = await fetch(apiBase + '/api/sync', { method: 'POST' });
                const data = await res.json();
                if (res.ok && data.success) {
                    showToast('Catalogue synchronisé (' + data.tools_count + ' outils trouvés)');
                    fetchTools();
                    fetchStatus();
                } else {
                    showToast(data.error || 'Échec de synchronisation', true);
                }
            } catch (err) {
                showToast('Erreur réseau lors de la synchronisation', true);
            } finally {
                btn.disabled = false;
                btn.innerText = '🔄 Actualiser Catalogue';
            }
        }

        async function fetchConfig() {
            try {
                const res = await fetch(apiBase + '/api/config');
                if (!res.ok) return;
                const data = await res.json();
                const urlInput = document.getElementById('cfg-melodi-url');
                const keyInput = document.getElementById('cfg-melodi-key');
                const keyHint = document.getElementById('cfg-melodi-key-hint');
                const prefixInput = document.getElementById('cfg-api-prefix');
                const erpUrlLabel = document.getElementById('kpi-erp-url');

                if (urlInput) urlInput.value = data.melodi_base_url || '';
                if (prefixInput) prefixInput.value = data.melodi_api_prefix || '';
                if (erpUrlLabel) erpUrlLabel.innerText = 'URL : ' + (data.melodi_base_url || '-');

                if (keyInput) {
                    keyInput.value = '';
                    if (data.has_melodi_api_key) {
                        keyInput.placeholder = 'Clé configurée (' + (data.melodi_api_key_preview || '••••') + ') — Laisser vide pour conserver';
                        if (keyHint) keyHint.innerHTML = 'Jeton secret partagé (X-Internal-MCP-Key). Actuel : <code>' + escapeHtml(data.melodi_api_key_preview) + '</code>';
                    } else {
                        keyInput.placeholder = 'Aucune clé configurée (saisir pour définir)';
                        if (keyHint) keyHint.innerHTML = 'Jeton secret partagé (X-Internal-MCP-Key). <span style="color: var(--warning-color);">Aucune clé configurée</span>';
                    }
                }
            } catch (e) {
                console.error('Erreur chargement config :', e);
            }
        }

        async function handleTestConfig() {
            const url = document.getElementById('cfg-melodi-url').value.trim();
            const key = document.getElementById('cfg-melodi-key').value.trim();
            const alertBox = document.getElementById('config-alert');
            const btn = document.getElementById('btn-test-config');
            alertBox.style.display = 'none';

            if (!url) {
                alertBox.className = 'alert alert-error';
                alertBox.innerText = 'Veuillez saisir une URL valide.';
                alertBox.style.display = 'block';
                return;
            }

            btn.disabled = true;
            btn.innerText = '🔍 Test en cours...';

            const payload = { melodi_base_url: url };
            if (key) {
                payload.melodi_api_key = key;
            }

            try {
                const res = await fetch(apiBase + '/api/config/test', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload)
                });
                const data = await res.json();
                if (res.ok && data.success) {
                    alertBox.className = 'alert alert-info';
                    alertBox.innerText = '✓ ' + (data.message || 'Connexion réussie !');
                    alertBox.style.display = 'block';
                } else {
                    alertBox.className = 'alert alert-error';
                    alertBox.innerText = '✗ ' + (data.error || 'Échec du test de connexion');
                    alertBox.style.display = 'block';
                }
            } catch (err) {
                alertBox.className = 'alert alert-error';
                alertBox.innerText = 'Erreur réseau : ' + err.message;
                alertBox.style.display = 'block';
            } finally {
                btn.disabled = false;
                btn.innerText = '🔍 Tester la Connexion';
            }
        }

        async function handleSaveConfig(e) {
            e.preventDefault();
            const url = document.getElementById('cfg-melodi-url').value.trim();
            const key = document.getElementById('cfg-melodi-key').value.trim();
            const alertBox = document.getElementById('config-alert');
            const btn = document.getElementById('btn-save-config');
            alertBox.style.display = 'none';

            btn.disabled = true;
            btn.innerText = '💾 Enregistrement...';

            const payload = {};
            if (url) payload.melodi_base_url = url;
            if (key) payload.melodi_api_key = key;

            try {
                const res = await fetch(apiBase + '/api/config', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload)
                });
                const data = await res.json();
                if (res.ok && data.success) {
                    showToast('Configuration MELODI mise à jour (' + data.tools_count + ' outils synchronisés)');
                    alertBox.className = 'alert alert-info';
                    alertBox.innerText = '✓ ' + (data.message || 'Configuration mise à jour avec succès.');
                    alertBox.style.display = 'block';
                    fetchStatus();
                    fetchTools();
                    fetchConfig();
                } else {
                    alertBox.className = 'alert alert-error';
                    alertBox.innerText = '✗ ' + (data.error || 'Erreur lors de la mise à jour');
                    alertBox.style.display = 'block';
                    showToast(data.error || 'Erreur', true);
                }
            } catch (err) {
                alertBox.className = 'alert alert-error';
                alertBox.innerText = 'Erreur réseau : ' + err.message;
                alertBox.style.display = 'block';
                showToast(err.message, true);
            } finally {
                btn.disabled = false;
                btn.innerText = '💾 Enregistrer & Appliquer';
            }
        }

        function escapeHtml(str) {
            if (!str) return '';
            return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
        }

        // Initialisation
        fetchMe();
        fetchStatus();
        fetchTools();
        fetchConfig();

        setInterval(() => {
            if (document.getElementById('auto-refresh').checked) {
                fetchStatus();
            }
        }, 5000);
    </script>
</body>
</html>
"""
