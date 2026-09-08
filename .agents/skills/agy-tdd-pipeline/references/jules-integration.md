# Jules CI Integration

## Purpose
Decoupled cloud verification for high-risk changes (Auth, DB, API Contracts).

## Config
Trigger via `jules_gate: true` in Conductor or manual call.

## Triage
- **Local Pass / Jules Fail:** Check Env Vars, DB state, Network policies in cloud env.
- **Both Fail:** Fix implementation/tests in local loop first.
- **Both Pass:** Advance to next MTU.
