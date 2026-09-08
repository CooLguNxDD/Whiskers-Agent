import asyncio
import importlib
import json
import logging
import pkgutil
import traceback
from pathlib import Path

from core.config_loader import load_plugin_config
from core.context import _DB_AVAILABLE
from core.plugin_loader import resolver
from core.plugin_loader.dependency_resolver import PluginDependencyResolver
from core.plugin_loader.discovery_service import PluginDiscoveryService
from core.plugin_loader.plugin_registry import PluginRegistry
from core.plugin_loader.scope_registry import clear as clear_plugin_scopes
from core.plugin_loader.scope_registry import mark_plugin_scopes_synthetic
from core.plugin_loader.scope_registry import set_plugin_scopes
from core.plugin_loader.skill_registry import clear as clear_plugin_skills
from core.plugin_loader.skill_registry import set_plugin_poll_specs, set_plugin_skills

logger = logging.getLogger("whiskers.plugins")

# Shared relay manifests dict (populated during plugin discovery)
_relay_manifests: dict[str, dict] = {}


class PluginLifecycleManager:
    """Manages the lifecycle of plugins: initial load, registration, and hot-reloading."""

    @staticmethod
    async def db_upsert_and_validate(plugin_id: str, manifest: dict, db_registry, vault) -> None:
        """Upsert plugin manifest to DB and validate required credentials."""
        await db_registry.register(manifest)
        await db_registry.validate_credentials_present(plugin_id, vault)

    @staticmethod
    async def run_plugin_migrations(spec) -> None:
        """Apply plugin-local schema migrations when ``schema_cfg`` is present.

        No-op when schema_cfg is None or migrations_dir is empty. Raises
        ``PluginMigrationError`` (fail-closed) on gate/apply failure.
        Uses PluginSchemaMigrator's own engine (no db_registry needed).
        """
        schema_cfg = getattr(spec, "schema_cfg", None)
        if not schema_cfg or not isinstance(schema_cfg, dict):
            return
        migrations_dir = schema_cfg.get("migrations_dir")
        if not migrations_dir:
            return
        from pathlib import Path as _Path
        from db_layer.plugin_schema_migrator import PluginSchemaMigrator

        migrator = PluginSchemaMigrator()
        version = (getattr(spec, "manifest", None) or {}).get("version", "0.0.0")
        await migrator.migrate_plugin(
            spec.name,
            version,
            _Path(migrations_dir),
            min_core_revision=schema_cfg.get("min_core_revision"),
            auto_migrate=schema_cfg.get("auto_migrate", True),
        )

    @staticmethod
    async def set_migration_error_meta(plugin_id: str, db_registry, message: str) -> None:
        """Stamp ``meta.migration_error`` (non-fatal on DB errors)."""
        if not db_registry or not plugin_id:
            return
        try:
            from db_layer.plugin_registry_store import PluginMetaKey

            record = await db_registry.get(plugin_id)
            if record is None:
                return
            meta = dict(record.meta or {})
            meta[PluginMetaKey.MIGRATION_ERROR.value] = message
            await db_registry.update_plugin_meta(plugin_id, meta)
        except Exception as exc:
            logger.warning(
                "Failed to set migration_error meta for %s: %s", plugin_id, exc
            )

    @staticmethod
    async def clear_migration_error_meta(plugin_id: str, db_registry) -> None:
        """Clear ``meta.migration_error`` after a successful migrate (non-fatal)."""
        if not db_registry or not plugin_id:
            return
        try:
            from db_layer.plugin_registry_store import PluginMetaKey

            record = await db_registry.get(plugin_id)
            if record is None:
                return
            meta = dict(record.meta or {})
            if PluginMetaKey.MIGRATION_ERROR.value not in meta:
                return
            meta.pop(PluginMetaKey.MIGRATION_ERROR.value, None)
            await db_registry.update_plugin_meta(plugin_id, meta)
        except Exception as exc:
            logger.warning(
                "Failed to clear migration_error meta for %s: %s", plugin_id, exc
            )

    @staticmethod
    async def persist_spec_content_hash(spec, db_registry) -> None:
        """Write PluginSpec.content_hash to ``plugins.content_hash`` (non-fatal).

        Also appends ``meta.version_history`` via ``set_content_hash``. Called
        immediately after DB register so credential-gated skips still persist the
        version signal. No-op when hash empty or row missing.
        """
        if not db_registry or not getattr(spec, "name", None):
            return
        content_hash = getattr(spec, "content_hash", "") or ""
        if not content_hash:
            return
        try:
            existing = await db_registry.get(spec.name)
            stored_hash = ""
            if existing is not None:
                stored_hash = str(getattr(existing, "content_hash", None) or "")
                # Pre-migration / dual-write fallback.
                if not stored_hash and existing.meta:
                    stored_hash = str(existing.meta.get("content_hash") or "")
            if stored_hash == content_hash:
                return
            if stored_hash:
                logger.info(
                    "plugin content changed for '%s' (%s→%s) — recording new version",
                    spec.name,
                    stored_hash[:24],
                    content_hash[:24],
                )
            else:
                logger.info(
                    "plugin content_hash: initial write for '%s' (%s)",
                    spec.name,
                    content_hash[:24],
                )
            await db_registry.set_content_hash(
                spec.name,
                content_hash,
                (getattr(spec, "manifest", None) or {}).get("version", "0.0.0"),
            )
        except Exception as hash_exc:
            logger.warning(
                "DB content_hash persist failed for %s (non-fatal): %s",
                getattr(spec, "name", "?"),
                hash_exc,
            )

    @staticmethod
    async def seed_runtime_from_spec(spec, db_registry, *, prefer_db_scopes: bool = False) -> None:
        """Populate relay manifests, skills, poll_specs, and scopes from a resolved spec.

        Used on full load and when rediscovering plugins already present in the
        lifecycle map (global clear must not leave live plugins with empty in-memory
        registries). When ``prefer_db_scopes`` is True (already-loaded rediscover),
        prefer DB-persisted scope rows so programmatic contribute_scopes survive.
        """
        if not spec.name:
            return
        _relay_manifests[spec.name] = spec.manifest
        try:
            declared = list(spec.manifest.get("skills", []) or [])
            fs_map: dict[str, str] = getattr(spec, "skills_map", {}) or {}
            skill_text = getattr(spec, "skills", "") or ""
            if db_registry:
                try:
                    db_map = await db_registry.get_skills(spec.name) or {}
                    seeded = False
                    for k in declared:
                        if k not in db_map and k in fs_map:
                            await db_registry.set_skill(spec.name, k, fs_map[k])
                            db_map[k] = fs_map[k]
                            seeded = True
                    if seeded:
                        logger.info("Seeded skills from FS to DB for plugin '%s'", spec.name)
                    effective_map = dict(db_map)
                    for k in declared:
                        if k not in effective_map and k in fs_map:
                            effective_map[k] = fs_map[k]
                    if effective_map:
                        skill_text = "\n\n".join(v for v in effective_map.values() if v)
                except Exception as db_exc:
                    logger.warning(
                        "DB skills lookup/seed failed for %s (using FS): %s",
                        spec.name,
                        db_exc,
                    )
            set_plugin_skills(spec.name, skill_text)
            poll_specs = spec.manifest.get("poll_specs") or []
            if poll_specs:
                set_plugin_poll_specs(spec.name, poll_specs)

            scopes_seeded = False
            if prefer_db_scopes and db_registry:
                try:
                    db_scopes, _ = await db_registry.get_scopes(spec.name)
                    if db_scopes:
                        set_plugin_scopes(spec.name, db_scopes)
                        scopes_seeded = True
                except Exception as db_exc:
                    logger.warning(
                        "DB scopes reseed failed for %s (using manifest): %s",
                        spec.name,
                        db_exc,
                    )
            if not scopes_seeded:
                scopes = spec.manifest.get("scopes") or []
                if scopes:
                    # In-memory declarative seed only; DB fingerprint persist
                    # runs after package.register so programmatic contribute_scopes
                    # entries are included (hot-swap resync).
                    set_plugin_scopes(spec.name, scopes)
                else:
                    # No manifest "scopes" block and (at this point in boot)
                    # no contribute_scopes call yet either — without a floor
                    # the plugin is silently admin-only forever, since
                    # `plugin:<id>` is never in any non-admin vocabulary.
                    # Synthesize the same shape a declared plugin would carry
                    # so it is at least reachable, and flag it loudly rather
                    # than let it boot silent (`run_boot_scope_health` reports
                    # it). Never fails boot.
                    logger.warning(
                        "Plugin '%s' has no manifest 'scopes' block — "
                        "synthesizing a fallback floor (plugin:%s, "
                        "group:%s:read, group:%s:write). Declare real scopes "
                        "in the manifest to silence this.",
                        spec.name, spec.name, spec.name, spec.name,
                    )
                    set_plugin_scopes(
                        spec.name,
                        [
                            {"token": f"plugin:{spec.name}", "description": "Synthesized fallback (no manifest scopes declared)"},
                            {"token": f"group:{spec.name}:read", "description": "Synthesized fallback read"},
                            {"token": f"group:{spec.name}:write", "description": "Synthesized fallback write"},
                        ],
                    )
                    mark_plugin_scopes_synthetic(spec.name)

            # Declarative memory namespaces → core MemoryRegistry
            memory_entries = spec.manifest.get("memory") or []
            if memory_entries:
                try:
                    from core.memory import get_memory_registry

                    get_memory_registry().register_entries(
                        spec.name, memory_entries, replace=True
                    )
                except Exception as mem_exc:
                    logger.warning(
                        "Memory namespace seed failed for %s: %s",
                        spec.name,
                        mem_exc,
                    )

            # Declarative specialist flows: settings.specialist_agent.flow_specs
            # is a list of paths relative to the plugin dir (manifest_path.parent).
            # See core_graph.subgraphs.specialist.flow_spec / flow_registry.
            settings = spec.manifest.get("settings") or {}
            sa = settings.get("specialist_agent") if isinstance(settings, dict) else None
            flow_spec_paths = (sa or {}).get("flow_specs") or [] if isinstance(sa, dict) else []
            if flow_spec_paths:
                PluginLifecycleManager._load_flow_specs(spec, flow_spec_paths)

            # Manifest-declared specialist effort default + per-goal_class
            # overrides (§8.3 of model-role-specs). Fail-closed: an unknown
            # effort level is logged and the plugin's effort config is simply
            # not registered (falls through to core defaults), never silently
            # coerced — must not abort plugin load.
            PluginLifecycleManager._load_specialist_effort(spec, sa)

            # Plugin-contributed ModelRoleSpecs: settings.model_roles is a list
            # of paths relative to the plugin dir, same shape/guards as flow_specs.
            model_role_paths = settings.get("model_roles") or [] if isinstance(settings, dict) else []
            if model_role_paths:
                PluginLifecycleManager._load_model_roles(spec, model_role_paths)
        except Exception as exc:
            logger.debug("Skills/poll_specs/scopes load skipped for %s: %s", spec.name, exc)

        # Own try/except at warning level (not folded into the block above's
        # broad debug-level except) — that handler previously swallowed a
        # LifecycleManager NameError for a year; a gate load failure must be
        # loud, not silently absorbed alongside routine skills/scopes noise.
        try:
            gate_data = spec.manifest.get("gate")
            if isinstance(gate_data, dict):
                PluginLifecycleManager._load_plugin_gate(spec, gate_data)
        except Exception as exc:
            logger.warning("Plugin gate load failed for %s: %s", spec.name, exc)

    @staticmethod
    def _load_flow_specs(spec, flow_spec_paths: list) -> None:
        """Resolve + register manifest-declared flow spec JSON files.

        Paths are relative to the plugin directory (``manifest_path.parent``);
        ``..`` segments and absolute paths are rejected so a plugin cannot
        declare a flow spec outside its own tree. Never raises — a bad flow
        spec is logged and skipped, it must not fail plugin load.
        """
        from core_graph.subgraphs.specialist.flow_registry import register_flow
        from core_graph.subgraphs.specialist.flow_spec import FlowSpecError, parse_flow_spec

        plugin_dir = Path(spec.manifest_path).parent
        for rel in flow_spec_paths:
            rel_s = str(rel or "").strip()
            if not rel_s:
                continue
            rel_path = Path(rel_s)
            if rel_path.is_absolute() or ".." in rel_path.parts:
                logger.warning(
                    "flow_specs entry rejected for %s: %r is absolute or escapes plugin dir",
                    spec.name, rel_s,
                )
                continue
            fpath = plugin_dir / rel_path
            try:
                resolved = fpath.resolve()
                if plugin_dir.resolve() not in resolved.parents and resolved != plugin_dir.resolve():
                    logger.warning(
                        "flow_specs entry rejected for %s: %r resolves outside plugin dir",
                        spec.name, rel_s,
                    )
                    continue
                data = json.loads(fpath.read_text(encoding="utf-8"))
                flow = parse_flow_spec(data, owner=spec.name)
                register_flow(flow)
            except FlowSpecError as fe:
                logger.warning("flow spec %s invalid for %s: %s", rel_s, spec.name, fe)
            except Exception as exc:
                logger.warning("flow spec %s load failed for %s: %s", rel_s, spec.name, exc)

    @staticmethod
    def _load_specialist_effort(spec, sa: dict | None) -> None:
        """Validate + register manifest.settings.specialist_agent.effort(_overrides).

        Never raises — an invalid effort config is logged and left
        unregistered (falls through to core defaults) rather than aborting
        plugin load or silently coercing a typo'd level.
        """
        from core_graph.model_roles.manifest_effort import (
            ManifestEffortError,
            parse_specialist_effort,
            register_manifest_effort,
        )

        if not isinstance(sa, dict) or not (sa.get("effort") or sa.get("effort_overrides")):
            return
        try:
            entry = parse_specialist_effort(sa, plugin_id=spec.name)
            register_manifest_effort(entry)
        except ManifestEffortError as me:
            logger.warning("specialist_agent effort config invalid for %s: %s", spec.name, me)
        except Exception as exc:
            logger.warning("specialist_agent effort config load failed for %s: %s", spec.name, exc)

    @staticmethod
    def _load_model_roles(spec, model_role_paths: list) -> None:
        """Resolve + register manifest-declared ModelRoleSpec JSON files.

        Same path-guard shape as ``_load_flow_specs``: paths relative to the
        plugin dir, ``..``/absolute rejected. A plugin registering a
        core-owned role_id is rejected (with a warning) by the registry
        itself, not here — see ``core_graph.model_roles.registry``.
        """
        from core_graph.model_roles.registry import register_model_role
        from core_graph.model_roles.role_spec import ModelRoleSpecError, parse_model_role_bundle

        plugin_dir = Path(spec.manifest_path).parent
        for rel in model_role_paths:
            rel_s = str(rel or "").strip()
            if not rel_s:
                continue
            rel_path = Path(rel_s)
            if rel_path.is_absolute() or ".." in rel_path.parts:
                logger.warning(
                    "model_roles entry rejected for %s: %r is absolute or escapes plugin dir",
                    spec.name, rel_s,
                )
                continue
            fpath = plugin_dir / rel_path
            try:
                resolved = fpath.resolve()
                if plugin_dir.resolve() not in resolved.parents and resolved != plugin_dir.resolve():
                    logger.warning(
                        "model_roles entry rejected for %s: %r resolves outside plugin dir",
                        spec.name, rel_s,
                    )
                    continue
                data = json.loads(fpath.read_text(encoding="utf-8"))
                for role in parse_model_role_bundle(data, owner=spec.name):
                    register_model_role(role)
            except ModelRoleSpecError as re_:
                logger.warning("model role spec %s invalid for %s: %s", rel_s, spec.name, re_)
            except Exception as exc:
                logger.warning("model role spec %s load failed for %s: %s", rel_s, spec.name, exc)

    @staticmethod
    def _load_plugin_gate(spec, gate_data: dict) -> None:
        """Validate + register manifest.gate (a level-3 ceiling), beside model roles.

        Inline dict (not a path-referenced file like flow_specs/model_roles —
        one plugin has exactly one gate). Never raises to its caller; the
        caller wraps this in its own warning-level try/except.
        """
        from core.scope_management.gates import get_plugin_gate_registry, parse_gate_spec

        gate = parse_gate_spec(spec.name, gate_data, owner=spec.name)
        get_plugin_gate_registry().register_manifest_gate(gate)

    @classmethod
    async def load_one_spec(
        cls,
        registry: PluginRegistry,
        spec,
        *,
        db_registry,
        vault,
        shortname_to_manifest_name: dict,
        manifests_by_shortname: dict,
    ) -> bool:
        """Import, configure, and register a single plugin spec. Returns True on success."""
        from core.context import mcp, mcp_context_builder

        try:
            # Defer importlib until after DB/credential/migration gates so a refused
            # plugin never executes package top-level code or lands in sys.modules.
            if spec.tier > registry.system_tier:
                registry.elevate_tier(spec.tier)
                logger.info("System tier elevated to %s by module %s", spec.tier, spec.package)

            # DB register + content hash must run even when credentials block the load.
            if db_registry and spec.name:
                cred_blocked = False
                if vault:
                    try:
                        await cls.db_upsert_and_validate(spec.name, spec.manifest, db_registry, vault)
                    except Exception as exc:
                        from db_layer.plugin_registry_store import PluginLoadError
                        if isinstance(exc, PluginLoadError):
                            logger.error("Plugin '%s' skipped — %s", spec.name, exc)
                            cred_blocked = True
                        else:
                            logger.warning(
                                "DB credential gate failed for '%s' (non-fatal): %s",
                                spec.name, exc,
                            )
                else:
                    try:
                        await db_registry.register(spec.manifest)
                    except Exception as reg_exc:
                        logger.warning(
                            "DB register failed for '%s' (non-fatal): %s",
                            spec.name, reg_exc,
                        )
                # Fail-closed plugin schema migrations (single chokepoint for boot /
                # lazy load / enable / reinit / content-hash reloads).
                # Run even when credentials are missing so DDL lands before vault setup.
                try:
                    await cls.run_plugin_migrations(spec)
                    await cls.clear_migration_error_meta(spec.name, db_registry)
                except Exception as mig_exc:
                    from db_layer.plugin_schema_migrator import PluginMigrationError
                    # Any migration failure (including unexpected) refuses the load.
                    msg = str(mig_exc)
                    if not isinstance(mig_exc, PluginMigrationError):
                        msg = f"schema migration failed: {mig_exc}"
                    logger.error("Plugin '%s' skipped — %s", spec.name, msg)
                    await cls.set_migration_error_meta(spec.name, db_registry, msg)
                    await cls.persist_spec_content_hash(spec, db_registry)
                    return False

                await cls.persist_spec_content_hash(spec, db_registry)
                if cred_blocked:
                    return False

            await cls.seed_runtime_from_spec(spec, db_registry, prefer_db_scopes=False)

            try:
                load_plugin_config(
                    str(spec.manifest_path), spec.manifest,
                    mcp_context_builder=mcp_context_builder, mcp=mcp,
                )
            except Exception as e:
                logger.error("Failed to load config for plugin %s: %s", spec.name, e)

            if spec.name and not spec.manifest.get("external_oauth") and shortname_to_manifest_name:
                requires_raw = spec.manifest.get("requires") or []
                parent_names = [
                    resolver._normalize_plugin_name(r) for r in requires_raw
                    if resolver._normalize_plugin_name(r) in shortname_to_manifest_name
                ]
                if parent_names:
                    _mbs = manifests_by_shortname or {}
                    chosen = next(
                        (p for p in parent_names if _mbs.get(p, {}).get("external_oauth")),
                        None,
                    )
                    if chosen is None:
                        # No declared parent carries external_oauth — fall back to the
                        # first declared `requires` entry. No plugin-id special case here:
                        # core must not hardcode a specific plugin's identity (guardrail).
                        chosen = parent_names[0]
                        logger.warning(
                            "Plugin '%s': no declared parent in %s carries external_oauth; "
                            "falling back to first requires entry '%s'",
                            spec.name, parent_names, chosen,
                        )
                    parent_manifest_name = shortname_to_manifest_name.get(chosen)
                    if parent_manifest_name:
                        try:
                            from core.context import oauth_relay as _relay
                            if _relay is not None:
                                _relay.register_delegate(spec.name, parent_manifest_name)
                        except Exception as exc:
                            logger.debug("register_delegate skipped for %s -> %s: %s", spec.name, parent_manifest_name, exc)
                        registry.auth.set_auth_delegate(spec.name, parent_manifest_name)
                        logger.info(
                            "Plugin '%s' auth delegates to '%s'",
                            spec.name, parent_manifest_name,
                        )

            package = importlib.import_module(spec.package)

            if not hasattr(package, "register"):
                return False

            try:
                package.register(registry)
                logger.info("Registered plugin module %s successfully.", spec.package)
            except Exception as e:
                logger.error("Failed to register %s: %s\n%s", spec.package, e, traceback.format_exc())
                return False

            if hasattr(package, "__path__"):
                for _, submodule_name, _ in pkgutil.iter_modules(package.__path__, package.__name__ + "."):
                    try:
                        submodule = importlib.import_module(submodule_name)
                        if hasattr(submodule, "register"):
                            submodule.register(registry)
                            logger.info("Registered plugin submodule %s successfully.", submodule_name)
                    except Exception as e:
                        logger.error("Failed to load plugin submodule %s: %s", submodule_name, e)

            # Persist scope fingerprint after register so programmatic contribute_scopes
            # (scopes.contribute event during on_load/register) is captured alongside
            # declarative manifest scopes.
            if db_registry:
                try:
                    from core.scope_management.registration import get_permission_registry
                    reg = get_permission_registry()
                    fp = reg.fingerprint(spec.name)
                    _, stored_hash = await db_registry.get_scopes(spec.name)
                    if fp != stored_hash:
                        if stored_hash:
                            logger.info(
                                "Scope registry hash changed for '%s' — resyncing persisted scopes",
                                spec.name,
                            )
                        await db_registry.set_scopes(
                            spec.name,
                            [e.to_dict() for e in reg.get_plugin_permissions(spec.name)],
                            fp,
                        )
                except Exception as db_exc:
                    logger.warning("DB scopes persist failed for %s (non-fatal): %s", spec.name, db_exc)

            return True

        except ImportError:
            return False
        except Exception as e:
            logger.error("Unexpected error loading package %s: %s", spec.package, e)
            return False

    @classmethod
    async def discover_and_load_plugins_async(
        cls,
        registry: PluginRegistry,
        config_path: str | None = None,
    ):
        """
        Dynamically discover and load plugins from known packages configured in config/plugin_config.json.
        Parses manifest.json, interpolates ${VAR} references, handles tier elevation,
        upserts plugin records to DB, and validates required credentials.
        """
        from core.context import vault
        from utils.config_registry import PLUGIN_CONFIG_PATH

        if config_path is None:
            config_path = PLUGIN_CONFIG_PATH

        # Reset the relay-manifest cache so removed plugins don't linger across reloads
        _relay_manifests.clear()
        clear_plugin_skills()  # reset skills in parallel
        clear_plugin_scopes()

        # Resolve optional DB-backed services
        _vault = None
        _db_registry = None
        if _DB_AVAILABLE:
            try:
                from db_layer.plugin_registry_store import DBPluginRegistry
                _vault = vault
                _db_registry = DBPluginRegistry()
            except Exception as exc:
                try:
                    from db_layer.plugin_registry_store import PluginLoadError as _PLE
                except Exception:
                    _PLE = RuntimeError
                raise _PLE(
                    f"DATABASE_URL is set but DB plugin services (VaultService / DBPluginRegistry) "
                    f"could not be initialised — DBPluginRegistry.validate_credentials_present() "
                    f"would be skipped, aborting discovery. Cause: {exc}"
                ) from exc

        packages = PluginDiscoveryService.load_config(registry, config_path)
        if not packages:
            return

        # Per-plugin enablement gate: skip any plugin persisted as disabled
        # (plugins.is_active = false) so it is never imported/registered/initialized.
        excluded: set[str] = set()
        if _db_registry is not None:
            try:
                excluded = await _db_registry.get_inactive_ids()
                if excluded:
                    logger.info("Excluding disabled plugins from load: %s", ", ".join(sorted(excluded)))
            except Exception as exc:
                logger.warning("Could not read disabled-plugin set (loading all): %s", exc)

        # Resolve the plan using the pure resolver
        plan = PluginDependencyResolver.resolve_plan(packages, registry, excluded=excluded)
        if not plan:
            return

        # Log skipped plugins
        for pkg, reason in plan.skipped:
            logger.warning("Skipping plugin package '%s' — %s", pkg, reason)

        # Reconstruct the expected mappings for auth delegation and configuration
        shortname_to_manifest_name = {
            resolver._normalize_plugin_name(spec.name): spec.name
            for spec in plan.order
        }
        manifests_by_shortname = {
            resolver._normalize_plugin_name(spec.name): spec.manifest
            for spec in plan.order
        }

        # Load each plugin in sorted order.
        # Boot-only dedupe: skip specs already present in the lifecycle map.
        # Never apply this skip in load_single_plugin (would break hot-swap).
        # After the global clear above, reseed runtime registries for skipped
        # plugins so skills/scopes/relay are not left permanently empty.
        for spec in plan.order:
            try:
                norm = resolver._normalize_plugin_name(spec.name)
                if norm in registry.lifecycle._plugin_id_map:
                    logger.warning(
                        "Skipping duplicate plugin load for '%s' — already loaded this boot",
                        spec.name,
                    )
                    await cls.seed_runtime_from_spec(
                        spec, _db_registry, prefer_db_scopes=True
                    )
                    continue
                await cls.load_one_spec(
                    registry,
                    spec,
                    db_registry=_db_registry,
                    vault=_vault,
                    shortname_to_manifest_name=shortname_to_manifest_name,
                    manifests_by_shortname=manifests_by_shortname,
                )
            except Exception as exc:
                logger.error("Failed to load plugin '%s': %s", spec.name, exc, exc_info=True)

        # Push the final resolved manifests into any live relay instance
        try:
            from core.context import oauth_relay as _relay
            if _relay is not None:
                _relay.update_manifests(_relay_manifests)
        except (ImportError, ModuleNotFoundError, AttributeError):
            pass
        except Exception:
            logger.exception("Failed to update relay manifests (relay wiring bug)")
            raise

    @classmethod
    async def load_single_plugin(
        cls,
        registry: PluginRegistry,
        plugin_id: str,
        config_path: str | None = None,
    ) -> bool:
        """Lazy-load a single plugin by id, ignoring the persisted is_active=false gate."""
        from utils.config_registry import PLUGIN_CONFIG_PATH

        if config_path is None:
            config_path = PLUGIN_CONFIG_PATH

        target = resolver._normalize_plugin_name(plugin_id)

        _vault = None
        _db_registry = None
        if _DB_AVAILABLE:
            try:
                from core.context import vault
                from db_layer.plugin_registry_store import DBPluginRegistry
                _vault = vault
                _db_registry = DBPluginRegistry()
            except Exception as exc:
                logger.warning("load_single_plugin: DB services unavailable: %s", exc)

        try:
            def _read_config():
                with open(config_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            config = await asyncio.to_thread(_read_config)
            packages = config.get("plugins", [])
            tier = PluginDiscoveryService.parse_tier(config.get("tier", "lite"))
            registry.elevate_tier(tier)
        except Exception as e:
            logger.error("load_single_plugin: failed to read config %s: %s", config_path, e)
            return False

        specs, _skipped = await asyncio.to_thread(resolver.build_specs, packages)
        spec = next(
            (s for s in specs if resolver._normalize_plugin_name(s.name) == target),
            None,
        )
        if spec is None:
            logger.warning("load_single_plugin: no spec found for plugin '%s'", plugin_id)
            return False

        shortname_to_manifest_name = {
            resolver._normalize_plugin_name(s.name): s.name for s in specs
        }
        manifests_by_shortname = {
            resolver._normalize_plugin_name(s.name): s.manifest for s in specs
        }

        try:
            # clear prior skills/scopes/flows for this plugin on hot/lazy reload (hot-swap correctness)
            clear_plugin_skills(target)
            clear_plugin_scopes(target)
            try:
                from core_graph.subgraphs.specialist.flow_registry import unregister_owner
                unregister_owner(target)
            except Exception as flow_exc:
                logger.debug("flow unregister skipped for %s: %s", target, flow_exc)
            try:
                from core_graph.model_roles.manifest_effort import unregister_manifest_effort
                from core_graph.model_roles.registry import unregister_model_role_owner
                unregister_manifest_effort(target)
                unregister_model_role_owner(target)
            except Exception as role_exc:
                logger.debug("model role unregister skipped for %s: %s", target, role_exc)
            ok = await cls.load_one_spec(
                registry,
                spec,
                db_registry=_db_registry,
                vault=_vault,
                shortname_to_manifest_name=shortname_to_manifest_name,
                manifests_by_shortname=manifests_by_shortname,
            )
            if not ok:
                return False

            try:
                from core.context import oauth_relay as _relay
                if _relay is not None:
                    _relay.update_manifests(_relay_manifests)
            except Exception as exc:
                logger.debug("update_manifests skipped after loading %s: %s", target, exc)

            return target in registry.lifecycle._plugin_id_map
        except Exception as exc:
            logger.error("load_single_plugin('%s') failed: %s", plugin_id, exc)
            return False
