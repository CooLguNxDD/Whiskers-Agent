#!/usr/bin/env python3
"""Live smoke test for the llama.cpp `embed` sidecar (embeddinggemma-2) via the Whiskers client.

Usage:
    python scripts/smoke_embed_multimodal.py [--write-docs]

Env: EMBED_BASE_URL (default http://127.0.0.1:8080/v1), EMBED_MODEL (default embeddinggemma-2),
EMBED_DIMENSIONS (default 768), EMBED_API_KEY (optional).

Exit codes: 0 no check FAILED (audio may be BLOCKED), 1 a check FAILED, 2 server unreachable.
Never run from unit tests: it needs the live server. `--write-docs` writes
docs/embed-smoke-results.md (UTC timestamp, server build, per-check PASS/FAIL/BLOCKED).
"""

from __future__ import annotations

import argparse
import asyncio
import io
import math
import os
import re
import struct
import sys
import wave
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
# The SSRF-safe transport blocks private/loopback hosts unless the operator opts in; the sidecar is local.
os.environ.setdefault("CAT_ALLOW_LOCAL_PROXIES", "1")

import httpx  # noqa: E402

TOLERANCE = 1e-2
PASS, FAIL, BLOCKED = "PASS", "FAIL", "BLOCKED"


def make_png(color: tuple[int, int, int]) -> bytes:
    """Solid-color 32x32 PNG."""
    from PIL import Image
    stream = io.BytesIO()
    Image.new("RGB", (32, 32), color).save(stream, format="PNG")
    return stream.getvalue()


def make_wav(freq: float = 440.0, seconds: float = 0.5, rate: int = 16000) -> bytes:
    """Short 16 kHz mono 16-bit PCM sine tone."""
    stream = io.BytesIO()
    with wave.open(stream, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(rate)
        audio.writeframes(b"".join(
            struct.pack("<h", int(12000 * math.sin(2 * math.pi * freq * i / rate)))
            for i in range(int(rate * seconds))))
    return stream.getvalue()


def norm(vector: list[float]) -> float:
    return math.sqrt(math.fsum(v * v for v in vector))


def cosine(a: list[float], b: list[float]) -> float:
    return math.fsum(x * y for x, y in zip(a, b)) / (norm(a) * norm(b))


def sanitize(text: object, limit: int = 160) -> str:
    """Single-line, length-capped message with long base64-like runs and paths removed."""
    clean = re.sub(r"[A-Za-z0-9+/=]{40,}", "<data>", str(text))
    clean = re.sub(r"(?:[A-Za-z]:)?[\\/][\w .\\/-]{6,}", "<path>", clean)
    return " ".join(clean.split())[:limit]


def vector_problem(vector: list[float], width: int) -> str | None:
    """Return why a vector fails the width/finite/unit-norm checks, else None."""
    if len(vector) != width:
        return f"width {len(vector)} != {width}"
    if not all(math.isfinite(v) for v in vector):
        return "nonfinite values"
    if abs(norm(vector) - 1) > TOLERANCE:
        return f"L2 norm {norm(vector):.4f} not within {TOLERANCE} of 1"
    return None


def server_root(base_url: str) -> str:
    return base_url.rstrip("/").removesuffix("/v1")


async def probe_server(base_url: str) -> tuple[bool, str]:
    """Reachability via /health; returns (reachable, build info from /props or 'unknown')."""
    root = server_root(base_url)
    build = "unknown"
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=False) as http:
            await http.get(f"{root}/health")
            try:
                props = (await http.get(f"{root}/props")).json()
                build = sanitize(props.get("build_info") or props.get("build") or "unknown", 80)
            except Exception:
                pass
    except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError):
        return False, build
    return True, build


async def raw_error(base_url: str, api_key: str | None, body: dict) -> str:
    """Contract probe: POST the same body with raw httpx and return 'HTTP <status> <sanitized message>'."""
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    try:
        async with httpx.AsyncClient(timeout=60, follow_redirects=False) as http:
            response = await http.post(f"{base_url.rstrip('/')}/embeddings", json=body, headers=headers)
    except Exception as exc:
        return f"transport error ({type(exc).__name__})"
    if response.status_code == 200:
        return "HTTP 200"
    try:
        message = (response.json().get("error") or {}).get("message", "")
    except Exception:
        message = ""
    return f"HTTP {response.status_code} {sanitize(message)}".strip()


def audio_body(model: str, width: int, wav: bytes) -> dict:
    import base64
    return {"model": model, "dimensions": width, "encoding_format": "float", "input": [{"content": [
        {"type": "input_audio", "input_audio": {"data": base64.b64encode(wav).decode(), "format": "wav"}}]}]}


