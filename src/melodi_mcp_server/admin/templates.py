"""Template HTML / CSS / JS embarqué autonome pour la console d'administration."""

DASHBOARD_HTML = """<!DOCTYPE html>
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
            --font: system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, Cantarell, sans-serif;
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
        .modal-content { background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); width: 90%; max-width: 650px; padding: 24px; position: relative; }
        .modal-close { position: absolute; top: 16px; right: 16px; background: none; border: none; color: var(--text-muted); font-size: 1.5rem; cursor: pointer; }
        .modal-close:hover { color: var(--text); }
        .tabs { display: flex; gap: 8px; margin-bottom: 16px; border-bottom: 1px solid var(--border); }
        .tab-btn { background: none; border: none; padding: 8px 16px; color: var(--text-muted); cursor: pointer; font-weight: 500; border-bottom: 2px solid transparent; }
        .tab-btn.active { color: var(--primary); border-bottom-color: var(--primary); }
    </style>
</head>
<body>
    <div class="container">
        <header>
            <div class="logo-group">
                <h1 style="font-size: 1.5rem; font-weight: 700;">⚡ MELODI MCP Server</h1>
                <span id="health-badge" class="badge badge-healthy">Chargement...</span>
            </div>
            <div style="display: flex; gap: 12px; align-items: center;">
                <label style="font-size: 0.85rem; color: var(--text-muted); display: flex; align-items: center; gap: 6px;">
                    <input type="checkbox" id="auto-refresh" checked> Auto-refresh (5s)
                </label>
                <button id="btn-sync" class="btn" onclick="triggerSync()">🔄 Actualiser le Catalogue</button>
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
            </div>
            <div class="card">
                <div class="kpi-title">Uptime & Transport</div>
                <div class="kpi-value" id="kpi-uptime" style="font-size: 1.4rem; padding-top: 6px;">-</div>
                <div class="kpi-sub" id="kpi-transport">Transport : stdio</div>
            </div>
        </div>

        <!-- Details Strip -->
        <div class="card" style="margin-bottom: 24px; padding: 14px 20px; font-size: 0.85rem; color: var(--text-muted); display: flex; justify-content: space-between; flex-wrap: wrap; gap: 10px;">
            <div><strong>ERP URL :</strong> <span id="info-base-url">-</span></div>
            <div><strong>Préfixe API Configuré :</strong> <span id="info-api-prefix" class="code">-</span></div>
            <div><strong>Dernière Synchro :</strong> <span id="info-last-sync">-</span></div>
        </div>

        <!-- Tools Section -->
        <div class="section-header">
            <div class="section-title">Catalogue des Outils Découverts (<span id="tools-count-header">0</span>)</div>
            <input type="text" id="tool-search" class="search-box" placeholder="Rechercher un outil..." oninput="filterTools()">
        </div>

        <div class="card" style="padding: 0; overflow-x: auto; margin-bottom: 24px;">
            <table>
                <thead>
                    <tr>
                        <th>Nom de l'Outil</th>
                        <th>Description</th>
                        <th>Type</th>
                        <th>Timeout</th>
                        <th>Permissions</th>
                        <th>Schéma</th>
                    </tr>
                </thead>
                <tbody id="tools-tbody">
                    <tr><td colspan="6" style="text-align: center; color: var(--text-muted); padding: 24px;">Chargement des outils...</td></tr>
                </tbody>
            </table>
        </div>

        <!-- Recent Activity -->
        <div class="section-header">
            <div class="section-title">Activité Récente</div>
        </div>
        <div class="card" style="padding: 0; overflow-x: auto;">
            <table>
                <thead>
                    <tr>
                        <th>Date (UTC)</th>
                        <th>Outil</th>
                        <th>Durée</th>
                        <th>Statut</th>
                        <th>ID d'appel</th>
                        <th>Message d'erreur</th>
                    </tr>
                </thead>
                <tbody id="calls-tbody">
                    <tr><td colspan="6" style="text-align: center; color: var(--text-muted); padding: 20px;">Aucun appel enregistré pour l'instant.</td></tr>
                </tbody>
            </table>
        </div>
    </div>

    <!-- Modal Schéma -->
    <div id="schema-modal" class="modal">
        <div class="modal-content">
            <button class="modal-close" onclick="closeModal()">&times;</button>
            <h3 id="modal-tool-name" style="margin-bottom: 12px; font-size: 1.2rem;">Détails du schéma</h3>
            <pre id="modal-schema-content"></pre>
        </div>
    </div>

    <!-- Notification Toast -->
    <div id="toast" class="toast">Action effectuée</div>

    <script>
        let cachedTools = [];

        function showToast(message, isError = false) {
            const toast = document.getElementById('toast');
            toast.textContent = message;
            toast.className = 'toast show ' + (isError ? 'toast-error' : 'toast-success');
            setTimeout(() => { toast.className = 'toast'; }, 3000);
        }

        async function fetchStatus() {
            try {
                const res = await fetch(window.location.pathname.replace(/\\/$/, '') + '/api/status');
                if (!res.ok) throw new Error('HTTP ' + res.status);
                const data = await res.json();
                updateUI(data);
            } catch (err) {
                console.error('Erreur status:', err);
                const badge = document.getElementById('health-badge');
                badge.textContent = 'DECONNECTÉ';
                badge.className = 'badge badge-unhealthy';
            }
        }

        async function fetchTools() {
            try {
                const res = await fetch(window.location.pathname.replace(/\\/$/, '') + '/api/tools');
                if (!res.ok) throw new Error('HTTP ' + res.status);
                const data = await res.json();
                cachedTools = data.tools || [];
                renderTools(cachedTools);
            } catch (err) {
                console.error('Erreur tools:', err);
            }
        }

        function updateUI(data) {
            const h = data.health || {};
            const m = data.metrics || {};

            // Health Badge
            const badge = document.getElementById('health-badge');
            const status = h.status || 'UNKNOWN';
            badge.textContent = status;
            badge.className = 'badge ' + (status === 'HEALTHY' ? 'badge-healthy' : (status === 'DEGRADED' ? 'badge-degraded' : 'badge-unhealthy'));

            // KPIs
            document.getElementById('kpi-tools').textContent = (h.catalog && h.catalog.tools_count) || 0;
            document.getElementById('kpi-revision').textContent = 'Révision : ' + ((h.catalog && h.catalog.revision) || 'N/A');
            document.getElementById('kpi-requests').textContent = m.total_requests || 0;
            document.getElementById('kpi-success-rate').textContent = 'Taux succès : ' + (m.success_rate_percent !== undefined ? m.success_rate_percent + '%' : '100%');
            
            const conn = h.melodi_connection || {};
            document.getElementById('kpi-latency').textContent = (conn.latency_ms !== null && conn.latency_ms !== undefined) ? conn.latency_ms + ' ms' : '-';
            document.getElementById('kpi-erp-status').textContent = 'Liaison : ' + (conn.status || 'Inconnue');

            // Uptime
            const s = (h.server && h.server.uptime_seconds) || 0;
            const hours = Math.floor(s / 3600);
            const mins = Math.floor((s % 3600) / 60);
            const secs = s % 60;
            document.getElementById('kpi-uptime').textContent = `${hours}h ${mins}m ${secs}s`;
            document.getElementById('kpi-transport').textContent = 'Transport : ' + ((h.server && h.server.transport) || 'stdio');

            // Strip
            document.getElementById('info-base-url').textContent = conn.base_url || '-';
            document.getElementById('info-api-prefix').textContent = conn.api_prefix || '-';
            document.getElementById('info-last-sync').textContent = (h.catalog && h.catalog.last_sync_time) || 'Jamais';

            // Calls table
            renderCalls(m.recent_calls || []);
        }

        function renderTools(tools) {
            document.getElementById('tools-count-header').textContent = tools.length;
            const tbody = document.getElementById('tools-tbody');
            if (tools.length === 0) {
                tbody.innerHTML = '<tr><td colspan="6" style="text-align: center; color: var(--text-muted); padding: 20px;">Aucun outil découvert. Vérifiez la connexion à MELODI ERP.</td></tr>';
                return;
            }
            tbody.innerHTML = tools.map((t, idx) => `
                <tr>
                    <td><strong>${t.name}</strong></td>
                    <td style="color: var(--text-muted); max-width: 350px;">${t.description || '-'}</td>
                    <td><span class="badge ${t.read_only ? 'badge-read' : 'badge-write'}">${t.read_only ? 'Lecture' : 'Écriture'}</span></td>
                    <td>${t.timeout_seconds || 30}s</td>
                    <td>${(t.required_permissions && t.required_permissions.length) ? t.required_permissions.map(p => '<span class="code">' + p + '</span>').join(' ') : '<span style="color:var(--text-muted);">-</span>'}</td>
                    <td><button class="btn btn-outline" style="padding: 4px 10px; font-size: 0.8rem;" onclick="viewSchema(${idx})">{ } Schéma</button></td>
                </tr>
            `).join('');
        }

        function filterTools() {
            const query = document.getElementById('tool-search').value.toLowerCase();
            const filtered = cachedTools.filter(t => t.name.toLowerCase().includes(query) || (t.description && t.description.toLowerCase().includes(query)));
            renderTools(filtered);
        }

        function viewSchema(idx) {
            const tool = cachedTools[idx];
            if (!tool) return;
            document.getElementById('modal-tool-name').textContent = 'Schéma d\\'entrée : ' + tool.name;
            document.getElementById('modal-schema-content').textContent = JSON.stringify(tool.input_schema, null, 2);
            document.getElementById('schema-modal').classList.add('active');
        }

        function closeModal() {
            document.getElementById('schema-modal').classList.remove('active');
        }

        function renderCalls(calls) {
            const tbody = document.getElementById('calls-tbody');
            if (calls.length === 0) {
                tbody.innerHTML = '<tr><td colspan="6" style="text-align: center; color: var(--text-muted); padding: 20px;">Aucun appel récent enregistré.</td></tr>';
                return;
            }
            tbody.innerHTML = calls.map(c => `
                <tr>
                    <td style="font-size: 0.8rem; color: var(--text-muted);">${c.timestamp ? c.timestamp.substring(11, 19) : '-'}</td>
                    <td><strong>${c.tool_name}</strong></td>
                    <td>${c.duration_ms} ms</td>
                    <td><span class="badge ${c.success ? 'badge-healthy' : 'badge-unhealthy'}">${c.success ? 'SUCCÈS' : 'ÉCHEC'}</span></td>
                    <td><span class="code" style="font-size: 0.75rem;">${c.call_id.substring(0, 8)}...</span></td>
                    <td style="color: var(--danger); font-size: 0.8rem;">${c.error_message || '-'}</td>
                </tr>
            `).join('');
        }

        async function triggerSync() {
            const btn = document.getElementById('btn-sync');
            btn.disabled = true;
            btn.textContent = '⏳ Synchronisation...';
            try {
                const res = await fetch(window.location.pathname.replace(/\\/$/, '') + '/api/sync', { method: 'POST' });
                const data = await res.json();
                if (res.ok && data.success) {
                    showToast('Catalogue synchronisé : ' + data.tools_count + ' outils enregistrés');
                    await fetchStatus();
                    await fetchTools();
                } else {
                    showToast('Erreur : ' + (data.error || 'Échec de synchronisation'), true);
                }
            } catch (err) {
                showToast('Erreur réseau lors de la synchro : ' + err.message, true);
            } finally {
                btn.disabled = false;
                btn.textContent = '🔄 Actualiser le Catalogue';
            }
        }

        // Init
        fetchStatus();
        fetchTools();
        setInterval(() => {
            if (document.getElementById('auto-refresh').checked) {
                fetchStatus();
            }
        }, 5000);
    </script>
</body>
</html>
"""
