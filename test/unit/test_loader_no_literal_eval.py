"""Guard: dynamic tools loader must not fall back to ast.literal_eval."""

from pathlib import Path


def test_loader_handler_does_not_use_ast_literal_eval():
    src = Path("core/dynamic_tools/loader.py").read_text(encoding="utf-8")
    # No live fallback call / import (comments alone are fine).
    assert "import ast" not in src
    assert "literal_eval(" not in src
