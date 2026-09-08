#!/usr/bin/env python3
"""
openapi_to_mcp.py
=================
Step 4 of the OpenAPI pipeline.

Converts openapi.json → mcp-tools.json.

For each path/operation:
  - Uses pydantic.create_model() to build a dynamic model from the
    requestBody JSON Schema (POST/PUT/PATCH) + path/query parameters.
  - model.model_json_schema() produces a ready-made MCP inputSchema.
  - Outputs mcp-tools.json: an array of universal MCP tool definitions.

No AI tokens consumed — pure Pydantic.

Requirements
------------
    pip install pydantic

Usage
-----
    python openapi/openapi_to_mcp.py [--input openapi/openapi.json] [--out openapi/mcp-tools.json]
    python openapi/openapi_to_mcp.py --print   # dump to stdout
"""

from __future__ import annotations

import argparse
import hashlib
import logging

logger = logging.getLogger("whiskers")
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List, Optional

try:
    from pydantic import BaseModel, create_model
    from pydantic.fields import FieldInfo
    import pydantic
    PYDANTIC_V2 = int(pydantic.VERSION.split(".")[0]) >= 2
except ImportError:
    print("ERROR: pydantic is required.  pip install pydantic", file=sys.stderr)
    sys.exit(1)


# ---------------------------------------------------------------------------
# Version / cache helpers
# ---------------------------------------------------------------------------

_VERSION_SUFFIX = ".version.json"


def _file_sha256(path: Path) -> str:
    """Return hex sha256 of a file's raw bytes."""
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def _version_path(out_path: Path) -> Path:
    """Sidecar version file path for the given output file."""
    return out_path.with_suffix("").with_suffix(_VERSION_SUFFIX)


def _load_version(out_path: Path) -> dict:
    vp = _version_path(out_path)
    if vp.exists():
        try:
            return json.loads(vp.read_text(encoding="utf-8"))
        except Exception:
            # Corrupt/unreadable version sidecar — treat as missing.
            logger.debug("openapi_to_mcp: version load failed", exc_info=True)
    return {}


def _save_version(out_path: Path, input_path: Path, tool_count: int) -> None:
    vp = _version_path(out_path)
    mtime = datetime.fromtimestamp(input_path.stat().st_mtime, tz=timezone.utc).isoformat()
    payload = {
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        "source_file":  input_path.name,
        "source_sha256": _file_sha256(input_path),
        "source_mtime":  mtime,
        "tool_count":    tool_count,
    }
    vp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Version info written to {vp}", file=sys.stderr)


# ---------------------------------------------------------------------------
# $ref / anyOf / oneOf / allOf resolution
# ---------------------------------------------------------------------------

def _resolve_schema(schema: dict, defs: dict) -> tuple[dict, bool]:
    """
    Resolve $ref references and flatten anyOf/oneOf/allOf into a concrete
    JSON Schema dict.

    Returns:
        (resolved_schema, is_nullable)

    Handles:
    - ``$ref: "#/$defs/Foo"`` or ``"#/definitions/Foo"``
    - ``anyOf: [{type: X}, {type: null}]``  → Optional[X]
    - ``oneOf`` identical to anyOf
    - ``allOf``  → shallow merge of sub-schemas

    defs is the ``$defs`` / ``definitions`` dict from the enclosing schema.
    """
    is_nullable = False

    # Resolve $ref
    if "$ref" in schema:
        ref: str = schema["$ref"]
        # e.g. "#/$defs/FooBar"  or  "#/definitions/FooBar"
        parts = ref.lstrip("#/").split("/")
        obj: Any = {"$defs": defs, "definitions": defs}
        for part in parts:
            if isinstance(obj, dict):
                obj = obj.get(part, {})
            else:
                obj = {}
                break
        return _resolve_schema(obj if obj else {}, defs)

    # anyOf / oneOf  → collapse nullable union
    for union_key in ("anyOf", "oneOf"):
        variants = schema.get(union_key)
        if variants:
            non_null = [s for s in variants if s.get("type") != "null"]
            if len(variants) > len(non_null):
                is_nullable = True
            if len(non_null) == 1:
                resolved, inner_nullable = _resolve_schema(non_null[0], defs)
                return resolved, (is_nullable or inner_nullable)
            # Multiple non-null branches → open object fallback
            return {"type": "object", "additionalProperties": True}, is_nullable

    # allOf → deep/recursive merge
    if "allOf" in schema:
        merged: dict = {}
        for sub in schema.get("allOf", []):
            resolved_sub, sub_nullable = _resolve_schema(sub, defs)
            for k, v in resolved_sub.items():
                if k not in merged:
                    merged[k] = v
                elif isinstance(merged[k], dict) and isinstance(v, dict):
                    merged[k].update(v)
                elif isinstance(merged[k], list) and isinstance(v, list):
                    merged[k].extend(v)
                else:
                    merged[k] = v
            if sub_nullable:
                is_nullable = True
        return merged, is_nullable

    return schema, is_nullable


