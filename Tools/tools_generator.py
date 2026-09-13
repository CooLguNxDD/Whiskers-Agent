#!/usr/bin/env python3
"""
Whiskers Agent Tools Generator
===========================
Generates boilerplate MCP tool modules from a YAML configuration file,
following the patterns established in the plugin-based architecture.

By default, generates modules with **plugin-scoped absolute imports**
(suitable for `plugins/<plugin_name>/MCPTools/<module>.py`).

Usage
-----
    # Generate plugin-scoped module (default — use inside plugins/)
    python tools_generator.py --config tools_config.yaml --output plugins/my_plugin/MCPTools/new_tools.py

    # Generate with schema entries for LangGraph tool_schemas.py
    python tools_generator.py --config tools_config.yaml --output plugins/my_plugin/MCPTools/new_tools.py --schema

    # Interactive mode – answer prompts to generate a tool
    python tools_generator.py --interactive

    # Print an example YAML config to stdout
    python tools_generator.py --example

    # Legacy relative imports (non-plugin MCPTools/ layout, rarely needed)
    python tools_generator.py --config tools_config.yaml --output MCPTools/new_tools.py --legacy-imports

    # Scaffold a plugin migration file (upgrade(conn) pattern via PluginSchemaMigrator)
    python tools_generator.py --gen-migration \\
        --plugin-id my_plugin --migration-name add_records \\
        --migration-type create_table --migration-table records \\
        --migration-columns "id BIGSERIAL PRIMARY KEY" "record_id VARCHAR NOT NULL"

    # Generate migration in dry-run (print to stdout, don't write file)
    python tools_generator.py --gen-migration --plugin-id my_plugin \\
        --migration-name add_status --migration-type add_column \\
        --migration-table records --migration-columns "status VARCHAR" --dry-run
"""

import argparse
import json
import sys
import textwrap
from pathlib import Path

_TOOLS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _TOOLS_DIR.parent
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))
if str(_REPO_ROOT) not in sys.path:
    # Needed so `from utils.response_shape_hints import ...` resolves regardless of
    # whether this script is invoked as `python tools_generator.py` (cwd=Tools/) or
    # `python Tools/tools_generator.py` (cwd=repo root, but sys.path[0] is still Tools/).
    sys.path.insert(0, str(_REPO_ROOT))
from _write_utils import WriteError, emit_generated, write_text_safe  # noqa: E402

try:
    import yaml
    HAS_YAML = True
except ImportError:
    HAS_YAML = False


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------

# Plugin-specific import lines for the module header.
# Keys are plugin IDs; value is the import line(s) to inject.
_PLUGIN_IMPORTS: dict[str, str] = {
    "jules_plugin": (
        "from plugins.jules_plugin.plugin_config import "
        "API_URL, plugin_auth_headers as _auth_headers"
    ),
    "search_plugin": (
        "from plugins.search_plugin.plugin_config import "
        "API_URL, plugin_auth_headers as _auth_headers"
    ),
    "job_search_plugin": (
        "from plugins.job_search_plugin.plugin_config import "
        "API_URL, plugin_auth_headers as _auth_headers"
    ),
    "cat_terminal_relay_plugin": (
        "from plugins.cat_terminal_relay_plugin.plugin_config import "
        "API_URL, plugin_auth_headers as _auth_headers"
    ),
    "portfolio_plugin": (
        "from plugins.portfolio_plugin.plugin_config import "
        "API_URL, plugin_auth_headers as _auth_headers"
    ),
}


def _plugin_import_line(plugin_id: str | None) -> str:
    """Return the plugin_config import line for *plugin_id*.

    Known plugins use their curated import (some also pull PROJECT_ID).
    An unrecognized or missing plugin_id falls back to a generic import
    derived from the id itself — never a specific plugin's default.
    """
    if plugin_id and plugin_id in _PLUGIN_IMPORTS:
        return _PLUGIN_IMPORTS[plugin_id]
    target = plugin_id or "my_plugin"
    return (
        f"from plugins.{target}.plugin_config import "
        "API_URL, plugin_auth_headers as _auth_headers"
    )


_MODULE_TEMPLATE = '''\
"""{module_doc}"""

import logging
from typing import Any

import requests

from core.context import mcp
{plugin_imports}
from utils import safe_api_call

logger = logging.getLogger("whiskers")

{functions}'''

_MODULE_TEMPLATE_LEGACY = '''\
"""{module_doc}"""

import logging
from typing import Any

import requests

from .context import mcp, API_URL, PROJECT_ID, _auth_headers
from .api_utils import safe_api_call

logger = logging.getLogger("whiskers")

{functions}'''

_FUNC_GET_TEMPLATE = '''\
@mcp.tool()
async def {name}(
{params_block}
) -> dict[str, Any]:
    """{docstring}

    Args:
{args_block}

    Returns:
        {return_doc}
    """
    pid = project_id or PROJECT_ID
{validation_block}
    logger.info(f"{log_msg}")

    url = f"{{API_URL}}{endpoint}"

    def _on_success(resp: requests.Response) -> dict[str, Any]:
        result = resp.json()
        logger.info(f"✓ {success_msg}")
        return result

    headers = await _auth_headers()
    return await safe_api_call(
        lambda: requests.get(url, headers=headers, timeout=30),
        _on_success,
        context=f"{log_msg}",
    )
'''

_FUNC_POST_TEMPLATE = '''\
@mcp.tool()
async def {name}(
{params_block}
) -> dict[str, Any]:
    """{docstring}

    Args:
{args_block}

    Returns:
        {return_doc}
    """
    pid = project_id or PROJECT_ID
{validation_block}
    logger.info(f"{log_msg}")

    payload: dict[str, Any] = {{
{payload_block}
    }}

    url = f"{{API_URL}}{endpoint}"

    def _on_success(resp: requests.Response) -> dict[str, Any]:
        result = resp.json()
        logger.info(f"✓ {success_msg}")
        return result

    headers = await _auth_headers()
    return await safe_api_call(
        lambda: requests.post(url, json=payload, headers=headers, timeout=30),
        _on_success,
        context=f"{log_msg}",
    )
'''

