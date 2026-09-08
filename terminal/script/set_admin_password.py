#!/usr/bin/env python3
"""
Bootstrap the admin password in the Vault.

Usage:
    python terminal/script/set_admin_password.py

Requires DATABASE_URL and MASTER_KEY to be set (via .env or environment).
"""

import asyncio
import getpass
import os
import sys
from pathlib import Path

# Ensure project root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))


async def admin_account_exists() -> bool:
    """Return True when an admin username is already stored in the Vault."""
    from core.context import vault

    username = await vault.get("admin", "username")
    return username is not None


async def set_admin_password(password: str, username: str = "admin") -> None:
    """Hash and store the admin password and username in the Vault."""
    if not password or not password.strip():
        raise ValueError("password cannot be empty")
    if not username or not username.strip():
        raise ValueError("username cannot be empty")

    import bcrypt
    from api.admin_routes import _ensure_admin_plugin_row
    from core.context import vault

    hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=12)).decode()
    await _ensure_admin_plugin_row()
    await vault.set("admin", "username", username.strip())
    await vault.set("admin", "password_hash", hashed)


async def main() -> None:
    """Interactive CLI entrypoint: skip if an admin already exists, otherwise
    prompt for a username/password (with confirmation) and bootstrap it into
    the Vault. Exits non-zero if required env vars are missing or the
    passwords don't match."""
    from dotenv import load_dotenv

    load_dotenv()

    db_url = os.environ.get("DATABASE_URL", "")
    if not db_url:
        print("Error: DATABASE_URL is not set.", file=sys.stderr)
        sys.exit(1)

    master_key = os.environ.get("MASTER_KEY", "")
    if not master_key:
        print("Error: MASTER_KEY is not set.", file=sys.stderr)
        sys.exit(1)

    if await admin_account_exists():
        print("Admin account already exists; not creating a new one.")
        return

    admin_username = os.environ.get("ADMIN_USERNAME", "").strip()
    if not admin_username:
        admin_username = input("Admin username [admin]: ").strip()
        if not admin_username:
            admin_username = "admin"

    password = getpass.getpass("Admin password: ")
    if not password:
        print("Error: password cannot be empty.", file=sys.stderr)
        sys.exit(1)

    confirm = getpass.getpass("Confirm password: ")
    if password != confirm:
        print("Error: passwords do not match.", file=sys.stderr)
        sys.exit(1)

    await set_admin_password(password, admin_username)
    print("Admin credentials set successfully.")


if __name__ == "__main__":
    if sys.platform == "win32":
        try:
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        except AttributeError:
            pass
    asyncio.run(main())

