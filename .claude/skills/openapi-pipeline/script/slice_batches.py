#!/usr/bin/env python3
"""
slice_batches.py
================
Splits route-manifest.json into batch files of BATCH_SIZE routes each.
Output: openapi/AItemp/batch_NN.json

Usage:
    python openapi/slice_batches.py [--batch-size 30] [--out-dir openapi/AItemp]
"""
import argparse, json, math
from pathlib import Path

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--batch-size", type=int, default=30)
    p.add_argument("--manifest", default="openapi/route-manifest.json")
    p.add_argument("--out-dir", default="openapi/AItemp")
    args = p.parse_args()

    manifest = json.loads(Path(args.manifest).read_text())
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    total = len(manifest)
    n_batches = math.ceil(total / args.batch_size)
    for i in range(n_batches):
        chunk = manifest[i * args.batch_size : (i + 1) * args.batch_size]
        out_path = out / f"batch_{i:02d}.json"
        out_path.write_text(json.dumps(chunk, indent=2), encoding="utf-8")
        start = i * args.batch_size
        print(f"  batch_{i:02d}.json  routes {start}-{start+len(chunk)-1}  ({len(chunk)} routes)")

    print(f"\nTotal: {n_batches} batches, {total} routes in {out}")

if __name__ == "__main__":
    main()
