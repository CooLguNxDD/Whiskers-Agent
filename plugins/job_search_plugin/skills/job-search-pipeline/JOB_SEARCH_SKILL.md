---
name: job-search-pipeline
description: Job search/ingest/enrich/evaluate/apply/track tool inventory for job_search_plugin, plus the one-call career_ops_apply_v1 pipeline.
---

# Job Search Pipeline

## 0. Prefer the flow for a normal apply

For "fetch this posting and apply/tailor/track it" goals, call
`run_career_ops_pipeline(goal, applicant_profile_id, url|raw_text)` instead of chaining the tools
below by hand — it already resolves every slot-name mismatch between them (see the
`career-ops-pipeline` Claude Code skill for the full stage table). Prefer this direct tool call over
describing the goal in plain language to `run_graph` — LLM triage classification is not a reliable
router into this pipeline. Use the tools below directly only for a partial/manual step, debugging,
or a shape the flow doesn't cover
(e.g. re-checking liveness on an already-tracked posting).

## 1. Tool inventory

**Ingest**
- `fetch_job_posting(url="", raw_text="")` — auto-detects Ashby/Greenhouse/Lever, falls back to
  generic web extraction (`search_plugin.fetch_url`) for anything else. `url` and `raw_text` may
  be supplied together: the paste is parsed into the ATS slot schema and an Indeed `jk`/`vjk` is
  kept as `job_id` without fetching a gated board. Indeed/LinkedIn Cloudflare/401 is
  `needs_browser_scrape` (not a ghost-job signal). Agency pastes set `via`/`posting_entity` and
  leave `end_employer` null when the client is unnamed. Returns `{provider, job_id, title,
  company, location, compensation{min,max,currency}, clean_description, source_url, posted_at,
  via, posting_entity, end_employer, employment_class, needs_browser_scrape, ingest_status}`.
- `search_jobs(query, location="", providers=None)` / `get_job_details(job_id, provider)` —
  provider-API search when credentials are configured; `needs_browser_scrape` per provider when not.
- `check_job_liveness(url, company="", role_title="")` — standalone repost/staleness/ghost-signal
  check for a posting already known by URL.

**Profile**
- `get_applicant_profile(applicant_profile_id)` — full row incl. `base_resume_text`; expensive for
  a CLI context budget, prefer the summary tool unless you need raw resume text.
- `upsert_applicant_profile(...)` — full profile write, incl. targeting fields (`target_titles`,
  `top_skills`, `constraints_text`, `notice_period_days`, `core_technologies`, `methodologies`,
  `languages`).
- `get_applicant_profile_summary(applicant_profile_id, tier="compact")` — `tier` one of `compact`,
  `skills_only`, `full`. Unknown tier errors rather than silently falling back.
- `get_candidate_proof_points(applicant_profile_id, keywords, max_bullets=5)` — hybrid search over
  indexed resume/preference chunks for keyword-matched evidence bullets.

**Evaluate**
- `index_preferences(applicant_profile_id, preferences_text="")` — embeds resume + preferences;
  run once per profile (or after a resume edit) before `evaluate_offer_fit` can retrieve anything.
- `evaluate_offer_fit(applicant_profile_id, offer_text, hybrid=True, top_k=8)` — dense+sparse
  (RRF) fit scoring by default; `hybrid=False` forces dense-only cosine. Returns
  `{verdict: {fit_score, skill_match_reasons, preference_match_reasons, mismatch_reasons,
  recommended}, retrieved, hybrid_used}`.

**Enrich**
- `tailor_resume(applicant_profile_id, job_description, company="", role="", job_id="",
  auto_bake_portfolio=False)` — anti-fabrication LLM tailoring (SKILLS section only lists what's
  verbatim/synonym-present in the base resume — never adds a JD-mentioned skill the candidate
  doesn't have). `auto_bake_portfolio=True` additionally dispatches
  `portfolio_plugin__bake_portfolio_for_job` and stamps `portfolio_job_id`/`portfolio_url` on the
  result — off by default since baking persists a row + mints a short_id, not a read-only side
  effect of tailoring.
- `tailor_cover_letter(applicant_profile_id, job_description, job_id="", portfolio_url="")` —
  Opening / Problems I Will Solve / Interactive Portfolio Reference structure; omits the portfolio
  section entirely (never a placeholder link) when `portfolio_url` isn't supplied.
- `render_resume_pdf(applicant_profile_id, tailored_text, kind="resume", job_id="",
  portfolio_job_id="")` — ATS PDF to MinIO with a presigned URL; when `portfolio_job_id` is set,
  bakes a clickable portfolio link into the contact header.
- `render_cover_letter_pdf(applicant_profile_id, cover_letter_text, job_id="", portfolio_job_id="")` —
  renders the tailored cover letter to an ATS-compliant PDF in MinIO with a presigned URL using the exact
  same theme, typography (Helvetica), margins, and contact header (name, email, phone, and clickable
  baked CatPortfolio link) as the resume PDF.

**Apply / Track**
- `create_application(job_id, provider, applicant_profile_id, ...)` /
  `submit_application(application_id)` — provider-submission lifecycle (ATS POST + tracking row).
- `mark_application_submitted(application_id, provider_application_id="")` — manual/browser-hands
  fallback when a real submit can't be automated.
- `update_application(application_id, status="", notes="", provider_application_id="")` — patches
  the provider-submission tracking row.
- `sync_application_status(applicant_profile_id, job_id, status="", notes="",
  portfolio_job_id="", application_status="")` — separate, idempotent lifecycle vocabulary
  (`drafted|applied|interviewing|offer|rejected`), upsert keyed on `(profile, job_id)`. Built for
  Career-Ops; `application_status` is an accepted alias for `status` (used by the flow's `track`
  stage) — `status` wins if both given.
- `list_applications(applicant_profile_id=None, status_filter="")` /
  `get_agent_status(job_id="", portfolio_job_id="")` — read-only lookups; the latter can look up by
  a bake's `portfolio_job_id`.

**Pipeline**
- `run_career_ops_pipeline(goal, applicant_profile_id, url="", raw_text="", theme="")` — see §0;
  `theme` is a CatPortfolio layout id (`cozy|neon|paper|latte|frappe|macchiato|mocha`); the tool to call for a normal apply.
- `resolve_pipeline_signals(...)` / `prepare_application_record(...)` — flow-internal slot-bridging
  adapters (§0), not usually called standalone.

## 2. Rules

- **Never submit.** `sync_application_status` (and the flow's `track` stage) only ever writes
  `"drafted"`. A human decides `"applied"` via `update_application`/`submit_application` outside
  this pipeline — matches Career-Ops's own "never submits on your behalf" doctrine.
- **Index before you evaluate.** `evaluate_offer_fit` retrieves nothing useful until
  `index_preferences` has run at least once for that profile.
- **`auto_bake_portfolio` is off by default on `tailor_resume`** for a reason — it persists a row.
  Prefer the flow (§0), where baking is one stage among nine and its failure doesn't abort tailoring.
- **Anti-fabrication is load-bearing.** Don't post-process a tailored resume to add JD skills the
  base resume doesn't show — the prompt already refuses this; don't work around it downstream.
