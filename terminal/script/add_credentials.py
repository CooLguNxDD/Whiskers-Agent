#!/usr/bin/env python3
"""
CLI script to add a credential to the Vault.

Usage:
    python terminal/script/add_credentials.py -p jules_plugin --key JULES_API_KEY --value mock_value
"""

import argparse
import asyncio
import os
import sys
from pathlib import Path

# Ensure project root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from dotenv import load_dotenv


async def add_credential(plugin: str, key: str, value: str) -> None:
    """
    Set a credential key/value pair for a specific plugin in the Vault.
    """
    from core.context import vault
    
    await vault.set(plugin, key, value)
    print(f"Successfully added credential '{key}' for plugin '{plugin}' to the Vault.")


async def main() -> None:
    """
    Parse arguments and set the credential.
    """
    parser = argparse.ArgumentParser(description="Add a credential to the Vault.")
    parser.add_argument("-p", "--plugin", required=True, help="Target plugin ID (e.g., jules_plugin)")
    parser.add_argument("--key", required=True, help="Credential key name (e.g., JULES_API_KEY)")
    parser.add_argument("--value", required=True, help="Credential value to set")
    
    args = parser.parse_args()
    
    db_url = os.environ.get("DATABASE_URL", "")
    if not db_url:
        print("Error: DATABASE_URL is not set.", file=sys.stderr)
        sys.exit(1)

    master_key = os.environ.get("MASTER_KEY", "")
    if not master_key:
        print("Error: MASTER_KEY is not set.", file=sys.stderr)
        sys.exit(1)
        
    try:
        await add_credential(args.plugin, args.key, args.value)
    except Exception as exc:
        print(f"Error setting credential: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    if sys.platform == "win32":
        try:
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        except AttributeError:
            pass
    load_dotenv()
    asyncio.run(main())

