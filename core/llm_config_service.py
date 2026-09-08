"""
Dynamic LLM provider pool (core_019).

A pool of selectable chat + embedding models lives in the ``llm_pool`` table.
Each entry's API token is encrypted at rest with the same pgcrypto master key as
the vault (``db_layer.vault._encrypt`` / ``_decrypt``). The active chat and
embedding selections live in ``server_settings`` under key ``llm_active``.

The dynamic graph (``core_graph/mcp_tool.py``) and the embeddings singleton
(``db_layer/embeddings/embeddings_core.py``) resolve their model from the active
pool entry, falling back to environment variables (the default) when no DB or no
active entry is configured.

Public API
----------
list_pool / add_pool_entry / delete_pool_entry / set_active / get_active
resolve_chat / resolve_embedding / config_version / get_graph_llm
"""

import hashlib
import json
import logging
import os
import time
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from core.llm_provider_management import LLMProvider, get_chat_llm
from db_layer.connection import get_async_session
from db_layer.models import LLMPoolEntry, ServerSetting, ToolConfig
from core.llm.pool_manager import _db_available, _VALID_KINDS, get_active_map, list_pool, add_pool_entry, delete_pool_entry, set_active, set_pool_entry_active, get_active
from core.llm.vault_registry import decrypt_api_key_column, _get_entry_with_token

logger = logging.getLogger("whiskers")

# Embed defaults live on each ProviderSpec under providers/ / cli_providers/.
# Keep thin aliases only for tests / back-compat string lookups.
def _default_embed_tables() -> tuple[dict[str, str], dict[str, int]]:
    from core.llm_provider_management import get_llm_provider_registry
    models: dict[str, str] = {}
    dims: dict[str, int] = {}
    for spec in get_llm_provider_registry().all():
        if spec.default_embed_model:
            models[spec.id] = spec.default_embed_model
        if spec.default_embed_dimensions:
            dims[spec.id] = int(spec.default_embed_dimensions)
    return models, dims


try:
    _DEFAULT_EMBED_MODELS, _DEFAULT_EMBED_DIMENSIONS = _default_embed_tables()
except Exception:
    _DEFAULT_EMBED_MODELS = {
        "openai": "text-embedding-3-small",
        "gemini": "gemini-embedding-001",
        "voyage": "voyage-4",
        "anthropic": "voyage-4",
        "claude-cli": "voyage-4",
    }
    _DEFAULT_EMBED_DIMENSIONS = {
        "openai": 1536,
        "gemini": 1536,
        "voyage": 1024,
        "anthropic": 1024,
        "claude-cli": 1024,
    }



# ---------------------------------------------------------------------------
# Pool CRUD
# ---------------------------------------------------------------------------




# ---------------------------------------------------------------------------
# Active selection (server_settings['llm_active'] = {"chat": id, "embedding": id})
# ---------------------------------------------------------------------------



_ENTRY_CACHE: dict[str, dict] = {}  # entry_id -> {"value": dict | None, "expires_at": float}



# ---------------------------------------------------------------------------
# Resolution — active pool entry, else env-var default
# ---------------------------------------------------------------------------

async def resolve_chat() -> dict:
    """Resolve the chat model config: active pool entry, else highest weight entry from pool, else env default."""
    if _db_available():
        try:
            entry = await get_active("chat")
            if not entry:
                async with get_async_session() as session:
                    stmt = select(
                        LLMPoolEntry.id,
                        LLMPoolEntry.provider,
                        LLMPoolEntry.model,
                        LLMPoolEntry.dimensions,
                        LLMPoolEntry.base_url,
                        LLMPoolEntry.strength,
                        decrypt_api_key_column(LLMPoolEntry.api_key).label("api_key"),
                    ).where(
                        LLMPoolEntry.kind == "chat",
                        LLMPoolEntry.is_active.is_(True)
                    ).order_by(LLMPoolEntry.strength.desc()).limit(1)
                    row = (await session.execute(stmt)).fetchone()
                    if row:
                        entry = {
                            "id": str(row.id),
                            "provider": row.provider,
                            "model": row.model,
                            "dimensions": row.dimensions,
                            "base_url": row.base_url,
                            "strength": row.strength,
                            "api_key": row.api_key,
                        }
        except Exception as exc:  # DB hiccup — fall back to env
            logger.warning("resolve_chat: DB lookup failed, using env default: %s", exc)
            entry = None
        if entry:
            return {
                "provider": entry["provider"],
                "model": entry["model"],
                "api_key": entry.get("api_key"),
                "base_url": entry.get("base_url"),
                "source": "pool",
            }
    return {
        "provider": os.environ.get("LLM_PROVIDER", "openai").lower(),
        "model": os.environ.get("LLM_MODEL", ""),
        "api_key": None,
        "base_url": None,
        "source": "env",
    }