# ---------------------------------------------------------------------------
# JSON Schema → Python type mapping
# ---------------------------------------------------------------------------

def _json_schema_type_to_python(schema: dict, defs: dict | None = None) -> Any:
    """
    Convert an **already-resolved** JSON Schema property definition to a
    Python type annotation compatible with pydantic.create_model().

    Pass defs so that any residual $ref inside array items can be resolved.
    This function does NOT build nested Pydantic models for object properties
    — that is handled by schema_to_pydantic_model so that field names are
    preserved.  Arrays of un-modelled objects fall back to ``list[dict]``.
    """
    if defs is None:
        defs = {}

    t = schema.get("type", "string")

    if t == "integer":
        return int
    if t == "number":
        return float
    if t == "boolean":
        return bool
    if t == "array":
        items = schema.get("items", {})
        resolved_items, _ = _resolve_schema(items, defs) if items else ({}, False)
        # Arrays of objects with defined properties are handled via the
        # schema_to_pydantic_model path in the callers; here return list[dict]
        # so that at minimum the field is typed as a list.
        inner = _json_schema_type_to_python(resolved_items, defs)
        return list[inner]  # type: ignore[valid-type]
    if t == "object":
        return dict
    if t == "string":
        return str
    # fallback / unknown
    return Any


def _make_field(prop_schema: dict, required: bool, defs: dict | None = None) -> tuple[Any, Any]:
    """
    Return (annotation, default) for pydantic create_model.
    Accepts an already-resolved schema.
    """
    if defs is None:
        defs = {}
    py_type = _json_schema_type_to_python(prop_schema, defs)
    description = prop_schema.get("description", "")
    example = prop_schema.get("example")

    if PYDANTIC_V2:
        from pydantic import Field
        field_kwargs: dict = {}
        if description:
            field_kwargs["description"] = description
        if example is not None:
            field_kwargs["examples"] = [example]
        if required:
            return (py_type, Field(**field_kwargs))
        else:
            return (Optional[py_type], Field(default=None, **field_kwargs))  # type: ignore[arg-type]
    else:
        # Pydantic v1 compat
        from pydantic import Field
        if required:
            return (py_type, Field(..., description=description))
        else:
            return (Optional[py_type], Field(None, description=description))


# ---------------------------------------------------------------------------
# Build pydantic model from a JSON Schema object
# ---------------------------------------------------------------------------