_FUNC_PUT_TEMPLATE = '''\
@mcp.tool()
async def {name}(
{params_block}
) -> dict[str, Any]:
    """{docstring}

    Args:
{args_block}

    Returns:
        {return_doc}
    """
    pid = project_id or PROJECT_ID
{validation_block}
    logger.info(f"{log_msg}")

    payload: dict[str, Any] = {{
{payload_block}
    }}

    url = f"{{API_URL}}{endpoint}"

    def _on_success(resp: requests.Response) -> dict[str, Any]:
        result = resp.json()
        logger.info(f"✓ {success_msg}")
        return result

    headers = await _auth_headers()
    return await safe_api_call(
        lambda: requests.put(url, json=payload, headers=headers, timeout=30),
        _on_success,
        context=f"{log_msg}",
    )
'''

_FUNC_DELETE_TEMPLATE = '''\
@mcp.tool()
async def {name}(
{params_block}
) -> dict[str, Any]:
    """{docstring}

    Args:
{args_block}

    Returns:
        {return_doc}
    """
    pid = project_id or PROJECT_ID
{validation_block}
    logger.info(f"{log_msg}")

    url = f"{{API_URL}}{endpoint}"

    def _on_success(resp: requests.Response) -> dict[str, Any]:
        result = resp.json()
        logger.info(f"✓ {success_msg}")
        return result

    headers = await _auth_headers()
    return await safe_api_call(
        lambda: requests.delete(url, headers=headers, timeout=30),
        _on_success,
        context=f"{log_msg}",
    )
'''

_METHOD_TEMPLATES = {
    "GET": _FUNC_GET_TEMPLATE,
    "POST": _FUNC_POST_TEMPLATE,
    "PUT": _FUNC_PUT_TEMPLATE,
    "DELETE": _FUNC_DELETE_TEMPLATE,
}


# ---------------------------------------------------------------------------
# JSON-based static generation (mirrors dynamic_tools_loader.py patterns)
# ---------------------------------------------------------------------------

_TYPE_MAP_STR: dict[str, str] = {
    "integer": "int",
    "string": "str",
    "boolean": "bool",
    "number": "float",
    "object": "dict",
    "array": "list",
}

_VALID_JSON_OPS: frozenset[str] = frozenset({"read", "write_update", "delete"})


def _prop_py_type_str(prop: dict) -> str:
    """Map a JSON Schema property dict to a Python type annotation string."""
    if "type" in prop:
        return _TYPE_MAP_STR.get(prop["type"], "str")
    if "$ref" in prop:
        return "dict"
    any_of = prop.get("anyOf", [])
    if any_of:
        for sub in any_of:
            if "$ref" in sub:
                return "dict"
        for sub in any_of:
            t = sub.get("type")
            if t and t != "null":
                return _TYPE_MAP_STR.get(t, "str")
    return "str"


