#!/usr/bin/env python3
import argparse
import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

# --- Constants & Simple Helpers ---
METHOD_TO_OP = {"GET": "read", "DELETE": "delete", "POST": "write_update", "PUT": "write_update", "PATCH": "write_update"}
IGNORED_SEGMENTS = {"api"}
PATH_PARAM_RE = re.compile(r"^[:{].*[}]?$")
VERSION_RE = re.compile(r"^v\d+$", re.IGNORECASE)

def slugify(v: str) -> str:
    if not v: return ""
    v = re.sub(r"([a-z0-9])([A-Z])", r"\1-\2", v)
    return re.sub(r"[^a-zA-Z0-9-]+", "-", v.replace("_", "-").replace(" ", "-")).strip("-").lower()

# --- Core Logic ---
class ToolRouter:
    def __init__(self, config: dict):
        self.config = config
        self.aliases = config.get("section_classification", {}).get("section_aliases", {})
        self.ignored = set(config.get("section_classification", {}).get("ignored_path_segments", IGNORED_SEGMENTS))
        # Reverse lookup: op_id -> (section, op_type)
        self.lookup = {
            op_id: (sec, op_type)
            for sec, ops in config.get("sections", {}).items()
            for op_type, ids in ops.items()
            for op_id in ids
        }

    def resolve_section(self, tool: dict) -> str:
        meta = tool.get("_meta", {})
        # Candidates in order of priority
        candidates = [meta.get("domain"), *meta.get("tags", [])]
        path_segs = [slugify(s) for s in meta.get("path", "").split("/") if s and not PATH_PARAM_RE.match(s)]
        candidates.extend([s for s in path_segs if s and s not in self.ignored and not VERSION_RE.match(s)])
        candidates.extend([meta.get("operationId"), tool.get("name")])

        for cand in filter(None, map(slugify, candidates)):
            # Check alias or singular version
            resolved = self.aliases.get(cand) or self.aliases.get(cand[:-1] if cand.endswith("s") else "")
            if resolved: return resolved
            return cand # Return first valid slugified candidate
        return "uncategorized"

    def route(self, tool: dict) -> tuple[str, str]:
        op_id = tool.get("_meta", {}).get("operationId") or tool.get("name", "unknown")
        if op_id in self.lookup:
            return self.lookup[op_id]
        
        section = self.resolve_section(tool)
        op_type = METHOD_TO_OP.get(tool.get("_meta", {}).get("method", "GET").upper(), "write_update")
        return section, op_type

# --- Action Functions ---
def build_initial_config(tools: list[dict]) -> dict:
    router = ToolRouter({"section_classification": {"ignored_path_segments": list(IGNORED_SEGMENTS)}})
    sections = defaultdict(lambda: defaultdict(list))
    
    for t in tools:
        sec, op_t = router.route(t)
        sections[sec][op_t].append(t.get("_meta", {}).get("operationId") or t.get("name"))

    return {
        "generated": datetime.now(timezone.utc).isoformat(),
        "section_classification": {"strategy": "dynamic", "section_aliases": {}, "ignored_path_segments": list(IGNORED_SEGMENTS)},
        "sections": {s: dict(o) for s, o in sorted(sections.items())}
    }

def process_pipeline(tools: list[dict], config: dict, out_dir: Path):
    router = ToolRouter(config)
    buckets = defaultdict(list)
    structure = defaultdict(dict)

    for t in tools:
        sec, op_t = router.route(t)
        buckets[(sec, op_t)].append(t)

    for (sec, op_t), t_list in sorted(buckets.items()):
        dest = out_dir / sec / op_t / "mcp-tools.json"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps(t_list, indent=2))
        
        structure[sec][op_t] = {
            "file": f"{sec}/{op_t}/mcp-tools.json",
            "tool_count": len(t_list),
            "tools": [tk.get("_meta", {}).get("operationId") or tk.get("name") for tk in t_list]
        }
        print(f"  Wrote {len(t_list):3d} tools -> {sec}/{op_t}")

    (out_dir / "mcp-tools-index.json").write_text(json.dumps({"total_tools": len(tools), "structure": structure}, indent=2))

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", "-i", required=True, help="Path to input mcp-tools-ai.json")
    parser.add_argument("--out-dir", "-o", default="mcp-tools-context", help="Output directory")
    parser.add_argument("--config", "-c", help="Path to config file (default: out-dir/mcp-tools-config.json)")
    parser.add_argument("--build-config", action="store_true")
    args = parser.parse_args()

    t_path = Path(args.input).resolve()
    out_dir = Path(args.out_dir).resolve()
    c_path = Path(args.config).resolve() if args.config else out_dir / "mcp-tools-config.json"

    if not t_path.exists(): raise SystemExit(f"Missing {t_path}")
    tools = json.loads(t_path.read_text())

    if args.build_config or not c_path.exists():
        config = build_initial_config(tools)
        c_path.write_text(json.dumps(config, indent=2))
        print(f"Generated new config: {c_path}")
    else:
        config = json.loads(c_path.read_text())

    process_pipeline(tools, config, out_dir)

if __name__ == "__main__":
    main()