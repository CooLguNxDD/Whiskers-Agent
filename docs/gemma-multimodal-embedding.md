# Configured multimodal embeddings through LiteLLM

## What this client does (and does not establish)

OpenCat now has an **opt-in, async-only** `gemma-multimodal` registry provider
for a LiteLLM gateway route implementing the **content-parts adapter v1** contract
below. It sends actual text, PNG/JPEG bytes and PCM WAV bytes, including text and
one reference media item as a single document/vector. It does not caption media,
transcribe it, stringify bytes, or substitute a text model.

`gemma-embedding-2-multimodal` is a **configurable gateway alias**, not a verified
upstream checkpoint name. In particular, `embeddinggemma:300m` (the existing
`gemma` example profile) is **text-only**, not an image/audio model. Do not confuse
EmbeddingGemma with Google's separately named Gemini Embedding models. No
multimodal Gemma checkpoint, local serving runtime, installed LiteLLM version or
live route has been verified by this change. If the intended model cannot embed
images and audio jointly with text, serving that model is a blocker, not a reason
to silently select another model. Record the actual model identity before indexing.

An OpenAI-compatible base URL by itself proves nothing about multimodal
`/embeddings`: the ordinary OpenAI API and typical Ollama EmbeddingGemma routes
accept text/token inputs, not these documents. **This is an explicit adapter
extension, not a claim of universal stock LiteLLM compatibility.** Mocked tests
verify the wire contract, not gateway or model deployment support.

## Compose `embed` sidecar (llama.cpp, embeddinggemma-2)

`docker-compose.yml` defines an `embed` service that serves
`ggml-org/embeddinggemma-2-GGUF` (text + image + audio, 768-wide vectors) through
llama.cpp's `llama-server` on an OpenAI-compatible `/v1/embeddings`. It is the
reference deployment of the content-parts adapter below; no LiteLLM adapter is
needed in front of it. `test/unit/test_embed_sidecar.py` parses the compose file
and pins every requirement in this section (no docker, no network).

**Start** (NVIDIA GPU and the container toolkit required):

```bash
docker compose up -d embed        # first start downloads weights, can take minutes
docker compose ps embed           # wait for (healthy): GET /health
```

Weights are fetched on first start into the named volume `embed_models`
(`LLAMA_CACHE=/models`). They are never bind-mounted from the repo and never
committed. `docker compose down -v` deletes the volume (and Postgres data: see
CLAUDE.md), forcing a re-download.

**Pinned runtime.** Image `ghcr.io/ggml-org/llama.cpp:server-cuda-b11459`: the
`gemma-embedding2` architecture landed in llama.cpp PR #30054 (build b11457; b11457 has no published CUDA image, b11459 is the first). The tag
is a fixed build number, never `latest` or an unpinned `server-cuda`; bump it
deliberately and re-run the smoke test.

**Model files.** One mechanism: `--hf-repo ggml-org/embeddinggemma-2-GGUF:Q8_0`
downloads the Q8_0 weights, and `--mmproj-auto` downloads the repo's matching
`mmproj-embeddinggemma-2-*.gguf` projector (needed for image/audio). Quant must be
Q8_0 or BF16, never FP16/F16. To pin BF16, change the tag to `:BF16`.

**Flags** (all fixed in the compose `command`): `--embeddings --pooling mean`;
`--ctx-size 4096 --batch-size 4096 --ubatch-size 4096` (all three equal, so one input
is never split across physical batches); `--host 0.0.0.0 --port 8080`.

**Network.** Only `whiskers-network`; no host port is published. Other containers
reach it as `http://embed:8080/v1`. For host-side debugging uncomment the
`127.0.0.1:8081:8080` line in the service (loopback only; never expose it publicly).
`whiskers-agent` does not `depends_on` the sidecar, so the stack still boots when
it is stopped and LM Studio remains a valid fallback.

### Environment

Defaults live in [`.env.example`](../.env.example) (copy by hand into `.env`; the
code reads exactly these names, compose forwards them to `whiskers-agent`):

