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
  text helpers keep their existing prefix behaviour. Typed multimodal documents
  are sent as authored, without adding active text-profile prefixes.

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
This API validates on use, not at dataclass construction. It validates the whole
input list before any HTTP call; decode/validation runs in a thread so the event
loop stays responsive. It does not resample audio or resize images: backend
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
retries, model fallbacks or worker loops. Transport errors are sanitized to avoid
leaking gateway bodies, credentials or inline media.

## Offline verification and limitations

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
