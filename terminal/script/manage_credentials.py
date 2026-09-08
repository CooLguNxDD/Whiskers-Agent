#!/usr/bin/env python3
"""Entry script for the vault credential manager TUI."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from dotenv import load_dotenv
from terminal.bootstrap import repo_root
from terminal.tui.credentials import main

if __name__ == "__main__":
    load_dotenv(repo_root() / ".env")
    asyncio.run(main())