def schema_to_pydantic_model(
    model_name: str,
    schema: dict,
    defs: dict | None = None,
) -> type[BaseModel]:
    """
    Build a pydantic model from an OpenAPI JSON Schema object.

    Handles:
    - ``properties`` + ``required`` (flat and deeply nested)
    - Nested ``object`` properties with their own ``properties`` (via recursion)
    - Arrays of embedded objects (``array`` items with ``properties``)
    - ``anyOf`` / ``oneOf`` nullable unions (resolved to ``Optional[T]``)
    - ``allOf`` merges
    - ``$ref`` references within the same ``$defs`` dict
    - ``additionalProperties`` without ``properties`` → plain ``dict``
    """
    if defs is None:
        defs = {}

    properties = schema.get("properties", {})
    required_fields = set(schema.get("required", []))

    field_definitions: dict[str, Any] = {}
    for prop_name, raw_prop_schema in properties.items():
        safe_name = re.sub(r"[^a-zA-Z0-9_]", "_", prop_name)
        is_required = prop_name in required_fields

        # Resolve $ref / anyOf / oneOf / allOf before inspecting the type
        prop_schema, is_nullable = _resolve_schema(raw_prop_schema, defs)
        effective_required = is_required and not is_nullable

        if PYDANTIC_V2:
            from pydantic import Field as _Field
        else:
            from pydantic import Field as _Field

        # ── Case 1: nested object with known properties ──────────────────
        if prop_schema.get("type") == "object" and prop_schema.get("properties"):
            nested_model = schema_to_pydantic_model(
                f"{model_name}_{safe_name}", prop_schema, defs
            )
            ann = nested_model if effective_required else Optional[nested_model]
            desc = prop_schema.get("description", "")
            if PYDANTIC_V2:
                field_definitions[safe_name] = (
                    ann,
                    _Field(..., description=desc) if effective_required else _Field(default=None, description=desc),
                )
            else:
                field_definitions[safe_name] = (
                    ann,
                    _Field(...) if effective_required else _Field(None),
                )

        # ── Case 2: array of embedded objects with known properties ──────
        elif prop_schema.get("type") == "array":
            items = prop_schema.get("items", {})
            resolved_items, _ = _resolve_schema(items, defs) if items else ({}, False)
            desc = prop_schema.get("description", "")

            if resolved_items.get("type") == "object" and resolved_items.get("properties"):
                # Build a named model for the array item type
                item_model = schema_to_pydantic_model(
                    f"{model_name}_{safe_name}_item", resolved_items, defs
                )
                ann: Any = List[item_model]  # type: ignore[valid-type]
                ann = ann if effective_required else Optional[ann]
                if PYDANTIC_V2:
                    field_definitions[safe_name] = (
                        ann,
                        _Field(..., description=desc) if effective_required else _Field(default=None, description=desc),
                    )
                else:
                    field_definitions[safe_name] = (
                        ann,
                        _Field(...) if effective_required else _Field(None),
                    )
            else:
                # Scalar array or array of open objects
                annotation, default = _make_field(prop_schema, effective_required, defs)
                field_definitions[safe_name] = (annotation, default)

        # ── Case 3: scalar / open object / unknown ───────────────────────
        else:
            annotation, default = _make_field(prop_schema, effective_required, defs)
            # If the field was nullable but still required structurally, wrap in Optional
            if is_nullable and not isinstance(default, type(None)):
                if PYDANTIC_V2:
                    from pydantic import Field as _F
                    annotation = Optional[annotation]  # type: ignore[assignment]
                    field_definitions[safe_name] = (annotation, _F(default=None, description=prop_schema.get("description", "")))
                    continue
            field_definitions[safe_name] = (annotation, default)

    if not field_definitions:
        # No defined properties → accept any object
        field_definitions["__extra__"] = (Optional[dict], None)  # type: ignore[assignment]

    try:
        model = create_model(model_name, **field_definitions)
    except Exception:
        model = create_model(model_name)
    return model


# ---------------------------------------------------------------------------
# Parameter → field builder
# ---------------------------------------------------------------------------

def params_to_fields(parameters: list[dict]) -> dict[str, Any]:
    """Build pydantic field_definitions dict from OpenAPI Parameter Objects."""
    fields: dict[str, Any] = {}
    for param in parameters:
        name = re.sub(r"[^a-zA-Z0-9_]", "_", param["name"])
        schema = param.get("schema", {"type": "string"})
        is_required = param.get("required", False)
        annotation, default = _make_field(schema, is_required)
        fields[name] = (annotation, default)
    return fields


