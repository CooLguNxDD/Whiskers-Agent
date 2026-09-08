"""Skill frontmatter meta — routing + quality floors (replaces recipe_registry).

Reads skills/*.md directly off disk so YAML frontmatter is available (the
shared manifest/DB skill loader strips frontmatter).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

logger = logging.getLogger("whiskers.plugins.portfolio.skill_meta")

_SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"

_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.DOTALL)


@dataclass(frozen=True)
class SkillMeta:
    """Routing + quality metadata from a skill markdown frontmatter."""

    name: str
    description: str = ""
    goal_classes: tuple[str, ...] = ()
    triggers: tuple[str, ...] = ()
    default_theme: str = ""
    quality: dict[str, Any] = field(default_factory=dict)
    target_blocks: int | None = None
    target_types: int | None = None
    path: str = ""

    def to_public(self) -> dict[str, Any]:
        """Serialize skill meta for MCP/API consumers (no filesystem path)."""
        return {
            "name": self.name,
            "id": self.name,
            "description": self.description,
            "goal_classes": list(self.goal_classes),
            "triggers": list(self.triggers),
            "default_theme": self.default_theme,
            "quality": dict(self.quality),
            "target_blocks": self.target_blocks,
            "target_types": self.target_types,
            "skeleton": [],
            "skill": self.name,
            "craft": [],
        }


def _parse_simple_yamlish(raw: str) -> dict[str, Any]:
    """Minimal frontmatter parser (prefers PyYAML when available)."""
    try:
        import yaml

        data = yaml.safe_load(raw)
        return data if isinstance(data, dict) else {}
    except Exception:
        logger.debug("skill_meta.py: swallowed exception", exc_info=True)
    out: dict[str, Any] = {}
    for line in raw.splitlines():
        if ":" not in line or line.strip().startswith("#"):
            continue
        key, _, val = line.partition(":")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if not key:
            continue
        if val.startswith("[") and val.endswith("]"):
            inner = val[1:-1].strip()
            out[key] = (
                [x.strip().strip('"').strip("'") for x in inner.split(",") if x.strip()]
                if inner
                else []
            )
        elif val.lower() in ("true", "false"):
            out[key] = val.lower() == "true"
        elif val.isdigit():
            out[key] = int(val)
        else:
            out[key] = val
    return out


def _parse_skill_file(path: Path) -> SkillMeta | None:
    try:
        text = path.read_text(encoding="utf-8")
    except Exception as exc:
        logger.warning("skill read failed %s: %s", path, exc)
        return None
    data: dict[str, Any] = {}
    m = _FRONTMATTER_RE.match(text)
    if m:
        data = _parse_simple_yamlish(m.group(1))
    name = str(data.get("name") or path.stem).strip()
    if not name:
        return None
    triggers = data.get("triggers") or []
    goal_classes = data.get("goal_classes") or []
    quality = data.get("quality") if isinstance(data.get("quality"), dict) else {}
    tb = data.get("target_blocks")
    tt = data.get("target_types")
    try:
        target_blocks = int(tb) if tb is not None else None
    except (TypeError, ValueError):
        target_blocks = None
    try:
        target_types = int(tt) if tt is not None else None
    except (TypeError, ValueError):
        target_types = None
    return SkillMeta(
        name=name,
        description=str(data.get("description") or ""),
        goal_classes=tuple(str(g).strip() for g in goal_classes if str(g).strip()),
        triggers=tuple(str(t).lower() for t in triggers if str(t).strip()),
        default_theme=str(data.get("default_theme") or ""),
        quality=dict(quality),
        target_blocks=target_blocks,
        target_types=target_types,
        path=str(path),
    )


@lru_cache(maxsize=1)
def list_skill_meta() -> tuple[SkillMeta, ...]:
    """Parse frontmatter from all skills/*.md (cheap; no body load)."""
    if not _SKILLS_DIR.is_dir():
        return ()
    out: list[SkillMeta] = []
    for path in sorted(_SKILLS_DIR.glob("*.md")):
        meta = _parse_skill_file(path)
        if meta:
            out.append(meta)
    return tuple(out)


def clear_skill_meta_cache() -> None:
    """Drop skill meta cache (tests / hot-reload)."""
    list_skill_meta.cache_clear()


def get_skill_meta(name: str) -> SkillMeta | None:
    """Look up a skill by stem name (with or without ``.md``)."""
    stem = (name or "").replace(".md", "").strip()
    if not stem:
        return None
    for m in list_skill_meta():
        if m.name == stem:
            return m
    return None


def match_skill_for_goal(
    goal_class: str | None = None,
    query: str = "",
) -> SkillMeta | None:
    """Pick best skill by trigger keywords + goal_class (recipe_registry scoring)."""
    q = (query or "").lower().strip()
    all_m = list(list_skill_meta())
    candidates = [
        m
        for m in all_m
        if not goal_class or not m.goal_classes or goal_class in m.goal_classes
    ] or all_m

    defaults = {
        "redesign": "layout-plan-authoring",
        "bake_for_job": "layout-plan-authoring",
        "scoped_ask": "live-layout-enrichment",
    }
    prefer = defaults.get(goal_class or "")

    if not q:
        if prefer:
            for m in candidates:
                if m.name == prefer:
                    return m
        for m in candidates:
            if goal_class and goal_class in m.goal_classes:
                return m
        return candidates[0] if candidates else None

    scored: list[tuple[int, SkillMeta]] = []
    for m in candidates:
        if goal_class and m.goal_classes and goal_class not in m.goal_classes:
            continue
        score = 0
        for t in m.triggers:
            if t and t in q:
                score += 1 + min(len(t) // 8, 3)
        if goal_class and goal_class in m.goal_classes:
            score += 2
        if prefer and m.name == prefer:
            score += 20
        if score > 0:
            scored.append((score, m))

    if not scored:
        if prefer:
            for m in candidates:
                if m.name == prefer:
                    return m
        for m in candidates:
            if goal_class and goal_class in m.goal_classes:
                return m
        return candidates[0] if candidates else None

    scored.sort(key=lambda x: (-x[0], x[1].name))
    return scored[0][1]


def load_skill_body(skill_name: str, *, max_chars: int = 8000) -> str:
    """Load skill markdown body (frontmatter stripped), capped once."""
    if not skill_name:
        return ""
    stem = skill_name.replace(".md", "").strip()
    candidates = [
        _SKILLS_DIR / f"{stem}.md",
        _SKILLS_DIR / "layout-plan-authoring.md",
        _SKILLS_DIR / "live-layout-enrichment.md",
    ]
    for fp in candidates:
        if not fp.is_file():
            continue
        try:
            text = fp.read_text(encoding="utf-8")
        except Exception:
            logger.debug("skill_meta.py: continue after exception", exc_info=True)
            continue
        m = _FRONTMATTER_RE.match(text)
        body = text[m.end() :] if m else text
        body = body.strip()
        if len(body) > max_chars:
            return body[: max_chars - 1].rstrip() + "…"
        return body
    return ""
