"""Initial-setup CLI for Whiskers Agent MCP Server."""

import argparse
import asyncio
import base64
import json
import logging
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse


def _repo_root() -> Path:
    """Return the repository root directory."""
    return Path(__file__).resolve().parent.parent.parent


def load_setup_defaults() -> dict:
    """Return hardcoded setup defaults since setup_defaults.json is removed."""
    return {
        "recommended_plugins": ["cat_terminal_relay_plugin", "portfolio_plugin", "job_search_plugin"],
        "default_tier": 100,
    }


def _marker(ok: bool) -> str:
    """Return a status glyph with ASCII fallback for limited encodings."""
    try:
        encoding = sys.stdout.encoding or "utf-8"
        "✓✗".encode(encoding)
        return "✓" if ok else "✗"
    except (LookupError, UnicodeEncodeError):
        return "[OK]" if ok else "[X]"


def _docker_daemon_status() -> tuple[bool, str]:
    """Return whether ``docker info`` succeeds (daemon actually running)."""
    if not shutil.which("docker"):
        return False, "docker not found — start Docker Desktop / Engine"
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            text=True,
            timeout=12,
        )
        if result.returncode == 0:
            return True, "daemon running"
        tail = (result.stderr or result.stdout or "docker info failed").strip()
        line = tail.splitlines()[-1] if tail else "docker info failed"
        return False, f"{line[:180]} — start Docker Desktop / Engine"
    except Exception as exc:
        return False, f"{exc} — start Docker Desktop / Engine"


def _port_listening(port: int, host: str = "127.0.0.1") -> bool:
    """Return True when something accepts TCP on host:port."""
    try:
        with socket.create_connection((host, port), timeout=0.4):
            return True
    except OSError:
        return False


def _master_key_from_env_file(root: Path) -> str:
    """Read MASTER_KEY from .env when present, else os.environ."""
    env_path = root / ".env"
    if env_path.is_file():
        try:
            for line in env_path.read_text(encoding="utf-8").splitlines():
                match = _ENV_KEY_RE.match(line)
                if match and match.group(1) == "MASTER_KEY":
                    return match.group(2).strip().strip('"').strip("'")
        except OSError:
            pass
    return os.environ.get("MASTER_KEY", "")


def _db_reachability() -> tuple[bool, str]:
    """TCP-probe DATABASE_URL host:port without opening a SQL session."""
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        return False, "DATABASE_URL not set"
    try:
        parsed = urlparse(url.replace("postgresql+psycopg://", "postgresql://", 1))
        host = parsed.hostname or "localhost"
        port = parsed.port or 5432
    except Exception as exc:
        return False, str(exc)
    try:
        with socket.create_connection((host, port), timeout=1.5):
            return True, f"{host}:{port} reachable"
    except OSError as exc:
        return False, f"{host}:{port} unreachable ({exc})"


