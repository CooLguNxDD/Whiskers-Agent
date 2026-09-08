#!/usr/bin/env python3
"""
Run Alembic database migrations.
Equivalent to scripts/migrate.sh and scripts/migrate.bat.

Usage:
    python scripts/migrate.py                    # upgrade to heads (default)
    python scripts/migrate.py upgrade heads      # same as above
    python scripts/migrate.py upgrade <rev_id>   # upgrade to specific revision
    python scripts/migrate.py downgrade -1       # downgrade one step
    python scripts/migrate.py downgrade base     # downgrade everything
    python scripts/migrate.py current            # show current revision
    python scripts/migrate.py history            # show migration history
"""

import sys
import os
import subprocess
from pathlib import Path
from dotenv import load_dotenv

def run_in_docker(container_name, db_url, alembic_args):
    """Executes the alembic command inside Docker (exec if running, compose run if stopped)."""
    # ── Check if container is running ────────────────────────────────────────
    running = False
    try:
        result = subprocess.run(
            ["docker", "ps", "--filter", f"name={container_name}", "--format", "{{.Names}}"],
            capture_output=True,
            text=True,
            check=True
        )
        running_names = [name.strip() for name in result.stdout.strip().split("\n") if name.strip()]
        running = container_name in running_names
    except FileNotFoundError:
        print("ERROR: 'docker' command not found. Ensure Docker Desktop is installed and running.")
        sys.exit(1)
    except subprocess.CalledProcessError as e:
        print(f"ERROR checking docker status: {e.stderr or e}")
        sys.exit(1)

    # ── Prepare docker command ───────────────────────────────────────────────
    if running:
        cmd = [
            "docker", "exec",
            container_name,
            "env", f"DATABASE_URL={db_url}",
            "alembic"
        ] + alembic_args
    else:
        cmd = [
            "docker", "compose", "run", "--rm",
            "-e", f"DATABASE_URL={db_url}",
            "whiskers-agent",
            "alembic"
        ] + alembic_args

    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as e:
        # The exception's return code matches the command's return code
        sys.exit(e.returncode)

def main():
    """
    Main entrypoint for running database migrations.
    """
    # ── Change to project root (parent of scripts/) ──────────────────────────
    project_root = Path(__file__).resolve().parent.parent
    os.chdir(project_root)

    # ── Load .env if it exists ───────────────────────────────────────────────
    env_file = project_root / ".env"
    if env_file.exists():
        load_dotenv(dotenv_path=env_file)

    container_name = os.environ.get("CONTAINER_NAME", "whiskers-agent-server")
    # Use Docker-internal database URL (postgres service on the whiskers-network)
    db_url = os.environ.get("DATABASE_URL", "postgresql+psycopg://whiskers:whiskers@postgres:5432/whiskers_mcp")

    # Parse arguments
    args = sys.argv[1:]
    cmd = args[0] if len(args) > 0 else "upgrade"
    arg = args[1] if len(args) > 1 else "heads"

    # ── Help / Usage checks ──────────────────────────────────────────────────
    if cmd in ["--help", "-h"]:
        print(__doc__.strip())
        sys.exit(0)

    if cmd == "upgrade":
        print(f"Upgrading to: {arg}")
        run_in_docker(container_name, db_url, ["upgrade", arg])
    elif cmd == "downgrade":
        if len(args) < 2:
            print("ERROR: downgrade requires a target revision, e.g.:  python scripts/migrate.py downgrade -1")
            sys.exit(1)
        print(f"Downgrading to: {arg}")
        run_in_docker(container_name, db_url, ["downgrade", arg])
    elif cmd == "current":
        run_in_docker(container_name, db_url, ["current"])
    elif cmd == "history":
        run_in_docker(container_name, db_url, ["history", "--verbose"])
    else:
        # Treat first arg as a bare revision (e.g. python scripts/migrate.py head)
        print(f"Upgrading to: {cmd}")
        run_in_docker(container_name, db_url, ["upgrade", cmd])

    print("\nDone.")

if __name__ == "__main__":
    main()
