"""Unit tests for plugin/proxy content hashing and version history."""

from pathlib import Path

import pytest

from core.plugin_loader.content_hash import (
    append_version_history,
    compute_plugin_tree_hash,
    compute_proxy_hash,
    iter_hashable_files,
)


def _write_tree(base: Path, files: dict[str, str]) -> None:
    for rel, content in files.items():
        path = base / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def test_iter_hashable_files_exclusions_and_order(tmp_path: Path) -> None:
    _write_tree(
        tmp_path,
        {
            "manifest.json": "{}",
            "config.json": "{}",
            "a.py": "a",
            "readme.md": "# r",
            "skip.pyc": "x",
            "data.txt": "nope",
            "__pycache__/mod.pyc": "x",
            "nested/b.py": "b",
            "node_modules/pkg/index.js": "x",
            ".git/config": "x",
        },
    )
    found = iter_hashable_files(tmp_path)
    rels = [p.relative_to(tmp_path).as_posix() for p in found]
    assert rels == ["a.py", "config.json", "manifest.json", "nested/b.py", "readme.md"]
    assert all(not r.endswith(".pyc") for r in rels)
    assert not any("node_modules" in r or "__pycache__" in r or ".git" in r for r in rels)


def test_compute_plugin_tree_hash_deterministic(tmp_path: Path) -> None:
    _write_tree(
        tmp_path,
        {
            "manifest.json": '{"name": "p"}',
            "mod.py": "print(1)\n",
            "docs/help.md": "hi",
        },
    )
    h1 = compute_plugin_tree_hash(tmp_path)
    h2 = compute_plugin_tree_hash(tmp_path)
    assert h1 == h2
    assert h1.startswith("sha256:")
    assert len(h1) == len("sha256:") + 64


def test_hash_independent_of_walk_order(tmp_path: Path) -> None:
    """Same content under different creation order yields the same hash."""
    a = tmp_path / "a"
    b = tmp_path / "b"
    files = {
        "z.py": "z",
        "a.py": "a",
        "manifest.json": "{}",
        "mid/m.py": "m",
    }
    _write_tree(a, files)
    # reverse write order
    _write_tree(b, dict(reversed(list(files.items()))))
    assert compute_plugin_tree_hash(a) == compute_plugin_tree_hash(b)


def test_content_change_changes_hash(tmp_path: Path) -> None:
    _write_tree(tmp_path, {"mod.py": "v1", "manifest.json": "{}"})
    h1 = compute_plugin_tree_hash(tmp_path)
    (tmp_path / "mod.py").write_text("v2", encoding="utf-8")
    h2 = compute_plugin_tree_hash(tmp_path)
    assert h1 != h2


def test_rename_changes_hash(tmp_path: Path) -> None:
    _write_tree(tmp_path, {"old.py": "same", "manifest.json": "{}"})
    h1 = compute_plugin_tree_hash(tmp_path)
    (tmp_path / "old.py").rename(tmp_path / "new.py")
    h2 = compute_plugin_tree_hash(tmp_path)
    assert h1 != h2


def test_unreadable_file_does_not_raise(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_tree(tmp_path, {"mod.py": "ok", "manifest.json": "{}"})
    real_open = open
    baseline = compute_plugin_tree_hash(tmp_path)

    def flaky_open(path, *args, **kwargs):
        if Path(path).name == "mod.py" and "b" in (args[0] if args else kwargs.get("mode", "r")):
            raise OSError("permission denied")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr("builtins.open", flaky_open)
    h = compute_plugin_tree_hash(tmp_path)
    assert h.startswith("sha256:")
    # Unreadable file contributes an error sentinel — fingerprint must change
    assert h != baseline


def test_proxy_hash_key_order_stable() -> None:
    a = {
        "custom_description": "d",
        "name": "n",
        "url": "http://x",
        "transport": "http",
        "auth_mode": "none",
    }
    b = {
        "auth_mode": "none",
        "transport": "http",
        "url": "http://x",
        "name": "n",
        "custom_description": "d",
    }
    assert compute_proxy_hash(a) == compute_proxy_hash(b)
    assert compute_proxy_hash(a).startswith("sha256:")


def test_proxy_hash_ignores_secrets() -> None:
    base = {
        "name": "n",
        "url": "http://x",
        "transport": "http",
        "auth_mode": "bearer",
        "custom_description": "",
    }
    with_secret = {**base, "bearer": "s3cret", "token": "t"}
    assert compute_proxy_hash(base) == compute_proxy_hash(with_secret)


def test_proxy_hash_description_change() -> None:
    a = {
        "name": "n",
        "url": "http://x",
        "transport": "http",
        "auth_mode": "none",
        "custom_description": "a",
    }
    b = {**a, "custom_description": "b"}
    assert compute_proxy_hash(a) != compute_proxy_hash(b)


def test_proxy_hash_workspace_label_change() -> None:
    base = {
        "name": "n",
        "url": "http://x",
        "transport": "http",
        "auth_mode": "none",
        "custom_description": "desc",
    }
    # Omitted vs None vs "" should produce identical hash for back-compat
    h_omitted = compute_proxy_hash(base)
    h_none = compute_proxy_hash({**base, "workspace_label": None})
    h_empty = compute_proxy_hash({**base, "workspace_label": ""})
    assert h_omitted == h_none == h_empty

    # Changing workspace_label changes hash
    h_ws1 = compute_proxy_hash({**base, "workspace_label": "workspace-a"})
    h_ws2 = compute_proxy_hash({**base, "workspace_label": "workspace-b"})
    assert h_ws1 != h_omitted
    assert h_ws1 != h_ws2


def test_append_version_history_no_dup() -> None:
    meta = append_version_history({}, "1.0.0", "sha256:aaa")
    assert len(meta["version_history"]) == 1
    meta2 = append_version_history(meta, "1.0.1", "sha256:aaa")
    assert len(meta2["version_history"]) == 1
    assert meta2["version_history"][0]["version"] == "1.0.0"


def test_append_version_history_cap() -> None:
    meta: dict = {}
    for i in range(25):
        meta = append_version_history(meta, f"0.0.{i}", f"sha256:{i:064d}", cap=20)
    hist = meta["version_history"]
    assert len(hist) == 20
    assert hist[0]["content_hash"] == f"sha256:{5:064d}"
    assert hist[-1]["content_hash"] == f"sha256:{24:064d}"
    assert "seen_at" in hist[-1]