def check_prerequisites() -> dict:
    """Return prerequisite check results keyed by check name."""
    root = _repo_root()

    python_ok = sys.version_info >= (3, 11)
    python_detail = (
        f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    )

    docker_path = shutil.which("docker")
    docker_ok = bool(docker_path)
    docker_detail = docker_path or "not found"

    daemon_ok, daemon_detail = _docker_daemon_status()

    compose_ok = False
    compose_detail = "docker not found"
    if docker_path:
        try:
            result = subprocess.run(
                ["docker", "compose", "version"],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode == 0:
                compose_ok = True
                compose_detail = (result.stdout or result.stderr or "").strip() or "available"
            else:
                compose_detail = (
                    result.stderr or result.stdout or "docker compose version failed"
                ).strip()
        except Exception as exc:
            compose_detail = str(exc)

    alembic_path = shutil.which("alembic")
    alembic_ok = bool(alembic_path)
    alembic_detail = alembic_path or "not found (optional)"

    psql_path = shutil.which("psql")
    psql_ok = bool(psql_path)
    psql_detail = psql_path or "not found (optional)"

    env_path = root / ".env"
    env_ok = env_path.is_file()
    env_detail = str(env_path) if env_ok else f"{env_path} missing"

    plugin_cfg = root / "config" / "plugin_config.json"
    plugin_cfg_ok = plugin_cfg.is_file()
    plugin_cfg_detail = str(plugin_cfg) if plugin_cfg_ok else f"{plugin_cfg} missing"

    config_paths = [root / "config" / pair[1] for pair in _CONFIG_SCAFFOLD_PAIRS]
    config_ok = all(p.is_file() for p in config_paths)
    missing = [p.name for p in config_paths if not p.is_file()]
    config_detail = "present" if config_ok else f"missing: {', '.join(missing)}"

    mk_value = _master_key_from_env_file(root)
    mk_ok = not is_placeholder_master_key(mk_value)
    mk_detail = "set (non-placeholder)" if mk_ok else "placeholder or missing — run: setup.py env"

    mcp_port = int(os.environ.get("MCP_PORT", "10000"))
    port_specs = [("port_mcp", mcp_port), ("port_postgres", 5432), ("port_frontend", 3000)]
    port_results = {}
    for name, port in port_specs:
        listening = _port_listening(port)
        # This is a pre-`up` collision check: a port already answering means some
        # other process (or a stack we didn't launch) holds it, which our
        # docker compose up would then fail to bind.
        port_results[name] = {
            "ok": not listening,
            "detail": f"{port} {'in use by another process — free it before `setup.py up`' if listening else 'available'}",
        }

    db_ok, db_detail = _db_reachability()

    return {
        "python": {"ok": python_ok, "detail": python_detail},
        "docker": {"ok": docker_ok, "detail": docker_detail},
        "docker_daemon": {"ok": daemon_ok, "detail": daemon_detail},
        "docker_compose": {"ok": compose_ok, "detail": compose_detail},
        "alembic": {"ok": alembic_ok, "detail": alembic_detail},
        "psql": {"ok": psql_ok, "detail": psql_detail},
        "env_file": {"ok": env_ok, "detail": env_detail},
        "plugin_config": {"ok": plugin_cfg_ok, "detail": plugin_cfg_detail},
        "config_files": {"ok": config_ok, "detail": config_detail},
        "master_key": {"ok": mk_ok, "detail": mk_detail},
        "db_reachable": {"ok": db_ok, "detail": db_detail},
        **port_results,
    }


_OPTIONAL_CHECKS = frozenset({
    "alembic",
    "psql",
    "config_files",
    "db_reachable",
    "port_mcp",
    "port_postgres",
    "port_frontend",
})

def run_doctor() -> int:
    """Print prerequisite checks and return 0 when required checks pass."""
    checks = check_prerequisites()
    required_ok = True

    for name, result in checks.items():
        marker = _marker(result["ok"])
        print(f"  {marker} {name}: {result['detail']}")
        if name in _OPTIONAL_CHECKS and not result["ok"]:
            print(f"    warning: {name} is optional but not satisfied")
        elif name not in _OPTIONAL_CHECKS and not result["ok"]:
            required_ok = False
            if name == "docker_daemon":
                print("    fix: start Docker Desktop (or: sudo systemctl start docker)")

    return 0 if required_ok else 1


_CONFIG_SCAFFOLD_PAIRS = (
    ("server_config_example.json", "server_config.json"),
    ("tools_api_config_example.json", "tools_api_config.json"),
    ("embedding_config_example.json", "embedding_config.json"),
    ("plugin_config_example.json", "plugin_config.json"),
)

_ENV_KEY_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")


def generate_master_key() -> str:
    """Return a fresh 32-byte random key encoded as base64 (44 characters)."""
    return base64.b64encode(os.urandom(32)).decode()


def generate_admin_password() -> str:
    """Return a strong random admin password (never persisted by setup)."""
    return secrets.token_urlsafe(18)


def is_placeholder_master_key(value: str) -> bool:
    """Return True when MASTER_KEY is empty, whitespace-only, or the shipped placeholder."""
    if not value or not value.strip():
        return True
    return value.strip() == "your-key"


def render_env(template_text: str, overrides: dict) -> str:
    """Apply override key=value pairs to .env text, preserving comments and other lines."""
    remaining = dict(overrides)
    lines_out = []

    for line in template_text.splitlines():
        match = _ENV_KEY_RE.match(line)
        if match and match.group(1) in remaining:
            key = match.group(1)
            lines_out.append(f"{key}={remaining.pop(key)}")
        else:
            lines_out.append(line)

    for key, value in remaining.items():
        lines_out.append(f"{key}={value}")

    result = "\n".join(lines_out)
    if template_text.endswith("\n"):
        result += "\n"
    return result


def scaffold_config_files(config_dir) -> list[str]:
    """Copy config *_example.json files to active names when the active file is missing."""
    config_dir = Path(config_dir)
    created = []

    for example_name, active_name in _CONFIG_SCAFFOLD_PAIRS:
        example = config_dir / example_name
        active = config_dir / active_name
        if not active.is_file() and example.is_file():
            shutil.copy2(example, active)
            created.append(active_name)

    return created


def run_env(args=None) -> int:
    """Create .env and config files from templates; ensure MASTER_KEY is not a placeholder."""
    root = _repo_root()
    env_path = root / ".env"
    sample_path = root / ".env_sample"
    non_interactive = getattr(args, "yes", False) if args is not None else False

    if env_path.is_file():
        template_text = env_path.read_text(encoding="utf-8")
        creating = False
    else:
        if not sample_path.is_file():
            print("error: .env_sample not found", file=sys.stderr)
            return 1
        template_text = sample_path.read_text(encoding="utf-8")
        creating = True

    current_key = ""
    for line in template_text.splitlines():
        match = re.match(r"^\s*MASTER_KEY\s*=\s*(.*)$", line)
        if match:
            current_key = match.group(1)
            break

    overrides = {}
    if is_placeholder_master_key(current_key):
        overrides["MASTER_KEY"] = generate_master_key()

    if creating:
        if not non_interactive:
            print(f"  creating {env_path.name} from .env_sample")
        env_path.write_text(render_env(template_text, overrides), encoding="utf-8")
    elif overrides:
        if not non_interactive:
            print(f"  replacing placeholder MASTER_KEY in {env_path.name}")
        env_path.write_text(render_env(template_text, overrides), encoding="utf-8")

    created = scaffold_config_files(root / "config")
    if created and not non_interactive:
        print(f"  created config: {', '.join(created)}")

    # Reload so later steps in the same `all` run see freshly written values.
    from dotenv import load_dotenv

    load_dotenv(env_path, override=True)

    return 0


_LLM_KINDS = ("chat", "embedding", "route", "core")

_PROVIDER_API_KEY_ENV = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "gemini": "GOOGLE_API_KEY",
    "gemini-vertex": "GOOGLE_VERTEX_API_KEY",
}


