---
name: scope-management
description: Unified scope-permission subsystem (ScopeManager). USE FOR: evaluating tool access, API key scopes, OAuth role inheritance, playground REST enforcement, adding scope rules, debugging 403/scope denied. MANDATORY READ before any scope/auth enforcement edit.
---

# Scope Management Skill

**Read this before changing scope/auth enforcement.** All five access paths go through `core.scope_management.evaluate_access` — never re-implement `is_allowed` inline.

## Package layout

```
core/scope_management/
  __init__.py       # public API: evaluate_access, is_allowed (compat), exports
  manager.py        # ScopeManager + get_scope_manager() singleton
  principal.py      # PrincipalKind, ScopeGrant, AccessDecision
  request.py        # AccessRequest value object (plugin_id/operation_id/tags/access/core_domain/...)
  requirements.py   # RequirementProvider chain -> resolve_requirements(AccessRequest)
  grammar.py        # scope token grammar: parse_scope, expand_implied, core-domain hierarchy
  gates.py           # PluginGateSpec (level-3 ceiling) + PluginGateRegistry
  gate_overlay.py    # DB gate override loader (mirrors model_roles/db_overlay.py)
  legacy_map.py      # LEGACY_SCOPE_MAP (terminal:use/host, whiskers -> core:* tokens)
  rules.py          # ordered RuleSpec chain (incl. plugin_gate_ceiling)
  sentinels.py      # SCOPE_ALL/WILDCARD/ADMIN, normalize_scopes_for_storage
  vocabulary.py     # required_scopes_for_route — thin adapter over requirements.py
  roles.py          # scopes_for_role, playground_mcp_scopes, resolve_api_key_scopes, caller_has_scope
  policy.py         # scope_enforcement_enabled + enforce|audit|off modes
  context.py        # set_request_principal / get_request_principal (in-process)
  contracts.py      # C01–C12 ACCESS_PATH_CONTRACTS + C13-C20 GATE_AND_GRAMMAR_CONTRACTS
  health.py         # run_boot_scope_health() boot self-test
  registration.py   # PermissionRegistry — dynamic plugin/proxy scope vocab (source of truth)
  defaults/core_scopes.json  # level-1 core-platform vocabulary, seeded under plugin id "core"
```

**Vocabulary source of truth is now `core/scope_management/registration.py`** (`PermissionRegistry` / `get_permission_registry()`). `core/plugin_loader/scope_registry.py` is a thin back-compat shim delegating to it — do not add state there.

Compat shim: `core/api_key_management/scopes.py` re-exports public helpers. New code should import from `core.scope_management`.

## Permission registration API (dynamic vocab)

Plugins/proxies register requested scope tokens instead of relying on a hardcoded list. Two paths, both anti-squat validated (`plugin:<own-id>` / `group:<own-id>:<tag>` only):

1. **Declarative (manifest)** — `manifest.json: "scopes": [{"token", "description"}]`, seeded at plugin load (`plugin_loader.py::_load_one_spec`, `replace=True`).
2. **Programmatic (runtime)** — `await ctx.contribute_scopes([...])` from `on_load`, emits `scopes.contribute` → `PluginRegistry._on_scopes_contribute` → `register_plugin_permissions(plugin_id, entries, replace=False)` (merges with manifest entries).

Proxies register via `core/proxy/proxy_manager.py::_register_proxy_scopes` (`plugin:proxy_<name>` + `group:proxy_<name>:proxy`) through the same front door (`core.scope_management.registration.register_plugin_permissions`).

`ScopeManager` exposes thin delegates: `register_plugin_permissions` / `unregister_plugin_permissions` / `.permissions` property — but the registry is a module-level singleton (not owned by the ScopeManager instance) so registrations survive `_set_scope_manager(None)` test resets and don't require boot-order coupling.

`vocabulary.get_valid_scopes()` = static `OAUTH_VALID_SCOPES` floor ∪ `get_permission_registry().all_tokens()`. `GET /api/auth/scope-vocabulary` returns `get_permission_registry().all_permissions()`.

