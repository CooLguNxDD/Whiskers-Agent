"""Agent design-spec layout composition.

Extracted from ``composer.py`` so custom/preset layout assembly is greppable
without behavior change. Imported by the ``composer`` façade after builders
are defined so module-level imports of composer helpers are safe.

Patch-compatibility: runtime lookups for ``SETTINGS`` / ``list_projects`` /
``rank_projects_by_query`` go through the ``composer`` module namespace so
existing unit tests that patch ``composer.*`` keep working.
"""

from __future__ import annotations

from datetime import datetime, timezone

from plugins.portfolio_plugin.compose.dag import stamp_dag_from_blocks as _stamp_dag_from_blocks
from plugins.portfolio_plugin.schema.ui_layout_schema import validate_layout


def _merge_design_spec(spec: dict | None, preset_name: str = "") -> dict:
    """Merge layout preset (if any) under agent spec overrides.

    Unknown presets are ignored gracefully. ``preset`` key is stripped from the
    result so it never reaches section walk / validation as a layout field.
    """
    # Resolve via composer so patch("…composer.SETTINGS") still works.
    from plugins.portfolio_plugin.compose import composer as _composer

    SETTINGS = _composer.SETTINGS

    raw = dict(spec or {}) if isinstance(spec, dict) else {}
    candidate = preset_name if preset_name else raw.get("preset")
    name = candidate.strip() if isinstance(candidate, str) else ""

    base: dict = {}
    presets = SETTINGS.get("layout_presets", {}) or {}
    if name and isinstance(presets, dict):
        preset = presets.get(name)
        if isinstance(preset, dict):
            base = dict(preset)
        # unknown preset → ignore (no error); agent can still pass full spec

    # Patch-only keys must never become compose fields.
    _STRIP_KEYS = frozenset({"preset", "base_layout", "patch_mode", "patch_from_short_id"})
    merged = dict(base)
    for key, value in raw.items():
        if key in _STRIP_KEYS:
            continue
        if value is not None:
            merged[key] = value
    return merged