def run_llm(args=None) -> int:
    """Write LLM_PROVIDER and the matching API key into .env (pool seeding stays in the TUI)."""
    root = _repo_root()
    env_path = root / ".env"
    if not env_path.is_file():
        print("error: .env missing; run the env step first", file=sys.stderr)
        return 1

    non_interactive = getattr(args, "yes", False) if args is not None else False
    provider = getattr(args, "provider", None) if args is not None else None
    if not provider:
        provider = os.environ.get("LLM_PROVIDER", "").strip() or None
    if not provider:
        if non_interactive:
            print("  skipped llm (pass --provider or set LLM_PROVIDER)")
            return 0
        allowed = ", ".join(_PROVIDER_API_KEY_ENV)
        raw = input(f"LLM provider [{allowed}] (default openai): ").strip()
        provider = raw or "openai"
    if provider not in _PROVIDER_API_KEY_ENV:
        print(f"error: unknown provider {provider!r}", file=sys.stderr)
        return 1

    key_name = _PROVIDER_API_KEY_ENV[provider]
    key_val = os.environ.get(key_name, "").strip()
    if not key_val and not non_interactive:
        import getpass

        key_val = getpass.getpass(f"{key_name} (blank to skip): ").strip()

    overrides = {"LLM_PROVIDER": provider}
    if key_val:
        overrides[key_name] = key_val
    elif non_interactive:
        print(f"  warning: {key_name} unset; wrote LLM_PROVIDER={provider} only")

    env_path.write_text(
        render_env(env_path.read_text(encoding="utf-8"), overrides),
        encoding="utf-8",
    )
    from dotenv import load_dotenv

    load_dotenv(env_path, override=True)
    print(f"  LLM_PROVIDER={provider}")
    return 0


def list_available_plugins() -> list[dict]:
    """Scan plugins/*/manifest.json and return name, tier, and requires for each plugin."""
    plugins_dir = _repo_root() / "plugins"
    plugins: list[dict] = []

    if not plugins_dir.is_dir():
        return plugins

    for manifest_path in sorted(plugins_dir.glob("*/manifest.json")):
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        plugins.append(
            {
                "name": manifest.get("name", manifest_path.parent.name),
                "tier": manifest.get("tier"),
                "requires": manifest.get("requires", []),
            }
        )
    return plugins


