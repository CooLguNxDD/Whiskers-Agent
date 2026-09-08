"""
Pure DAG resolver for Whiskers Agent plugins.

Provides cycle detection via Kahn's algorithm, manifest parsing,
tier checking, and transitive reachability filters.
"""
import heapq
import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
import re
from typing import Tuple, List, Set, Dict, Callable

from core.config_loader import PROJECT_ROOT

logger = logging.getLogger("whiskers.plugins")

@dataclass(frozen=True)
class PluginSpec:
    """Specification of a discovered plugin."""
    package: str
    name: str
    tier: int
    requires: Tuple[str, ...]
    required_credentials: Tuple[str, ...]
    manifest_path: Path
    manifest: dict
    skills: str = ""
    skills_map: dict[str, str] = field(default_factory=dict)
    # Non-gating credentials: surfaced in TUI/health but not enforced at load time.
    optional_credentials: Tuple[str, ...] = field(default_factory=tuple)
    # Full source-tree sha256 ("sha256:<hex>"); empty when hashing failed.
    content_hash: str = ""
    # Parsed manifest "schema" block (migrations_path, min_core_revision, auto_migrate,
    # resolved migrations_dir). None when absent and no migrations/ directory.
    schema_cfg: dict | None = None

@dataclass(frozen=True)
class ResolvedPlan:
    """Resolved execution plan for loading plugins."""
    order: Tuple[PluginSpec, ...]
    skipped: Tuple[Tuple[str, str], ...]


_NAME_PIPELINE: List[Callable[[str], str]] = [
    lambda n: n.split("@", 1)[0],      # strip @version specifier (e.g. pkg@>=1.0.0)
    lambda n: n.rsplit(".", 1)[-1],     # strip package prefix (e.g. plugins.core -> core)
]


def _normalize_plugin_name(name: str) -> str:
    """Normalize a plugin identifier to its short name.

    Examples: 'plugins.portfolio_plugin' -> 'portfolio_plugin',
    'portfolio_plugin' -> 'portfolio_plugin'
    """
    if not name:
        return name
    for transform in _NAME_PIPELINE:
        name = transform(name)
    return name


_VAR_RE = re.compile(r"\$\{([^}]+)\}")


def _interpolate_manifest(manifest: dict) -> dict:
    """Recursively replace `${VAR}` references with os.environ values.

    Empty/missing env vars resolve to an empty string.
    """
    def _replace(match):
        var = match.group(1)
        return os.environ.get(var, "")

    def _walk(node):
        if isinstance(node, str):
            return _VAR_RE.sub(_replace, node)
        if isinstance(node, dict):
            return {k: _walk(v) for k, v in node.items()}
        if isinstance(node, list):
            return [_walk(item) for item in node]
        return node

    return _walk(manifest)


from utils.server_config import MAX_SKILL_FILE_CHARS, MAX_SKILL_TOTAL_CHARS


def _strip_yaml_frontmatter(text: str) -> str:
    """Remove a leading YAML frontmatter block delimited by --- ... ---."""
    if not text:
        return ""
    s = text.strip()
    if not s.startswith("---"):
        return text
    # split on the second occurrence of ---
    parts = text.split("---", 2)
    if len(parts) >= 3:
        return parts[2].strip()
    return text


def _load_plugin_skills(manifest_path: Path, skills_entries: list) -> str:
    """Read, strip frontmatter, cap, and concatenate skill files declared in manifest.

    Delegates to map loader + join. Total cap applied on the final joined text.
    """
    skills_map = _load_plugin_skills_map(manifest_path, skills_entries)
    if not skills_map:
        return ""
    parts = list(skills_map.values())
    total = 0
    capped: list[str] = []
    for content in parts:
        if total + len(content) > MAX_SKILL_TOTAL_CHARS:
            remaining = MAX_SKILL_TOTAL_CHARS - total
            if remaining > 0:
                capped.append(content[:remaining].rstrip() + "\n... [skills truncated]")
            break
        capped.append(content)
        total += len(content) + 2
    return "\n\n".join(capped).strip()


