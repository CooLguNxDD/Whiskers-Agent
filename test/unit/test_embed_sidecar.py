"""Contract test for the ``embed`` llama.cpp sidecar in docker-compose.yml.

Pure file parsing: no docker, no network.
"""
import re
import shlex
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
MIN_BUILD = 11457  # gemma-embedding2 arch (llama.cpp PR #30054)


@pytest.fixture(scope="module")
def compose() -> dict:
    return yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def embed(compose) -> dict:
    svc = compose["services"].get("embed")
    assert svc, "docker-compose.yml has no `embed` service"
    return svc


def _command(svc: dict) -> str:
    cmd = svc.get("command", "")
    return " ".join(str(c) for c in cmd) if isinstance(cmd, list) else str(cmd)


def _flag(cmd: str, name: str) -> str | None:
    """Value of ``--name VALUE`` or ``--name=VALUE`` in a command string."""
    m = re.search(rf"--{re.escape(name)}(?:=|\s+)(\S+)", cmd)
    return m.group(1) if m else None


def test_image_pinned_to_fixed_cuda_build(embed):
    m = re.fullmatch(r"ghcr\.io/ggml-org/llama\.cpp:server-cuda-b(\d+)", str(embed["image"]))
    assert m, f"image must be ghcr.io/ggml-org/llama.cpp:server-cuda-b<N>, got {embed['image']}"
    assert int(m.group(1)) >= MIN_BUILD
    assert "latest" not in embed["image"]


def test_model_precision_guard_rejects_fp16_but_allows_bf16_and_q8():
    precision_guard = re.compile(r"(?<!b)f(?:p)?16", re.I)
    for precision in ("FP16", "F16", "fp16"):
        assert precision_guard.search(precision), f"{precision} must be rejected"
    for precision in ("BF16", "Q8_0"):
        assert not precision_guard.search(precision), f"{precision} must be allowed"


def test_model_is_embeddinggemma2_q8_with_matching_mmproj(embed):
    cmd = _command(embed)
    assert "ggml-org/embeddinggemma-2-GGUF" in cmd
    assert "embeddinggemma-2" in cmd
    assert re.search(r"q8_0|bf16", cmd, re.I), "quant must be Q8_0 or BF16"
    assert not re.search(r"(?<!b)f(?:p)?16", cmd, re.I), "FP16 weights are not allowed"

    args = shlex.split(cmd)
    assert "--no-mmproj" not in args
    assert "--no-mmproj-auto" not in args
    repo = _flag(cmd, "hf-repo")
    projector = _flag(cmd, "mmproj")
    assert "--mmproj-auto" in args and repo and repo.startswith(
        "ggml-org/embeddinggemma-2-GGUF"
    ) or (
        projector
        and re.fullmatch(r"mmproj-embeddinggemma-2.*\.gguf", Path(projector).name, re.I)
    ), "a matching embeddinggemma-2 projector must be configured"


def test_embedding_flags(embed):
    cmd = _command(embed)
    assert "--embeddings" in shlex.split(cmd)
    assert _flag(cmd, "pooling") == "mean"
    assert _flag(cmd, "host") == "0.0.0.0"
    assert _flag(cmd, "port") == "8080"


def test_ctx_batch_ubatch_all_4096(embed):
    cmd = _command(embed)
    values = {n: _flag(cmd, n) for n in ("ctx-size", "batch-size", "ubatch-size")}
    assert values == {"ctx-size": "4096", "batch-size": "4096", "ubatch-size": "4096"}


def test_healthcheck_hits_health(embed):
    test = embed["healthcheck"]["test"]
    test = " ".join(test) if isinstance(test, list) else str(test)
    assert "http://localhost:8080/health" in test
    assert "curl" in test or "wget" in test


def test_nvidia_gpu_reservation(embed):
    devices = embed["deploy"]["resources"]["reservations"]["devices"]
    nvidia = [d for d in devices if d.get("driver") == "nvidia"]
    assert nvidia
    assert "gpu" in nvidia[0]["capabilities"]


def test_internal_network_only_and_no_public_port(embed):
    nets = embed.get("networks") or []
    nets = list(nets.keys()) if isinstance(nets, dict) else list(nets)
    assert nets == ["whiskers-network"]
    assert not embed.get("ports"), "embed must not publish a host port by default"


def test_restart_policy(embed):
    assert embed["restart"] == "unless-stopped"


def test_weights_live_in_declared_named_volume(compose, embed):
    cache_env = (embed.get("environment") or {})
    if isinstance(cache_env, list):
        cache_env = dict(e.split("=", 1) for e in cache_env)
    cache_dir = cache_env.get("LLAMA_CACHE")
    assert cache_dir, "LLAMA_CACHE must point the HF download cache at the volume"
    mounts = {}
    for v in embed.get("volumes") or []:
        src, dst = str(v).split(":")[:2]
        mounts[dst] = src
    assert cache_dir in mounts, "LLAMA_CACHE dir is not a mounted volume"
    src = mounts[cache_dir]
    assert not src.startswith((".", "/", "~")), f"weights must not be bind-mounted: {src}"
    assert src in compose["volumes"], f"named volume {src} not declared top-level"


def test_agent_does_not_hard_depend_on_embed(compose):
    deps = compose["services"]["whiskers-agent"].get("depends_on") or {}
    if isinstance(deps, list):
        assert "embed" not in deps
    elif "embed" in deps:
        assert deps["embed"].get("required") is False


def test_env_example_documents_sidecar_settings():
    text = (ROOT / ".env.example").read_text(encoding="utf-8")
    active = {}
    for line in text.splitlines():
        if line.strip() and not line.lstrip().startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            active[k.strip()] = v.strip()
    assert active.get("EMBED_PROVIDER") == "gemma-multimodal"
    assert active.get("EMBED_BASE_URL") == "http://embed:8080/v1"
    assert active.get("EMBED_MODEL") == "embeddinggemma-2"
    assert active.get("EMBED_DIMENSIONS") == "768"
    # LM Studio legacy text-only alternative stays documented, commented out.
    assert re.search(r"^#\s*EMBED_PROVIDER=openai", text, re.M)
    assert "text-embedding-qwen3-embedding-0.6b" in text
    assert "host.docker.internal:1234/v1" in text


def test_env_example_has_no_secret_values():
    text = (ROOT / ".env.example").read_text(encoding="utf-8")
    for line in text.splitlines():
        if re.match(r"\s*\w*(KEY|TOKEN|SECRET|PASSWORD)\w*=\S", line):
            pytest.fail(f"secret-like value in .env.example: {line}")
