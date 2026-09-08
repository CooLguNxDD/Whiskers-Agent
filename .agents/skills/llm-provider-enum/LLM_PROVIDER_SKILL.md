---
name: llm-provider-enum
description: 'Thread-safe lazy graph initialization + enum-based LLM provider selection in graph_tool.py. USE FOR: multi-threaded singleton patterns, adding new LLM providers, avoiding race conditions in Uvicorn. DO NOT USE FOR: general coding tasks.'
---

# LLM Provider Registry + Thread-Safe Lazy Init

- Prevents concurrent MCP clients from building the expensive LangGraph + LLM instance multiple times
- Supports multiple LLM providers without if/elif chains and without a hardcoded factory dict —
  each provider is a self-contained module that registers itself
- Uses `threading.Lock` (not asyncio) — safe in Uvicorn multi-threaded context

Package: `core/llm_provider_management/` (replaces the old single-file `core/llm_provider.py`,
which no longer exists — hard-replaced, not shimmed).

```
core/llm_provider_management/
  spec.py             # ProviderSpec dataclass — id, factories, env_key, is_available
  registry.py         # LLMProviderRegistry (dict + lock) + get_llm_provider_registry() singleton
  enum_types.py        # LLMProvider enum (back-compat coercion only — NOT the dispatch key)
  factory.py          # get_chat_llm / make_llm / make_embeddings / default_embed_for / _llm_available
  base.py             # shared non-embed helpers only (Vertex env isolation)
  providers/           # openai.py, anthropic.py, gemini.py, gemini_vertex.py, voyage.py
                       #   — each owns its embeddings_factory + defaults (no OpenAI fallback)
  cli_providers/        # claude_cli.py (reuses voyage embeddings), agy_cli.py, grok_cli.py
```

---

## Part 1: `ProviderSpec` + Registry (replaces the enum-keyed factory dict)

```python
# spec.py
@dataclass(frozen=True)
class ProviderSpec:
    id: str                                    # "openai", "claude-cli", ...
    is_cli: bool = False
    env_key: str | None = None                 # None for CLI providers
    default_chat_model: str = ""
    default_embed_model: str | None = None
    default_embed_dimensions: int | None = None
    chat_factory: Callable[[str, str|None, str|None], BaseChatModel] | None = None       # (model, api_key, base_url)
    embeddings_factory: Callable[[str, int, str|None, str|None], Embeddings] | None = None  # None = no native embeddings
    is_available: Callable[[], bool] | None = None
```

```python
# registry.py — mirrors core.scope_management.registration.PermissionRegistry
class LLMProviderRegistry:
    def __init__(self):
        self._lock = threading.Lock()
        self._providers: dict[str, ProviderSpec] = {}

    def register(self, spec): ...
    def get(self, provider_id) -> ProviderSpec | None: ...
    def ids(self) -> set[str]: ...

def get_llm_provider_registry() -> LLMProviderRegistry:
    """Lazy singleton; first call imports providers/ + cli_providers/ so each
    module's register_provider(SPEC) call seeds the dict."""
```

Each provider is one file that builds a `ProviderSpec` and calls `register_provider(spec)`
at import time — e.g. `providers/openai.py`, `cli_providers/claude_cli.py`. `LLMProvider`
(the enum) still exists in `enum_types.py` for callers that want a closed, typed set
(pool validation, `LLMProvider(provider_str)` coercion) — but **dispatch never switches on
the enum**; it always looks the provider up in the registry by `spec.id` (a plain string).
A provider can be registered without ever getting an enum member.

# Vertex isolation (post-review hardening, moved to base.py)
# gemini_vertex.py's chat/embeddings factories use isolated_vertex_env() (patch.dict + RLock)
# + the shared get_gemini_embeddings_cls() to eliminate duplication and prevent cross-key leakage.

---

## Part 2: Dispatch (factory.py) — no if/elif, no hardcoded dict

```python
def make_llm(provider: LLMProvider | str, model: str, api_key=None, base_url=None):
    pid = provider.value if isinstance(provider, LLMProvider) else str(provider)
    spec = get_llm_provider_registry().get(pid)
    if spec is None or spec.chat_factory is None:
        raise ValueError(f"Unknown or chat-incapable LLM provider: '{pid}'")
    return spec.chat_factory(model, api_key, base_url)
```