def _generate_json_tool_function(
    tool_def: dict,
    op_name: str = "read",
    domain_name: str = "",
    plugin_id: str | None = None,
) -> str:
    """Generate a static async function source for one mcp-tools.json tool definition."""
    name: str = tool_def["name"]
    description: str = tool_def.get("description", f"Tool: {name}")
    meta: dict = tool_def.get("_meta", {})
    method: str = meta.get("method", "GET").upper()
    path_template: str = meta.get("path", "")
    operation_id: str = meta.get("operationId", name)
    query_fields: list = meta.get("query", [])

    input_schema: dict = tool_def.get("inputSchema", {})
    schema_props: dict = input_schema.get("properties", {})
    required_fields: frozenset = frozenset(input_schema.get("required", []))

    # Derive read/idempotent hints from HTTP method (not op_name, which is just a category label)
    # GET/HEAD/OPTIONS are safe read-only and idempotent
    # PUT/DELETE are not read-only but are idempotent
    # POST/PATCH are neither read-only nor idempotent
    _READ_METHODS: frozenset[str] = frozenset({"GET", "HEAD", "OPTIONS"})
    _IDEMPOTENT_METHODS: frozenset[str] = frozenset({"GET", "HEAD", "OPTIONS", "PUT", "DELETE"})
    is_read_method: bool = method in _READ_METHODS
    is_idempotent: bool = method in _IDEMPOTENT_METHODS

    # Shape injection: explicit meta flag wins; default to read (GET) ops only
    inject_shape: bool = bool(meta.get("shapeable", is_read_method))

    # Order params: required first, then optional — alphabetical within each group
    ordered_params = sorted(
        schema_props.keys(), key=lambda p: (0 if p in required_fields else 1, p)
    )

    # Function signature lines
    sig_lines: list[str] = []
    for pname in ordered_params:
        py_type = _prop_py_type_str(schema_props[pname])
        if pname in required_fields:
            sig_lines.append(f"    {pname}: {py_type},")
        else:
            sig_lines.append(f"    {pname}: Optional[{py_type}] = None,")
    if inject_shape:
        sig_lines.append("    _response_shape: Optional[dict] = None,")

    # _kw dict literal lines
    kw_lines: list[str] = [f'        "{pname}": {pname},' for pname in ordered_params]

    # Docstring
    schema_json = json.dumps(input_schema, indent=2)
    doc_parts = [
        description,
        f"\n### Input Schema (Pay attention to nested object structures):\n```json\n{schema_json}\n```",
    ]
    if inject_shape:
        # Core-owned text (utils/response_shape_hints.py), rendered straight from the
        # shaping pipeline's own vocabulary so generated tool docstrings can't drift
        # from utils/response_shape.py / utils/response_format.py the way the old
        # hand-written blurb here did.
        from utils.response_shape_hints import get_shape_hint, get_format_hint

        doc_parts.append(f"\n### Response Shaping (_response_shape parameter):\n{get_shape_hint()}\n\n{get_format_hint()}")
    full_desc = "\n".join(doc_parts).replace('"""', "'''")

    # Decorator: use "read" tag for safe methods, "write" for mutating methods
    op_tag: str = "read" if is_read_method else "write"
    tag_parts = []
    if domain_name:
        tag_parts.append(f'"{domain_name}"')
    tag_parts.append(f'"{op_tag}"')
    tag_set = ", ".join(tag_parts) if tag_parts else '"tool"'
    read_only_hint = "True" if is_read_method else "False"
    idempotent_hint = "True" if is_idempotent else "False"

    # Python literals for runtime use inside generated function
    schema_props_lit = repr(dict(schema_props))
    required_lit = f"frozenset({repr(sorted(list(required_fields)))})"

    lines: list[str] = []

    # --- decorator ---
    lines.append("@mcp.tool(")
    if domain_name:
        lines.append(f'    title="{domain_name}",')
    lines.append(f"    tags={{{tag_set}}},")
    lines.append(
        f'    annotations={{"readOnlyHint": {read_only_hint}, "idempotentHint": {idempotent_hint}}},'
    )
    lines.append(")")

    # --- signature ---
    lines.append(f"async def {name}(")
    lines.extend(sig_lines)
    lines.append(") -> Any:")
    lines.append('    """' + full_desc + '"""')

    # --- body ---
    lines.append(f'    _ep_meta = get_endpoint_meta("{name}")')
    if inject_shape:
        lines.append("    _shape: Optional[dict] = _response_shape")
    lines.append("    _kw: dict = {k: v for k, v in {")
    lines.extend(kw_lines)
    lines.append("    }.items() if v is not None}")
    lines.append("")

    # path substitution
    lines.append(f"    _path = {repr(path_template)}")
    lines.append(f"    for _m in _PATH_PARAM_RE.finditer({repr(path_template)}):")
    lines.append("        _p = _m.group(1) or _m.group(2)")
    lines.append("        if _p in _kw:")
    lines.append('            _token = _m.group(0)')
    lines.append('            _val = str(_kw.pop(_p))')
    lines.append('            _rep = f"/{_val}" if _token.startswith("/:") else _val')
    lines.append('            _path = _path.replace(_token, _rep)')
    lines.append("")

    # unresolved path-param guard
    lines.append(
        "    _unresolved = [_m.group(1) or _m.group(2) for _m in _PATH_PARAM_RE.finditer(_path)]"
    )
    lines.append("    if _unresolved:")
    lines.append('        return {')
    lines.append('            "status": "error",')
    lines.append('            "error": "missing_path_params",')
    lines.append('            "missing_fields": _unresolved,')
    _msg_line = (
        f'            "message": "Required path parameter(s) not provided: "'
        f' + ", ".join(_unresolved) + ". URL template: {path_template}",'
    )
    lines.append(_msg_line)
    lines.append("        }")
    lines.append("")

    # full URL + pagination defaults
    lines.append('    _full_url = f"{API_URL}{_path}"')
    lines.append("    _kw = inject_pagination_defaults(_kw, _ep_meta)")

    # shape merging (read / shapeable tools only)
    if inject_shape:
        lines.append("    if _shape is not None:")
        lines.append("        _merged_shape = dict(_shape)")
        lines.append(
            '        if "response_format" not in _merged_shape'
            " and (_dfmt := _ep_meta.get(\"default_response_format\")):"
        )
        lines.append('            _merged_shape["response_format"] = _dfmt')
        lines.append(
            '        if "include_meta" not in _merged_shape and "include_meta" in _ep_meta:'
        )
        lines.append('            _merged_shape["include_meta"] = bool(_ep_meta.get("include_meta"))')
        lines.append("        _shape = _merged_shape")

    lines.append("    _headers = await _auth_headers()")
    lines.append(f"    _schema_props = {schema_props_lit}")
    lines.append(f"    _required_fields = {required_lit}")

    # HTTP dispatch
    if method == "GET":
        lines.append("    _query = sanitize_params(_kw, _schema_props, _required_fields)")
        lines.append("    return await safe_api_call(")
        lines.append(
            "        lambda: requests.get(_full_url, params=_query, headers=_headers, timeout=30),"
        )
        lines.append("        lambda r: safe_json_response(r),")
        lines.append(f'        context="{name}",')
        lines.append(f'        operation_id="{operation_id}",')
        if inject_shape:
            lines.append("        shape=_shape,")
            lines.append(f'        tool_name="{name}",')
            lines.append("        request_params=_query,")
        if plugin_id:
            lines.append("        headers=_headers,")
            lines.append(f'        plugin_id="{plugin_id}",')
        lines.append("    )")
    else:
        lines.append(f"    _query_fields = {repr(query_fields)}" if query_fields else "    _query_fields: list = []")
        lines.append("    _query_kw = {k: v for k, v in _kw.items() if k in _query_fields}")
        lines.append("    _body_kw = {k: v for k, v in _kw.items() if k not in _query_fields}")
        lines.append("    _body = sanitize_body(_body_kw, _schema_props)")
        lines.append("    return await safe_api_call(")
        lines.append(
            f'        lambda: requests.request("{method}", _full_url,'
            " params=_query_kw, json=_body, headers=_headers, timeout=30),"
        )
        lines.append("        lambda r: safe_json_response(r),")
        lines.append(f'        context="{name}",')
        lines.append(f'        operation_id="{operation_id}",')
        if plugin_id:
            lines.append("        headers=_headers,")
            lines.append(f'        plugin_id="{plugin_id}",')
        lines.append("    )")

    return "\n".join(lines)


