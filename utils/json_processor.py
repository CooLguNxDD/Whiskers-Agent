"""JSON extraction and transformation utilities."""

from __future__ import annotations

from typing import Any

import requests


def extract_lists(node: Any) -> list[list[dict[str, Any]]]:
    """Recursively hunt for all lists of records (dicts) buried in a nested structure.

    Returns a list of all found lists, sorted by length (largest first).
    """
    found: list[list[dict[str, Any]]] = []
    if isinstance(node, list) and node and isinstance(node[0], dict):
        found.append(node)
    elif isinstance(node, dict):
        for k, v in node.items():
            if k != "_meta":  # Ignore standard metadata wrappers
                found.extend(extract_lists(v))
    return sorted(found, key=len, reverse=True)

def resolve_array_string(value: Any, joiner: str = "\n") -> str:
    """Resolve a config value that could be a string or a list of strings into a single string."""
    if isinstance(value, list):
        return joiner.join(map(str, value))
    return str(value) if value is not None else ""

def flatten_dict(d: dict[str, Any], parent_key: str = "", sep: str = "_") -> dict[str, Any]:
    """Flatten nested dictionaries into a single level using dot/underscore notation.

    List values are kept as-is (not recursed into, not dropped) — a caller that
    already separated out list-of-record collections (see extract_lists) only
    hands this function scalar-ish leftovers, and a list-of-scalars field
    (tags, skills, keywords, ...) is a legitimate leaf value to preserve.
    """
    flat: dict[str, Any] = {}
    for k, v in d.items():
        new_key = f"{parent_key}{sep}{k}" if parent_key else k
        if isinstance(v, dict):
            flat.update(flatten_dict(v, new_key, sep=sep))
        else:
            flat[new_key] = v
    return flat


def extract_by_keys(
    data: dict[str, Any],
    keys: tuple[str, ...] | list[str],
) -> str | None:
    """Extract a value from a dict by searching a list of candidate keys in order.

    If the value is a list of dicts with a "message" field, returns the first message.
    Handles nested error structures gracefully.

    Args:
        data: The dict to search
        keys: Ordered list of keys to try

    Returns:
        A string value if found, else None
    """
    for field in keys:
        val = data.get(field)
        if val:
            if isinstance(val, list) and val:
                first = val[0]
                if isinstance(first, dict) and "message" in first:
                    return first["message"]
                return str(first)
            return str(val)
    return None

def safe_json_response(r: requests.Response) -> dict | list:
    """Safely parse JSON response body regardless of Content-Type header.

    - Empty body → returns []  (empty list is the correct semantic for "no results")
    - Valid JSON array → returns as-is
    - Valid JSON object → returns as-is
    - Unparseable body → returns {} (don't raise — let safe_api_call stay clean)
    """
    text = (r.text or "").strip()

    if not text:
        return []  # ← was returning {}, but empty body = empty collection

    try:
        result = r.json()
    except Exception:
        # requests.exceptions.JSONDecodeError (subclass of RequestException) would
        # otherwise be caught upstream as request_failed — swallow it here instead
        print(f"Warning: failed to parse JSON response (status={r.status_code}): {text[:200]}") 
        return {}

    if isinstance(result, (dict, list)):
        return result

    return {}


def safe_text_response(r: requests.Response) -> str:
    """Return the raw response body as a string (for non-JSON endpoints like file downloads)."""
    return r.text or ""