### Cached accessor — `get_chat_llm(provider, model, api_key=None, base_url=None)`
`make_llm` is the raw factory (always builds a new instance). **Callers should use
`get_chat_llm`**, which caches one instance per `(provider, model, api_key, base_url)` for the
process lifetime via double-checked locking — so the agent CLI and `run_graph` share a single
client (also gives Gemini a stable object for implicit caching). Wire entry points
(`graph_tool.py`, `agent.py`, `llm_config_service.py`) through it; keep `make_llm` internal.

```python
_llm_cache: dict[tuple, object] = {}
_llm_cache_lock = threading.Lock()
_LLM_CACHE_MAX = 32

def get_chat_llm(provider, model, api_key=None, base_url=None):
    key = (provider, model or "", api_key or "", base_url or "")
    cached = _llm_cache.get(key)
    if cached is not None:
        return cached
    with _llm_cache_lock:
        cached = _llm_cache.get(key)
        if cached is None:
            cached = make_llm(provider, model, api_key=api_key, base_url=base_url)
            if len(_llm_cache) >= _LLM_CACHE_MAX:
                _llm_cache.pop(next(iter(_llm_cache)))
            _llm_cache[key] = cached
        return cached
```

Embeddings dispatch (`make_embeddings` / `default_embed_for`) works the same way, keyed by
`spec.id` (a raw string, no enum involved). **Implementation lives in `providers/`**, not
`base.py`. **No silent OpenAI fallback.**

- `providers/openai.py` — OpenAIEmbeddings
- `providers/gemini.py` — Gemini wrapper (`get_gemini_embeddings_cls` shared with vertex)
- `providers/gemini_vertex.py` — same wrapper + `isolated_vertex_env`
- `providers/voyage.py` — Voyage AI client (`make_voyage_embeddings`, defaults `voyage-4`/1024)
- `providers/anthropic.py` / `cli_providers/claude_cli.py` — import Voyage factory from
  `providers.voyage` (Anthropic has no native embed API)
- `agy-cli` / `grok-cli` — `embeddings_factory=None`; set `EMBED_PROVIDER` explicitly

---

## Part 3: Availability Check

```python
def _llm_available() -> bool:
    provider_str = os.environ.get("LLM_PROVIDER", "openai").strip().lower()
    spec = get_llm_provider_registry().get(provider_str)
    if spec is None or spec.is_available is None:
        return False
    return spec.is_available()
```
Each provider owns its own `is_available` check (env-key presence for cloud providers;
soft-pass / `shutil.which` binary check for CLI providers — see `cli_providers/_shared.py`).

---

## Part 4: Thread-Safe Double-Checked Locking

```python
import threading

_compiled_graph = None
_graph_lock = threading.Lock()

def _get_graph():
    global _compiled_graph
    if _compiled_graph is not None:      # fast path — no lock contention
        return _compiled_graph
    with _graph_lock:
        if _compiled_graph is not None:  # re-check after acquiring lock
            return _compiled_graph
        provider_str = os.environ.get("LLM_PROVIDER", "openai").lower()
        model = os.environ.get("LLM_MODEL", "")
        try:
            provider = LLMProvider(provider_str)
        except ValueError:
            raise ValueError(f"Unsupported LLM_PROVIDER='{provider_str}'")
        llm = _make_llm(provider, model)
        _compiled_graph = build_agent_graph(llm, tools, with_confirmation=False)
    return _compiled_graph
```

| Scenario | Behavior |
|---|---|
| Graph already built | Fast path — return immediately, no lock |
| First call | Slow path — acquire lock, build graph |
| Concurrent second call | Blocked on lock → re-check inside → return already-built graph |
| Build raises exception | `_compiled_graph` stays `None`; next call retries |

Without inner re-check: both concurrent callers could see `None`, both acquire lock sequentially, both build (wasted work).

---

## Part 5: Pickable Embedding Models

Embedding models can be picked independently for different parts of the system (e.g. route discovery vs. record data).

- **Global Route Embedding**: Configured via `server_config.json` `route_embedding_model`.
- **Per-Tool Data Embedding**: Can be specified at tool level; defaults to environment `EMBEDDING_MODEL`.
- **Model-Aware Identity**: Different model embeddings coexist in the database under the `model` column. Search queries filter on model identity to ensure dimension parity.

