#!/usr/bin/env python3
"""Entry script for the master setup TUI."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from terminal.tui.setup_tui import main

if __name__ == "__main__":
    main()