def _embed_defaults(provider: str) -> tuple[str, int]:
    """Return (model, dimensions) defaults from provider modules (ProviderSpec)."""
    from core.llm_provider_management import default_embed_for
    return default_embed_for(provider)


def _env_embedding() -> dict:
    """Return the environment default embedding config."""
    provider = os.environ.get("EMBED_PROVIDER", os.environ.get("LLM_PROVIDER", "openai")).lower()
    default_model, default_dims = _embed_defaults(provider)
    return {
        "provider": provider,
        "model": os.environ.get("EMBED_MODEL", "") or default_model,
        "dimensions": int(os.environ.get("EMBED_DIMENSIONS", "") or default_dims),
        "api_key": os.environ.get("EMBED_API_KEY") or os.environ.get("VOYAGE_API_KEY"),
        "base_url": os.environ.get("EMBED_BASE_URL") or os.environ.get("VOYAGE_BASE_URL"),
        "source": "env",
    }


async def resolve_embedding() -> dict:
    """Resolve the embedding model config: active pool entry, else highest weight entry from pool, else env default."""
    if _db_available():
        try:
            entry = await get_active("embedding")
            if not entry:
                async with get_async_session() as session:
                    stmt = select(
                        LLMPoolEntry.id,
                        LLMPoolEntry.provider,
                        LLMPoolEntry.model,
                        LLMPoolEntry.dimensions,
                        LLMPoolEntry.base_url,
                        LLMPoolEntry.strength,
                        decrypt_api_key_column(LLMPoolEntry.api_key).label("api_key"),
                    ).where(
                        LLMPoolEntry.kind == "embedding",
                        LLMPoolEntry.is_active.is_(True)
                    ).order_by(LLMPoolEntry.strength.desc()).limit(1)
                    row = (await session.execute(stmt)).fetchone()
                    if row:
                        entry = {
                            "id": str(row.id),
                            "provider": row.provider,
                            "model": row.model,
                            "dimensions": row.dimensions,
                            "base_url": row.base_url,
                            "strength": row.strength,
                            "api_key": row.api_key,
                        }
        except Exception as exc:
            logger.warning("resolve_embedding: DB lookup failed, using env default: %s", exc)
            entry = None
        if entry:
            provider = entry["provider"]
            default_model, default_dims = _embed_defaults(provider)
            return {
                "provider": provider,
                "model": entry["model"] or default_model,
                "dimensions": entry.get("dimensions") or default_dims,
                "api_key": entry.get("api_key"),
                "base_url": entry.get("base_url"),
                "source": "pool",
            }
    return _env_embedding()


async def resolve_route_embedding() -> dict:
    """Resolve the active route embedding model config: active route pool entry, else env default."""
    if _db_available():
        try:
            entry = await get_active("route")
            if entry:
                provider = entry["provider"]
                default_model, default_dims = _embed_defaults(provider)
                return {
                    "provider": provider,
                    "model": entry["model"] or default_model,
                    "dimensions": entry.get("dimensions") or default_dims,
                    "api_key": entry.get("api_key"),
                    "base_url": entry.get("base_url"),
                    "source": "pool",
                }
        except Exception as exc:
            logger.warning("resolve_route_embedding: DB lookup failed, using env default: %s", exc)
    return _env_embedding()


# Search tool -> the producer tool whose embedding config it must reuse, so a
# query is embedded with the same model that wrote the vectors. a plugin adding a search/upsert pair
# registers it here.
_PRODUCER_TOOL_MAP: dict[str, str] = {}


