"""
Unit tests for manifest-declared plugin skills loading + prompt formatting.

Covers:
- PluginSpec.skills population via build_specs
- Graceful skip on missing skill file (warning, no crash, skills="")
- Size caps (per-file + total) are enforced
- _format_plugin_skills emits block only for candidates whose (short) plugin has skills; "" otherwise
"""

import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from core.plugin_loader.resolver import PluginSpec, build_specs
from core_graph.node.helpers import _format_plugin_skills
from core.plugin_loader.skill_registry import (
    set_plugin_skills,
    get_plugin_skills,
    clear as clear_skills,
    get_all_skills,
)


def test_build_specs_populates_skills_field(tmp_path, caplog):
    """build_specs should attach a (possibly empty) skills str to every valid spec."""
    pkg_dir = tmp_path / "plugins" / "demo_plugin"
    pkg_dir.mkdir(parents=True)
    manifest_path = pkg_dir / "manifest.json"
    manifest_path.write_text('{"name": "demo_plugin", "tier": "free", "skills": []}', encoding="utf-8")

    with patch("core.plugin_loader.resolver.PROJECT_ROOT", tmp_path):
        specs, skipped = build_specs(["plugins.demo_plugin"])

    assert skipped == []
    assert len(specs) == 1
    spec = specs[0]
    assert isinstance(spec, PluginSpec)
    assert spec.name == "demo_plugin"
    assert spec.skills == ""  # no skill files declared with content
    assert hasattr(spec, "skills")


def test_build_specs_loads_and_strips_frontmatter(tmp_path):
    pkg_dir = tmp_path / "plugins" / "skilled"
    skill_dir = pkg_dir / "skills" / "wf"
    skill_dir.mkdir(parents=True)
    manifest_path = pkg_dir / "manifest.json"
    skill_path = skill_dir / "SKILL.md"
    manifest_path.write_text(
        '{"name": "skilled", "tier": "free", "skills": ["skills/wf/SKILL.md"]}',
        encoding="utf-8",
    )
    skill_path.write_text(
        "---\nname: wf\ndescription: test\n---\n\n# Real Content\nUse me for chaining.",
        encoding="utf-8",
    )

    with patch("core.plugin_loader.resolver.PROJECT_ROOT", tmp_path):
        specs, skipped = build_specs(["plugins.skilled"])

    assert skipped == []
    assert specs[0].skills.strip().startswith("# Real Content")
    assert "Use me for chaining" in specs[0].skills


def test_build_specs_skips_missing_skill_file_gracefully(tmp_path, caplog):
    pkg_dir = tmp_path / "plugins" / "broken"
    pkg_dir.mkdir(parents=True)
    manifest_path = pkg_dir / "manifest.json"
    manifest_path.write_text(
        '{"name": "broken", "tier": "free", "skills": ["skills/does/not/exist.md"]}',
        encoding="utf-8",
    )

    with patch("core.plugin_loader.resolver.PROJECT_ROOT", tmp_path):
        with caplog.at_level("WARNING"):
            specs, skipped = build_specs(["plugins.broken"])

    assert skipped == []
    assert specs[0].skills == ""
    assert "Skill file not found" in caplog.text or "does/not/exist" in caplog.text


def test_skills_caps_enforced(tmp_path):
    pkg_dir = tmp_path / "plugins" / "capped"
    skill_dir = pkg_dir / "skills"
    skill_dir.mkdir(parents=True)
    manifest_path = pkg_dir / "manifest.json"
    big_skill = skill_dir / "BIG.md"
    manifest_path.write_text(
        '{"name": "capped", "tier": "free", "skills": ["skills/BIG.md"]}',
        encoding="utf-8",
    )
    # Exceed per-file cap (config-driven MAX_SKILL_FILE_CHARS) to trigger truncation
    from utils.server_config import MAX_SKILL_FILE_CHARS

    big_content = "X" * (MAX_SKILL_FILE_CHARS + 1500)
    big_skill.write_text(big_content, encoding="utf-8")

    with patch("core.plugin_loader.resolver.PROJECT_ROOT", tmp_path):
        specs, _ = build_specs(["plugins.capped"])

    loaded = specs[0].skills
    assert len(loaded) <= MAX_SKILL_FILE_CHARS + 40  # cap + truncation suffix
    assert "truncated" in loaded


