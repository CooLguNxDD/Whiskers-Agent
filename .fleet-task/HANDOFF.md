# Handoff — S20-report-checkpoint-table-row

Implemented cached checkpoint telemetry gauges. The worker refreshes exact counts for `checkpoints`, `checkpoint_blobs`, and `checkpoint_writes` during each tick, captures deletions from the existing sweeper, and exposes last/total deletions through sync cache-only providers. Gauge providers are installed on worker start and removed on stop. Count-query errors are debug-logged and preserve the last successful counts.

## Files changed
- `core_graph/worker/checkpoint_sweeper.py` — gauge cache/providers, lifecycle registration, tick refresh and deletion accounting.
- `core_graph/runtime/bootstrap.py` — async checkpoint table count query through the existing checkpointer pool.
- `test/unit/test_checkpoint_telemetry.py` — tests cache-only providers, registration/removal, tick updates and count-query failure preservation.

## Verification
- `bash "$(git rev-parse --path-format=absolute --git-common-dir)/../../../goal/opencat/2026-10-24-1535-embed-gemma2/docs/verify-opencat.sh" test/unit/test_checkpoint_sweeper.py test/unit/test_checkpoint_telemetry.py` — **9 passed**.
- Full fleet verify and orchestrator-owned CHECK were not run per contract.

## Unfinished
None.

## Dependencies
None added.

## Learned
- Telemetry collector snapshot keys directly expose registered gauge providers; worker gauge callbacks can remain synchronous and cache-only.