def normalize_plugin_package(name: str) -> str:
    """Return the dotted plugins.<name> package string, preserving an existing prefix."""
    if name.startswith("plugins."):
        return name
    return f"plugins.{name}"


def write_plugin_boot_list(plugins: list[str], tier, config_path) -> None:
    """Write {tier, plugins} boot-list JSON to config_path with normalised package names."""
    payload = {
        "tier": tier,
        "plugins": [normalize_plugin_package(p) for p in plugins],
    }
    path = Path(config_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _bare_plugin_names(plugins: list) -> list[str]:
    """Strip a ``plugins.`` prefix from boot-list entries."""
    names = []
    for item in plugins:
        if not isinstance(item, str):
            continue
        names.append(item.split(".")[-1] if "." in item else item)
    return names


async def mirror_plugin_active(
    enabled_names: list[str], all_names: list[str], registry=None
) -> dict:
    """Mirror enabled plugin names to DB is_active flags; skip when DATABASE_URL is unset."""
    if not os.environ.get("DATABASE_URL", "").strip():
        logging.getLogger("whiskers_agent").warning(
            "DATABASE_URL not set; skipping plugin active mirror"
        )
        return {"skipped": True}

    if registry is None:
        from db_layer.plugin_registry_store import DBPluginRegistry

        registry = DBPluginRegistry()

    enabled = set(enabled_names)
    for name in all_names:
        # set_active is UPDATE-only and would no-op on a fresh (empty) DB,
        # leaving non-recommended plugins active at boot. Ensure the row first;
        # register()'s ON CONFLICT preserves is_active, so this survives the
        # later full re-registration on server startup.
        await registry.register({"name": name})
        await registry.set_active(name, name in enabled)
    return {
        "enabled": [n for n in all_names if n in enabled],
        "disabled": [n for n in all_names if n not in enabled],
    }


def run_plugins(args=None) -> int:
    """Merge or write the plugin boot list and mirror active state to the database."""
    defaults = load_setup_defaults()
    config_path = _repo_root() / "config" / "plugin_config.json"
    non_interactive = getattr(args, "yes", False) if args is not None else False
    reset = getattr(args, "reset_plugins", False) if args is not None else False
    tier = defaults.get("default_tier", 100)
    plugins = list(defaults["recommended_plugins"])

    if config_path.is_file() and not reset:
        try:
            existing = json.loads(config_path.read_text(encoding="utf-8"))
            if isinstance(existing, dict):
                tier = existing.get("tier", tier)
                loaded = _bare_plugin_names(existing.get("plugins") or [])
                if loaded:
                    plugins = loaded
                else:
                    write_plugin_boot_list(plugins, tier, config_path)
            else:
                write_plugin_boot_list(plugins, tier, config_path)
        except (OSError, json.JSONDecodeError):
            write_plugin_boot_list(plugins, tier, config_path)
    else:
        write_plugin_boot_list(plugins, tier, config_path)

    all_names = [p["name"] for p in list_available_plugins()]
    result = asyncio.run(mirror_plugin_active(plugins, all_names))

    if result.get("skipped"):
        if not non_interactive:
            print("  skipped plugin DB mirror (DATABASE_URL not set)")
        return 0

    if not non_interactive:
        enabled = ", ".join(result.get("enabled", []))
        print(f"  plugin boot list (tier={tier}): {enabled}")
    return 0


_POSTGRES_CONTAINER = "whiskers-postgres"


def docker_compose_up(
    services: list[str],
    detached: bool = True,
    build: bool = False,
    dev: bool = False,
) -> int:
    """Run docker compose up for the given services from the repo root."""
    root = _repo_root()
    cmd = ["docker", "compose", "-f", "docker-compose.yml"]
    if dev:
        cmd.extend(["-f", "docker-compose.dev.yml"])
    cmd.append("up")
    if detached:
        cmd.append("-d")
    if build:
        cmd.append("--build")
    cmd.extend(services)
    try:
        result = subprocess.run(cmd, cwd=root)
        return result.returncode
    except FileNotFoundError:
        print("error: docker command not found", file=sys.stderr)
        return 1


def wait_for_postgres(timeout_s: int = 90, on_tick=None) -> bool:
    """Poll pg_isready in the postgres container until ready or timeout."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            result = subprocess.run(
                ["docker", "exec", _POSTGRES_CONTAINER, "pg_isready"],
                capture_output=True,
            )
            if result.returncode == 0:
                return True
        except FileNotFoundError:
            return False
        remaining = deadline - time.monotonic()
        if on_tick is not None:
            on_tick(max(0.0, remaining))
        time.sleep(2)
    return False


def run_migrations() -> int:
    """Shell out to scripts/migrate.py upgrade heads from the repo root."""
    root = _repo_root()
    result = subprocess.run(
        [sys.executable, "scripts/migrate.py", "upgrade", "heads"],
        cwd=root,
    )
    return result.returncode


def run_migrate(args=None) -> int:
    """Run Alembic database migrations."""
    return run_migrations()


def run_plugin_migrate(args=None) -> int:
    """Apply plugin-owned SQL migrations (same script CI runs)."""
    if not os.environ.get("DATABASE_URL", "").strip():
        print("error: DATABASE_URL not set; cannot apply plugin migrations", file=sys.stderr)
        return 1
    root = _repo_root()
    result = subprocess.run(
        [sys.executable, "scripts/apply_plugin_migrations.py"],
        cwd=root,
    )
    return result.returncode


def run_db(args=None) -> int:
    """Bring up the full Docker stack and wait for postgres readiness."""
    dev = getattr(args, "dev", False) if args is not None else False
    rc = docker_compose_up([], detached=True, dev=dev)
    if rc != 0:
        return rc
    if not wait_for_postgres():
        print("error: postgres did not become ready in time", file=sys.stderr)
        return 1
    return 0


def run_up(args=None) -> int:
    """Build and start the full Docker stack, then print access URLs."""
    rc = docker_compose_up(
        [], detached=True, build=True, dev=getattr(args, "dev", False)
    )
    if rc != 0:
        return rc
    port = int(os.environ.get("MCP_PORT", "10000"))
    print(f"  MCP:            http://localhost:{port}")
    print(f"  pgAdmin:        http://localhost:5050")
    print(f"  MinIO console:  http://localhost:9001")
    print(f"  Frontend:       http://localhost:3000")
    return rc


def run_restart(args=None) -> int:
    """Restart the MCP server container via the Docker SDK, returning 0 on success."""
    name = os.environ.get("WHISKERS_CONTAINER_NAME", "whiskers-agent-server")
    try:
        import docker
        from docker.errors import NotFound, DockerException
    except ImportError:
        print("ERROR: docker SDK not installed. Run 'pip install docker' or rebuild the image.")
        return 1

    try:
        client = docker.from_env()
        print(f"Restarting {name} ...")
        client.containers.get(name).restart(timeout=10)
        print(f"Restarted {name}.")
        return 0
    except NotFound:
        print(f"ERROR: container '{name}' not found.")
        return 1
    except (DockerException, Exception) as exc:
        print(f"ERROR: could not reach Docker daemon: {exc}")
        return 1


def check_mcp_endpoint(port: int | None = None, timeout_s: int = 60, on_tick=None) -> bool:
    """Poll the MCP HTTP endpoint until reachable or the timeout expires."""
    import urllib.error
    import urllib.request

    resolved = port if port is not None else int(os.environ.get("MCP_PORT", "10000"))
    url = f"http://localhost:{resolved}/mcp"
    deadline = time.monotonic() + timeout_s

    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(url, timeout=5)  # noqa: S310 (localhost only)
            return True
        except urllib.error.HTTPError:
            return True  # any HTTP status means the endpoint is serving
        except Exception:
            # Transient during startup: URLError, socket resets,
            # http.client.RemoteDisconnected, etc. — keep polling.
            remaining = deadline - time.monotonic()
            if on_tick is not None:
                on_tick(max(0.0, remaining))
            time.sleep(2)
    return False


async def check_active_llm() -> dict:
    """Return the active LLM kind map, or empty when DATABASE_URL is unset."""
    if not os.environ.get("DATABASE_URL", "").strip():
        return {}
    from core.llm_config_service import get_active_map

    return await get_active_map()


def run_health(args=None) -> int:
    """Print stack health checks and return 0 when the MCP endpoint is reachable."""
    mcp_ok = check_mcp_endpoint()
    db_present = bool(os.environ.get("DATABASE_URL", "").strip())
    active_llm = asyncio.run(check_active_llm())

    print(f"  mcp_endpoint: {'reachable' if mcp_ok else 'unreachable'}")
    print(f"  database: {'present' if db_present else 'not configured'}")
    print(f"  active_llm: {active_llm}")

    return 0 if mcp_ok else 1


def _load_set_admin_password_module():
    """Load terminal/script/set_admin_password.py by path (not a package)."""
    import importlib.util

    path = Path(__file__).resolve().parent / "set_admin_password.py"
    spec = importlib.util.spec_from_file_location("set_admin_password", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def run_admin(args=None) -> int:
    """Set admin password (first-time only). Plugin credentials belong in setup_tui."""
    import getpass

    non_interactive = getattr(args, "yes", False) if args is not None else False

    db_url = os.environ.get("DATABASE_URL", "").strip()
    master_key = os.environ.get("MASTER_KEY", "").strip()
    if not db_url or not master_key:
        logging.getLogger("whiskers_agent").warning(
            "DATABASE_URL or MASTER_KEY not set; skipping admin setup"
        )
        if not non_interactive:
            print("  skipped admin setup (DATABASE_URL or MASTER_KEY not set)")
        return 0

    mod = _load_set_admin_password_module()
    set_admin_password = mod.set_admin_password
    admin_account_exists = mod.admin_account_exists

    # Match HTTP signup: skip when vault already has admin/username.
    try:
        already_exists = asyncio.run(admin_account_exists())
    except Exception as exc:
        logging.getLogger("whiskers_agent").exception("admin existence check failed")
        print(f"error: could not check for existing admin account: {exc}", file=sys.stderr)
        return 1

    if already_exists:
        if not non_interactive:
            print("  admin account already exists; skipping creation")
        return 0

    admin_username = os.environ.get("ADMIN_USERNAME", "").strip()
    admin_password = os.environ.get("ADMIN_PASSWORD", "").strip()
    generated = False
    if non_interactive:
        if not admin_username:
            admin_username = "admin"
        if not admin_password:
            admin_password = generate_admin_password()
            generated = True
        password = admin_password
        username = admin_username
        if generated:
            banner = (
                "\n"
                "============================================================\n"
                "SAVE THIS NOW — admin password is not written to disk\n"
                f"  username: {username}\n"
                f"  password: {password}\n"
                "============================================================\n"
            )
            print(banner)
    else:
        username = input("Admin username [admin]: ").strip()
        if not username:
            username = "admin"

        password = None
        for _ in range(3):
            candidate = getpass.getpass("Admin password: ")
            if not candidate:
                print("error: password cannot be empty", file=sys.stderr)
                continue
            confirm = getpass.getpass("Confirm password: ")
            if candidate != confirm:
                print("error: passwords do not match", file=sys.stderr)
                continue
            password = candidate
            break
        if password is None:
            print("error: too many password attempts", file=sys.stderr)
            return 1

    asyncio.run(set_admin_password(password, username))
    if not non_interactive:
        print("  admin credentials set")
    return 0


def run_tui(args=None) -> int:
    """Launch the master setup TUI for plugins, credentials, and live config."""
    non_interactive = getattr(args, "yes", False) if args is not None else False
    if non_interactive:
        print(
            "  skipped tui (non-interactive mode); "
            "run: python terminal/script/setup_tui.py  or  python -m terminal"
        )
        return 0

    print("  launching setup TUI (plugin credentials, vault, live config)…")
    # Lazy import: setup_tui imports this module; keep load order one-way.
    from terminal.tui.setup_tui import main as tui_main

    tui_main()
    return 0


_ALL_STEPS = [
    "doctor",
    "env",
    "llm",
    "db",
    "migrate",
    "plugin_migrate",
    "admin",
    "plugins",
    "up",
    "health",
    "tui",
]

# Steps that need DATABASE_URL; "tui" is interactive-only and skipped under -y.
_DB_DEPENDENT_STEPS = frozenset({"db", "migrate", "plugin_migrate", "admin"})

# Final interactive step — TUI "run all" skips this to avoid nested menus.
_INTERACTIVE_ONLY_STEPS = frozenset({"tui"})

_SUBCOMMANDS = (
    "doctor",
    "env",
    "llm",
    "db",
    "migrate",
    "plugin_migrate",
    "admin",
    "plugins",
    "up",
    "health",
    "tui",
    "restart",
    "all",
)


def plan_steps(command: str) -> list[str]:
    """Return the ordered setup steps for a subcommand or the full pipeline for 'all'."""
    if command == "all":
        return list(_ALL_STEPS)
    return [command]


def setup_state_path() -> Path:
    """Return the on-disk resume state path (gitignored under .whiskers/ or legacy .whiskers/)."""
    legacy = _repo_root() / ".whiskers" / "setup-state.json"
    if legacy.is_file():
        return legacy
    return _repo_root() / ".whiskers" / "setup-state.json"


def load_setup_state() -> dict:
    """Load per-step resume state; missing/corrupt files start empty."""
    path = setup_state_path()
    if not path.is_file():
        return {"version": 1, "steps": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"version": 1, "steps": {}}
    if not isinstance(data, dict):
        return {"version": 1, "steps": {}}
    data.setdefault("version", 1)
    data.setdefault("steps", {})
    if not isinstance(data["steps"], dict):
        data["steps"] = {}
    return data


def save_setup_state(state: dict) -> None:
    """Persist resume state after each step."""
    path = setup_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def _record_step(state: dict, step: str, status: str, started: str, duration: float, error=None) -> None:
    state.setdefault("steps", {})[step] = {
        "status": status,
        "started": started,
        "duration": round(duration, 3),
        "error": error,
    }
    save_setup_state(state)


def _add_shared_flags(parser: argparse.ArgumentParser) -> None:
    """Attach flags shared across setup subcommands."""
    parser.add_argument("-y", "--yes", action="store_true", help="non-interactive")
    parser.add_argument(
        "--provider",
        choices=["openai", "anthropic", "gemini", "gemini-vertex"],
        help="LLM provider preset (writes LLM_PROVIDER + matching key into .env)",
    )
    parser.add_argument("--dev", action="store_true", help="use docker-compose.dev.yml")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        dest="dry_run",
        help="print planned steps without executing",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="re-run steps already marked ok in setup-state.json",
    )
    parser.add_argument(
        "--from",
        dest="from_step",
        metavar="STEP",
        choices=_ALL_STEPS,
        help="resume the all-pipeline at this step",
    )
    parser.add_argument(
        "--reset-plugins",
        action="store_true",
        dest="reset_plugins",
        help="overwrite config/plugin_config.json with recommended defaults",
    )


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level argparse parser with setup subcommands."""
    parser = argparse.ArgumentParser(
        description="Whiskers Agent MCP Server initial setup",
    )
    subparsers = parser.add_subparsers(dest="command")
    for name in _SUBCOMMANDS:
        sub = subparsers.add_parser(name, help=f"run the {name} setup step")
        _add_shared_flags(sub)
    return parser


def dispatch_step(step: str, args) -> int:
    """Run a named setup step and return its exit code."""
    if step == "doctor":
        return run_doctor()
    if step == "migrate":
        return run_migrations()
    fn = globals().get(f"run_{step}")
    if fn is None:
        print(f"error: unknown step {step}", file=sys.stderr)
        return 1
    return fn(args)


def _skip_reason(step: str, args, state: dict) -> str | None:
    """Return a skip reason or None when the step should run."""
    if getattr(args, "force", False):
        return None
    from_step = getattr(args, "from_step", None)
    steps = plan_steps("all")
    if from_step:
        if from_step not in steps:
            return None
        if steps.index(step) < steps.index(from_step):
            return f"--from {from_step}"
        return None
    rec = (state.get("steps") or {}).get(step) or {}
    if rec.get("status") == "ok":
        return "already ok"
    return None


def _print_summary(rows: list[dict]) -> None:
    """Print the all-pipeline summary table."""
    print()
    print("  setup summary")
    print(f"  {'step':<16} {'status':<12} {'duration':<10} next action")
    print("  " + "-" * 64)
    for row in rows:
        nxt = row.get("next") or "—"
        print(
            f"  {row['step']:<16} {row['status']:<12} {row['duration']:<10} {nxt}"
        )


_NEXT_ACTION = {
    "doctor": "fix the failing check, then re-run",
    "env": "run: python terminal/script/setup.py env",
    "llm": "pass --provider or set the matching API key, then re-run",
    "db": "start Docker and re-run; never docker compose down -v",
    "migrate": "ensure DATABASE_URL points at postgres, then re-run migrate",
    "plugin_migrate": "re-run: python terminal/script/setup.py plugin_migrate",
    "admin": "set ADMIN_PASSWORD or re-run admin interactively",
    "plugins": "inspect config/plugin_config.json, then re-run plugins",
    "up": "docker compose logs -f whiskers-agent",
    "health": "wait for the server, then re-run health",
    "tui": "python -m terminal",
}


def run_all(args) -> int:
    """Run the full ordered setup pipeline with resume, summary, and fail-closed skips."""
    steps = plan_steps("all")
    state = load_setup_state()
    plan = []
    for step in steps:
        reason = _skip_reason(step, args, state)
        plan.append((step, reason))

    if getattr(args, "dry_run", False):
        print("  planned steps:")
        for step, reason in plan:
            if reason:
                print(f"    {step}  (skip: {reason})")
            else:
                print(f"    {step}")
        return 0

    skipped_ok = [s for s, r in plan if r]
    if skipped_ok:
        print("  resume: skipping already-complete steps:")
        for step, reason in plan:
            if reason:
                print(f"    {step} ({reason})")

    # Doctor treats .env / plugin_config as required; scaffold first on a cold clone.
    root = _repo_root()
    if not (root / ".env").is_file() or not (root / "config" / "plugin_config.json").is_file():
        print("  scaffolding .env / config before doctor")
        run_env(args)

    rows: list[dict] = []
    required_skipped = False

    for step, reason in plan:
        if step in _INTERACTIVE_ONLY_STEPS and getattr(args, "yes", False):
            started_iso = datetime.now(timezone.utc).isoformat()
            _record_step(state, step, "skipped", started_iso, 0.0, "non-interactive")
            rows.append({"step": step, "status": "skipped", "duration": "0s", "next": _NEXT_ACTION.get(step)})
            print(f"  skipped {step} (non-interactive)")
            continue
        if reason:
            rec = (state.get("steps") or {}).get(step) or {}
            dur = rec.get("duration", 0)
            rows.append({
                "step": step,
                "status": "skipped",
                "duration": f"{dur}s",
                "next": "already done" if reason == "already ok" else reason,
            })
            continue
        if step in _DB_DEPENDENT_STEPS and not os.environ.get("DATABASE_URL", "").strip():
            started_iso = datetime.now(timezone.utc).isoformat()
            _record_step(state, step, "skipped", started_iso, 0.0, "DATABASE_URL not set")
            print(f"  error: skipping {step} (DATABASE_URL not set) — pipeline cannot complete")
            rows.append({
                "step": step,
                "status": "skipped",
                "duration": "0s",
                "next": _NEXT_ACTION.get(step, "set DATABASE_URL"),
            })
            required_skipped = True
            continue

        print(f"  → {step}")
        started = time.monotonic()
        started_iso = datetime.now(timezone.utc).isoformat()
        try:
            rc = dispatch_step(step, args)
        except KeyboardInterrupt:
            _record_step(state, step, "failed", started_iso, time.monotonic() - started, "interrupted")
            print(f"  interrupted during {step}", file=sys.stderr)
            rows.append({
                "step": step,
                "status": "failed",
                "duration": f"{time.monotonic() - started:.1f}s",
                "next": "re-run to resume",
            })
            _print_summary(rows)
            return 130
        duration = time.monotonic() - started
        if rc != 0:
            _record_step(state, step, "failed", started_iso, duration, f"exit {rc}")
            rows.append({
                "step": step,
                "status": "failed",
                "duration": f"{duration:.1f}s",
                "next": _NEXT_ACTION.get(step, "re-run"),
            })
            _print_summary(rows)
            return rc
        _record_step(state, step, "ok", started_iso, duration, None)
        rows.append({
            "step": step,
            "status": "ok",
            "duration": f"{duration:.1f}s",
            "next": "—",
        })

    _print_summary(rows)
    if required_skipped:
        return 1
    return 0


def main(argv=None) -> int:
    """Parse CLI args, load environment, and dispatch to the selected setup step."""
    if argv is None:
        argv = sys.argv[1:]
    if not argv:
        argv = ["all"]

    root = _repo_root()
    root_str = str(root)
    if root_str not in sys.path:
        sys.path.insert(0, root_str)

    if sys.platform == "win32":
        try:
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        except AttributeError:
            pass

    from dotenv import load_dotenv

    load_dotenv()

    args = build_parser().parse_args(argv)
    if args.command == "all":
        return run_all(args)
    if args.command == "doctor":
        return run_doctor()
    return dispatch_step(args.command, args)


if __name__ == "__main__":
    sys.exit(main())
