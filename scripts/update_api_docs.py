#!/usr/bin/env python3
"""Script to automatically scan API files for route decorators and update their top-level module docstrings."""

import ast
import re
import sys
from pathlib import Path

# Mirror AuthPolicy.value without importing the package (script is host-runnable).
_AUTH_POLICY_VALUES = {
    "PUBLIC": "public",
    "SESSION_GATED": "session_gated",
    "SCOPE_REQUIRED": "scope_required",
    "NONE": "none",
}


def _const_str(node: ast.AST | None) -> str | None:
    """Return a string Constant value, else None."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _auth_policy_gate(node: ast.AST | None) -> str | None:
    """Resolve AuthPolicy.X or a bare gate string constant to its path segment."""
    if node is None:
        return None
    gate = _const_str(node)
    if gate is not None:
        return gate
    # AuthPolicy.PUBLIC / AuthPolicy.SESSION_GATED
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
        if node.value.id in ("AuthPolicy", "auth_policy") or node.attr in _AUTH_POLICY_VALUES:
            return _AUTH_POLICY_VALUES.get(node.attr)
    return None


def _compose_api_path(route: str, gate: str, endpoint: str = "") -> str:
    """Compose /api/{route}/{gate}/{endpoint} (same rules as build_api_path)."""
    base = f"/api/{route.strip('/')}/{gate}"
    ep = (endpoint or "").strip("/")
    return f"{base}/{ep}" if ep else base


def _resolve_decorator_path(decorator: ast.Call) -> str:
    """Resolve free-form path= or structured route=/endpoint=/auth_policy= to a URL path."""
    path = ""
    route_name = None
    endpoint = ""
    gate = None

    if decorator.args:
        first = _const_str(decorator.args[0])
        if first is not None:
            path = first

    for kw in decorator.keywords:
        if kw.arg == "path":
            val = _const_str(kw.value)
            if val is not None:
                path = val
        elif kw.arg == "route":
            route_name = _const_str(kw.value)
        elif kw.arg == "endpoint":
            val = _const_str(kw.value)
            if val is not None:
                endpoint = val
        elif kw.arg == "auth_policy":
            gate = _auth_policy_gate(kw.value)

    # Structured form wins when route= is present (matches HttpRouteRegistry.route).
    if route_name is not None:
        if not gate:
            gate = "none"  # decorator default AuthPolicy.NONE
        return _compose_api_path(route_name, gate, endpoint)
    return path


def extract_routes(file_path):
    """Scan a Python file for http_route_registry.route decorators and extract endpoint info.
    
    Returns a list of tuples: (method, path, description) and the AST tree / source code.
    """
    with open(file_path, "r", encoding="utf-8") as f:
        source = f.read()
    
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        print(f"Syntax error parsing {file_path}: {e}", file=sys.stderr)
        return None, None, None
        
    routes = []
    
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for decorator in node.decorator_list:
                # Check for @http_route_registry.route(...)
                if not isinstance(decorator, ast.Call):
                    continue
                
                func = decorator.func
                is_route_decorator = False
                if isinstance(func, ast.Attribute):
                    if isinstance(func.value, ast.Name) and func.value.id == "http_route_registry" and func.attr == "route":
                        is_route_decorator = True
                
                if not is_route_decorator:
                    continue
                
                path = _resolve_decorator_path(decorator)
                if not path:
                    print(
                        f"  warn: {file_path.name}:{node.name} has undecorated/empty path; skipped",
                        file=sys.stderr,
                    )
                    continue

                # Extract methods (usually keyword arg 'methods')
                methods = ["GET"]
                for kw in decorator.keywords:
                    if kw.arg == "methods":
                        if isinstance(kw.value, ast.List):
                            methods = []
                            for elt in kw.value.elts:
                                if isinstance(elt, ast.Constant):
                                    methods.append(str(elt.value))
                        elif isinstance(kw.value, ast.Tuple):
                            methods = []
                            for elt in kw.value.elts:
                                if isinstance(elt, ast.Constant):
                                    methods.append(str(elt.value))
                                    
                # Extract first line of function docstring
                docstring = ast.get_docstring(node)
                desc = ""
                if docstring:
                    desc = docstring.split("\n")[0].strip()
                    
                for method in methods:
                    routes.append((method, path, desc))
                    
    # Sort routes by path, then method
    routes.sort(key=lambda r: (r[1], r[0]))
    return routes, tree, source


ROUTE_LINE_RE = re.compile(
    r'^\s*(?:-\s*)?(?:GET|POST|PUT|DELETE|PATCH)\s+/[a-zA-Z0-9_/{}?&=:.-]+', 
    re.IGNORECASE
)

def clean_old_route_descriptions(docstring):
    """Scrub lines that match manual route descriptions like '- GET /admin/exists'."""
    if not docstring:
        return ""
    lines = docstring.split("\n")
    cleaned_lines = []
    for line in lines:
        if ROUTE_LINE_RE.match(line):
            continue
        cleaned_lines.append(line)
    return "\n".join(cleaned_lines).rstrip()

def update_module_docstring(file_path, routes, tree, source):
    """Replace or append the Endpoints section in the module's docstring."""
    has_docstring = False
    start_line, end_line = None, None
    existing_docstring = ""
    
    if tree.body and isinstance(tree.body[0], ast.Expr):
        first_val = tree.body[0].value
        if isinstance(first_val, ast.Constant):
            has_docstring = True
            existing_docstring = first_val.value
            start_line = tree.body[0].lineno
            end_line = tree.body[0].end_lineno
            
    # Determine base docstring
    if has_docstring:
        # Scrub any existing manual route lists before looking for Endpoints headers
        existing_docstring = clean_old_route_descriptions(existing_docstring)
        lines = existing_docstring.split("\n")
        header_idx = -1
        for i, line in enumerate(lines):
            clean = line.strip().lower()
            if clean in ("endpoints", "routes") and i + 1 < len(lines) and lines[i+1].strip().startswith(("-", "=")):
                header_idx = i
                break
            elif clean.startswith("endpoints:") or clean.startswith("routes:"):
                header_idx = i
                break
                
        if header_idx != -1:
            base_docstring = "\n".join(lines[:header_idx]).rstrip()
        else:
            base_docstring = existing_docstring.rstrip()
    else:
        module_name = Path(file_path).stem.replace("_", " ").title()
        base_docstring = f"{module_name} REST API."

    # Build Endpoints section
    endpoints_lines = ["", "Endpoints", "---------"]
    if routes:
        pad = max(len(path) for _, path, _ in routes) + 2
        pad = max(30, min(80, pad))  # Cap padding
        for method, path, desc in routes:
            endpoints_lines.append(f"{method:<6} {path:<{pad}} — {desc}")
    else:
        endpoints_lines.append("No endpoints registered.")
        
    new_docstring_content = base_docstring + "\n" + "\n".join(endpoints_lines) + "\n"
    formatted_docstring = f'"""\n{new_docstring_content}"""'
    
    # Replace in source
    source_lines = source.splitlines()
    if has_docstring:
        source_lines[start_line - 1 : end_line] = [formatted_docstring]
    else:
        source_lines.insert(0, formatted_docstring)
        
    updated_source = "\n".join(source_lines) + "\n"
    
    with open(file_path, "w", encoding="utf-8", newline="") as f:
        f.write(updated_source)
    print(f"Successfully updated docstring in: {file_path}")

def main():
    """
    Main entrypoint for updating API documentation.
    """
    # Target path can be passed as argument, default to repository api/ directory
    repo_root = Path(__file__).parent.parent
    target_dir = repo_root / "api"
    
    if len(sys.argv) > 1:
        target_path = Path(sys.argv[1])
        if target_path.is_file():
            files_to_process = [target_path]
        elif target_path.is_dir():
            files_to_process = list(target_path.glob("*.py"))
        else:
            print(f"Error: path '{target_path}' not found.", file=sys.stderr)
            sys.exit(1)
    else:
        files_to_process = list(target_dir.glob("*.py"))
        
    for file_path in files_to_process:
        if file_path.name == "__init__.py" or file_path.name == "middleware.py":
            continue
            
        routes, tree, source = extract_routes(file_path)
        if routes is None:
            continue
            
        # Only update files that have at least one registered route
        if routes:
            update_module_docstring(file_path, routes, tree, source)
        else:
            print(f"Skipping {file_path.name}: No HTTP routes found.")

if __name__ == "__main__":
    main()