def _build_json_module_header(module_doc: str, legacy: bool = False) -> str:
    """Build the imports/header block for a JSON-based generated module."""
    if legacy:
        ctx_import = "from .context import mcp, API_URL, _auth_headers"
        utils_block = (
            "from .api_utils import (\n"
            "    safe_api_call,\n"
            "    safe_json_response,\n"
            "    sanitize_params,\n"
            "    sanitize_body,\n"
            "    get_endpoint_meta,\n"
            "    inject_pagination_defaults,\n"
            "    SAFE_DEFAULT_PAGE_SIZE,\n"
            "    SAFE_DEFAULT_PAGE_INDEX,\n"
            "    MAX_RECOMMENDED_PAGE_SIZE,\n"
            ")"
        )
    else:
        ctx_import = (
            "from core.context import mcp\n"
            + _plugin_import_line(plugin_id)
        )
        utils_block = (
            "from utils import (\n"
            "    safe_api_call,\n"
            "    safe_json_response,\n"
            "    sanitize_params,\n"
            "    sanitize_body,\n"
            "    get_endpoint_meta,\n"
            "    inject_pagination_defaults,\n"
            "    SAFE_DEFAULT_PAGE_SIZE,\n"
            "    SAFE_DEFAULT_PAGE_INDEX,\n"
            "    MAX_RECOMMENDED_PAGE_SIZE,\n"
            ")"
        )

    # Regex pattern: \\{ → \{ in the written file (inside a raw string r"...")
    regex_line = '_PATH_PARAM_RE = re.compile(r"\\{(\\w+)\\}|(?:^|/):(\\w+)")'

    return (
        f'"""{module_doc}"""\n'
        "\n"
        "import logging\n"
        "import re\n"
        "from typing import Any, Optional\n"
        "\n"
        "import requests\n"
        "\n"
        f"{ctx_import}\n"
        f"{utils_block}\n"
        "\n"
        'logger = logging.getLogger("whiskers")\n'
        "\n"
        f"{regex_line}\n"
        "\n"
    )


def generate_module_from_json_tools(
    tool_defs_with_meta: list[tuple[dict, str, str]],
    module_doc: str = "Auto-generated MCP tools (from mcp-tools.json).",
    legacy_imports: bool = False,
    plugin_id: str | None = None,
) -> str:
    """Generate a full Python module from (tool_def, op_name, domain_name) tuples."""
    header = _build_json_module_header(module_doc, legacy=legacy_imports)
    functions = [
        _generate_json_tool_function(td, op_name=op, domain_name=dom, plugin_id=plugin_id)
        for td, op, dom in tool_defs_with_meta
    ]
    return header + "\n\n".join(functions) + "\n"


def load_json_tools_file(
    path: "str | Path",
    op_name: str = "",
    domain_name: str = "",
) -> list[tuple[dict, str, str]]:
    """Load a single mcp-tools.json file → list of (tool_def, op_name, domain_name)."""
    p = Path(path)
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"ERROR: could not parse {p}: {exc}", file=sys.stderr)
        sys.exit(1)

    tool_defs: list[dict] = [raw] if isinstance(raw, dict) else raw

    # Infer op_name from parent dir name if not supplied
    if not op_name:
        inferred = p.parent.name
        op_name = inferred if inferred in _VALID_JSON_OPS else "read"

    # Infer domain from grandparent dir name if not supplied
    if not domain_name:
        grandparent = p.parent.parent.name
        if grandparent not in _VALID_JSON_OPS and grandparent != p.parent.name:
            domain_name = grandparent

    return [(td, op_name, domain_name) for td in tool_defs if td.get("name")]


def scan_json_tools_dir(
    dir_path: "str | Path",
    domains: "list[str] | None" = None,
    ops: "list[str] | None" = None,
) -> list[tuple[dict, str, str]]:
    """Walk a mcp-tools-context dir tree → list of (tool_def, op_name, domain_name).

    Mirrors load_tools() in dynamic_tools_loader.py.
    """
    base = Path(dir_path)
    if not base.exists():
        print(f"ERROR: directory not found: {base}", file=sys.stderr)
        sys.exit(1)

    op_filter = {o for o in ops if o in _VALID_JSON_OPS} if ops else _VALID_JSON_OPS
    domain_dirs = sorted(
        d for d in base.iterdir()
        if d.is_dir() and (domains is None or d.name in domains)
    )

    result: list[tuple[dict, str, str]] = []
    registered_names: set[str] = set()

    for domain_dir in domain_dirs:
        for op_dir in sorted(domain_dir.iterdir()):
            if not op_dir.is_dir() or op_dir.name not in op_filter:
                continue
            json_file = op_dir / "mcp-tools.json"
            if not json_file.exists():
                continue
            try:
                tool_defs = json.loads(json_file.read_text(encoding="utf-8"))
            except Exception as exc:
                print(f"WARNING: could not parse {json_file}: {exc}", file=sys.stderr)
                continue

            for tool_def in tool_defs:
                tool_name = tool_def.get("name", "").strip()
                if not tool_name:
                    continue
                if tool_name in registered_names:
                    original = tool_name
                    suffix = 1
                    while tool_name in registered_names:
                        tool_name = f"{original}_{suffix:02d}"
                        suffix += 1
                    print(
                        f"WARNING: duplicate '{original}' in {json_file} — using '{tool_name}'",
                        file=sys.stderr,
                    )
                    tool_def = dict(tool_def)
                    tool_def["name"] = tool_name
                registered_names.add(tool_name)
                result.append((tool_def, op_dir.name, domain_dir.name))

    return result


def generate_json_schema_entries(tool_defs_with_meta: list[tuple[dict, str, str]]) -> str:
    """Generate TOOL_SCHEMAS entries for all JSON-sourced tool definitions."""
    entries: list[str] = []
    for tool_def, _, _ in tool_defs_with_meta:
        name = tool_def["name"]
        input_schema = tool_def.get("inputSchema", {})
        schema_props = input_schema.get("properties", {})
        required_set = set(input_schema.get("required", []))

        req_names = [p for p in schema_props if p in required_set]
        opt_names = [p for p in schema_props if p not in required_set]
        descs = {p: schema_props[p].get("description", "") for p in schema_props}

        lines = [f'    "{name}": {{']
        lines.append(f'        "required": {json.dumps(req_names)},')
        lines.append(f'        "optional": {json.dumps(opt_names)},')
        lines.append('        "descriptions": {')
        for k, v in descs.items():
            lines.append(f'            "{k}": {json.dumps(v)},')
        lines.append("        },")
        lines.append("    },")
        entries.append("\n".join(lines))

    header = "# Add the following entries to TOOL_SCHEMAS in tool_schemas.py:\n"
    return header + "\n".join(entries)

