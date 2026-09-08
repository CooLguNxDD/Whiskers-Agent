"""Context block formatting for the GOAP planning agent."""

from datetime import datetime, timezone

_ROLE_LABELS = {
    "HumanMessage": "user",
    "AIMessage": "assistant",
    "SystemMessage": "system",
}

_DEFAULT_MAX_TURNS = 12
_DEFAULT_MAX_CHARS = 800


def history_from_messages(messages, *, include_internal: bool = False) -> list[dict]:
    """Project checkpointed LangChain messages into role/content dicts for the context block."""
    out: list[dict] = []
    for m in messages or []:
        extra = getattr(m, "additional_kwargs", None) or {}
        if extra.get("internal") and not include_internal:
            continue
        cls = type(m).__name__
        role = _ROLE_LABELS.get(cls, cls.lower())
        content = getattr(m, "content", "")
        if not isinstance(content, str):
            if isinstance(content, list):
                parts = []
                for b in content:
                    if isinstance(b, dict) and b.get("type") == "text":
                        parts.append(str(b.get("text") or ""))
                    elif isinstance(b, str):
                        parts.append(b)
                content = "".join(parts)
            else:
                content = str(content) if content is not None else ""
        out.append({"role": role, "content": content})
    return out


def format_context_block(
    history=None,
    working_memory=None,
    last_summary=None,
    max_turns=_DEFAULT_MAX_TURNS,
    *,
    max_chars=_DEFAULT_MAX_CHARS,
) -> str:
    """Format the conversation context block including history, working memory, and summary."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ (%A)")
    sections = [f"Current time (UTC): {now}"]

    if history:
        lines = ["Conversation so far:"]
        for turn in history[-max_turns:]:
            role = turn.get("role", "")
            content = turn.get("content", "")
            if not isinstance(content, str):
                content = str(content) if content is not None else ""
            if max_chars and len(content) > max_chars:
                content = content[:max_chars] + "…"
            lines.append(f"- {role}: {content}")
        sections.append("\n".join(lines))

    if working_memory:
        items = []
        for k in sorted(working_memory.keys()):
            items.append(f"{k}={working_memory[k]}")
        sections.append(f"Known entities (working memory): {', '.join(items)}")

    if last_summary:
        sections.append(f"Prior result summary: {last_summary}")

    return "\n\n".join(sections)