def _load_plugin_skills_map(manifest_path: Path, skills_entries: list) -> dict[str, str]:
    """Read, strip frontmatter, cap per-file, return map of relative_path -> content.

    Used for DB seeding of individual skill files and UI management of "skill files".
    Total cap is not applied here (concat cap is handled at join time if needed).
    Missing files warned and skipped (empty entry omitted).
    """
    if not skills_entries:
        return {}
    base = manifest_path.parent
    result: dict[str, str] = {}
    for entry in skills_entries:
        if not isinstance(entry, str) or not entry.strip():
            continue
        rel = entry.strip()
        skill_path = base / rel
        try:
            if not skill_path.exists():
                logger.warning("Skill file not found for plugin manifest %s: %s", manifest_path, rel)
                continue
            with open(skill_path, "r", encoding="utf-8") as f:
                raw = f.read()
            content = _strip_yaml_frontmatter(raw)
            if len(content) > MAX_SKILL_FILE_CHARS:
                content = content[:MAX_SKILL_FILE_CHARS].rstrip() + "\n... [truncated]"
            result[rel] = content
        except Exception as exc:
            logger.warning("Failed to load skill file %s: %s", rel, exc)
    return result


def parse_tier(tier_value) -> int:
    """Normalize a tier value to a plain integer."""
    if isinstance(tier_value, int):
        return tier_value
    if isinstance(tier_value, str):
        val = tier_value.lower().strip()
        if val in ("free", "lite"):
            return 1
        if val == "pro":
            return 100
        if val == "admin":
            return 500
        if val == "test":
            return 9999
    return 1


