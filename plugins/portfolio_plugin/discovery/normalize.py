"""Normalize raw discovery payloads into ContextDoc + guardrails.

Guardrails:
- GitHub owner allowlist (static + hero links + token login)
- Directive-shaped content stripped before embed
- Quality bar (description/readme, not archived/fork, recent push)
- max_items_per_source cap
"""

from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from plugins.portfolio_plugin.discovery.period import parse_period
from plugins.portfolio_plugin.discovery.slug import canonical_slug
from utils.short_id import slugify

logger = logging.getLogger("whiskers.plugins.portfolio.discovery")

# Fenced blocks that look like agent/system instructions
_DIRECTIVE_FENCE_RE = re.compile(
    r"```(?:markdown|md|text|prompt|system|instructions)?\s*\n"
    r"(?:.*(?:you are|system prompt|ignore previous|follow these instructions|"
    r"as an ai|act as|your role is).*)\n```",
    re.IGNORECASE | re.DOTALL,
)
_DIRECTIVE_LINE_RE = re.compile(
    r"^\s*(?:system\s*:|assistant\s*:|ignore (?:all |previous )?instructions|"
    r"you are (?:an? |the )?(?:ai|assistant|llm))\b.*$",
    re.IGNORECASE | re.MULTILINE,
)


@dataclass
class ContextDoc:
    """Normalized, guard-filtered discovery document ready for indexing."""

    source: str
    ref: str
    kind: str
    title: str
    text: str
    url: str | None = None
    updated_at: str | None = None
    slug_hint: str | None = None
    tags: list[str] = field(default_factory=list)
    # Project timeline signal — parsed from a Notion "Period Covered" line
    # (discovery/period.py) or derived from GitHub repo activity dates.
    # period_source in {"notion_period", "github"}; reconcile.py prefers
    # notion_period over github when a slug has both.
    started_on: str | None = None
    ended_on: str | None = None
    period_source: str | None = None

    def to_metadata(self) -> dict[str, Any]:
        """Metadata for search_content_vectors (excludes full text)."""
        d = asdict(self)
        d.pop("text", None)
        return d

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def strip_directives(text: str) -> str:
    """Remove directive-shaped content before embedding."""
    if not text:
        return ""
    cleaned = _DIRECTIVE_FENCE_RE.sub(" ", text)
    cleaned = _DIRECTIVE_LINE_RE.sub(" ", cleaned)
    # Collapse whitespace
    cleaned = re.sub(r"[ \t]+\n", "\n", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()



def chunk_text(text: str, max_chars: int = 10_000) -> list[str]:
    """Split long text into ~max_chars chunks for embedding."""
    body = (text or "").strip()
    if not body:
        return []
    step = max(1000, int(max_chars))
    return [body[i : i + step] for i in range(0, len(body), step)]

def _parse_iso(dt: str | None) -> datetime | None:
    if not dt or not isinstance(dt, str):
        return None
    try:
        raw = dt.replace("Z", "+00:00")
        return datetime.fromisoformat(raw)
    except Exception:
        return None


def _months_ago(dt: datetime, months: int) -> bool:
    """True if dt is older than ``months`` months from now (UTC)."""
    now = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    # Approximate months as 30 days
    age_days = (now - dt).total_seconds() / 86400.0
    return age_days > (months * 30)


def _slug_from_repo(full_name: str) -> str:
    name = full_name.split("/")[-1] if "/" in full_name else full_name
    return canonical_slug(name, max_words=8, max_len=48)


def _slug_from_title(title: str) -> str:
    return canonical_slug(title or "page", max_words=6, max_len=48)


def _iter_github_items(raw: Any) -> list[dict[str, Any]]:
    """Extract repo-like dicts from heterogeneous proxy/local shapes."""
    if raw is None:
        return []
    # Proxy CSV/text fallthrough — cannot parse repos
    if isinstance(raw, str):
        return []
    if isinstance(raw, dict):
        if raw.get("status") in ("error", "not_configured"):
            return []
        # local list_owned_repos
        if isinstance(raw.get("repos"), list):
            return [r for r in raw["repos"] if isinstance(r, dict)]
        # GitHub search: { total_count, items: [...] }
        if isinstance(raw.get("items"), list):
            return [r for r in raw["items"] if isinstance(r, dict)]
        # common list envelopes
        for key in ("results", "data", "repositories", "nodes", "content"):
            val = raw.get(key)
            if isinstance(val, list):
                return [r for r in val if isinstance(r, dict)]
            if isinstance(val, dict):
                # nested data.repositories / data.items etc.
                for k2 in ("items", "results", "repositories", "nodes", "repos"):
                    if isinstance(val.get(k2), list):
                        return [r for r in val[k2] if isinstance(r, dict)]
        # discovery envelope leftover
        if isinstance(raw.get("_meta"), dict) and "data" in raw:
            return _iter_github_items(raw.get("data"))
        # single repo object
        if raw.get("full_name") or (raw.get("name") and raw.get("owner")):
            return [raw]
        return []
    if isinstance(raw, list):
        return [r for r in raw if isinstance(r, dict)]
    return []


def _iter_notion_items(raw: Any) -> list[dict[str, Any]]:
    """Extract page/search hits from Notion MCP proxy payloads.

    Handles official REST search, MCP ``notion-search``
    (``{results:[{id,title,url,highlight}], type: workspace_search}``), and
    nested ``{_meta,data}`` envelopes.
    """
    if raw is None:
        return []
    if isinstance(raw, dict):
        if raw.get("status") in ("error", "not_configured"):
            return []
        # Unwrap status-less discovery envelopes
        if "status" not in raw and "_meta" in raw and "data" in raw:
            return _iter_notion_items(raw.get("data"))
        for key in ("results", "items", "data", "pages"):
            val = raw.get(key)
            if isinstance(val, list):
                return [r for r in val if isinstance(r, dict)]
            if isinstance(val, dict) and key == "data":
                nested = _iter_notion_items(val)
                if nested:
                    return nested
        if raw.get("id") or raw.get("url"):
            return [raw]
        return []
    if isinstance(raw, list):
        return [r for r in raw if isinstance(r, dict)]
    return []


def _notion_title(item: dict[str, Any]) -> str:
    # Notion search result shape
    props = item.get("properties") if isinstance(item.get("properties"), dict) else {}
    for _k, v in props.items():
        if not isinstance(v, dict):
            continue
        if v.get("type") == "title":
            title_arr = v.get("title") or []
            parts = []
            for t in title_arr:
                if isinstance(t, dict):
                    parts.append(t.get("plain_text") or "")
            if parts:
                return "".join(parts).strip()
    for key in ("title", "name", "Title"):
        val = item.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
        # MCP title sometimes arrives as rich-text list
        if isinstance(val, list):
            parts = []
            for t in val:
                if isinstance(t, dict):
                    parts.append(t.get("plain_text") or t.get("text") or "")
                elif isinstance(t, str):
                    parts.append(t)
            joined = "".join(str(p) for p in parts).strip()
            if joined:
                return joined
    return str(item.get("id") or "notion-page")[:80]


async def _allowed_owners() -> set[str]:
    try:
        from plugins.portfolio_plugin.MCPTools.context_tools import _allowed_github_owners

        owners = await _allowed_github_owners()
        return {o.lower() for o in owners}
    except Exception as exc:
        logger.debug("allowed owners resolve failed: %s", exc)
        return set()


async def _is_allowed_repo(ref: str) -> bool:
    """Broad fetch allowlist (owners ∪ token login) — used by live fetch tools."""
    try:
        from plugins.portfolio_plugin.MCPTools.context_tools import _is_allowed_repo as _check

        return await _check(ref)
    except Exception:
        return False


def discovery_allowlist_refs() -> set[str]:
    """Explicit repo refs for bulk discovery (lowercased).

    Includes ``github_allowlist.repos`` and hero ``owner/repo`` links only —
    **not** every repo under an allowlisted owner. That owner-wide rule is for
    ``fetch_external_context`` / agent fetch, not inventoring.
    """
    try:
        from plugins.portfolio_plugin.MCPTools.context_tools import (
            _static_github_allowlist_detailed,
        )

        _owners, repos_l, _original = _static_github_allowlist_detailed()
        return set(repos_l)
    except Exception as exc:
        logger.debug("discovery allowlist refs failed: %s", exc)
        return set()


async def is_discovery_allowed_ref(ref: str, *, strict: bool = True) -> bool:
    """Whether a GitHub ref may enter the discovery index / reconcile plan."""
    if not ref or "/" not in ref:
        return False
    if strict:
        allowed = discovery_allowlist_refs()
        if not allowed:
            # No explicit repos configured — fall back to broad allowlist so we
            # do not hard-lock an empty inventory.
            return await _is_allowed_repo(ref)
        return ref.lower() in allowed
    return await _is_allowed_repo(ref)


def _github_ref(item: dict[str, Any]) -> str | None:
    full = item.get("full_name") or item.get("fullName") or item.get("repository")
    if isinstance(full, str) and "/" in full:
        return full.strip()
    owner = item.get("owner")
    name = item.get("name")
    if isinstance(owner, dict):
        owner = owner.get("login")
    if isinstance(owner, str) and isinstance(name, str) and owner and name:
        return f"{owner}/{name}"
    return None


def normalize_github_item(
    item: dict[str, Any],
    *,
    max_age_months: int = 24,
    allowed_owners: set[str] | None = None,
) -> ContextDoc | None:
    """Normalize one GitHub repo item; return None if filtered out."""
    ref = _github_ref(item)
    if not ref:
        return None
    owner = ref.split("/", 1)[0].lower()
    if allowed_owners is not None and owner not in allowed_owners:
        # Exact allowlist miss — still allow if quality later path uses _is_allowed_repo
        # (caller may pass pre-filtered set including token login)
        return None

    if item.get("archived") is True or item.get("isArchived") is True:
        return None
    if item.get("fork") is True or item.get("isFork") is True:
        return None

    pushed = item.get("pushed_at") or item.get("pushedAt") or item.get("updated_at")
    pushed_dt = _parse_iso(str(pushed) if pushed else None)
    if pushed_dt and _months_ago(pushed_dt, max_age_months):
        return None

    description = item.get("description") or ""
    if not isinstance(description, str):
        description = str(description or "")
    readme = item.get("readme") or item.get("content") or ""
    if not isinstance(readme, str):
        readme = ""
    readme = strip_directives(readme)

    topics = item.get("topics") or item.get("topic") or []
    if not isinstance(topics, list):
        topics = []
    tags = [str(t) for t in topics if t]
    lang = item.get("language")
    if isinstance(lang, str) and lang and lang not in tags:
        tags.append(lang)

    title = str(item.get("full_name") or item.get("name") or ref)
    # Allowlisted / owned repos with empty description still index as a title stub
    # so portfolio projects without a GitHub description are not dropped.
    if not description.strip() and not readme.strip():
        description = f"GitHub repository {ref}"

    text_parts = [f"# {title}", description.strip(), readme.strip()]
    text = strip_directives("\n\n".join(p for p in text_parts if p))
    if not text.strip():
        return None

    url = item.get("html_url") or item.get("url") or f"https://github.com/{ref}"

    # Timeline fallback for repos without a Notion "Period Covered" line:
    # created_at -> started, pushed_at -> ended only when the repo has gone
    # quiet (>6mo stale) — an actively pushed repo reads as ongoing work.
    created = item.get("created_at") or item.get("createdAt")
    created_dt = _parse_iso(str(created) if created else None)
    repo_started = created_dt.date() if created_dt else None
    repo_ended = pushed_dt.date() if pushed_dt and _months_ago(pushed_dt, 6) else None

    return ContextDoc(
        source="github",
        ref=ref,
        kind="github",
        title=title,
        text=text[:50000],
        url=str(url) if url else None,
        updated_at=str(pushed) if pushed else None,
        slug_hint=_slug_from_repo(ref),
        tags=tags,
        started_on=repo_started.isoformat() if repo_started else None,
        ended_on=repo_ended.isoformat() if repo_ended else None,
        period_source="github" if (repo_started or repo_ended) else None,
    )


def notion_allowlist_config() -> dict[str, list[str]]:
    """Return notion_allowlist from plugin settings (databases + page_prefixes)."""
    try:
        from plugins.portfolio_plugin.plugin_config import SETTINGS

        settings = SETTINGS if isinstance(SETTINGS, dict) else {}
    except Exception:
        settings = {}
    raw = settings.get("notion_allowlist") if isinstance(settings.get("notion_allowlist"), dict) else {}
    databases = raw.get("databases") if isinstance(raw.get("databases"), list) else []
    prefixes = raw.get("page_prefixes") if isinstance(raw.get("page_prefixes"), list) else []
    return {
        "databases": [str(x).strip() for x in databases if str(x).strip()],
        "page_prefixes": [str(x).strip().lower() for x in prefixes if str(x).strip()],
    }


def is_notion_allowlisted(
    item: dict[str, Any],
    *,
    allow: dict[str, list[str]] | None = None,
) -> bool:
    """True when item passes notion_allowlist (empty allowlist = allow all)."""
    cfg = allow if allow is not None else notion_allowlist_config()
    databases = cfg.get("databases") or []
    prefixes = cfg.get("page_prefixes") or []
    if not databases and not prefixes:
        # No allowlist configured — do not hard-lock empty Notion inventory.
        return True

    page_id = str(item.get("id") or "").strip().replace("-", "").lower()
    url = str(item.get("url") or item.get("public_url") or "").lower()
    parent = item.get("parent") if isinstance(item.get("parent"), dict) else {}
    parent_db = str(
        parent.get("database_id") or parent.get("databaseId") or ""
    ).replace("-", "").lower()

    if databases:
        db_norm = {str(d).replace("-", "").lower() for d in databases}
        if parent_db and parent_db in db_norm:
            return True
        # Some search hits expose database_id top-level
        top_db = str(item.get("database_id") or item.get("databaseId") or "").replace("-", "").lower()
        if top_db and top_db in db_norm:
            return True

    if prefixes:
        for p in prefixes:
            p_n = p.replace("-", "").lower()
            if p_n and (page_id.startswith(p_n) or p_n in url or page_id == p_n):
                return True
            # URL path prefixes (e.g. notion.so/MyWorkspace/)
            if p and p in url:
                return True

    # Allowlist configured but no match
    return False


def normalize_notion_item(
    item: dict[str, Any],
    *,
    allow: dict[str, list[str]] | None = None,
) -> ContextDoc | None:
    """Normalize one Notion page/search hit."""
    page_id = str(item.get("id") or "").strip()
    if not page_id:
        return None
    if not is_notion_allowlisted(item, allow=allow):
        return None
    title = _notion_title(item)
    url = item.get("url") or item.get("public_url")
    # plain text body if present (MCP search uses ``highlight``)
    body = ""
    for key in ("content", "text", "plain_text", "snippet", "highlight", "markdown"):
        if isinstance(item.get(key), str) and item[key].strip():
            body = item[key]
            break
    body = strip_directives(body)
    text = strip_directives(f"# {title}\n\n{body}".strip())
    if not text.strip():
        return None
    updated = item.get("last_edited_time") or item.get("updated_at")

    # Notion pages are the authoritative timeline source — a "Period
    # Covered" line here beats a GitHub-derived date in reconcile.py.
    started_on, ended_on, matched = parse_period(text)

    return ContextDoc(
        source="notion",
        ref=page_id,
        kind="notion",
        title=title,
        text=text[:50000],
        url=str(url) if url else None,
        updated_at=str(updated) if updated else None,
        slug_hint=_slug_from_title(title),
        tags=["notion"],
        started_on=started_on.isoformat() if started_on else None,
        ended_on=ended_on.isoformat() if ended_on else None,
        period_source="notion_period" if matched else None,
    )


async def normalize_source_results(
    fetched: list[dict[str, Any]],
    *,
    max_items_per_source: int = 50,
    max_age_months: int = 24,
    strict_allowlist_repos: bool = True,
) -> list[ContextDoc]:
    """Normalize all fetched source envelopes into ContextDoc list.

    When ``strict_allowlist_repos`` is True (default), only explicit
    ``github_allowlist.repos`` (+ hero owner/repo links) are kept. Owner-wide
    matching (``owners: [CooLguNxDD]`` + token login) is intentionally **not**
    used for bulk discovery — that was inventoring every owned non-fork repo.
    """
    docs: list[ContextDoc] = []
    seen_refs: set[str] = set()
    strict_refs = discovery_allowlist_refs() if strict_allowlist_repos else set()

    for envelope in fetched:
        if not isinstance(envelope, dict):
            continue
        capability = str(envelope.get("capability") or "")
        raw = envelope.get("raw")
        cap_l = capability.lower()

        treat_as_github = "github" in cap_l or (
            "notion" not in cap_l and bool(_iter_github_items(raw))
        )
        if treat_as_github:
            count = 0
            for item in _iter_github_items(raw):
                if count >= max_items_per_source:
                    break
                ref = _github_ref(item)
                if not ref:
                    continue
                if not await is_discovery_allowed_ref(ref, strict=strict_allowlist_repos):
                    continue
                doc = normalize_github_item(
                    item,
                    max_age_months=max_age_months,
                    allowed_owners=None,  # inclusion already gated
                )
                if doc is None:
                    continue
                key = f"github:{doc.ref.lower()}"
                if key in seen_refs:
                    continue
                seen_refs.add(key)
                docs.append(doc)
                count += 1

        if "notion" in cap_l:
            notion_allow = notion_allowlist_config()
            count = 0
            for item in _iter_notion_items(raw):
                if count >= max_items_per_source:
                    break
                doc = normalize_notion_item(item, allow=notion_allow)
                if doc is None:
                    continue
                key = f"notion:{doc.ref.lower()}"
                if key in seen_refs:
                    continue
                seen_refs.add(key)
                docs.append(doc)
                count += 1

    if strict_allowlist_repos and strict_refs:
        logger.info(
            "discovery normalize: strict allowlist %d ref(s); kept %d github doc(s)",
            len(strict_refs),
            sum(1 for d in docs if d.source == "github"),
        )
    notion_n = sum(1 for d in docs if d.source == "notion")
    if notion_n:
        logger.info("discovery normalize: kept %d notion doc(s)", notion_n)

    return docs