_EXAMPLE_CONFIG = """\
# Whiskers Agent Tools Generator – example configuration
# Save this as tools_config.yaml and run:
#   python tools_generator.py --config tools_config.yaml --output plugins/my_plugin/MCPTools/new_tools.py --schema

module_doc: "Record tools: query and manage records."

tools:
  - name: get_records
    description: "Get records for a user."
    method: GET
    endpoint: "/api/v1/projects/{pid}/users/{user_id}/records"
    return_doc: "Records from the API."
    log_msg: "Getting records for user {user_id}"
    success_msg: "Retrieved records for user {user_id}"
    params:
      required:
        - name: user_id
          type: int
          description: "The user's ID."
      optional:
        - name: from_date
          type: "str | None"
          default: "None"
          description: "Filter results from this date (YYYY-MM-DD)."
        - name: project_id
          type: "int | None"
          default: "None"
          description: "Override the default project ID."

  - name: create_record
    description: "Create a new record for a user."
    method: POST
    endpoint: "/api/v1/projects/{pid}/users/{user_id}/records"
    return_doc: "The created record from the API."
    log_msg: "Creating record for user {user_id}"
    success_msg: "Record created for user {user_id}"
    params:
      required:
        - name: user_id
          type: int
          description: "The user's ID."
        - name: record_name
          type: str
          description: "Name of the record."
        - name: record_value
          type: str
          description: "Record value."
      optional:
        - name: record_date
          type: "str | None"
          default: "None"
          description: "Date of the record (YYYY-MM-DD)."
        - name: project_id
          type: "int | None"
          default: "None"
          description: "Override the default project ID."
    payload_fields:
      - key: recordName
        value: record_name
      - key: recordValue
        value: record_value
      - key: recordDate
        value: record_date
"""


# ---------------------------------------------------------------------------
# Code generation helpers
# ---------------------------------------------------------------------------

def _build_params_block(params: dict) -> str:
    """Generate the function parameter block."""
    lines = []
    for p in params.get("required", []):
        lines.append(f"    {p['name']}: {p.get('type', 'str')},")
    for p in params.get("optional", []):
        default = p.get("default", "None")
        lines.append(f"    {p['name']}: {p.get('type', 'str | None')} = {default},")
    return "\n".join(lines)


def _build_args_block(params: dict) -> str:
    """Generate the Args docstring section."""
    lines = []
    for p in params.get("required", []) + params.get("optional", []):
        lines.append(f"        {p['name']}: {p.get('description', '')}")
    return "\n".join(lines)


def _build_validation_block(params: dict) -> str:
    """Generate validation code for required fields."""
    required = params.get("required", [])
    if not required:
        return ""

    lines = ["    missing = []"]
    for p in required:
        name = p["name"]
        ptype = p.get("type", "str")
        if ptype == "str":
            lines.append(
                f"    if not {name} or not str({name}).strip():\n"
                f"        missing.append(\"{name} ({p.get('description', 'Required')})\")"
            )
        else:
            lines.append(
                f"    if {name} is None:\n"
                f"        missing.append(\"{name} ({p.get('description', 'Required')})\")"
            )
    lines.append(
        '    if missing:\n'
        '        return {\n'
        '            "status": "error",\n'
        '            "error": "missing_required_fields",\n'
        '            "missing_fields": missing,\n'
        '            "message": f"Please provide: {\', \'.join(missing)}",\n'
        '        }'
    )
    return "\n".join(lines)


def _build_payload_block(payload_fields: list[dict] | None) -> str:
    """Generate the payload dict literal lines."""
    if not payload_fields:
        return '        # TODO: define payload fields'
    lines = []
    for pf in payload_fields:
        lines.append(f'        "{pf["key"]}": {pf["value"]},')
    return "\n".join(lines)


def generate_tool_function(tool_spec: dict) -> str:
    """Generate a single tool function from a spec dict."""
    method = tool_spec.get("method", "GET").upper()
    template = _METHOD_TEMPLATES.get(method, _FUNC_GET_TEMPLATE)

    params = tool_spec.get("params", {})

    kwargs = {
        "name": tool_spec["name"],
        "docstring": tool_spec.get("description", ""),
        "endpoint": tool_spec["endpoint"],
        "return_doc": tool_spec.get("return_doc", "API response."),
        "log_msg": tool_spec.get("log_msg", f"Calling {tool_spec['name']}"),
        "success_msg": tool_spec.get("success_msg", f"{tool_spec['name']} completed"),
        "params_block": _build_params_block(params),
        "args_block": _build_args_block(params),
        "validation_block": _build_validation_block(params),
    }

    if method in ("POST", "PUT"):
        kwargs["payload_block"] = _build_payload_block(
            tool_spec.get("payload_fields")
        )

    return template.format(**kwargs)


def generate_module(config: dict, legacy_imports: bool = False) -> str:
    """Generate a full Python module from a config dict."""
    functions = []
    for tool_spec in config.get("tools", []):
        functions.append(generate_tool_function(tool_spec))

    template = _MODULE_TEMPLATE_LEGACY if legacy_imports else _MODULE_TEMPLATE
    return template.format(
        module_doc=config.get("module_doc", "Auto-generated MCP tools."),
        plugin_imports=_plugin_import_line(config.get("plugin_id")),
        functions="\n\n".join(functions),
    )


# ---------------------------------------------------------------------------
# Interactive mode
# ---------------------------------------------------------------------------