```env
EMBED_PROVIDER=gemma-multimodal
EMBED_BASE_URL=http://embed:8080/v1
EMBED_MODEL=embeddinggemma-2
EMBED_DIMENSIONS=768
CAT_ALLOW_LOCAL_PROXIES=1   # the SSRF-safe transport blocks private hosts otherwise
```

### Task prefixes (text only)

EmbeddingGemma expects a task prefix on text inputs:

- queries: `task: search result | query: <text>`
- documents: `title: none | text: <text>`, or `title: <title> | text: <text>` when a title exists

`GemmaMultimodalEmbeddings` adds them itself, to the **text part only**; image and
audio parts are sent as-is. `aembed_query` uses the query prefix. `aembed_documents`
and the text of every `aembed_multimodal` document (including a joint text+media
document) use the document prefix. Titles come from `EmbeddingInput.title` or, for
`embed_batch` / `embed_documents_with`, their `titles` argument (the core hands raw
text and titles to this client rather than formatting with the profile first).

- On by default for `gemma-multimodal`. Turn off with the `task_prefixes=False`
  constructor/selection option or `EMBED_TASK_PREFIXES=0` (`false|no|off` also work).
- Idempotent: text that already starts with the query prefix, or with
  `title: ... | text: `, is never prefixed twice.
- Changing this changes the vector space of text: a collection indexed without
  prefixes needs a re-index after the switch.

### Vector normalization

The client requires `dimensions` (768 for embeddinggemma-2) finite values per
vector, a complete unique index permutation, and then checks the L2 norm. If a
vector is not within `|norm - 1| <= 1e-2`, it is **normalized client-side** (not
rejected); a zero vector is an error. Disable with the `normalize=False`
option only for toy/test vectors.

### LM Studio: legacy text-only option

LM Studio with Qwen3 Embedding 0.6B stays supported as the fallback: set
`EMBED_PROVIDER=openai`, `EMBED_MODEL=text-embedding-qwen3-embedding-0.6b`,
`EMBED_DIMENSIONS=1024`, `EMBED_BASE_URL=http://host.docker.internal:1234/v1`,
`EMBED_PROFILE=lmstudio` (commented block in `.env.example`). It embeds text only, and
its 1024-wide vectors are a different space than embeddinggemma-2's 768: switching
either way requires a re-index; never mix the two in one collection.

### Do not use `/embedding` with `image_data`