async def run_checks(base_url: str, model: str, width: int, api_key: str | None) -> list[tuple[str, str, str]]:
    from db_layer.embeddings.multimodal import (
        EmbeddingInput, EmbeddingMedia, GemmaMultimodalEmbeddings, MultimodalEmbeddingError)
    client = GemmaMultimodalEmbeddings(model, width, api_key, base_url, batch_size=4, max_concurrency=1)
    results: list[tuple[str, str, str]] = []
    red, blue = make_png((220, 20, 20)), make_png((20, 20, 220))
    png = lambda data: EmbeddingMedia(data, "image/png")  # noqa: E731

    async def embed(inputs: list[EmbeddingInput]) -> list[list[float]]:
        return await client.aembed_multimodal(inputs)

    async def shape_check(name: str, inputs: list[EmbeddingInput]) -> list[float] | None:
        try:
            [vector] = await embed(inputs)
        except MultimodalEmbeddingError as exc:
            results.append((name, FAIL, f"{exc.category} HTTP {exc.status}"))
            return None
        except Exception as exc:
            results.append((name, FAIL, sanitize(f"{type(exc).__name__}: {exc}")))
            return None
        problem = vector_problem(vector, width)
        results.append((name, FAIL if problem else PASS, problem or f"{width} finite values, norm {norm(vector):.4f}"))
        return None if problem else vector

    await shape_check("text", [EmbeddingInput(text="a stone bridge over a river")])
    await shape_check("image (PNG)", [EmbeddingInput(media=png(red))])
    await shape_check("joint text+image", [EmbeddingInput(text="a solid red square", media=png(red))])

    # Cross-modal sanity: a caption sits closer to its own image than to an unrelated one.
    try:
        caption, img_red, img_blue = await embed([
            EmbeddingInput(text="a solid red square"), EmbeddingInput(media=png(red)),
            EmbeddingInput(media=png(blue))])
        own, other = cosine(caption, img_red), cosine(caption, img_blue)
        results.append(("cross-modal caption vs images", PASS if own > other else FAIL,
                        f"cos(caption, red)={own:.4f} {'>' if own > other else '<='} cos(caption, blue)={other:.4f}"))
    except Exception as exc:
        results.append(("cross-modal caption vs images", FAIL, sanitize(f"{type(exc).__name__}: {exc}")))

    # Audio: PASS, or BLOCKED with the exact (sanitized) server answer; never a silent text fallback.
    wav_low, wav_high = make_wav(440.0), make_wav(880.0)
    try:
        low, high = await embed([EmbeddingInput(media=EmbeddingMedia(wav_low, "audio/wav")),
                                 EmbeddingInput(media=EmbeddingMedia(wav_high, "audio/wav"))])
    except Exception:
        results.append(("audio (WAV 16 kHz mono)", BLOCKED,
                        await raw_error(base_url, api_key, audio_body(model, width, wav_low))))
    else:
        problem = vector_problem(low, width) or vector_problem(high, width)
        if problem:
            results.append(("audio (WAV 16 kHz mono)", FAIL, problem))
        elif low == high:
            results.append(("audio (WAV 16 kHz mono)", FAIL, "440 Hz and 880 Hz tones embed identically (audio ignored)"))
        else:
            results.append(("audio (WAV 16 kHz mono)", PASS,
                            f"{width} finite values, norm {norm(low):.4f}; cos(440 Hz, 880 Hz)={cosine(low, high):.4f}"))
    return results


def render_markdown(base_url: str, model: str, build: str, results: list[tuple[str, str, str]]) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    lines = ["# Embed sidecar smoke results", "",
             f"- Run: {stamp}", f"- Endpoint: `{base_url}`", f"- Model alias: `{model}`",
             f"- Server build: {build}", "", "| Check | Result | Detail |", "| --- | --- | --- |"]
    lines += [f"| {name} | {status} | {detail.replace('|', '/')} |" for name, status, detail in results]
    lines += ["", "Generated by `scripts/smoke_embed_multimodal.py --write-docs`.", ""]
    return "\n".join(lines)


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Live smoke test for the embed sidecar.")
    parser.add_argument("--write-docs", action="store_true", help="write docs/embed-smoke-results.md")
    args = parser.parse_args(argv)
    base_url = os.environ.get("EMBED_BASE_URL") or "http://127.0.0.1:8080/v1"
    model = os.environ.get("EMBED_MODEL") or "embeddinggemma-2"
    width = int(os.environ.get("EMBED_DIMENSIONS") or 768)
    api_key = os.environ.get("EMBED_API_KEY") or None

    reachable, build = await probe_server(base_url)
    if not reachable:
        print(f"embed server unreachable at {server_root(base_url)} "
              "(start it with `docker compose up -d embed`, or set EMBED_BASE_URL)", file=sys.stderr)
        return 2
    results = await run_checks(base_url, model, width, api_key)
    for name, status, detail in results:
        print(f"{status:7} {name}: {detail}")
    if args.write_docs:
        target = ROOT / "docs" / "embed-smoke-results.md"
        target.write_text(render_markdown(base_url, model, build, results), encoding="utf-8")
        print(f"wrote {target.relative_to(ROOT)}")
    return 1 if any(status == FAIL for _, status, _ in results) else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