async def resolve_tool_embedding(plugin_id: str, tool_name: str) -> dict:
    """Resolve the embedding model config for a specific tool.

    Applies the producer tool mapping if the tool is a search tool.
    Retrieves the selected embedding model ID from tool_config. If configured
    and the pool entry exists, resolves to it; otherwise falls back to env default.
    """
    if "." in plugin_id:
        plugin_id = plugin_id.rsplit(".", 1)[-1]

    effective_tool = _PRODUCER_TOOL_MAP.get(tool_name, tool_name)

    if _db_available():
        try:
            from db_layer.tool_config_store import get_tool_embedding_model
            model_pool_id = await get_tool_embedding_model(plugin_id, effective_tool)
            if model_pool_id:
                async with get_async_session() as session:
                    stmt = select(
                        LLMPoolEntry.id,
                        LLMPoolEntry.provider,
                        LLMPoolEntry.model,
                        LLMPoolEntry.dimensions,
                        LLMPoolEntry.base_url,
                        decrypt_api_key_column(LLMPoolEntry.api_key).label("api_key"),
                    ).where(LLMPoolEntry.id == int(model_pool_id), LLMPoolEntry.kind == "embedding")
                    row = (await session.execute(stmt)).fetchone()
                    if row:
                        provider = row.provider
                        default_model, default_dims = _embed_defaults(provider)
                        return {
                            "provider": provider,
                            "model": row.model or default_model,
                            "dimensions": row.dimensions or default_dims,
                            "api_key": row.api_key,
                            "base_url": row.base_url,
                            "source": "pool",
                        }
        except Exception as exc:
            logger.warning("resolve_tool_embedding for %s/%s failed: %s", plugin_id, effective_tool, exc)

    return _env_embedding()


async def resolve_core_chat() -> dict:
    """Resolve the core chat model config: active pool entry, else highest weight entry from pool, else env default."""
    if _db_available():
        try:
            entry = await get_active("core")
            if not entry:
                async with get_async_session() as session:
                    stmt = select(
                        LLMPoolEntry.id,
                        LLMPoolEntry.provider,
                        LLMPoolEntry.model,
                        LLMPoolEntry.dimensions,
                        LLMPoolEntry.base_url,
                        LLMPoolEntry.strength,
                        decrypt_api_key_column(LLMPoolEntry.api_key).label("api_key"),
                    ).where(
                        LLMPoolEntry.kind == "core",
                        LLMPoolEntry.is_active.is_(True)
                    ).order_by(LLMPoolEntry.strength.desc()).limit(1)
                    row = (await session.execute(stmt)).fetchone()
                    if row:
                        entry = {
                            "id": str(row.id),
                            "provider": row.provider,
                            "model": row.model,
                            "dimensions": row.dimensions,
                            "base_url": row.base_url,
                            "strength": row.strength,
                            "api_key": row.api_key,
                        }
        except Exception as exc:  # DB hiccup — fall back to env
            logger.warning("resolve_core_chat: DB lookup failed, using env default: %s", exc)
            entry = None
        if entry:
            return {
                "provider": entry["provider"],
                "model": entry["model"],
                "api_key": entry.get("api_key"),
                "base_url": entry.get("base_url"),
                "source": "pool",
            }
    return {
        "provider": os.environ.get("LLM_PROVIDER", "openai").lower(),
        "model": os.environ.get("LLM_MODEL", ""),
        "api_key": None,
        "base_url": None,
        "source": "env",
    }


async def get_graph_core_llm():
    """Resolve the active core chat model and return a cached LangChain chat client."""
    sel = await resolve_core_chat()
    try:
        provider = LLMProvider(sel["provider"])
    except ValueError:
        logger.warning("Unknown core provider '%s'; defaulting to openai", sel["provider"])
        provider = LLMProvider.OPENAI
    return get_chat_llm(
        provider,
        sel["model"],
        api_key=sel.get("api_key"),
        base_url=sel.get("base_url"),
    )


