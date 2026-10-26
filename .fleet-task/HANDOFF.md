# Handoff — S04-embed-sidecar-review-fix

## Built
Hardened the embedding sidecar contract tests against the two review blockers. The precision guard now catches FP16/F16/fp16 while self-checks confirm BF16 and Q8_0 are allowed. Projector validation requires either `--mmproj-auto` with the matching embeddinggemma-2 HF repository or an explicitly named matching projector, and rejects disable flags. The port contract now requires no published ports by default.

## Review findings
- **BLOCKER — FP16/model contract: fixed.** `test/unit/test_embed_sidecar.py` tests the regex against FP16, F16, fp16, BF16 and Q8_0, and applies the guard to the configured command.
- **BLOCKER — loose projector configuration: fixed.** Requires supported auto-projector/repository combination or a matching explicit projector filename; disabled projector options are rejected.
- **BLOCKER — published port assertion: fixed.** Test asserts `not embed.get("ports")`; existing compose has no ports configured, with opt-in guidance remaining commented out.
- Other compose, env, and documentation checks in the review: no changes needed; findings reported them as checked, not blockers.

## Files changed
- `test/unit/test_embed_sidecar.py` — strengthened model precision, projector, and default port contract assertions.

## Verification
- `bash .fleet-task/CHECK.sh` — passed (`CHECK OK`).
- `bash "$(git rev-parse --path-format=absolute --git-common-dir)/../../../goal/opencat/2026-10-24-1535-embed-gemma2/docs/verify-opencat.sh" test/unit/test_embed_sidecar.py` — passed (13 passed).
- `git diff --check` — passed.
- Full verify command intentionally not run; the fleet gate owns it.

## Unfinished
None. No dependencies added.

## Learned
- No new durable project fact from this change.