# ---------------------------------------------------------------------------
# OpenAPI Operation → MCP tool definition
# ---------------------------------------------------------------------------

def operation_to_mcp_tool(
    path: str,
    method: str,
    operation: dict,
) -> dict:
    """
    Convert a single OpenAPI operation into an MCP tool definition:
    {
        "name":        str,
        "description": str,
        "inputSchema": { ... JSON Schema ... }
    }
    """
    op_id = operation.get("operationId") or f"{method}_{path.replace('/', '_')}"
    # Sanitise name for MCP (snake_case)
    tool_name = re.sub(r"([A-Z])", r"_\1", op_id).lower().lstrip("_")
    tool_name = re.sub(r"[^a-z0-9_]", "_", tool_name)

    description = operation.get("summary") or operation.get("description") or op_id
    tags = operation.get("tags", [])
    if tags:
        description = f"[{tags[0]}] {description}"

    # Start with path & query parameters
    parameters = [p for p in operation.get("parameters", []) if p.get("in") in ("path", "query")]
    field_defs = params_to_fields(parameters)

    # Merge in requestBody schema (POST/PUT/PATCH)
    body_schema: dict = {}
    request_body = operation.get("requestBody", {})
    content = request_body.get("content", {})
    json_content = content.get("application/json", {})
    body_schema = json_content.get("schema", {})

    if body_schema.get("properties"):
        # Extract $defs (used for $ref resolution throughout the body)
        defs = body_schema.get("$defs", body_schema.get("definitions", {}))
        required_body = set(body_schema.get("required", []))

        if PYDANTIC_V2:
            from pydantic import Field as _Field
        else:
            from pydantic import Field as _Field

        for prop, raw_prop_schema in body_schema["properties"].items():
            safe = re.sub(r"[^a-zA-Z0-9_]", "_", prop)
            if safe not in field_defs:  # params take precedence
                is_req_raw = prop in required_body

                # Resolve $ref / anyOf / oneOf / allOf before inspecting type
                prop_schema, is_nullable = _resolve_schema(raw_prop_schema, defs)
                is_req = is_req_raw and not is_nullable
                desc = prop_schema.get("description", "")

                # ── nested object with known properties ──────────────────
                if prop_schema.get("type") == "object" and prop_schema.get("properties"):
                    nested_model = schema_to_pydantic_model(f"{op_id}_{safe}", prop_schema, defs)
                    ann = nested_model if is_req else Optional[nested_model]
                    if PYDANTIC_V2:
                        field_defs[safe] = (ann, _Field(..., description=desc) if is_req else _Field(default=None, description=desc))
                    else:
                        field_defs[safe] = (ann, _Field(...) if is_req else _Field(None))

                # ── array of embedded objects ─────────────────────────────
                elif prop_schema.get("type") == "array":
                    items = prop_schema.get("items", {})
                    resolved_items, _ = _resolve_schema(items, defs) if items else ({}, False)
                    if resolved_items.get("type") == "object" and resolved_items.get("properties"):
                        item_model = schema_to_pydantic_model(f"{op_id}_{safe}_item", resolved_items, defs)
                        ann: Any = List[item_model]  # type: ignore[valid-type]
                        ann = ann if is_req else Optional[ann]  # type: ignore[assignment]
                        if PYDANTIC_V2:
                            field_defs[safe] = (ann, _Field(..., description=desc) if is_req else _Field(default=None, description=desc))
                        else:
                            field_defs[safe] = (ann, _Field(...) if is_req else _Field(None))
                    else:
                        field_defs[safe] = _make_field(prop_schema, is_req, defs)

                # ── scalar / open object ──────────────────────────────────
                else:
                    field_defs[safe] = _make_field(prop_schema, is_req, defs)

    # Build pydantic model and extract JSON Schema
    if field_defs:
        model = create_model(f"{op_id}Input", **field_defs)
        if PYDANTIC_V2:
            input_schema = model.model_json_schema()
        else:
            input_schema = model.schema()
        # Remove pydantic title clutter
        input_schema.pop("title", None)
    else:
        input_schema = {"type": "object", "properties": {}, "additionalProperties": True}

    # Add security context to description if present
    security = operation.get("security", [])
    if security:
        scheme_names = [list(s.keys())[0] for s in security if s]
        description += f" (requires: {', '.join(scheme_names)})"

    return {
        "name":        tool_name,
        "description": description,
        "inputSchema": input_schema,
        "_meta": {
            "operationId": op_id,
            "method":      method.upper(),
            "path":        path,
            "tags":        tags,
            "security":    security,
            "query":       [p["name"] for p in parameters if p.get("in") == "query"],
        },
    }


