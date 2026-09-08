#!/usr/bin/env python3
"""
merge_batches.py
================
Merges all openapi/AItemp/enriched_batch_*.json files into
openapi/enriched-manifest.json.

Usage:
    python openapi/merge_batches.py [--in-dir openapi/AItemp] [--out openapi/enriched-manifest.json]
"""
import argparse, json
from pathlib import Path

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--in-dir", default="openapi/AItemp")
    p.add_argument("--out", default="openapi/enriched-manifest.json")
    args = p.parse_args()

    in_dir = Path(args.in_dir)
    files = sorted(in_dir.glob("enriched_batch_*.json"))

    if not files:
        print(f"No enriched_batch_*.json found in {in_dir}")
        return

    merged = []
    for f in files:
        entries = json.loads(f.read_text(encoding="utf-8"))
        merged.extend(entries)
        print(f"  {f.name}  {len(entries)} routes")

    Path(args.out).write_text(json.dumps(merged, indent=2), encoding="utf-8")
    enriched = sum(1 for r in merged if r.get("openapi_path_item"))
    print(f"\nWritten: {args.out}")
    print(f"  Total:    {len(merged)}")
    print(f"  Enriched: {enriched}")
    print(f"  Skeleton: {len(merged) - enriched}")

if __name__ == "__main__":
    main()
