#!/usr/bin/env python3
"""
Development server launcher with auto-restart on file changes.
Equivalent to scripts/dev.sh and scripts/dev.bat.

Usage:
    python scripts/dev.py          # Run with docker-compose (full stack with postgres)
    python scripts/dev.py local    # Run locally (requires DB already running)
"""

import sys
import os
import subprocess
from pathlib import Path
from dotenv import load_dotenv

def main():
    """
    Main entrypoint for development server.
    """
    # ── Default mode is compose ──────────────────────────────────────────────
    dev_mode = sys.argv[1] if len(sys.argv) > 1 else "compose"

    # ── Help / Usage checks ──────────────────────────────────────────────────
    if dev_mode in ["--help", "-h"]:
        print(__doc__.strip())
        sys.exit(0)

    # ── Change to project root (parent of scripts/) ──────────────────────────
    project_root = Path(__file__).resolve().parent.parent
    os.chdir(project_root)

    if dev_mode == "local":
        print("🚀 Starting Whiskers Agent Server (local development mode)")
        print("   Auto-restarts on file changes...\n")

        # Load .env if it exists
        env_file = project_root / ".env"
        if env_file.exists():
            load_dotenv(dotenv_path=env_file)

        # Run with watchfiles
        # The watchfiles CLI command accepts a command string to run
        cmd = [
            "watchfiles",
            "--poll",
            "--delay",
            "0.5",
            "python whiskers_agent_mcp.py --transport http --host 0.0.0.0 --port 10000"
        ]
        
        try:
            subprocess.run(cmd, check=True)
        except KeyboardInterrupt:
            print("\nStopped.")
        except FileNotFoundError:
            print("ERROR: 'watchfiles' command not found. Make sure to run 'pip install -r requirements.txt'")
            sys.exit(1)
        except subprocess.CalledProcessError as e:
            print(f"Error running watchfiles: {e}")
            sys.exit(e.returncode)

    elif dev_mode == "compose":
        print("🐳 Starting Whiskers Agent Stack (Docker Compose)")
        print("   Services: postgres, whiskers-agent (with auto-restart), pgadmin\n")
        print("   Access:")
        print("   - MCP Server: http://localhost:10000")
        print("   - pgAdmin: http://localhost:5050\n")
        print("   To stop: Ctrl+C or 'docker compose down' (NEVER use -v or keys+data are lost)\n")
        # IMPORTANT: never pass -v here. The postgres_data volume holds api_keys, auth_keypairs,
        # and all persistent state. `docker compose down -v` or changing project/dir name
        # mounts a fresh volume, making keys "vanish" after restart. Use `docker restart`
        # for the server container only. MASTER_KEY must also remain constant.

        # Run with both compose files (base + dev override)
        cmd = [
            "docker", "compose",
            "-f", "docker-compose.yml",
            "-f", "docker-compose.dev.yml",
            "up",
            "--build",
            "whiskers-agent", "postgres", "pgadmin"
        ]

        try:
            subprocess.run(cmd, check=True)
        except KeyboardInterrupt:
            print("\nStopped.")
        except FileNotFoundError:
            print("ERROR: 'docker' or 'docker compose' command not found. Ensure Docker Desktop is installed and running.")
            sys.exit(1)
        except subprocess.CalledProcessError as e:
            print(f"Docker compose failed: {e}")
            sys.exit(e.returncode)
    else:
        print(f"ERROR: Unknown development mode '{dev_mode}'.")
        print("Usage:")
        print("  python scripts/dev.py          # docker compose mode")
        print("  python scripts/dev.py local    # local mode")
        sys.exit(1)

if __name__ == "__main__":
    main()
