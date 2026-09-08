import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.plugin_loader.plugin_loader import (
    _interpolate_manifest,
    parse_tier,
    discover_and_load_plugins_async,
    _relay_manifests,
)
from core.plugin_loader.resolver import PluginSpec, ResolvedPlan, build_dag, build_specs, toposort
from core.plugin_loader.skill_registry import get_plugin_skills, clear as clear_plugin_skills
from core.plugin_loader.scope_registry import get_plugin_scopes, clear as clear_plugin_scopes
from core.plugin_loader.tier import Tier


def _spec(name: str, requires: tuple[str, ...] = ()) -> PluginSpec:
    """Minimal PluginSpec fixture for DAG/toposort tests."""
    return PluginSpec(
        package=f"plugins.{name}",
        name=name,
        tier=1,
        requires=requires,
        required_credentials=(),
        manifest_path=Path("x"),
        manifest={},
    )


def test_toposort_linear_chain():
    # p1 depends on nothing
    # p2 depends on p1
    # p3 depends on p2
    specs = [
        _spec("p1"),
        _spec("p2", ("p1",)),
        _spec("p3", ("p2",)),
    ]
    sorted_pkgs = toposort(build_dag(specs))
    assert sorted_pkgs == ["p1", "p2", "p3"]

def test_toposort_no_deps_returns_all():
    specs = [_spec("p1"), _spec("p2"), _spec("p3")]
    sorted_pkgs = toposort(build_dag(specs))
    assert set(sorted_pkgs) == {"p1", "p2", "p3"}
    # Kahn min-heap yields deterministic lexicographic order
    assert sorted_pkgs == ["p1", "p2", "p3"]

def test_toposort_raises_on_cycle():
    specs = [
        _spec("p1", ("p2",)),
        _spec("p2", ("p1",)),
    ]
    with pytest.raises(ValueError, match=r"Circular plugin dependency detected among:"):
        toposort(build_dag(specs))

def test_toposort_missing_dep_skips_with_warning():
    # p1 depends on p2 (which is missing) — edge is omitted, p1 still sorts
    specs = [_spec("p1", ("p2",))]
    sorted_pkgs = toposort(build_dag(specs))
    assert sorted_pkgs == ["p1"]

def test_interpolate_replaces_env_var():
    with patch.dict(os.environ, {"MY_VAR": "hello"}):
        res = _interpolate_manifest({"url": "${MY_VAR}/api"})
        assert res["url"] == "hello/api"

def test_interpolate_empty_var_warns(caplog):
    res = _interpolate_manifest({"url": "${MISSING_VAR}/api"})
    assert res["url"] == "/api"
    assert "Manifest interpolation: ${MISSING_VAR} resolved to empty string" in caplog.text

def test_interpolate_recurses_nested_dict():
    with patch.dict(os.environ, {"V1": "a", "V2": "b"}):
        res = _interpolate_manifest({"n1": {"n2": ["${V1}", "${V2}"]}})
        assert res["n1"]["n2"] == ["a", "b"]

def test_parse_tier_int_passthrough():
    assert parse_tier(Tier.PRO) == Tier.PRO

def test_parse_tier_known_string():
    assert parse_tier("pro") == Tier.PRO

def test_parse_tier_case_insensitive():
    assert parse_tier("PRO") == Tier.PRO

def test_parse_tier_unknown_defaults_to_lite():
    assert parse_tier("ultimate") == Tier.LITE


@pytest.mark.asyncio
async def test_rediscover_reseeds_already_loaded_runtime_state(tmp_path):
    """Second discover clears globals but must reseed skipped live plugins."""
    clear_plugin_skills()
    clear_plugin_scopes()
    _relay_manifests.clear()

    spec = PluginSpec(
        package="plugins.demo_live",
        name="demo_live",
        tier=1,
        requires=(),
        required_credentials=(),
        manifest_path=Path("plugins/demo_live/manifest.json"),
        manifest={
            "name": "demo_live",
            "version": "1.0.0",
            "scopes": [{"token": "plugin:demo_live", "description": "demo"}],
            "skills": [],
        },
        skills="skill body for demo",
        skills_map={},
    )
    plan = ResolvedPlan(order=[spec], skipped=[])

    cfg = tmp_path / "plugin_config.json"
    cfg.write_text(json.dumps({"plugins": ["demo_live"], "tier": "lite"}), encoding="utf-8")

    registry = MagicMock()
    registry.system_tier = 1
    registry.config = {}
    registry.lifecycle._plugin_id_map = {"demo_live": object()}
    registry.elevate_tier = MagicMock()

    db = AsyncMock()
    db.get_inactive_ids = AsyncMock(return_value=set())
    db.get_skills = AsyncMock(return_value={})
    db.get_scopes = AsyncMock(
        return_value=([{"token": "plugin:demo_live", "description": "from-db"}], "hash1")
    )

    with patch(
        "core.plugin_loader.lifecycle_manager._DB_AVAILABLE", True
    ), patch(
        "db_layer.plugin_registry_store.DBPluginRegistry", return_value=db
    ), patch(
        "core.plugin_loader.resolver.resolve", return_value=plan
    ), patch(
        "core.plugin_loader.lifecycle_manager.PluginLifecycleManager.load_one_spec",
        new_callable=AsyncMock,
    ) as load_one:
        await discover_and_load_plugins_async(registry, config_path=str(cfg))

    load_one.assert_not_called()
    assert _relay_manifests.get("demo_live") is not None
    assert get_plugin_skills("demo_live") == "skill body for demo"
    tokens = [s.get("token") for s in get_plugin_scopes("demo_live")]
    assert "plugin:demo_live" in tokens

    clear_plugin_skills()
    clear_plugin_scopes()
    _relay_manifests.clear()


def test_plugin_spec_has_content_hash_field():
    spec = PluginSpec(
        package="plugins.demo",
        name="demo",
        tier=1,
        requires=(),
        required_credentials=(),
        manifest_path=Path("plugins/demo/manifest.json"),
        manifest={},
        content_hash="sha256:abc",
    )
    assert spec.content_hash == "sha256:abc"
    # default empty
    bare = PluginSpec(
        package="plugins.demo",
        name="demo",
        tier=1,
        requires=(),
        required_credentials=(),
        manifest_path=Path("plugins/demo/manifest.json"),
        manifest={},
    )
    assert bare.content_hash == ""


def test_build_specs_dedupe_package_and_name(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    pkg_dir = tmp_path / "plugins" / "alpha"
    pkg_dir.mkdir(parents=True)
    (pkg_dir / "manifest.json").write_text(
        json.dumps({"name": "alpha_plugin", "version": "1.0.0", "tier": "free"}),
        encoding="utf-8",
    )
    # second package path claiming same manifest name
    pkg_dir2 = tmp_path / "plugins" / "alpha_dup"
    pkg_dir2.mkdir(parents=True)
    (pkg_dir2 / "manifest.json").write_text(
        json.dumps({"name": "alpha_plugin", "version": "1.0.0", "tier": "free"}),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "core.plugin_loader.resolver.PROJECT_ROOT",
        tmp_path,
    )
    packages = [
        "plugins.alpha",
        "plugins.alpha",  # duplicate package entry
        "plugins.alpha_dup",  # duplicate name
    ]
    specs, skipped = build_specs(packages)
    assert len(specs) == 1
    assert specs[0].name == "alpha_plugin"
    assert specs[0].content_hash.startswith("sha256:")
    reasons = " ".join(r for _, r in skipped)
    assert "duplicate package entry" in reasons
    assert "duplicate manifest name" in reasons