llama-server's native `/embedding` endpoint accepts an `image_data` field, but it
**silently returns a text-only vector** and ignores the image (llama.cpp issue
#30082), so a "multimodal" index built that way is wrong without any error. Use only
`/v1/embeddings` with nested content parts
(`input: [{content: [{type: "image_url", ...}]}]`) and `--mmproj` loaded, as the
wire contract below describes. Audio through llama-server is unverified until the
live smoke test below reports it. The client rejects a `base_url` ending in
`/embedding` or `/embeddings`, and never serializes `image_data`.

### Live smoke test

`scripts/smoke_embed_multimodal.py` checks a running sidecar (never run by unit tests):

```bash
docker compose up -d embed && docker compose ps embed   # wait for (healthy)
EMBED_BASE_URL=http://127.0.0.1:8081/v1 python scripts/smoke_embed_multimodal.py --write-docs
```

`EMBED_BASE_URL` defaults to `http://127.0.0.1:8080/v1` (uncomment the loopback port
mapping above to reach the sidecar from the host); `EMBED_MODEL` / `EMBED_DIMENSIONS`
default to `embeddinggemma-2` / `768`. Exit 2 means the server is unreachable, 1 a
failed check, 0 no failure. Checks: text, a generated PNG, a joint text+image
document, a cross-modal sanity check (caption closer to its own image than to an
unrelated one) and a generated 16 kHz mono WAV, each with 768 finite values and unit
norm. Audio is PASS or BLOCKED with the exact HTTP status and sanitized server
message; two different tones must also embed differently, so an ignored audio part is
never reported as a pass. `--write-docs` writes `docs/embed-smoke-results.md` (UTC
timestamp, server build from `/props`, PASS/FAIL/BLOCKED per check).

## Human-operated registration in local-model-stacks

These are prerequisites/instructions for the human operator; this change does
not edit that repository, deploy anything, download weights or call a model.

1. Identify a genuinely multimodal embedding checkpoint and serving runtime that
   accept PNG/JPEG and PCM WAV, plus text with a reference media item, in one
   shared vector space. Verify its pooling, joint-document semantics, supported
   sample rates/media limits and output width. A chat/VLM route does not suffice.
2. Register a distinct alias such as `gemma-embedding-2-multimodal` in the existing
   LiteLLM `model_list`. Point it at the verified embedding model adapter, **not**
   `embeddinggemma:300m`, a completion route, or an unrelated text model. Alias
   and model mapping remain operator configuration, not code constants.
3. The gateway must accept `input: [{content: [parts...]}]` on `/v1/embeddings`
   without tokenizing, flattening, dropping, or stringifying its contents. Use a
   LiteLLM model adapter supporting this shape or a custom embedding handler
   (e.g. a `CustomLLM.aembedding` implementation registered through
   `custom_provider_map`); verify that the installed gateway's request validation
   accepts object inputs **before** handler dispatch. Registering an OpenAI
   provider alias alone is not enough. Adapter work is a serving prerequisite,
   not implemented in this OpenCat repository.
4. That adapter decodes data URLs and `input_audio.data` into image/audio tensors
   (or the native model's inline bytes parts), assembles text/media into **one
   document per input item**, calls the actual embedding API, and returns the
   indexed float-vector response below. Native APIs differ: translation belongs
   in the gateway/model adapter, not a silent fallback in OpenCat. If an installed
   LiteLLM native multimodal embedding adapter uses different part names, the
   custom handler must translate this versioned contract explicitly.
5. Verify all four modalities and joint text/media with non-secret test content.
   Reject unsupported content, dimensional reduction, or media sizes instead of
   returning captions or text-only vectors. If the backend ignores `dimensions`,
   configure its actual fixed output width; arbitrary projection/truncation is
   not performed by this client. Make sure it supports the configured batch size.

Keep gateway/backend credentials in the existing runtime environment/vault.
Do not put tokens in model aliases, URLs, configuration examples or logs. A
missing/unsupported adapter yields a failure in OpenCat, never a fabricated vector.

## OpenCat configuration

The disabled `gemma_multimodal` profile in
`config/embedding_config_example.json` declares the capability separately from
`gemma`. Copy/register that profile in the operator's embedding config and set:

- `provider`: `gemma-multimodal` (the registry adapter identity).
- `model`: the exact LiteLLM alias registered above.
- `dimensions`: the verified positive integer width. The example intentionally
  uses `null`, so an unconfigured profile **fails** rather than assuming a width.
- `base_url`: the gateway base including `/v1`, not `/embeddings`; there is no
  deployment URL default. The client appends `/embeddings`.
- `batch_size`, `max_concurrency`, `max_media_bytes`, `timeout_seconds`: bounded
  settings (defaults 8, 2, 10 MB, 60 s; ceilings 256, 32, 100 MB, 600 s).
- `query_prefix` / `document_prefix`: `{text}` for this adapter profile; existing
  text helpers keep their existing prefix behaviour. The client itself adds the
  EmbeddingGemma task prefixes to text parts (see Task prefixes above).

Use `selection_for_profile("gemma_multimodal")` for explicit producer/query model
selection. `EMBED_BASE_URL` and `EMBED_API_KEY` supply runtime endpoint/credential
fallbacks. For shared active resolution, explicitly select this profile using
`EMBED_PROFILE=gemma_multimodal` (or choose it as the active config profile).
`EMBED_PROVIDER`, `EMBED_MODEL`, `EMBED_DIMENSIONS`, `EMBED_BASE_URL` override
profile identity/endpoint as usual. An explicit different provider discards the
multimodal profile identity rather than attaching it to an unrelated text client.
Existing text-only profiles still control formatting only; their historical
environment/provider defaults are unchanged. No active model is globally replaced.

Alternatively register an embedding pool entry with provider `gemma-multimodal`,
model alias, dimensions, base URL and vault-backed token. The existing shared
`resolve_embedding` / `resolve_tool_embedding` machinery selects it. Matching
provider/model profiles supply batching settings; an explicit selection can
override those settings. Pool selection takes precedence over environment/profile
identity. No separate worker or plugin-local client is introduced.

Local/private gateway access through the SSRF-safe transport requires the existing
explicit operator opt-in `CAT_ALLOW_LOCAL_PROXIES=1`. Redirects are disabled. Do
not turn that on for user-supplied destinations; the endpoint is runtime config.

## Public APIs for asset and hex producers

```python
from db_layer.embeddings.multimodal import EmbeddingInput, EmbeddingMedia
from db_layer.embeddings.embeddings_core import (
    embed_multimodal, embed_multimodal_with, model_id_for,
)
from utils.embedding_config import selection_for_profile

selection = selection_for_profile("gemma_multimodal")
# Caller owns loading bytes from an authorized asset/reference, not this client.
document = EmbeddingInput(
    text="Stone bridge over a river",
    media=EmbeddingMedia(data=png_bytes, mime_type="image/png"),
)
vectors = await embed_multimodal_with(selection, [document])
# Or await embed_multimodal([document]) for the active shared selection.
identity = model_id_for(selection)  # provider:model:dimensions, unchanged convention
```

Signatures:

- `EmbeddingMedia(data: bytes, mime_type: str)` — immutable; bytes hidden from repr.
- `EmbeddingInput(text: str | None = None, media: EmbeddingMedia | None = None)`.
- `async embed_multimodal_with(sel: dict, inputs: list[EmbeddingInput]) -> list[list[float]]`.
- `async embed_multimodal(inputs: list[EmbeddingInput]) -> list[list[float]]`.
- Client method `async aembed_multimodal(inputs: list[EmbeddingInput]) -> list[list[float]]`.

A document needs non-whitespace text or valid media (or both). Exact MIME types:
`image/png`, `image/jpeg`, `audio/wav`. PNG/JPEG must match their declared format
and pass Pillow verification **and decoding**; truncated scans and decompression
bombs fail. WAV must be nonempty uncompressed PCM, have a positive sample rate,
and contain all declared frames. Compressed WAV, GIF, MP3, URL/path references,
bytes in the text field, empty/truncated media and oversized media fail explicitly.
This API validates on use, not at dataclass construction. A preflight pass
validates the whole input list before any HTTP call (or client creation) without
keeping encoded payloads; base64 encoding then happens one window
(`batch_size * max_concurrency` documents) at a time and each window's payloads
are released before the next is encoded. Decode/validation and encoding run in a
thread so the event loop stays responsive. It does not resample audio or resize images: backend
constraints beyond the byte cap belong in the adapter and must fail visibly.

Existing async `embed`, `embed_batch`, `embed_query_with`, `embed_documents_with`
work for this provider as text-only documents and still dispatch non-Gemma models
to their original factories. Direct synchronous LangChain methods on this *new*
client are intentionally unsupported; no nested/background event loop is started.

## Wire contract: content-parts adapter v1

POST to `<configured base_url>/embeddings`. Body:

```text
{
  "model": "<configured gateway alias>",
  "dimensions": <verified configured integer>,
  "encoding_format": "float",
  "input": [
    {"content": [{"type": "text", "text": "Stone bridge"}]},
    {"content": [
      {"type": "text", "text": "Bridge reference"},
      {"type": "image_url", "image_url": {"url": "data:image/png;base64,<actual PNG bytes>"}}
    ]},
    {"content": [
      {"type": "input_audio", "input_audio": {"data": "<actual WAV bytes, base64>", "format": "wav"}}
    ]}
  ]
}
```

JPEG uses `data:image/jpeg;base64,...`. Images are inline data URLs, never remote
fetch URLs. Audio is base64 of the **entire WAV file**, not raw samples or a file
name. Text plus audio uses the same two-part document form as text plus image.

A non-secret request builder with a tiny synthetic reference image (no network):

```python
import base64, io, json, os
from PIL import Image

buf = io.BytesIO()
Image.new("RGB", (2, 2), (20, 50, 80)).save(buf, format="PNG")
request = {
    "model": "gemma-embedding-2-multimodal",  # operator's registered alias
    "dimensions": int(os.environ["EMBED_DIMENSIONS"]),  # verified width
    "encoding_format": "float",
    "input": [{"content": [
        {"type": "text", "text": "A small blue reference tile"},
        {"type": "image_url", "image_url": {
            "url": "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
        }},
    ]}],
}
print(json.dumps(request))  # no authorization header/credentials
```

The response is an OpenAI-style object with `data` entries, each containing an
integer zero-based `index` relative to **that request batch**, and an `embedding`
array of exactly the configured width. All values must be numeric and finite;
bools, strings, null, NaN and infinity are rejected. Entries may arrive shuffled,
but indexes must be a complete unique permutation. OpenCat restores input order.
Wrong cardinality, missing/duplicate/out-of-range indexes or wrong dimensions
fail the whole call. No partial success or synthetic vectors are returned.

Requests split by batch size, run in bounded windows, and use a client-wide
semaphore. Empty input returns `[]` before resolving a model or making HTTP calls.
Every in-flight task in a window is awaited even on a provider error. Cache keys
include endpoint, runtime credential and batching configuration (as a digest);
vector identity remains `provider:model:dimensions`. There are no automatic
retries, model fallbacks or worker loops. Each `aembed_multimodal` call opens one
SSRF-safe client (`core.proxy.ssrf_safety._safe_async_client`, redirects off)
shared by all of its windows for connection reuse and closed on success, failure
or cancellation; every request still re-validates the endpoint URL. Concurrent
calls get independent clients but share the client-wide semaphore.

Failures raise `MultimodalEmbeddingError` (a `ValueError`) with `category` in
`unsafe_endpoint | timeout | http_status | transport | invalid_json` and `status`
(HTTP code for `http_status`, else `None`); malformed vectors raise `ValueError`
and log `invalid_response`. One `whiskers` warning per failed request carries only
`category`, `status` and `batch_size` — never exception text, tracebacks, endpoint,
headers, credentials, media or provider bodies. Non-numeric configured dimensions
fail with the single message `embedding dimensions must be a configured positive
integer`.

## Offline verification and limitations

`test/unit/test_embed_llama_contract.py` runs the client against an in-process fake
llama-server (`httpx.MockTransport`: nested content parts in, indexed `data` out,
shuffled indices, llama-style 400/500 error bodies, 404 on `/embedding`) and pins
task prefixes, normalization, width, sanitized errors and the legacy-route guard.
`test/unit/test_gemma_multimodal_embed.py` mocks the async HTTP transport boundary
and checks configured request identity/endpoint, byte-exact PNG/JPEG/WAV payloads,
joint documents, splitting/order/concurrency, empty calls, invalid media and
malformed responses. `test_gemma_multimodal_config.py` covers pool/profile settings,
invalid/unconfigured widths and legacy formatting/model defaults.

These tests establish the OpenCat client contract only. A compatible LiteLLM
adapter and real multimodal embedding model remain human serving prerequisites;
a live deployment smoke test is not authorized by this step. If the intended
Gemma route is text-only, image/audio serving remains blocked. Nothing here edits
local-model-stacks or asserts that its current gateway accepts this extension.

## Re-indexing existing vectors

Moving existing LM Studio text vectors to `gemma-multimodal:embeddinggemma-2:768` is a dry-run-first,
resumable, tenant-scoped re-embed: see [embedding-reindex.md](embedding-reindex.md)
(`python scripts/reindex_embeddings.py --help`).
