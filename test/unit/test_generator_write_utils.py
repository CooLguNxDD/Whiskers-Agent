"""Unit tests for Tools/_write_utils.py (generator safe write helpers)."""

import importlib.util
import sys
from pathlib import Path

import pytest

_TOOLS_DIR = Path(__file__).resolve().parents[2] / "Tools"
_WRITE_UTILS_PATH = _TOOLS_DIR / "_write_utils.py"


def _load_write_utils():
    spec = importlib.util.spec_from_file_location("_write_utils", _WRITE_UTILS_PATH)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {_WRITE_UTILS_PATH}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("_write_utils", mod)
    spec.loader.exec_module(mod)
    return mod


_write_utils = _load_write_utils()
WriteError = _write_utils.WriteError
emit_generated = _write_utils.emit_generated
write_text_safe = _write_utils.write_text_safe


def test_write_text_safe_creates_nested_parents(tmp_path):
    target = tmp_path / "nested" / "dir" / "module.py"
    result = write_text_safe(target, "print('ok')\n")
    assert result == target
    assert target.read_text(encoding="utf-8") == "print('ok')\n"


def test_write_text_safe_no_create_parents_fails_when_missing(tmp_path):
    target = tmp_path / "missing" / "file.txt"
    with pytest.raises(WriteError, match="cannot write"):
        write_text_safe(target, "x", create_parents=False)


def test_write_text_safe_surfaces_permission_error(tmp_path, monkeypatch):
    target = tmp_path / "out.py"

    def _boom(self, data, encoding=None):
        raise PermissionError("denied")

    monkeypatch.setattr(Path, "write_text", _boom, raising=False)
    with pytest.raises(WriteError, match="cannot write"):
        write_text_safe(target, "code")


def test_emit_generated_writes_file(tmp_path, capsys):
    out = tmp_path / "gen.py"
    emit_generated("x = 1\n", out)
    assert out.read_text(encoding="utf-8") == "x = 1\n"
    assert "Generated →" in capsys.readouterr().out


def test_emit_generated_prints_stdout_when_no_output(capsys):
    emit_generated("hello\n", None)
    assert capsys.readouterr().out == "hello\n"


def test_emit_generated_exits_on_write_failure(tmp_path, monkeypatch):
    def _fail(path, content, **kwargs):
        raise WriteError("cannot write /no/such/path: denied")

    monkeypatch.setattr(_write_utils, "write_text_safe", _fail)
    with pytest.raises(SystemExit) as exc:
        emit_generated("x", tmp_path / "x.py")
    assert exc.value.code == 1