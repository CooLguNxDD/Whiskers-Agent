"""
Full compiler and updater for NotebookLM (Whiskers Agent & CatPortfolio).
"""
import os
import sys
import json
import subprocess
from pathlib import Path

NOTEBOOK_ID = "0e899e5e-5db7-4254-af2d-399fbffe5198"
ROOT_DIR = Path(__file__).parent.parent.resolve()
SCRIPTS_DIR = ROOT_DIR / "scripts"

def run_compilers():
    print("=== Step 1: Running Compilation Scripts ===")
    compilers = [
        "compile_notebooklm_tunnel.py",
        "compile_notebooklm_skills.py",
        "compile_notebooklm_research_reports.py",
        "compile_notebooklm_cat_portfolio.py"
    ]
    for script in compilers:
        script_path = SCRIPTS_DIR / script
        if script_path.exists():
            print(f"Running {script}...")
            res = subprocess.run([sys.executable, str(script_path)], capture_output=True, text=True)
            if res.returncode == 0:
                print(f"  [OK] {script}:\n{res.stdout.strip()}")
            else:
                print(f"  [ERROR] {script}:\n{res.stderr.strip()}")
        else:
            print(f"  [SKIP] Script not found: {script}")

def get_existing_sources():
    print("\n=== Step 2: Fetching Existing NotebookLM Sources ===")
    cmd = ["notebooklm", "source", "list", "--notebook", NOTEBOOK_ID, "--json"]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        print(f"Failed to list sources: {res.stderr}")
        return {}
    
    try:
        # Find JSON object in stdout (in case of warning lines)
        raw = res.stdout
        start = raw.find("{")
        if start != -1:
            data = json.loads(raw[start:])
            sources = data.get("sources", [])
            mapping = {}
            for s in sources:
                mapping.setdefault(s["title"], []).append(s["id"])
            print(f"Found {len(sources)} existing sources in notebook.")
            return mapping
    except Exception as e:
        print(f"Error parsing source list JSON: {e}")
    return {}

def main():
    run_compilers()
    
    files_to_sync = [
        ROOT_DIR / "CLAUDE.md",
        ROOT_DIR / ".claude" / "docs" / "context.md",
        
        # Tunnel docs
        ROOT_DIR / ".claude" / "docs" / "notebooklm-tunnel" / "architecture.md",
        ROOT_DIR / ".claude" / "docs" / "notebooklm-tunnel" / "auth_scopes.md",
        ROOT_DIR / ".claude" / "docs" / "notebooklm-tunnel" / "core_graph_planner.md",
        ROOT_DIR / ".claude" / "docs" / "notebooklm-tunnel" / "frontend_console.md",
        ROOT_DIR / ".claude" / "docs" / "notebooklm-tunnel" / "plugins_and_features.md",
        ROOT_DIR / ".claude" / "docs" / "notebooklm-tunnel" / "relay_backend.md",
        ROOT_DIR / ".claude" / "docs" / "notebooklm-tunnel" / "tui_and_cli.md",
        ROOT_DIR / ".claude" / "docs" / "notebooklm-tunnel" / "vscode_extension.md",

        # Skills docs
        ROOT_DIR / ".claude" / "docs" / "notebooklm-skills" / "notebooklm-skills-00-index.md",
        ROOT_DIR / ".claude" / "docs" / "notebooklm-skills" / "notebooklm-skills-01-core.md",
        ROOT_DIR / ".claude" / "docs" / "notebooklm-skills" / "notebooklm-skills-02-auth-data.md",
        ROOT_DIR / ".claude" / "docs" / "notebooklm-skills" / "notebooklm-skills-03-devops.md",
        ROOT_DIR / ".claude" / "docs" / "notebooklm-skills" / "notebooklm-skills-04-frontend-openapi.md",

        # Research package
        ROOT_DIR / ".claude" / "docs" / "notebooklm-research" / "notebooklm-research-reports-package.md",

        # CatPortfolio docs
        ROOT_DIR / ".claude" / "docs" / "notebooklm-catportfolio" / "catportfolio_architecture.md",
        ROOT_DIR / ".claude" / "docs" / "notebooklm-catportfolio" / "catportfolio_layout_engine.md",
        ROOT_DIR / ".claude" / "docs" / "notebooklm-catportfolio" / "catportfolio_frontend_blocks.md",
    ]

    existing = get_existing_sources()
    
    print("\n=== Step 3: Updating Sources in NotebookLM ===")
    for f in files_to_sync:
        if not f.exists():
            print(f"[SKIP] File does not exist: {f}")
            continue

        filename = f.name
        
        # If already exists in notebook, remove old instance first to ensure clean update
        if filename in existing:
            for old_id in existing[filename]:
                print(f"Deleting previous version of '{filename}' (ID: {old_id})...")
                del_cmd = ["notebooklm", "source", "delete", old_id, "--notebook", NOTEBOOK_ID, "-y"]
                del_res = subprocess.run(del_cmd, capture_output=True, text=True)
                if del_res.returncode == 0:
                    print(f"  [DELETED] {filename}")
                else:
                    print(f"  [DELETE FAILED] {del_res.stderr.strip()}")
        
        # Upload new version
        print(f"Uploading new '{filename}'...")
        add_cmd = [
            "notebooklm", "source", "add", str(f),
            "--notebook", NOTEBOOK_ID,
            "--follow-symlinks",
            "--json"
        ]
        add_res = subprocess.run(add_cmd, capture_output=True, text=True)
        if add_res.returncode == 0:
            print(f"  [UPLOAD OK] {filename}")
        else:
            print(f"  [UPLOAD ERROR] {filename}: {add_res.stderr.strip()}")

    print("\n=== Step 4: Final Source List Verification ===")
    final_res = subprocess.run(["notebooklm", "source", "list", "--notebook", NOTEBOOK_ID], capture_output=True, text=True)
    print(final_res.stdout)

if __name__ == "__main__":
    main()