def interactive_mode() -> str:
    """Walk the user through creating a tool spec and return generated code."""
    print("\n=== Whiskers Agent Tool Generator (Interactive) ===\n")

    module_doc = input("Module docstring (one line): ").strip() or "Auto-generated MCP tools."

    tools = []
    while True:
        print(f"\n--- Tool #{len(tools) + 1} ---")
        name = input("  Function name (e.g. get_records): ").strip()
        if not name:
            break

        desc = input("  Description: ").strip()
        method = input("  HTTP method [GET]: ").strip().upper() or "GET"
        endpoint = input("  API endpoint (use {pid}, {user_id} placeholders): ").strip()
        return_doc = input("  Return description [API response.]: ").strip() or "API response."

        params: dict = {"required": [], "optional": []}

        print("  Required params (empty name to stop):")
        while True:
            pname = input("    Param name: ").strip()
            if not pname:
                break
            ptype = input(f"    Type [{pname}] [str]: ").strip() or "str"
            pdesc = input(f"    Description [{pname}]: ").strip()
            params["required"].append({"name": pname, "type": ptype, "description": pdesc})

        print("  Optional params (empty name to stop):")
        while True:
            pname = input("    Param name: ").strip()
            if not pname:
                break
            ptype = input(f"    Type [{pname}] [str | None]: ").strip() or "str | None"
            pdefault = input(f"    Default [{pname}] [None]: ").strip() or "None"
            pdesc = input(f"    Description [{pname}]: ").strip()
            params["optional"].append({
                "name": pname, "type": ptype, "default": pdefault, "description": pdesc,
            })

        payload_fields = None
        if method in ("POST", "PUT"):
            print("  Payload fields (empty key to stop):")
            payload_fields = []
            while True:
                key = input("    JSON key: ").strip()
                if not key:
                    break
                val = input(f"    Python expression for '{key}': ").strip()
                payload_fields.append({"key": key, "value": val})

        tools.append({
            "name": name,
            "description": desc,
            "method": method,
            "endpoint": endpoint,
            "return_doc": return_doc,
            "log_msg": f"Calling {name}",
            "success_msg": f"{name} completed",
            "params": params,
            "payload_fields": payload_fields,
        })

        more = input("\n  Add another tool? [y/N]: ").strip().lower()
        if more not in ("y", "yes"):
            break

    config = {"module_doc": module_doc, "tools": tools}
    return generate_module(config)


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

def load_config(path: str) -> dict:
    """Load a YAML or JSON config file."""
    p = Path(path)
    text = p.read_text(encoding="utf-8")

    if p.suffix in (".yaml", ".yml"):
        if not HAS_YAML:
            print("ERROR: pyyaml is required for YAML configs.  pip install pyyaml", file=sys.stderr)
            sys.exit(1)
        return yaml.safe_load(text)
    else:
        return json.loads(text)


# ---------------------------------------------------------------------------
# LangGraph tool_schemas.py updater
# ---------------------------------------------------------------------------

def generate_schema_entry(tool_spec: dict) -> str:
    """Generate a TOOL_SCHEMAS dict entry for a single tool."""
    params = tool_spec.get("params", {})
    req_names = [p["name"] for p in params.get("required", [])]
    opt_names = [p["name"] for p in params.get("optional", [])]

    descs = {}
    for p in params.get("required", []) + params.get("optional", []):
        descs[p["name"]] = p.get("description", "")

    lines = [f'    "{tool_spec["name"]}": {{']
    lines.append(f'        "required": {json.dumps(req_names)},')
    lines.append(f'        "optional": {json.dumps(opt_names)},')
    lines.append('        "descriptions": {')
    for k, v in descs.items():
        lines.append(f'            "{k}": "{v}",')
    lines.append('        },')
    lines.append('    },')
    return "\n".join(lines)


def generate_all_schema_entries(config: dict) -> str:
    """Generate TOOL_SCHEMAS entries for all tools in a config."""
    entries = []
    for tool_spec in config.get("tools", []):
        entries.append(generate_schema_entry(tool_spec))
    header = "# Add the following entries to TOOL_SCHEMAS in tool_schemas.py:\n"
    return header + "\n".join(entries)


# ---------------------------------------------------------------------------
# Credentials scaffolding
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _discover_all_plugins() -> list[dict]:
    """Scan plugins/*/manifest.json and return plugin metadata dicts."""
    from core.plugin_loader.credentials_loader import normalize_credential_keys

    plugins_dir = _REPO_ROOT / "plugins"
    result = []
    if not plugins_dir.is_dir():
        return result
    for manifest_path in sorted(plugins_dir.glob("*/manifest.json")):
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        result.append({
            "id": manifest.get("name", manifest_path.parent.name),
            "dir": manifest_path.parent,
            # Accept string or {key, description} manifest entries.
            "required": normalize_credential_keys(
                manifest.get("required_credentials", [])
            ),
            "optional": normalize_credential_keys(
                manifest.get("optional_credentials", [])
            ),
        })
    return result


def _scaffold_single_plugin_credentials(plugin: dict) -> None:
    """Write credential template files for one plugin directory."""
    plugin_dir: Path = plugin["dir"]
    required: list[str] = plugin["required"]
    optional: list[str] = plugin["optional"]
    all_keys = required + [k for k in optional if k not in required]

    example_data: dict = {
        "_comment": (
            "Copy this file to credentials.json and fill in your values. "
            "credentials.json is gitignored."
        ),
    }
    for key in required:
        example_data[key] = f"your_{key.lower()}_here"
    for key in optional:
        if key not in required:
            example_data[key] = f"your_{key.lower()}_here"

    if not all_keys:
        example_data["_note"] = (
            "This plugin declares no credentials. Add any custom keys here "
            "(e.g. API_KEY, token) as needed."
        )

    example_path = plugin_dir / "credentials_example.json"
    write_text_safe(
        example_path,
        json.dumps(example_data, indent=2) + "\n",
        create_parents=False,
    )
    print(f"  ✓ Wrote  {example_path.relative_to(_REPO_ROOT)}")

    cred_path = plugin_dir / "credentials.json"
    if cred_path.exists():
        print(f"  ✓ Exists {cred_path.relative_to(_REPO_ROOT)}  (skipped — already present)")
    else:
        cred_data: dict = {}
        for key in required:
            cred_data[key] = ""
        for key in optional:
            if key not in required:
                cred_data[key] = ""
        write_text_safe(
            cred_path,
            json.dumps(cred_data, indent=2) + "\n",
            create_parents=False,
        )
        print(f"  + Created {cred_path.relative_to(_REPO_ROOT)}")


