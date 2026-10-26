"""``core.proxy.compose`` must import without ``httpx2``; ``RelayAuth`` stays an ``httpx2.Auth``."""

from __future__ import annotations

import importlib
import sys

import pytest

_MODULES = ("core.proxy.compose", "core.proxy.proxy_manager", "core_graph.runtime.bootstrap")


def _snapshot() -> dict:
    return {m: sys.modules[m] for m in _MODULES if m in sys.modules}


def _restore(saved: dict) -> None:
    for m in _MODULES:
        sys.modules.pop(m, None)
    sys.modules.update(saved)
    # Importing a child sets an attribute on its parent package; restore those
    # references too so they agree with the restored sys.modules entries.
    for module_name, module in saved.items():
        parent_name, _, child_name = module_name.rpartition(".")
        parent = sys.modules.get(parent_name)
        if parent is not None:
            setattr(parent, child_name, module)


def test_imports_without_httpx2(monkeypatch):
    saved = _snapshot()
    for m in _MODULES:
        monkeypatch.delitem(sys.modules, m, raising=False)
    monkeypatch.setitem(sys.modules, "httpx2", None)  # import httpx2 -> ImportError
    try:
        compose = importlib.import_module("core.proxy.compose")
        assert callable(compose.build_proxy)
        importlib.import_module("core_graph.runtime.bootstrap")
        with pytest.raises(ImportError):
            compose.RelayAuth  # noqa: B018 - lazy class needs httpx2 on first access
    finally:
        _restore(saved)


def test_relay_auth_subclasses_httpx2_auth():
    httpx2 = pytest.importorskip("httpx2")
    from core.proxy import compose

    cls = compose.RelayAuth
    assert issubclass(cls, httpx2.Auth)
    assert compose.RelayAuth is cls  # cached: one class, stable isinstance
    auth = cls(plugin_id="proxy_x")
    assert isinstance(auth, cls)
    assert auth.auth_header == "Authorization"