**Persistence + hot-swap resync**: `db_layer/plugin_registry_store.py::get_scopes`/`set_scopes` persist entries + a sha256 fingerprint (`PermissionRegistry.fingerprint(plugin_id)`) under `plugins.meta.scopes` / `meta.scopes_hash`, mirroring the skills pattern. On load, a fingerprint mismatch against the stored hash logs a resync notice and rewrites — this is what surfaces a manifest scope edit across a hot-swap. Boot phase `scope-registry-preseed` (`core/bootstrap.py`, before `scope-health`) reads persisted `meta.scopes` for plugins not yet registered this boot (lazy/boot-excluded ones) so the vocab endpoint is complete pre-load.

**Missing-manifest fallback**: `PluginLifecycleManager` (`core/plugin_loader/lifecycle_manager.py`) synthesizes `plugin:<id>` / `group:<id>:read` / `group:<id>:write` and calls `PermissionRegistry.mark_synthetic(plugin_id)` when a plugin has no manifest `"scopes"` and never called `contribute_scopes` — without this, `_plugin_provider`'s requirement (`{plugin:<id>}`) is a token no non-admin caller can ever hold, so the plugin would be silently admin-only. `run_boot_scope_health()` reports every synthetic plugin id (`scopes_synthetic: ...`) so it stays visible; `test/unit/test_plugin_manifest_scopes.py` fails CI on any `plugins/*/manifest.json` shipping without a real top-level `"scopes"` block, so this floor should never actually be load-bearing in practice.

## Three-level grammar (added, this branch)

```
scope    := sentinel | core | plugin | group | op
sentinel := "all" | "*" | "admin"
core     := "core:" <domain> ":" <access>        # level 1 — core-platform. domain := seg["."seg]
plugin   := "plugin:" <id> [":" <access>]         # level 2 — coarse or read/write-split
group    := "group:" <id> ":" <tag>               # level 2 — unchanged
op       := "op:" <id> ":" <operation_id>          # level 2 — per-operation
access   := "read" | "write"
```

`grammar.py::expand_implied(token)` is the grant-side closure only (never applied to a
required set): `core:<d>:write ⊃ core:<d>:read`; bare `plugin:<id> ⊃ plugin:<id>:write ⊃
plugin:<id>:read`. Cross-domain hierarchy (`core:whiskers:write` covers
`core:whiskers.proxy:read`) and plugin/group/op coverage live in `grant_covers_core` /
`plugin_grant_covers`, wired into `rules.py::_tag_intersection` as a second pass after the
literal+closure check finds nothing — additive only, never narrows.

