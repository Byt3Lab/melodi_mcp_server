"""Templates HTML / CSS pour les pages OAuth 2.0."""

import html
from typing import Optional


def render_authorize_page(
    client_id: str,
    redirect_uri: str,
    state: Optional[str] = None,
    code_challenge: Optional[str] = None,
    code_challenge_method: Optional[str] = None,
    error_message: Optional[str] = None,
) -> str:
    """Génère la page web HTML d'autorisation OAuth 2.0 pour la connexion des clients IA."""
    safe_client_id = html.escape(client_id or "Client IA")
    safe_redirect_uri = html.escape(redirect_uri or "")
    safe_state = html.escape(state or "")
    safe_code_challenge = html.escape(code_challenge or "")
    safe_code_challenge_method = html.escape(code_challenge_method or "")

    error_html = ""
    if error_message:
        safe_error = html.escape(error_message)
        error_html = f'<div class="alert alert-error" style="display: block;">⚠️ {safe_error}</div>'

    cancel_uri = redirect_uri or "#"
    separator = "&" if "?" in cancel_uri else "?"
    cancel_url = f"{cancel_uri}{separator}error=access_denied"
    if state:
        cancel_url += f"&state={html.escape(state)}"

    return f"""<!DOCTYPE html>
<html lang="fr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Connexion Client IA - MELODI MCP</title>
    <style>
        :root {{
            --bg: #0f172a;
            --surface: #1e293b;
            --border: #334155;
            --text: #f8fafc;
            --text-muted: #94a3b8;
            --primary: #3b82f6;
            --primary-hover: #2563eb;
            --success: #10b981;
            --danger: #ef4444;
            --radius: 10px;
            --font: system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
        }}
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{
            font-family: var(--font);
            background: var(--bg);
            color: var(--text);
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 20px;
        }}
        .card {{
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: var(--radius);
            padding: 32px;
            width: 100%;
            max-width: 520px;
            box-shadow: 0 10px 30px rgba(0,0,0,0.6);
        }}
        .header {{
            text-align: center;
            margin-bottom: 24px;
        }}
        .logo {{
            font-size: 1.5rem;
            font-weight: 700;
            margin-bottom: 8px;
            letter-spacing: -0.5px;
        }}
        .badge {{
            display: inline-block;
            background: rgba(59, 130, 246, 0.15);
            color: #60a5fa;
            border: 1px solid rgba(59, 130, 246, 0.3);
            padding: 4px 10px;
            border-radius: 9999px;
            font-size: 0.75rem;
            font-weight: 600;
            margin-bottom: 12px;
        }}
        .client-info {{
            background: rgba(15, 23, 42, 0.6);
            border: 1px solid var(--border);
            border-radius: var(--radius);
            padding: 14px 16px;
            margin-bottom: 20px;
            font-size: 0.85rem;
        }}
        .client-info div {{
            margin-bottom: 6px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }}
        .client-info div:last-child {{ margin-bottom: 0; }}
        .client-info span.label {{ color: var(--text-muted); font-weight: 500; }}
        .client-info span.value {{
            font-family: monospace;
            background: rgba(0,0,0,0.3);
            padding: 2px 6px;
            border-radius: 4px;
            color: #38bdf8;
            max-width: 280px;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
        }}
        .form-group {{ margin-bottom: 18px; }}
        label {{
            display: block;
            font-size: 0.85rem;
            font-weight: 600;
            margin-bottom: 8px;
            color: var(--text);
        }}
        textarea {{
            width: 100%;
            height: 110px;
            padding: 12px;
            background: #0f172a;
            border: 1px solid var(--border);
            border-radius: var(--radius);
            color: var(--text);
            font-family: monospace;
            font-size: 0.85rem;
            line-height: 1.4;
            resize: vertical;
            outline: none;
            transition: border-color 0.2s;
        }}
        textarea:focus {{
            border-color: var(--primary);
            box-shadow: 0 0 0 2px rgba(59, 130, 246, 0.2);
        }}
        .alert {{
            padding: 12px 14px;
            border-radius: var(--radius);
            font-size: 0.85rem;
            margin-bottom: 18px;
            line-height: 1.4;
        }}
        .alert-error {{
            background: rgba(239, 68, 68, 0.15);
            border: 1px solid var(--danger);
            color: #fca5a5;
        }}
        .actions {{
            display: flex;
            gap: 12px;
            margin-top: 24px;
        }}
        .btn {{
            flex: 1;
            padding: 12px;
            border-radius: var(--radius);
            font-size: 0.95rem;
            font-weight: 600;
            cursor: pointer;
            text-align: center;
            text-decoration: none;
            transition: all 0.2s;
            border: none;
        }}
        .btn-primary {{
            background: var(--primary);
            color: white;
        }}
        .btn-primary:hover {{ background: var(--primary-hover); }}
        .btn-secondary {{
            background: transparent;
            border: 1px solid var(--border);
            color: var(--text-muted);
        }}
        .btn-secondary:hover {{
            background: rgba(255, 255, 255, 0.05);
            color: var(--text);
        }}
        .instructions {{
            font-size: 0.8rem;
            color: var(--text-muted);
            margin-top: 20px;
            padding-top: 16px;
            border-top: 1px solid var(--border);
            line-height: 1.5;
        }}
        .instructions ol {{
            padding-left: 20px;
            margin-top: 6px;
        }}
    </style>
</head>
<body>
    <div class="card">
        <div class="header">
            <div class="logo">⚡ MELODI MCP Server</div>
            <span class="badge">Autorisation OAuth 2.0</span>
            <p style="font-size: 0.9rem; color: var(--text-muted); margin-top: 6px;">
                Une application IA demande l'accès aux outils de votre ERP MELODI.
            </p>
        </div>

        <div class="client-info">
            <div>
                <span class="label">Client IA</span>
                <span class="value" title="{safe_client_id}">{safe_client_id}</span>
            </div>
            <div>
                <span class="label">Redirection</span>
                <span class="value" title="{safe_redirect_uri}">{safe_redirect_uri}</span>
            </div>
        </div>

        {error_html}

        <form method="POST" action="/oauth/authorize">
            <input type="hidden" name="client_id" value="{safe_client_id}">
            <input type="hidden" name="redirect_uri" value="{safe_redirect_uri}">
            <input type="hidden" name="state" value="{safe_state}">
            <input type="hidden" name="code_challenge" value="{safe_code_challenge}">
            <input type="hidden" name="code_challenge_method" value="{safe_code_challenge_method}">

            <div class="form-group">
                <label for="jwt_token">Jeton JWT d'authentification MELODI ERP *</label>
                <textarea
                    id="jwt_token"
                    name="jwt_token"
                    placeholder="Collez ici votre jeton JWT (généré dans MELODI ERP)"
                    required
                    autofocus
                ></textarea>
            </div>

            <div class="actions">
                <a href="{cancel_url}" class="btn btn-secondary">Annuler</a>
                <button type="submit" class="btn btn-primary">Valider & Autoriser</button>
            </div>
        </form>

        <div class="instructions">
            <strong>Où trouver votre jeton JWT ?</strong>
            <ol>
                <li>Connectez-vous à votre interface <strong>MELODI ERP</strong>.</li>
                <li>Accédez au module <strong>MELODI MCP</strong> > <em>Mes Jetons / Intégration IA</em>.</li>
                <li>Copiez votre jeton JWT actif et collez-le ci-dessus.</li>
            </ol>
        </div>
    </div>
</body>
</html>
"""
