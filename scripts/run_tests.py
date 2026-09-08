#!/usr/bin/env python3
"""
Test runner script for the Whiskers Agent Server.

Usage:
    python scripts/run_tests.py                  # Run all tests
    python scripts/run_tests.py test/unit/       # Run specific directory or file
    python scripts/run_tests.py -k "test_name"   # Run specific test matching keyword
"""

import sys
import os
import subprocess
from pathlib import Path

# Declarative default test paths. Kept in sync with pytest.ini's `testpaths`
# (single source of truth there — this constant exists only so the two
# runtime branches below that fall back to it can't drift from each other).
DEFAULT_TEST_PATHS = ["test/", "plugins/"]

def is_docker_container():
    """Detect if the script is running inside a Docker container."""
    return os.path.exists("/.dockerenv") or os.path.exists("/app")

def _test_database_url(prod_url: str) -> str:
    """Derive a dedicated `_test`-suffixed DATABASE_URL from the prod one.

    Tests must never point at the same database as a running dev/prod stack —
    autouse fixtures in test/unit/test_api_key_store.py and friends do
    unfiltered `delete(ApiKey)` / `delete(User)`, which previously wiped real
    octk API keys and users whenever `run_tests.py` ran against the default
    DATABASE_URL. See goals/why-the-octk-api-cozy-elephant.md.
    """
    if "/" not in prod_url:
        return prod_url
    base, _, dbname = prod_url.rpartition("/")
    dbname = dbname.split("?", 1)[0]
    if dbname.endswith("_test"):
        return prod_url
    return f"{base}/{dbname}_test"

def _ensure_test_database(test_url: str) -> None:
    """Create the test database (if absent) and run Alembic migrations against it."""
    import re
    import psycopg
    from psycopg import sql

    m = re.match(r"^[a-zA-Z0-9+]+://([^:]+):([^@]+)@([^:/]+):?(\d+)?/([^?]+)", test_url)
    if not m:
        print(f"WARNING: could not parse DATABASE_URL '{test_url}' to bootstrap test DB; skipping create/migrate.")
        return
    user, password, host, port, dbname = m.groups()
    port = port or "5432"

    admin_conn = psycopg.connect(
        host=host, port=port, user=user, password=password, dbname="postgres", autocommit=True
    )
    try:
        exists = admin_conn.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", (dbname,)
        ).fetchone()
        if not exists:
            print(f"[Test DB] Creating '{dbname}'...")
            admin_conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(dbname)))
    finally:
        admin_conn.close()

    print(f"[Test DB] Running Alembic migrations against '{dbname}'...")
    env = os.environ.copy()
    env["DATABASE_URL"] = test_url
    subprocess.run(["alembic", "upgrade", "heads"], check=True, env=env)

def _run_legacy_branding_check(project_root: Path) -> None:
    """Fail the run when pre-rebrand branding reappears.

    Cheap, needs no database, and catches the one failure mode a rename has:
    the old name creeping back into a new docstring. Skipped (with a note) when
    the checker is unavailable, e.g. an older image without the script.
    """
    checker = project_root / "scripts" / "check_legacy_branding.py"
    if not checker.exists():
        print("[Branding] check_legacy_branding.py not found — skipping")
        return
    result = subprocess.run([sys.executable, str(checker)], cwd=project_root)
    if result.returncode != 0:
        sys.exit(result.returncode)


def main():
    """
    Main entrypoint for test execution.
    """
    project_root = Path(__file__).resolve().parent.parent
    os.chdir(project_root)

    # Capture any additional pytest args passed to this script
    pytest_args = sys.argv[1:]

    _run_legacy_branding_check(project_root)

    if is_docker_container():
        # Inside Docker: Execute pytest directly in process
        print("[Docker] Running tests inside container...")

        # Ensure PYTHONPATH and sys.path include /app in the current environment
        os.environ["PYTHONPATH"] = "/app"
        repo_root = str(Path(__file__).resolve().parent.parent)
        for p in ("/app", repo_root):
            if p not in sys.path:
                sys.path.insert(0, p)

        # Point tests at a dedicated `_test` database, never the live one —
        # see _test_database_url docstring for why this matters.
        prod_url = os.environ.get("DATABASE_URL", "")
        if prod_url:
            test_url = _test_database_url(prod_url)
            _ensure_test_database(test_url)
            os.environ["DATABASE_URL"] = test_url
            print(f"[Test DB] DATABASE_URL -> {test_url.rsplit('@', 1)[-1]}")

        try:
            import pytest
        except ImportError:
            print("ERROR: 'pytest' not found. Make sure dependencies are installed.")
            sys.exit(1)

        # Default to running test/ + plugins/ if no specific test path is provided
        if not pytest_args:
            pytest_args = list(DEFAULT_TEST_PATHS)

        sys.exit(pytest.main(pytest_args))
    else:
        # On Host machine: Check if the Docker container is running
        container_candidates = [
            os.environ.get("WHISKERS_CONTAINER_NAME", "whiskers-agent-server"),
        ]
        container_name = container_candidates[0]
        container_running = False

        for candidate in container_candidates:
            print(f"[Host] Checking if Docker container '{candidate}' is active...")
            check_cmd = ["docker", "ps", "--filter", f"name={candidate}", "--format", "{{.Names}}"]
            try:
                result = subprocess.run(check_cmd, capture_output=True, text=True, check=True)
                if candidate in result.stdout:
                    container_name = candidate
                    container_running = True
                    break
            except (subprocess.SubprocessError, FileNotFoundError):
                pass

        if container_running:
            print(f"[Docker] Found running container '{container_name}'. Executing tests inside Docker...")
            
            # Attach no stdin/TTY by default. The dev container runs with
            # `tty: true` (docker-compose.dev.yml), so `docker exec -it` fails with
            # "cannot attach stdin to a TTY-enabled container" whenever the caller's
            # stdin isn't a real terminal — and `sys.stdin.isatty()` can't be trusted
            # to detect that (Git Bash / MSYS on Windows reports True for pipes).
            # Tests never read stdin; set WHISKERS_TESTS_TTY=1 for coloured output
            # when running from a genuine interactive terminal.
            exec_flags = ["-it"] if os.environ.get("WHISKERS_TESTS_TTY") else []
            
            # Re-enter this same script inside the container (not a bare `pytest`)
            # so the test-DB isolation logic above always applies.
            docker_cmd = ["docker", "exec"] + exec_flags + ["-e", "PYTHONPATH=/app", container_name, "python", "scripts/run_tests.py"] + pytest_args
            try:
                sys.exit(subprocess.run(docker_cmd).returncode)
            except KeyboardInterrupt:
                print("\nStopped.")
                sys.exit(1)
        else:
            print(f"[Warning] No active server container found ({', '.join(container_candidates)}).")
            print("[Local] Falling back to running tests locally on host...")

            repo_root = str(Path(__file__).resolve().parent.parent)
            if repo_root not in sys.path:
                sys.path.insert(0, repo_root)
            
            # Default to running test/ + plugins/ if no specific test path is provided
            if not pytest_args:
                pytest_args = list(DEFAULT_TEST_PATHS)

            try:
                import pytest
            except ImportError:
                print("ERROR: 'pytest' not found locally on host. Run 'pip install -r requirements.txt' or start docker stack first.")
                sys.exit(1)
                
            sys.exit(pytest.main(pytest_args))

if __name__ == "__main__":
    main()
