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

    # Synchronize to users table
    try:
        from core.user_management import get_user_by_username, update_user_password, create_user
        existing = await get_user_by_username(username.strip())
        if existing is not None:
            await update_user_password(username.strip(), password)
        else:
            await create_user(username.strip(), password, role="master", tenant_id=1)
    except Exception as exc:
        import logging
        logging.getLogger("whiskers").warning("Failed to sync admin user to users table: %s", exc)


async def main() -> None:
    """CLI entrypoint: prompt for a username/password (with confirmation)
    and bootstrap or reset it into the Vault and users table."""
    import argparse
    from dotenv import load_dotenv

    parser = argparse.ArgumentParser(description="Set or reset admin console credentials.")
    parser.add_argument("-f", "--force", "-r", "--reset", action="store_true", dest="force", help="reset existing credentials")
    parser.add_argument("-u", "--username", help="Admin username")
    parser.add_argument("-p", "--password", help="Admin password")
    args = parser.parse_args()

    load_dotenv()

    db_url = os.environ.get("DATABASE_URL", "")
    if not db_url:
        print("Error: DATABASE_URL is not set.", file=sys.stderr)
        sys.exit(1)

    master_key = os.environ.get("MASTER_KEY", "")
    if not master_key:
        print("Error: MASTER_KEY is not set.", file=sys.stderr)
        sys.exit(1)

    exists = await admin_account_exists()
    if exists and not args.force:
        if args.password:
            print("Admin account already exists; pass --reset or --force to overwrite.", file=sys.stderr)
            sys.exit(1)
        reset = input("Admin account already exists. Would you like to reset the password? [y/N]: ").strip().lower()
        if reset not in ("y", "yes"):
            print("Aborted.")
            return

    admin_username = args.username or os.environ.get("ADMIN_USERNAME", "").strip()
    if not admin_username:
        admin_username = input("Admin username [admin]: ").strip()
        if not admin_username:
            admin_username = "admin"

    if args.password:
        password = args.password
    else:
        password = getpass.getpass("New admin password: ")
        if not password:
            print("Error: password cannot be empty.", file=sys.stderr)
            sys.exit(1)

        confirm = getpass.getpass("Confirm password: ")
        if password != confirm:
            print("Error: passwords do not match.", file=sys.stderr)
            sys.exit(1)

    await set_admin_password(password, admin_username)
    print(f"Admin credentials for '{admin_username}' set successfully.")


if __name__ == "__main__":
    if sys.platform == "win32":
        try:
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        except AttributeError:
            pass
    asyncio.run(main())

