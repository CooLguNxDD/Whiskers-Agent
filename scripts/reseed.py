#!/usr/bin/env python3
"""
Truncate all MCP database tables and optionally re-run migrations.
Equivalent to scripts/reseed.sh and scripts/reseed.bat.

Usage:
    python scripts/reseed.py            # truncate data only (keeps schema)
    python scripts/reseed.py --full     # downgrade to base, then upgrade to head
    python scripts/reseed.py --help     # show usage help
"""

import sys
import os
import subprocess
from pathlib import Path
from dotenv import load_dotenv

def print_help():
    """
    Prints the help message for the reseed script.
    """
    print("""
Usage:
  python scripts/reseed.py            Truncate all MCP table data (keeps schema)
  python scripts/reseed.py --full     Drop all tables (downgrade base) then recreate (upgrade head)
  python scripts/reseed.py --help     Show this message

Requires DATABASE_URL in .env or environment.
--full requires only alembic on PATH.
Data-only mode also requires psql on PATH.
""")

def main():
    """
    Main entrypoint for database reseeding.
    """
    # ── Help / Usage checks ──────────────────────────────────────────────────
    args = sys.argv[1:]
    if "--help" in args or "-h" in args:
        print_help()
        sys.exit(0)

    # ── Change to project root (parent of scripts/) ──────────────────────────
    project_root = Path(__file__).resolve().parent.parent
    os.chdir(project_root)

    # ── Load .env ────────────────────────────────────────────────────────────
    env_file = project_root / ".env"
    if env_file.exists():
        load_dotenv(dotenv_path=env_file)

    # ── Require DATABASE_URL ──────────────────────────────────────────────────
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("ERROR: DATABASE_URL is not set.")
        print("       Set it in .env or export it before running this script.")
        sys.exit(1)

    # ── Replace docker internal hostname if running locally ──────────────────
    # If running locally and host is postgres, replace with localhost (unless inside docker)
    if not Path("/.dockerenv").exists() and not os.environ.get("RUNNING_IN_DOCKER"):
        database_url = database_url.replace("@postgres:", "@localhost:")
        database_url = database_url.replace("//postgres:", "//localhost:")
    os.environ["DATABASE_URL"] = database_url

    # ── Confirm action ───────────────────────────────────────────────────────
    print(f"\nWARNING: This will delete ALL data from the MCP database tables.")
    print(f"DATABASE_URL: {database_url}\n")
    # NOTE: reseed does NOT truncate api_keys / auth_keypairs (by design).
    #   However `docker compose down -v` or volume loss will wipe them permanently.
    #   api_keys and auth_keypairs are encrypted under MASTER_KEY — it must never change.
    #   See CLAUDE.md "Operational guardrails".

    try:
        confirm = input("Type YES to continue: ")
    except KeyboardInterrupt:
        print("\nAborted.")
        sys.exit(0)

    if confirm.strip() != "YES":
        print("Aborted.")
        sys.exit(0)

    # ── Full reset: downgrade base then upgrade head ─────────────────────────
    if "--full" in args:
        print("\n[1/2] Downgrading to base...")
        try:
            subprocess.run(["alembic", "downgrade", "base"], check=True)
        except FileNotFoundError:
            print("ERROR: 'alembic' command not found. Ensure it is installed and in your PATH/venv.")
            sys.exit(1)
        except subprocess.CalledProcessError as e:
            print(f"ERROR: downgrade failed with exit code {e.returncode}")
            sys.exit(e.returncode)

        print("\n[2/2] Upgrading to heads...")
        try:
            subprocess.run(["alembic", "upgrade", "heads"], check=True)
        except subprocess.CalledProcessError as e:
            print(f"ERROR: upgrade failed with exit code {e.returncode}")
            sys.exit(e.returncode)

        print("\nFull reset complete.")
        sys.exit(0)

    # ── Data-only truncate via psql ──────────────────────────────────────────
    # Strip SQLAlchemy driver prefix so psql gets a plain libpq URI
    pg_url = database_url
    pg_url = pg_url.replace("postgresql+psycopg://", "postgresql://")
    pg_url = pg_url.replace("postgresql+psycopg2://", "postgresql://")

    print("\nTruncating tables: api_cache, oauth_tokens, oauth_clients\n")

    sql_cmd = (
        "TRUNCATE TABLE api_cache, oauth_tokens, oauth_clients RESTART IDENTITY CASCADE;"
    )

    try:
        subprocess.run(["psql", pg_url, "-c", sql_cmd], check=True)
    except FileNotFoundError:
        print("ERROR: 'psql' command not found. Ensure PostgreSQL client is installed and 'psql' is in your PATH.")
        print("       Alternatively, run with --full:  python scripts/reseed.py --full")
        sys.exit(1)
    except subprocess.CalledProcessError as e:
        print(f"\nERROR: TRUNCATE failed with exit code {e.returncode}.")
        print("       Make sure DATABASE_URL is reachable.")
        sys.exit(e.returncode)

    print("\nReseed complete — all table data cleared.")

if __name__ == "__main__":
    main()