# ---------------------------------------------------------------------------
# Full spec converter
# ---------------------------------------------------------------------------

def openapi_to_mcp_tools(spec: dict) -> list[dict]:
    """Convert all operations in an OpenAPI spec to MCP tool definitions."""
    tools: list[dict] = []
    paths = spec.get("paths", {})

    for oa_path, path_item in sorted(paths.items()):
        for method, operation in path_item.items():
            if method.startswith("x-") or not isinstance(operation, dict):
                continue
            try:
                tool = operation_to_mcp_tool(oa_path, method, operation)
                tools.append(tool)
            except Exception as exc:
                op_id = operation.get("operationId", f"{method} {oa_path}")
                print(f"WARN: skipped {op_id}: {exc}", file=sys.stderr)

    return tools


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    repo_root = Path(__file__).parent.parent

    parser = argparse.ArgumentParser(
        description="Convert openapi.json → mcp-tools.json via Pydantic"
    )
    parser.add_argument(
        "--input", "-i",
        default=str(repo_root / "openapi/openapi.json"),
        help="Path to openapi.json",
    )
    parser.add_argument(
        "--out", "-o",
        default=str(repo_root / "openapi/mcp-tools.json"),
        help="Output path for mcp-tools.json",
    )
    parser.add_argument(
        "--print", dest="print_only", action="store_true",
        help="Print JSON to stdout instead of writing file",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Regenerate even if source hash is unchanged",
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"ERROR: {input_path} not found. Run build_openapi.py first.", file=sys.stderr)
        sys.exit(1)

    out_path = Path(args.out)

    # ── Version / cache check ──────────────────────────────────────────────
    if not args.print_only and not args.force and out_path.exists():
        cached = _load_version(out_path)
        current_hash = _file_sha256(input_path)
        if cached.get("source_sha256") == current_hash:
            print(
                f"mcp-tools output is up to date (source hash unchanged).\n"
                f"  source : {input_path}  sha256={current_hash[:16]}…\n"
                f"  tools  : {cached.get('tool_count', '?')} (generated {cached.get('generated_at', '?')})\n"
                f"  Pass --force to regenerate.",
                file=sys.stderr,
            )
            return

    spec = json.loads(input_path.read_text(encoding="utf-8"))
    print(f"Converting {len(spec.get('paths', {}))} paths …", file=sys.stderr)

    tools = openapi_to_mcp_tools(spec)
    output = json.dumps(tools, indent=2)

    if args.print_only:
        print(output)
        return

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(output, encoding="utf-8")
    print(f"MCP tools written to {out_path}")
    print(f"  {len(tools)} tools generated")

    # Write version sidecar so future runs can skip regen if source unchanged
    _save_version(out_path, input_path, len(tools))

    # Print a sample
    if tools:
        print(f"\nSample tool: {tools[0]['name']}")
        print(f"  description: {tools[0]['description'][:80]}")
        schema_props = tools[0]["inputSchema"].get("properties", {})
        print(f"  inputSchema properties: {list(schema_props.keys())[:8]}")


if __name__ == "__main__":
    main()