def scaffold_credentials(
    plugin_id: str | None = None,
    all_plugins: bool = False,
) -> None:
    """Generate credentials.json (if missing) and credentials_example.json for plugins.

    For each plugin, writes:
    - ``credentials_example.json`` — safe template with placeholder values (always written)
    - ``credentials.json``          — gitignored dev override (only written if absent)

    Args:
        plugin_id:   ID of a single plugin to scaffold.  Pass None with
                     ``all_plugins=True`` to scaffold every discovered plugin.
        all_plugins: When True, scaffold all plugins under ``plugins/``.
    """
    plugins = _discover_all_plugins()
    if not plugins:
        print("ERROR: No plugins found under plugins/*/manifest.json.", file=sys.stderr)
        sys.exit(1)

    targets = plugins if all_plugins else [
        p for p in plugins if p["id"] == plugin_id
    ]

    if not targets:
        available = ", ".join(p["id"] for p in plugins)
        print(f"ERROR: plugin '{plugin_id}' not found. Available: {available}", file=sys.stderr)
        sys.exit(1)

    failures = 0
    for plugin in targets:
        try:
            _scaffold_single_plugin_credentials(plugin)
        except WriteError as exc:
            print(f"  ✗ {plugin['id']}: {exc}", file=sys.stderr)
            failures += 1

    if failures:
        print(f"\nERROR: {failures} plugin(s) failed to scaffold.", file=sys.stderr)
        sys.exit(1)

    print(f"\nDone. {len(targets)} plugin(s) scaffolded.")
    print("Edit credentials.json with real values; never commit it — it is gitignored.")


# ---------------------------------------------------------------------------
# Migration scaffolding (delegates to migration_generator)
# ---------------------------------------------------------------------------