async def resolve_step_llm_config(step: dict | None) -> dict | None:
    """Resolve a specific step's LLM configuration dictionary."""
    hint = (step.get("model") or "") if step else ""
    hint = str(hint).strip()
    
    if not hint or not _db_available():
        return None
        
    try:
        async with get_async_session() as session:
            stmt = select(
                LLMPoolEntry.id,
                LLMPoolEntry.name,
                LLMPoolEntry.provider,
                LLMPoolEntry.model,
                LLMPoolEntry.dimensions,
                LLMPoolEntry.base_url,
                LLMPoolEntry.strength,
                LLMPoolEntry.is_active,
                decrypt_api_key_column(LLMPoolEntry.api_key).label("api_key"),
            ).where(LLMPoolEntry.kind == "chat")
            rows = (await session.execute(stmt)).all()
            
            if not rows:
                return None
                
            active_entries = [r for r in rows if r.is_active]
            if not active_entries:
                return None
                
            selected_row = None
            # 1. Try to find exact active match by name
            for r in active_entries:
                if r.name.lower() == hint.lower():
                    selected_row = r
                    break
                    
            if not selected_row:
                # 2. Try to find inactive match by name to get its strength
                target_strength = None
                for r in rows:
                    if not r.is_active and r.name.lower() == hint.lower():
                        target_strength = r.strength
                        break
                
                # 3. Try to parse hint as a float if no strength found
                if target_strength is None:
                    try:
                        target_strength = float(hint)
                    except ValueError:
                        pass
                
                # 4. Find nearest active entry by strength
                if target_strength is not None:
                    selected_row = min(active_entries, key=lambda r: abs((r.strength or 0.0) - target_strength))
                    
            # 5. If still not resolved, use the highest strength active chat entry
            if not selected_row:
                selected_row = max(active_entries, key=lambda r: r.strength or 0.0)
                
            return {
                "provider": selected_row.provider,
                "model": selected_row.model,
                "api_key": selected_row.api_key,
                "base_url": selected_row.base_url,
                "source": "pool",
            }
    except Exception as exc:
        logger.warning("resolve_step_llm_config failed: %s", exc)
        return None


async def resolve_step_llm(step: dict | None) -> Any:
    """Resolve a specific step's LLM based on its model hint."""
    sel = await resolve_step_llm_config(step)
    if not sel:
        return await get_graph_core_llm()
    try:
        provider = LLMProvider(sel["provider"])
    except ValueError:
        logger.warning("Unknown chat provider '%s'; defaulting to openai", sel["provider"])
        provider = LLMProvider.OPENAI
    return get_chat_llm(
        provider,
        sel["model"],
        api_key=sel.get("api_key"),
        base_url=sel.get("base_url"),
    )



async def config_version() -> str:
    """Short hash of the active chat+embedding+route selection and tool configs.

    Used by the embeddings singleton to detect a runtime model switch and rebuild.
    The API token is intentionally excluded from the hash.
    """
    chat = await resolve_chat()
    core = await resolve_core_chat()
    emb = await resolve_embedding()
    route = await resolve_route_embedding()

    tool_configs = {}
    if _db_available():
        try:
            async with get_async_session() as session:
                stmt = select(ToolConfig.plugin_id, ToolConfig.tool_name, ToolConfig.embedding_model)
                rows = (await session.execute(stmt)).all()
                tool_configs = {f"{r.plugin_id}:{r.tool_name}": r.embedding_model for r in rows}
        except Exception as exc:
            logger.debug("config_hash: tool_configs lookup failed, omitting from hash: %s", exc)

    raw = json.dumps(
        {
            "chat": {k: chat.get(k) for k in ("provider", "model", "base_url")},
            "core": {k: core.get(k) for k in ("provider", "model", "base_url")},
            "emb": {k: emb.get(k) for k in ("provider", "model", "dimensions", "base_url")},
            "route": {k: route.get(k) for k in ("provider", "model", "dimensions", "base_url")},
            "tools": tool_configs,
        },
        sort_keys=True,
    )
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


async def get_graph_llm():
    """Resolve the active chat model and return a cached LangChain chat client."""
    sel = await resolve_chat()
    try:
        provider = LLMProvider(sel["provider"])
    except ValueError:
        logger.warning("Unknown chat provider '%s'; defaulting to openai", sel["provider"])
        provider = LLMProvider.OPENAI
    return get_chat_llm(
        provider,
        sel["model"],
        api_key=sel.get("api_key"),
        base_url=sel.get("base_url"),
    )
