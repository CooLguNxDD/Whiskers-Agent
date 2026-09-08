"""
Compiles NotebookLM tunnel and project document bundles.
"""
import os
from pathlib import Path
from datetime import datetime

# Repo root relative to scripts/compile_notebooklm_tunnel.py
workspace_root = Path(__file__).parent.parent.resolve()
docs_dir = workspace_root / ".claude" / "docs"
out_dir = docs_dir / "notebooklm-tunnel"

def compile_bundle(filename: str, title: str, sections: list):
    """
    Compiles a bundle of files and headers.
    """
    os.makedirs(out_dir, exist_ok=True)
    output_path = out_dir / filename
    today = datetime.now().strftime("%Y-%m-%d")
    
    content_parts = [
        f"# {title} — {filename}",
        f"**Compiled:** {today}",
        "**Notebook:** Whiskers Agent project",
        "",
        ""
    ]
    
    for label, relative_path in sections:
        content_parts.append("---")
        content_parts.append("")
        content_parts.append(f"## Source: {label} ({relative_path})")
        content_parts.append("")
        
        p = workspace_root / relative_path
        if p.exists():
            text = p.read_text(encoding="utf-8")
            # If it's code, wrap it in a code block
            ext = p.suffix
            if ext == ".py":
                content_parts.append(f"```python\n{text.strip()}\n```")
            elif ext in (".ts", ".tsx"):
                content_parts.append(f"```typescript\n{text.strip()}\n```")
            elif ext == ".json":
                content_parts.append(f"```json\n{text.strip()}\n```")
            else:
                content_parts.append(text.strip())
        else:
            content_parts.append(f"*File not found: {relative_path}*")
            
        content_parts.append("")
        
    compiled_text = "\n".join(content_parts)
    output_path.write_text(compiled_text, encoding="utf-8")
    print(f"Compiled {filename} successfully ({len(compiled_text)} chars).")

# 1. architecture.md
sections_arch = [
    ("Relay Setup and Overview", "plugins/cat_terminal_relay_plugin/SETUP.md"),
    ("VS Code Extension README & Design", "whiskers-vscode/README.md"),
    ("Main Project Readme", "ReadMe.md"),
    ("Project Setup Guide", "SetupGuide.md")
]
compile_bundle("architecture.md", "Whiskers Agent Architecture", sections_arch)

# 2. relay_backend.md
sections_backend = [
    ("Plugin Init", "plugins/cat_terminal_relay_plugin/__init__.py"),
    ("Plugin Manifest", "plugins/cat_terminal_relay_plugin/manifest.json"),
    ("Plugin Config", "plugins/cat_terminal_relay_plugin/config.json"),
    ("Command Guard", "plugins/cat_terminal_relay_plugin/command_guard.py"),
    ("Relay Session Service", "plugins/cat_terminal_relay_plugin/services/relay_session.py"),
    ("Session Registry", "plugins/cat_terminal_relay_plugin/services/session_registry.py"),
    ("IDE Registry", "plugins/cat_terminal_relay_plugin/services/ide_registry.py"),
    ("Control Endpoints", "plugins/cat_terminal_relay_plugin/routes/control_routes.py"),
    ("Extension WebSocket Route", "plugins/cat_terminal_relay_plugin/routes/extension_ws.py"),
    ("Console WebSocket Route", "plugins/cat_terminal_relay_plugin/routes/console_ws.py")
]
compile_bundle("relay_backend.md", "Whiskers Agent Relay Backend", sections_backend)

# 3. vscode_extension.md
sections_extension = [
    ("VS Code Package JSON", "whiskers-vscode/package.json"),
    ("VS Code Extension Entrypoint", "whiskers-vscode/src/extension.ts"),
    ("VS Code Relay Client", "whiskers-vscode/src/relayClient.ts"),
    ("VS Code PTY Host", "whiskers-vscode/src/ptyHost.ts"),
    ("VS Code Command Guard", "whiskers-vscode/src/commandGuard.ts"),
    ("VS Code Ambient Definitions", "whiskers-vscode/src/ambient.d.ts"),
    ("VS Code Session Connection", "whiskers-vscode/src/sessionConnection.ts")
]
compile_bundle("vscode_extension.md", "Whiskers Agent VS Code Extension", sections_extension)