**Level 3 — plugin gate** (`gates.py::PluginGateSpec`): a manifest `"gate"` block (or a DB
override that **fully replaces** it, never merges) caps what a plugin's own request may
reach: `core` token list, `operations` allow/deny, `endpoints` glob list. Absence of a gate
= no ceiling (today's unconstrained behaviour). Enforced by `rules.py`'s
`plugin_gate_ceiling` rule at position 5 (after `admin_bypass`/`all_wildcard`, before
`deny_anonymous`) — admin and `all`/`*` always bypass it. Seeded example:
`plugins/portfolio_plugin/manifest.json`'s `gate.core: ["core:graph:read"]`.

**`AccessRequest`** (`request.py`) is the typed value object new call sites build
(`plugin_id`, `operation_id`, `tags`, `access: AccessClass`, `core_domain`, `http_path`,
`path`); `requirements.py`'s `RequirementResolver` (same register-pattern as the rule chain)
turns one into the required-token set. `vocabulary.required_scopes_for_route(plugin_id,
tags)` is now a thin adapter over it, byte-parity-tested against the pre-3-level formula.

**Legacy compatibility** (`legacy_map.py`): `core_047_scope_cutover` rewrote every stored
scope column (`api_keys`/`api_key_scope_presets`.scopes JSONB, `oauth_tokens`.scopes /
`oauth_auth_codes`.scopes_granted TEXT space-joined, `mcp_bearer_tokens`.scopes ARRAY) through
`LEGACY_SCOPE_MAP` (`terminal:use → core:terminal:write`, `terminal:host → core:terminal:read`,
`whiskers → core:graph:write`). All terminal relay MCP tools and WS routes are migrated onto
`evaluate_access` (`core:terminal:write` and `core:terminal:read`), with `expand_legacy_alias_for_read`
ensuring backwards compatibility for any legacy callers on the read side.

## Five access paths

| # | Path | PrincipalKind | Effective scopes |
|---|------|---------------|------------------|
| 1 | Admin UI (chat/goap/invoke/mcp-mode) | `SESSION_USER` | `playground_mcp_scopes(role)` |
| 2 | `agent.py` CLI (stdio) | `LOCAL_CLI` | `None` (unrestricted) |
| 3 | OAuth MCP client | `OAUTH_CLIENT` | client-requested ∪ role scopes |
| 4 | API key (`octk_`) | `API_KEY` | `resolve_api_key_scopes(row)` (normalized) |
| 5 | Unauthenticated network | `ANONYMOUS` | `[]` → deny |

## Rule chain (first decisive wins)

1. `enforcement_off` — config/env disable
2. `unrestricted_none` — `scopes is None` only for `LOCAL_CLI` / local stdio
3. `admin_bypass` — `"admin"` in scopes
4. `all_wildcard` — `"all"` or `"*"` in scopes (**Bug A fix**)
5. `plugin_gate_ceiling` — level-3 ceiling on a gated plugin's own request (admin/all above already bypassed; absent gate = no-op)
6. `deny_anonymous` — ANONYMOUS kind → deny (C07); **before** intersection so middleware can emit "authentication required"
7. `empty_required` — authenticated + empty required → **deny** (unresolved plugin, C09)
8. `tag_intersection` — literal `required ∩ scopes`, widened by grammar closure + core-domain/plugin-group-op hierarchy

Default: deny.

## HTTP endpoint enforcement (Stages 3 & 6)

`api/middleware.py::SessionGateMiddleware` runs a scope step after session-cookie validity:
`core.route_registry.http_route_registry.required_scopes_for(path, method)` (exact hit, then a
compiled `{param}`-template regex match, method-sensitive) resolves the bound `required_scopes` for the
concrete request path; non-empty triggers `evaluate_access` against the session's JWT
`scope` claim, **independent of the `AuthPolicy` gate segment** — `scope_required` stays
reserved for new routes, no URL churn. Deny → 403 `{"error": "scope_denied",
"required_scopes": [...]}`. All seven route owner groups (`api.config`, `api.analytics`,
`api.apikeys`, `api.route`, `api.proxy`, `api.admin`, `api.plugins`) are fully bound and
enforced — see `test/unit/test_capability_bindings.py`'s `_BOUND_OWNERS`.

## Sentinels & storage

- UI "All" / null selection → persist `["all"]` via `normalize_scopes_for_storage`
- `create_api_key` / `update_api_key_scopes` always normalize
- Frontend: `useScopeEditor.scopesForPersist()` + `applyPreset("all")` → `["all"]`

## Enforcement modes

Config: `security.scope_enforcement_mode` (`enforce` | `audit` | `off`) + legacy `scope_enforcement_enabled`.

- **enforce** (default): denials apply
- **audit**: denials logged, calls allowed (`reason=audit_allow`)
- **off**: requires **both** config `off` **and** env `SCOPE_ENFORCEMENT_OFF=1` (double-key)

## OAuth role inheritance (Bug B)

- Pending auth stores `ocat_role` / `ocat_user_id` / `ocat_tenant` at connect/complete time
- `_complete_authorization` unions: client scopes ∪ `playground_mcp_scopes(role)` ∪ `extra_scopes`
- JWT mint threads `extra_claims` (role/tenant/user) via `_auth_code_claims` → `_issue_token_pair`
- **Restart-safe**: `create_auth_code` also persists `extra_claims` on `oauth_auth_codes.extra_claims` (migration `core_039`); `load_authorization_code` rehydrates into `_auth_code_claims` when the in-memory dict was lost

## Contract matrix

`test/unit/test_scope_contracts.py` parametrizes `ACCESS_PATH_CONTRACTS` (C01–C12) and
`GATE_AND_GRAMMAR_CONTRACTS` (C13–C20: core-domain closure/hierarchy, plugin gate ceiling,
DB-override full-replace). Boot phase `scope-health` self-tests C01–C05 + admin/all bypass +
one core-scope contract + one gate contract.

## Plugin-facing auth boundary (`core/auth_service.py`)

A plugin resolving an inbound bearer/session-cookie to a principal, or minting
a first-party scoped token, must go through `core.auth_service.get_auth_service()`
(`IAuthService` in `core/interfaces/auth_service.py`) — never
`oauth_provider._svc` (private attribute), `OAuthService._mint_jwt` (private
method), or `db_layer.api_key_store`/`core.api_key_management.*` directly.
`test/unit/test_plugin_core_import_boundary.py` fails CI on any bypass.

- `principal_from_bearer(token) -> Principal | None` — handles both `octk_`
  API keys (`core.api_key_management.store.lookup_active_by_token` +
  `resolve_api_key_scopes`) and Layer-1 JWTs (`OAuthService.validate_token`)
  behind one call. Returns `Principal(subject, scopes, role)` — `role` carries
  the JWT's `ocat_role` claim (None for API-key principals) and must still be
  passed through to `ScopeGrant(role=...)` for admin-bypass evaluation; a
  caller that drops it silently loses admin bypass on the JWT path.
- `principal_from_session_cookie(token) -> str | None` — subject-only, for the
  Operator Console's HttpOnly admin session cookie.
- `mint_scoped_token(*, subject, client_id, scopes, ttl) -> (jwt, jti, expires_at)`
  — absorbs `ensure_internal_client` → `ensure_keypair` → `_mint_jwt`.
- `AuthServiceUnavailable` — raised (not returned as None) when
  `core.context._oauth_svc` is None (OAuth/DB not configured). Callers that
  had an `oauth_provider is None` fallback (e.g. a dev-auth token seam) catch
  this specifically rather than a bare `except Exception`.
- Why this exists rather than "just use `SessionGateMiddleware`": the
  terminal relay's WS routes (`console_ws`/`data_ws`/`extension_ws`/`hosts_ws`,
  `AuthPolicy.NONE`) authenticate the bearer themselves **before**
  `websocket.accept()` (`cat_terminal_relay_plugin/routes/auth.py::authenticate_handshake`)
  — middleware cannot gate a WS handshake pre-accept, so this has to be a
  plain callable, not route middleware.
