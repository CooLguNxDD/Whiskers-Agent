NO-CHANGE: wake counters are outside this repo

## Proposal for the fleet owner

Use one definition everywhere: a **model wake** is one orchestrator invocation that spends model tokens, represented by exactly one increment of `wakes.model` in `state.json`. Heartbeat ticks and other orchestrator activity without a model call increment `wakes.total` only.

Both renderers must read that same `wakes.model` value: the run `STATE.md` header (`wakes: N model / M total`) and the weekly report `data.json` model-wake field. Neither renderer should independently infer model wakes from total ticks or report-specific events.

Minimal test: create one fixture `state.json` containing distinct `wakes.model` and `wakes.total` values, render both the `STATE.md` header and weekly `data.json` from it, and assert that each reports the identical model-wake number from `wakes.model`.

## Work performed

Verified the task's scope note: `grep -ri "model.wake" .` finds only `.fleet-task/TASK.md`; this Whiskers-Agent repository has no wake-counter implementation. No product files changed.

## Verify

- `bash .fleet-task/CHECK.sh`: passed (`CHECK OK`).
- Fleet verify command: intentionally not run; this step's contract says the fleet gate runs it.

## Files changed

- `.fleet-task/HANDOFF.md` — records the required fleet-side definition and proposal; wake counters are maintained outside this repository.

## Unfinished

The fleet owner must implement the shared counter source and test in the Cat Fleet skill repository.

## Dependencies

None.

## Learned

