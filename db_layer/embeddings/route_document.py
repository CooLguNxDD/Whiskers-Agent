"""
Route document builder.

Composes the rich text document embedded and tsvector-indexed for route embeddings.
"""

from __future__ import annotations

DOC_SCHEMA_VERSION = 1


def build_route_document(
    plugin_id: str,
    operation_id: str,
    method: str,
    path: str,
    description: str,
    tags: list[str] | None = None,
    workspace_label: str | None = None,
    param_names: list[str] | None = None,
) -> str:
    """Compose the rich text document embedded + tsvector-indexed for a route.

    Format:
    [plugin: notion · workspace: Team A]
    operation: proxy_notion_teamA__search_pages (search pages)
    POST /pages/search
    <description>
    params: query, filter, page_size
    tags: notion, proxy, workspace:team-a
    """
    parts: list[str] = []

    # 1. Header
    if workspace_label:
        parts.append(f"[plugin: {plugin_id} · workspace: {workspace_label}]")
    else:
        parts.append(f"[plugin: {plugin_id}]")

    # 2. Operation
    verb_target = operation_id.split("__")[-1] if "__" in operation_id else operation_id
    humanized = verb_target.replace("_", " ").strip().lower()
    if humanized:
        parts.append(f"operation: {operation_id} ({humanized})")
    else:
        parts.append(f"operation: {operation_id}")

    # 3. Method & Path
    method_str = (method or "").upper().strip()
    path_str = (path or "").strip()
    if method_str or path_str:
        method_path = f"{method_str} {path_str}".strip()
        parts.append(method_path)

    # 4. Description
    if description:
        parts.append(description)

    # 5. Params
    if param_names:
        parts.append(f"params: {', '.join(str(p) for p in param_names)}")

    # 6. Tags
    if tags:
        parts.append(f"tags: {', '.join(str(t) for t in tags)}")

    return "\n".join(parts)