# 4. frontend_console.md
sections_frontend = [
    ("Terminal Panel React Component", "frontend/cat-admin-frontend/src/components/Terminal/TerminalPanel.tsx"),
    ("Terminal View React Component", "frontend/cat-admin-frontend/src/components/Terminal/TerminalView.tsx"),
    ("Session Sidebar React Component", "frontend/cat-admin-frontend/src/components/Terminal/SessionSidebar.tsx"),
    ("New Session Dialog React Component", "frontend/cat-admin-frontend/src/components/Terminal/NewSessionDialog.tsx"),
    ("Zustand Terminal State Slice", "frontend/cat-admin-frontend/src/store/terminalSlice.ts"),
    ("Cozy Terminal Styling Design Spec", "frontend/cat-admin-frontend/DESIGN.md"),
    ("Scope Configurator React Component", "frontend/cat-admin-frontend/src/components/ApiKeys/ScopeConfigurator/ScopeConfigurator.tsx"),
    ("Sortable Table Component", "frontend/cat-admin-frontend/src/components/Table/Table.tsx")
]
compile_bundle("frontend_console.md", "Whiskers Agent Frontend Console", sections_frontend)

# 5. auth_scopes.md
sections_auth = [
    ("Setup Auth & Provisioning Details", "plugins/cat_terminal_relay_plugin/SETUP.md"),
    ("Elevation Service (TOTP/Password)", "plugins/cat_terminal_relay_plugin/services/elevation_service.py"),
    ("Terminal Token Service", "plugins/cat_terminal_relay_plugin/services/terminal_token_service.py"),
    ("Auth REST Endpoints", "plugins/cat_terminal_relay_plugin/routes/auth.py"),
    ("Host Token Copy Button", "frontend/cat-admin-frontend/src/components/Terminal/HostTokenButton.tsx"),
    ("Elevation Prompt Dialog", "frontend/cat-admin-frontend/src/components/Terminal/ElevationPrompt.tsx"),
    ("Scope Manager Core", "core/scope_management/manager.py"),
    ("Scope Rules Implementation", "core/scope_management/rules.py"),
    ("Scope Policies", "core/scope_management/policy.py")
]
compile_bundle("auth_scopes.md", "Whiskers Agent Auth & Scopes", sections_auth)

# 6. plugins_and_features.md
sections_plugins = [
    ("Web & Brave/Tavily Search Tools", "plugins/search_plugin/MCPTools/search_tools.py"),
    ("Semantic Vector Search Tools", "plugins/search_plugin/MCPTools/content_search_tools.py"),
    ("Content Vector DB Store", "db_layer/content_vectors_store.py"),
    ("Job Search Plugin Tools", "plugins/job_search_plugin/MCPTools/job_search_tools.py"),
    ("Plugin Loader & Registry", "core/plugin_loader/plugin_registry.py")
]
compile_bundle("plugins_and_features.md", "Whiskers Agent Project Plugins & Features", sections_plugins)

# 7. tui_and_cli.md
sections_tui = [
    ("Interactive Installer and Doctor Setup", "terminal/script/setup.py"),
    ("Interactive Setup TUI Master Hub", "terminal/script/setup_tui.py"),
    ("Credential Manager TUI Runner", "terminal/script/manage_credentials.py"),
    ("Credential Vault GUI UI", "terminal/tui/credentials.py"),
    ("Main Configuration Setup UI Panel", "terminal/tui/setup_tui.py")
]
compile_bundle("tui_and_cli.md", "Whiskers Agent Project TUI & Installer Scripts", sections_tui)

# 8. core_graph_planner.md
sections_planner = [
    ("GOAP Goal Bounded Loop", "core_graph/goap/goal_loop.py"),
    ("GOAP A* Algorithm Planner", "core_graph/goap/planner.py"),
    ("GOAP Step Model Policy Integration", "core_graph/goap/integrate/__init__.py"),
    ("Executor Parallel Dispatch Node", "core_graph/node/executor.py")
]
compile_bundle("core_graph_planner.md", "Whiskers Agent LangGraph Core Planner & Executor", sections_planner)