def read_manifest(package: str) -> dict:
    """Read and interpolate manifest.json for a dotted package path."""
    project_root = PROJECT_ROOT
    manifest_path = project_root / Path(*package.split(".")) / "manifest.json"
    with open(manifest_path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    return _interpolate_manifest(raw)


def parse_schema_cfg(manifest: dict, plugin_dir: Path) -> dict | None:
    """Parse optional manifest schema block; sandbox migrations path."""
    schema = manifest.get("schema") if isinstance(manifest, dict) else None
    default_migrations = plugin_dir / "migrations"
    if schema is None and not default_migrations.is_dir():
        return None
    if schema is None:
        schema = {}
    if not isinstance(schema, dict):
        logger.warning(
            "parse_schema_cfg: schema block must be an object for %s — ignoring",
            plugin_dir,
        )
        return None

    migrations_path = schema.get("migrations_path", "migrations")
    if not isinstance(migrations_path, str) or not migrations_path.strip():
        migrations_path = "migrations"
    migrations_path = migrations_path.strip()

    plugins_root = (PROJECT_ROOT / "plugins").resolve()
    try:
        resolved = (plugin_dir / migrations_path).resolve()
        if not resolved.is_relative_to(plugins_root):
            logger.warning(
                "parse_schema_cfg: migrations_path %r for %s escapes plugins/ — rejected",
                migrations_path,
                plugin_dir,
            )
            return None
    except (ValueError, OSError) as exc:
        logger.warning(
            "parse_schema_cfg: cannot resolve migrations_path %r for %s — %s",
            migrations_path,
            plugin_dir,
            exc,
        )
        return None

    auto_migrate = schema.get("auto_migrate", True)
    if isinstance(auto_migrate, str):
        auto_migrate = auto_migrate.lower().strip() in ("true", "1", "yes", "on")
    elif not isinstance(auto_migrate, bool):
        auto_migrate = bool(auto_migrate)

    min_core = schema.get("min_core_revision")
    if min_core is not None and not isinstance(min_core, str):
        min_core = str(min_core) if min_core else None
    if isinstance(min_core, str) and not min_core.strip():
        min_core = None

    return {
        "migrations_path": migrations_path,
        "migrations_dir": str(resolved),
        "min_core_revision": min_core,
        "auto_migrate": auto_migrate,
    }


def build_specs(packages: List[str]) -> Tuple[List[PluginSpec], List[Tuple[str, str]]]:
    """Build PluginSpec objects for configured packages.

    Returns a tuple of (valid_specs, skipped_packages_with_reasons).
    Dedupes duplicate package entries and duplicate normalized manifest names.
    """
    from core.plugin_loader.content_hash import compute_plugin_tree_hash
    from core.plugin_loader.credentials_loader import normalize_credential_keys

    specs = []
    skipped = []
    project_root = PROJECT_ROOT
    seen_pkgs: Set[str] = set()
    seen_names: Set[str] = set()
    for pkg in packages:
        if pkg in seen_pkgs:
            skipped.append((pkg, "duplicate package entry in plugin_config.json"))
            logger.warning("build_specs: skipping duplicate package entry '%s'", pkg)
            continue
        seen_pkgs.add(pkg)
        try:
            manifest_path = project_root / Path(*pkg.split(".")) / "manifest.json"
            if not manifest_path.exists():
                skipped.append((pkg, f"manifest.json not found at {manifest_path}"))
                continue
            with open(manifest_path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            manifest = _interpolate_manifest(raw)
            tier = parse_tier(manifest.get("tier", "free"))
            requires = tuple(manifest.get("requires", []))
            # Accept string or {key, description} manifest entries.
            required_credentials = tuple(
                normalize_credential_keys(manifest.get("required_credentials", []))
            )
            optional_credentials = tuple(
                normalize_credential_keys(manifest.get("optional_credentials", []))
            )
            name = manifest.get("name", _normalize_plugin_name(pkg))
            norm_name = _normalize_plugin_name(name)
            if norm_name in seen_names:
                reason = f"duplicate manifest name '{name}' (already claimed)"
                skipped.append((pkg, reason))
                logger.warning("build_specs: skipping %s — %s", pkg, reason)
                continue
            seen_names.add(norm_name)

            skills_entries = manifest.get("skills", []) or []
            skills_map = _load_plugin_skills_map(manifest_path, skills_entries)
            skills_text = _load_plugin_skills(manifest_path, skills_entries)  # for backward/compat

            content_hash = ""
            try:
                content_hash = compute_plugin_tree_hash(manifest_path.parent)
            except Exception as hash_exc:
                logger.warning(
                    "build_specs: content hash failed for %s (%s) — continuing without hash",
                    pkg,
                    hash_exc,
                )

            schema_cfg = parse_schema_cfg(manifest, manifest_path.parent)

            spec = PluginSpec(
                package=pkg,
                name=name,
                tier=tier,
                requires=requires,
                required_credentials=required_credentials,
                optional_credentials=optional_credentials,
                manifest_path=manifest_path,
                manifest=manifest,
                skills=skills_text,
                skills_map=skills_map,
                content_hash=content_hash,
                schema_cfg=schema_cfg,
            )
            specs.append(spec)
        except Exception as exc:
            skipped.append((pkg, f"Failed to load manifest: {exc}"))
    return specs, skipped


def build_dag(specs: List[PluginSpec]) -> Dict[str, Set[str]]:
    """Build a directed dependency graph.

    Keys are short plugin names, values are sets of short names of direct dependents.
    """
    dag = { _normalize_plugin_name(s.name): set() for s in specs }
    for s in specs:
        u = _normalize_plugin_name(s.name)
        for dep in s.requires:
            dep_short = _normalize_plugin_name(dep)
            if dep_short in dag:
                dag[dep_short].add(u)
    return dag


def toposort(dag: Dict[str, Set[str]]) -> List[str]:
    """Perform Kahn's algorithm to obtain topological order.

    Raises ValueError with cyclic nodes listed if a cycle is detected.
    """
    all_nodes = set(dag.keys())
    in_degree = { node: 0 for node in all_nodes }
    
    for u, dependents in dag.items():
        for v in dependents:
            if v in in_degree:
                in_degree[v] += 1
                
    # Min-heap for deterministic lexicographic order (O(log V) per push/pop).
    queue = [node for node in all_nodes if in_degree[node] == 0]
    heapq.heapify(queue)
    order = []

    while queue:
        u = heapq.heappop(queue)
        order.append(u)
        for v in dag.get(u, set()):
            if v in in_degree:
                in_degree[v] -= 1
                if in_degree[v] == 0:
                    heapq.heappush(queue, v)

    if len(order) < len(all_nodes):
        leftovers = sorted([node for node in all_nodes if in_degree[node] > 0])
        raise ValueError(f"Circular plugin dependency detected among: {', '.join(leftovers)}")

    return order


def filter_excluded(specs: List[PluginSpec], excluded: Set[str]) -> Tuple[List[PluginSpec], List[Tuple[str, str]]]:
    """Drop specs whose (normalized) name is in ``excluded`` (e.g. disabled plugins).

    Used by the loader to honor the persisted per-plugin ``is_active=false`` flag
    so a disabled plugin is never imported, registered, or initialized.
    """
    ex = { _normalize_plugin_name(e) for e in (excluded or set()) }
    allowed = []
    skipped = []
    for s in specs:
        if _normalize_plugin_name(s.name) in ex:
            skipped.append((s.package, "Disabled (is_active=false)"))
        else:
            allowed.append(s)
    return allowed, skipped


def filter_by_tier(specs: List[PluginSpec], system_tier: int) -> Tuple[List[PluginSpec], List[Tuple[str, str]]]:
    """Filter out plugins whose tier exceeds the system tier."""
    allowed = []
    skipped = []
    for s in specs:
        if s.tier > system_tier:
            skipped.append((s.package, f"Requires tier {s.tier}, system is {system_tier}"))
        else:
            allowed.append(s)
    return allowed, skipped


def reachable_from_roots(specs: List[PluginSpec], roots: Set[str]) -> Tuple[List[PluginSpec], List[Tuple[str, str]]]:
    """Keep only specs that are transitively reachable from the given root names."""
    spec_map = { _normalize_plugin_name(s.name): s for s in specs }
    reachable = set()
    queue = list(roots)
    
    while queue:
        curr = queue.pop(0)
        curr_short = _normalize_plugin_name(curr)
        if curr_short in spec_map and curr_short not in reachable:
            reachable.add(curr_short)
            spec = spec_map[curr_short]
            for dep in spec.requires:
                dep_short = _normalize_plugin_name(dep)
                if dep_short not in reachable:
                    queue.append(dep_short)
                    
    allowed = []
    skipped = []
    for s in specs:
        short = _normalize_plugin_name(s.name)
        if short in reachable:
            allowed.append(s)
        else:
            skipped.append((s.package, "Not reachable from loading roots"))
    return allowed, skipped


def filter_missing_dependencies(specs: List[PluginSpec]) -> Tuple[List[PluginSpec], List[Tuple[str, str]]]:
    """Transitively filter out plugins missing dependencies."""
    current_specs = list(specs)
    skipped = []
    removed_any = True
    
    while removed_any:
        removed_any = False
        available = { _normalize_plugin_name(s.name) for s in current_specs }
        next_specs = []
        for s in current_specs:
            missing = [dep for dep in s.requires if _normalize_plugin_name(dep) not in available]
            if missing:
                skipped.append((s.package, f"Missing dependencies: {', '.join(missing)}"))
                removed_any = True
            else:
                next_specs.append(s)
        current_specs = next_specs
        
    return current_specs, skipped


def resolve(
    packages: List[str],
    system_tier: int,
    roots: Set[str] = None,
    excluded: Set[str] = None,
) -> ResolvedPlan:
    """Resolve the final order of plugins to load.

    ``excluded`` is the set of plugin names persisted as disabled
    (``plugins.is_active = false``); those specs are dropped before tier and
    reachability filters so a disabled plugin is never loaded.
    """
    specs, skipped_specs = build_specs(packages)
    specs, skipped_disabled = filter_excluded(specs, excluded)
    specs, skipped_tier = filter_by_tier(specs, system_tier)

    skipped_reachability = []
    if roots is not None:
        specs, skipped_reachability = reachable_from_roots(specs, roots)

    specs, skipped_deps = filter_missing_dependencies(specs)

    all_skipped = []
    all_skipped.extend(skipped_specs)
    all_skipped.extend(skipped_disabled)
    all_skipped.extend(skipped_tier)
    all_skipped.extend(skipped_reachability)
    all_skipped.extend(skipped_deps)
    
    dag = build_dag(specs)
    sorted_shorts = toposort(dag)
    
    spec_map = { _normalize_plugin_name(s.name): s for s in specs }
    ordered_specs = [spec_map[short] for short in sorted_shorts]
    
    return ResolvedPlan(
        order=tuple(ordered_specs),
        skipped=tuple(all_skipped)
    )