- Mounted on `PluginContext` as `ctx.auth_service` for lifecycle-hook code
  that has a `ctx`; most call sites (route handlers, `@mcp.tool()`
  functions, the pre-accept WS handshake) don't, and import
  `get_auth_service()` directly instead — same singleton either way.
- Storage has the identical shape: `core.artifact_store.get_artifact_store()`
  (`IArtifactStore`) / `ctx.artifact_store` wraps `minio_client` +
  `db_layer.artifact_link_store` (`put_bytes`/`get_bytes`/`presigned_url`/
  `create_link`/`link_by_short_id`/`short_id_exists`).

## Debugging checklist

1. Resolve caller's scopes (token / session role / API key row / contextvar)
2. Resolve required set: `required_scopes_for_route(plugin_id, tags)`
3. Check bypasses: admin, all/*, enforcement off
4. Check path: middleware (`direct_tool`) vs permission_gate (`graph_step`) vs REST pre-check
5. For OAuth: did pending carry `ocat_role`? Was union applied at complete?
6. For `/tools/invoke`: is `set_request_principal` wrapping the call?
7. For terminal: does the key hold `terminal:use`?

## Related skills

- [oauth-two-layer](../oauth-two-layer/SKILL.md) — Layer 1/2 auth; role claims live on Layer 1 JWTs
- [plugin-system](../plugin-system/SKILL.md) — permission registration (manifest + `contribute_scopes`) on plugin load