async def compose_custom_layout(
    spec: dict,
    tenant_id: int = 1,
    preset: str = "",
    *,
    soft_sections: bool = False,
) -> tuple[dict | None, list[str]]:
    """Compose a layout from an agent design spec (named sections + literal blocks).

    Named string sections reuse the same builders as ``compose_layout`` (DB projects /
    STAR vectors only — no fabricated project data). Dict entries are treated as
    literal blocks and validated by ``validate_layout``.

    When ``spec["refresh"]`` is true, projects with ``context_sources`` are
    fail-open live-enriched before builders run (no DB write).

    When ``soft_sections`` is True, per-section errors drop the bad section and
    stash messages on ``meta["_sectionErrors"]`` instead of aborting the whole
    compose (used by incremental patch). Default False keeps the existing
    hard-fail 2-tuple contract for scoped compose / design_layout full paths.

    Returns ``(layout, [])`` on success or ``(None, errors)`` with path-ish messages
    so the GOAP agent can self-correct.
    """
    # Local import: composer is the façade; helpers live there and are defined
    # before this module is loaded from composer's end-of-file re-export.
    # Attribute access via the module object keeps unittest.patch targets stable.
    from plugins.portfolio_plugin.compose import composer as _composer

    COMPOSED_SECTION_NAMES = _composer.COMPOSED_SECTION_NAMES
    get_audience_template = _composer.get_audience_template
    list_projects = _composer.list_projects
    rank_projects_by_query = _composer.rank_projects_by_query
    _live_enrich = _composer._live_enrich
    _compose_named_section = _composer._compose_named_section
    _is_incomplete_composed_block = _composer._is_incomplete_composed_block
    _is_incomplete_arch_diagram = _composer._is_incomplete_arch_diagram
    _enrich_arch_diagram = _composer._enrich_arch_diagram

    if not isinstance(spec, dict):
        return None, ["spec: must be a JSON object"]

    merged = _merge_design_spec(spec, preset_name=preset or "")
    errors: list[str] = []

    audience_raw = merged.get("audience") or "default"
    audience, template = get_audience_template(str(audience_raw))

    max_projects = merged.get("max_projects")
    if max_projects is None:
        max_projects = template.get("max_projects", 6)
    try:
        max_projects = int(max_projects)
    except (TypeError, ValueError):
        return None, ["max_projects: must be an integer"]

    star_query = merged.get("star_query")
    if star_query is None or star_query == "":
        star_query = template.get("star_query")
    star_k = merged.get("star_k")
    if star_k is None or star_k == "":
        star_k = template.get("star_k")
    try:
        if star_k is not None:
            star_k = int(star_k)
    except (TypeError, ValueError):
        return None, ["star_k: must be an integer"]

    theme = merged.get("theme")
    if theme is not None and theme != "":
        if not isinstance(theme, str):
            return None, ["theme: must be a string"]
        theme = theme.strip() or None
    else:
        theme = None

    refresh = bool(merged.get("refresh"))

    sections = merged.get("sections")
    if sections is None:
        # Omitted → audience template (preset / simple redesign path).
        sections = list(template.get("sections", []))
    elif isinstance(sections, list) and len(sections) == 0:
        # Explicit empty list is a planner collapse anti-pattern (false-ok layout).
        return None, [
            "sections: empty list is not allowed — pass named sections, "
            "block dicts from build_layout_block, omit sections for the audience "
            "template, or use a preset"
        ]
    if not isinstance(sections, list):
        return None, ["sections: must be a list of section names and/or block objects"]

    proj_audience = None if audience == "default" else audience
    projects = await list_projects(audience=proj_audience, tenant_id=tenant_id)
    rank_q = str(star_query or template.get("star_query") or audience)
    projects = await rank_projects_by_query(
        projects, rank_q, tenant_id=tenant_id, top_k=max(12, int(max_projects) * 2)
    )
    projects = projects[:max_projects]
    if refresh:
        projects = await _live_enrich(projects, tenant_id=tenant_id)

    # portfolio_star retired — starStory only when plan supplies authored props.
    stories: list = []
    _ = star_k

    blocks: list = []
    cited_sources: list[str] = []
    seen_refs: set[str] = set()

    def _collect_refs(sec_dict: dict) -> None:
        refs = sec_dict.pop("_sourceRefs", None)
        if isinstance(refs, list):
            for r in refs:
                r_str = str(r).strip()
                if r_str and r_str not in seen_refs:
                    seen_refs.add(r_str)
                    cited_sources.append(r_str)

    for i, sec in enumerate(sections):
        if isinstance(sec, str):
            name = sec.strip()
            if name in COMPOSED_SECTION_NAMES:
                blocks.extend(
                    _compose_named_section(name, projects=projects, stories=stories)
                )
            else:
                msg = (
                    f"sections[{i}]: unknown composed section '{name}' "
                    f"(allowed: {sorted(COMPOSED_SECTION_NAMES)})"
                )
                if soft_sections:
                    errors.append(msg)
                else:
                    errors.append(msg)
        elif isinstance(sec, dict):
            sec = dict(sec)
            _collect_refs(sec)
            block_type = sec.get("type")
            # Agent often wraps composed sections as objects with empty props
            # (e.g. {"type":"projectGrid","props":{}}). Rehydrate from DB.
            if _is_incomplete_composed_block(sec):
                name = str(block_type)
                block_id = sec.get("id") if isinstance(sec.get("id"), str) else None
                blocks.extend(
                    _compose_named_section(
                        name,
                        projects=projects,
                        stories=stories,
                        block_id=block_id,
                    )
                )
            elif _is_incomplete_arch_diagram(sec):
                # Thin arch stubs (title-only / query-only) get a mermaid fill-in
                # (or a generated SVG motif when props.kind == "svg" + props.motif).
                blocks.append(_enrich_arch_diagram(sec, projects, theme=str(theme or "")))
            else:
                # Literal custom block (prose / complete archDiagram / codeSnippet / …).
                blocks.append(sec)
        else:
            errors.append(
                f"sections[{i}]: must be a composed section name (string) or a block object"
            )

    # Hard fail only when soft_sections is off (legacy contract).
    if errors and not soft_sections:
        return None, errors

    # Soft path with zero usable blocks → still fail so callers can self-correct.
    if soft_sections and not blocks and errors:
        return None, errors

    generated_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    meta: dict = {"audience": audience, "generatedAt": generated_at}
    if theme:
        meta["theme"] = theme
    if cited_sources:
        meta["sources"] = [{"ref": r} for r in cited_sources]
    # Optional GenUI signals from agentic / scoped compose (passthrough).
    for key in ("mode", "curationLabel", "scopedProjectCount"):
        val = merged.get(key)
        if val is not None and val != "":
            meta[key] = val
    # Agent may pass meta.dag / accent; else stamp from blocks.
    if isinstance(merged.get("dag"), dict):
        meta["dag"] = merged["dag"]
    if isinstance(merged.get("accent"), str) and merged["accent"].strip():
        meta["accent"] = merged["accent"].strip()
    # Keep soft errors aside — validate_layout may strip private meta keys.
    soft_section_errors = list(errors) if soft_sections and errors else []

    # Dump composed Pydantic blocks to plain dicts for uniform validation.
    raw_blocks: list = []
    for b in blocks:
        if hasattr(b, "model_dump"):
            raw_blocks.append(b.model_dump(mode="json", exclude_none=True))
        else:
            raw_blocks.append(b)

    if "dag" not in meta:
        dag = _stamp_dag_from_blocks(raw_blocks)
        if dag:
            meta["dag"] = dag

    candidate = {"version": 1, "meta": meta, "blocks": raw_blocks}
    layout, verrs = validate_layout(candidate)
    if layout is not None and soft_section_errors:
        m = dict(layout.get("meta") or {})
        m["_sectionErrors"] = soft_section_errors
        layout = {**layout, "meta": m}
    return layout, verrs