---

## Part 6: Asymmetric Embedding Format

Document texts are formatted using custom asymmetric templates before embedding, ensuring they are searchable by their context.

- **Templates**: Loaded from `embedding_config.json` (e.g., `"title: {title} | text: {text}"`).
- **Operation IDs**: When generating route embeddings, the `operation_id` is passed as the title.
- **Tool Data**: Fixed titles like `"record"` or `"message"` are used for tool-specific data.

---

## Adding a New LLM Provider (1 file, 0 edits elsewhere)

Drop `core/llm_provider_management/providers/aws_bedrock.py` (or `cli_providers/` for a
headless CLI driver):

```python
from core.llm_provider_management.registry import register_provider
from core.llm_provider_management.spec import ProviderSpec
import os

def _chat(model, api_key, base_url):
    from langchain_aws import ChatBedrock
    return ChatBedrock(model_id=model or "anthropic.claude-3-sonnet", temperature=0)

def _is_available():
    return bool(os.environ.get("AWS_ACCESS_KEY_ID", "").strip())

SPEC = ProviderSpec(
    id="aws-bedrock",
    env_key="AWS_ACCESS_KEY_ID",
    default_chat_model="anthropic.claude-3-sonnet",
    chat_factory=_chat,
    embeddings_factory=None,   # or wire a real one
    is_available=_is_available,
)
register_provider(SPEC)
```

Then add one import line to `providers/__init__.py` (or `cli_providers/__init__.py`) so the
module runs at seed time. No changes to `factory.py`, `_llm_available()`, `_get_graph()`,
`run_graph`, or any dispatch site — every consumer already resolves providers by string id
through the registry. Only add an `LLMProvider` enum member if some caller specifically needs
the closed/typed set (pool dropdown validation, etc.) — dispatch doesn't require it.

---

## Anti-Patterns to Avoid

- **if/elif chains on provider string** — register a `ProviderSpec`, dispatch via the registry
- **A second hardcoded factory dict/table** — that's exactly what this registry replaced
  (there used to be two: one for chat, one for embeddings, plus scattered env-key/default-model
  tables in three different files); a new lookup table for provider metadata is a regression
- **No inner re-check inside lock** — double-check is required to prevent duplicate builds
- **Eager imports of all providers** — fails if any package missing; keep imports deferred
  inside each provider's `_chat`/`_embeddings` factory functions
- **Thread locals or instance state in singleton** — defeats the singleton purpose

---

---

## Part 7: Core vs Execution LLM Split & Multi-Model Execution

To prevent newly-added execution models from destabilizing the core agentic graph (confidence gating, triage, summaries, etc.), the core graph backbone runs on a dedicated, stable core pool LLM (kind `"core"`), falling back to the environment defaults.

1. **Dedicated Core Graph LLM**:
   - `resolve_core_chat()` queries the LLM pool for entries with `kind == "core"` and `is_active == True`. If none exist, it falls back to the environment variable defaults.
   - `get_graph_core_llm()` acts as the cached provider for building the orchestrator graph.
2. **Multi-Model Step Execution**:
   - Planner tags each step with a `model` (preset difficulty tier or exact pool entry name).
   - `resolve_step_llm(step)` maps the step's model to an active pool entry, falling back to the core LLM when no specific hint resolves.
   - `builder_node` compiles step instructions using `resolve_step_llm(step)`.
   - The parallel executor resolves each group step's LLM using `resolve_step_llm(step)`.
3. **Active/Inactive Filtering**:
   - All auto-select resolvers (`resolve_chat()`, `resolve_embedding()`, `resolve_core_chat()`) filter LLM pool entries by `is_active = True`.

---

## Dropped
- Purpose/When to use section (self-evident from code)
- "Why This Pattern" subsections (replaced by anti-patterns list)
- Testing section (unit tests trivial; integration test pattern too long for marginal benefit)
- Reference URL links

## Related
- `.claude/skills/model-role-specs/` — `core_graph/model_roles/resolver.py::resolve_role_llm` is the
  per-pipeline-role caller of this provider registry (selector -> hint -> `resolve_step_llm_config` ->
  `get_chat_llm`), one layer above the per-step `resolve_step_llm` described here.