def test_format_plugin_skills_only_for_skilled_candidates():
    clear_skills()
    try:
        set_plugin_skills("reporty", "# Report workflow\nStep A then B")
        set_plugin_skills("other", "# Other")

        candidates = [
            {"plugin_id": "reporty", "operation_id": "r1", "score": 0.9},
            {"plugin_id": "proxy_something", "operation_id": "p1", "score": 0.8},  # no skills
            {"plugin_id": "reporty", "operation_id": "r2", "score": 0.7},  # dup
        ]

        block = _format_plugin_skills(candidates)
        assert "Plugin Capabilities / Skills" in block
        assert "reporty" in block
        assert "Report workflow" in block
        assert "proxy_something" not in block
        assert "Other" not in block  # not in candidates
    finally:
        clear_skills()


def test_format_plugin_skills_returns_empty_for_no_match():
    clear_skills()
    try:
        set_plugin_skills("unrelated", "stuff")
        candidates = [{"plugin_id": "foo", "operation_id": "x", "score": 0.1}]
        assert _format_plugin_skills(candidates) == ""
        assert _format_plugin_skills([]) == ""
        assert _format_plugin_skills([{"plugin_id": "foo"}]) == ""
    finally:
        clear_skills()


def test_registry_set_get_clear_roundtrip():
    clear_skills()
    try:
        assert get_plugin_skills("none") == ""
        set_plugin_skills("p1", "hello skills")
        assert get_plugin_skills("p1") == "hello skills"
        set_plugin_skills("p1", "updated")
        assert get_plugin_skills("p1") == "updated"
        clear_skills("p1")
        assert get_plugin_skills("p1") == ""
        set_plugin_skills("p2", "p2s")
        clear_skills()
        assert get_all_skills() == {}
    finally:
        clear_skills()


def test_build_specs_populates_skills_map(tmp_path):
    """build_specs now also provides per-file skills_map for DB seeding + UI."""
    pkg_dir = tmp_path / "plugins" / "skilled2"
    skill_dir = pkg_dir / "skills" / "wf"
    skill_dir.mkdir(parents=True)
    (pkg_dir / "manifest.json").write_text(
        '{"name": "skilled2", "tier": "free", "skills": ["skills/wf/SKILL.md"]}',
        encoding="utf-8",
    )
    (skill_dir / "SKILL.md").write_text("---\n---\nStep one then two.", encoding="utf-8")

    with patch("core.plugin_loader.resolver.PROJECT_ROOT", tmp_path):
        specs, skipped = build_specs(["plugins.skilled2"])

    assert skipped == []
    sp = specs[0]
    assert sp.skills_map
    assert "skills/wf/SKILL.md" in sp.skills_map
    assert "Step one then two" in sp.skills_map["skills/wf/SKILL.md"]
    # joined text also present
    assert "Step one then two" in (sp.skills or "")


def test_load_plugin_skills_map_caps_per_file(tmp_path):
    pkg_dir = tmp_path / "plugins" / "bigskill"
    (pkg_dir).mkdir(parents=True)
    (pkg_dir / "manifest.json").write_text(
        '{"name": "bigskill", "tier": "free", "skills": ["BIG.md"]}',
        encoding="utf-8",
    )
    from utils.server_config import MAX_SKILL_FILE_CHARS
    from core.plugin_loader.resolver import _load_plugin_skills_map

    (pkg_dir / "BIG.md").write_text("X" * (MAX_SKILL_FILE_CHARS + 1500), encoding="utf-8")

    m = _load_plugin_skills_map(pkg_dir / "manifest.json", ["BIG.md"])
    assert "BIG.md" in m
    assert len(m["BIG.md"]) <= MAX_SKILL_FILE_CHARS + 40  # cap + truncation suffix
    assert "truncated" in m["BIG.md"]