def _run_gen_migration(args: "argparse.Namespace") -> None:
    """Delegate --gen-migration to migration_generator.generate_migration + write.

    Uses the same PluginSchemaMigrator-compatible upgrade(conn) pattern as
    plugins/my_plugin/migrations/*.py.
    """
    from migration_generator import (
        generate_migration,
        _next_revision,
        _migrations_dir,
        _resolve_output_path,
        _MIGRATION_TYPES,
    )

    plugin_id: str = getattr(args, "plugin_id", "") or ""
    mig_name: str = getattr(args, "migration_name", "") or ""
    mig_type: str = getattr(args, "migration_type", "blank") or "blank"
    mig_table: str = getattr(args, "migration_table", "") or ""
    mig_columns: list[str] = list(getattr(args, "migration_columns", []) or [])
    mig_desc: str = getattr(args, "migration_description", "") or ""
    mig_revision: str = getattr(args, "migration_revision", "") or ""
    dry_run: bool = bool(getattr(args, "dry_run", False))

    if not plugin_id:
        print("ERROR: --plugin-id is required for --gen-migration.", file=sys.stderr)
        sys.exit(1)
    if not mig_name:
        print("ERROR: --migration-name is required for --gen-migration.", file=sys.stderr)
        sys.exit(1)
    if mig_type not in _MIGRATION_TYPES:
        print(
            f"ERROR: --migration-type must be one of: {', '.join(_MIGRATION_TYPES)}",
            file=sys.stderr,
        )
        sys.exit(1)

    mig_dir = _migrations_dir(plugin_id)
    revision = mig_revision if (mig_revision and len(mig_revision) == 4 and mig_revision.isdigit()) \
        else _next_revision(mig_dir)

    code = generate_migration(
        plugin_id=plugin_id,
        name=mig_name,
        migration_type=mig_type,
        table=mig_table,
        columns=mig_columns,
        description=mig_desc,
        revision=revision,
    )

    if dry_run:
        print(f"# [dry-run] Would write: {_resolve_output_path(plugin_id, revision, mig_name)}")
        print(code)
        return

    out_path = _resolve_output_path(plugin_id, revision, mig_name)
    try:
        write_text_safe(out_path, code)
        rel = out_path.relative_to(_REPO_ROOT) if out_path.is_absolute() else out_path
        print(f"✓ Migration generated → {rel}")
    except WriteError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    """CLI entry: generate an MCP tool module from YAML, JSON, or an interactive spec."""
    parser = argparse.ArgumentParser(
        description=(
            "Generate Whiskers Agent tool modules.\n\n"
            "YAML/JSON config mode (--config):  hand-authored tool specs → Python module.\n"
            "JSON schema mode (--from-json / --from-dir):  mcp-tools.json files → static\n"
            "  Python module that mirrors the dynamic_tools_loader.py runtime patterns."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--config", help="Path to a YAML or JSON tool config file.")
    group.add_argument("--interactive", action="store_true", help="Interactive tool builder.")
    group.add_argument("--example", action="store_true", help="Print an example YAML config.")
    group.add_argument(
        "--from-json",
        metavar="FILE",
        help="Path to a single mcp-tools.json file (array of tool definitions).",
    )
    group.add_argument(
        "--from-dir",
        metavar="DIR",
        help=(
            "Path to a mcp-tools-context directory.  Walks the folder tree "
            "(domain/<op>/mcp-tools.json) and generates one module for all matching tools."
        ),
    )
    group.add_argument(
        "--scaffold-credentials",
        action="store_true",
        dest="scaffold_credentials",
        help=(
            "Generate credentials.json (if absent) and credentials_example.json for "
            "one plugin (--plugin-id) or all plugins (--all-plugins)."
        ),
    )
    group.add_argument(
        "--gen-migration",
        action="store_true",
        dest="gen_migration",
        help=(
            "Scaffold a plugin-local migration file (upgrade(conn) pattern). "
            "Requires --plugin-id and --migration-name. "
            "Output: plugins/<plugin>/migrations/NNNN_<name>.py — "
            "compatible with PluginSchemaMigrator (db_layer/plugin_schema_migrator.py)."
        ),
    )

    parser.add_argument("--output", "-o", help="Output file path (default: stdout).")
    parser.add_argument(
        "--all-plugins",
        action="store_true",
        dest="all_plugins",
        help="(--scaffold-credentials only) Scaffold credentials for every discovered plugin.",
    )
    parser.add_argument(
        "--schema", action="store_true",
        help="Also print TOOL_SCHEMAS entries for the LangGraph agent.",
    )
    parser.add_argument(
        "--legacy-imports", action="store_true",
        help="Use relative imports (for legacy MCPTools/ outside plugin structure).",
    )
    # JSON-mode filters
    parser.add_argument(
        "--domains",
        metavar="D1,D2",
        help="(--from-dir only) Comma-separated domain folder names to include.",
    )
    parser.add_argument(
        "--ops",
        metavar="read,write_update",
        help="(--from-dir only) Comma-separated op types to include (read, write_update, delete).",
    )
    parser.add_argument(
        "--module-doc",
        metavar="TEXT",
        default="Auto-generated MCP tools (from mcp-tools.json).",
        help="Module docstring for --from-json / --from-dir output.",
    )
    parser.add_argument(
        "--plugin-id",
        help="The ID of the plugin these tools belong to (e.g., portfolio_plugin, jules_plugin).",
    )
    # --gen-migration arguments
    parser.add_argument(
        "--migration-name", metavar="NAME",
        help="(--gen-migration) Migration name in snake_case (e.g. add_records).",
    )
    parser.add_argument(
        "--migration-type", metavar="TYPE", dest="migration_type",
        default="blank",
        help=(
            "(--gen-migration) Type of migration. "
            "One of: create_table, add_column, drop_column, add_index, "
            "create_table_if_not_exists, blank. Default: blank."
        ),
    )
    parser.add_argument(
        "--migration-table", metavar="TABLE", dest="migration_table", default="",
        help="(--gen-migration) Target table name.",
    )
    parser.add_argument(
        "--migration-columns", metavar="COL", dest="migration_columns",
        nargs="+", default=[],
        help='(--gen-migration) Column definitions, e.g. "id BIGSERIAL PRIMARY KEY".'
    )
    parser.add_argument(
        "--migration-description", metavar="TEXT", dest="migration_description", default="",
        help="(--gen-migration) One-line description for the migration docstring.",
    )
    parser.add_argument(
        "--migration-revision", metavar="NNNN", dest="migration_revision", default="",
        help="(--gen-migration) Explicit 4-digit revision (auto-derived if omitted).",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="(--gen-migration) Print to stdout without writing to disk.",
    )

    args = parser.parse_args()
    legacy = getattr(args, "legacy_imports", False)
    plugin_id = args.plugin_id

    # ── scaffold-credentials ──────────────────────────────────────────────────
    if getattr(args, "scaffold_credentials", False):
        scaffold_credentials(
            plugin_id=plugin_id,
            all_plugins=getattr(args, "all_plugins", False),
        )
        return

    # ── gen-migration ─────────────────────────────────────────────────────────
    if getattr(args, "gen_migration", False):
        _run_gen_migration(args)
        return

    if not plugin_id and args.output:
        out_path = str(Path(args.output).resolve())
        for _pid in (
            "cat_terminal_relay_plugin", "portfolio_plugin",
            "jules_plugin", "search_plugin", "job_search_plugin",
        ):
            if _pid in out_path:
                plugin_id = _pid
                break

    # ── example ──────────────────────────────────────────────────────────────
    if args.example:
        print(_EXAMPLE_CONFIG)
        return

    # ── interactive ───────────────────────────────────────────────────────────
    if args.interactive:
        code = interactive_mode()
        emit_generated(code, args.output)
        return

    # ── YAML/JSON config mode ─────────────────────────────────────────────────
    if args.config:
        config = load_config(args.config)
        code = generate_module(config, legacy_imports=legacy)
        emit_generated(code, args.output)
        if args.schema:
            print("\n" + "=" * 60)
            print(generate_all_schema_entries(config))
        return

    # ── mcp-tools.json mode ───────────────────────────────────────────────────
    tool_defs_with_meta: list[tuple[dict, str, str]] = []

    if args.from_json:
        tool_defs_with_meta = load_json_tools_file(args.from_json)

    elif args.from_dir:
        domains = [d.strip() for d in args.domains.split(",")] if args.domains else None
        ops = [o.strip() for o in args.ops.split(",")] if args.ops else None
        tool_defs_with_meta = scan_json_tools_dir(args.from_dir, domains=domains, ops=ops)

    if not tool_defs_with_meta:
        print("ERROR: no tools found — check your input path/filters.", file=sys.stderr)
        sys.exit(1)

    code = generate_module_from_json_tools(
        tool_defs_with_meta,
        module_doc=args.module_doc,
        legacy_imports=legacy,
        plugin_id=plugin_id,
    )

    if args.output:
        try:
            out = write_text_safe(args.output, code)
        except WriteError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            sys.exit(1)
        print(f"Generated {len(tool_defs_with_meta)} tool(s) → {out}")
    else:
        print(code)

    if args.schema:
        print("\n" + "=" * 60)
        print(generate_json_schema_entries(tool_defs_with_meta))


if __name__ == "__main__":
    main()
