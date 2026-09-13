# Pullfrog Agent User Manual (Local pullfrog Integration)

Pullfrog is an autonomous AI coding agent integrated directly into the Whiskers Agent. It runs fully locally on github action, enabling automated code reviews, interactive bug-fixing, automated issue planning, and interactive task execution directly from GitHub.

---

## 🚀 How to Interact with Pullfrog

### 1. Triggering via GitHub Comments
You can invoke the agent by typing `@pullfrog` in any issue comment, pull request comment, or issue description.

> [!NOTE]
> Pullfrog is configured to only listen to **explicit mentions in comments**. It does not trigger automatically on standard GitHub review submission comments to avoid noisy concurrency conflict.

#### Special Command Conventions:
*   **`@pullfrog fix all`**: Directs the agent to scan, implement, and push fixes for all unresolved review threads on the current pull request.
*   **`@pullfrog fix thumbs`**: Directs the agent to address only those PR review threads that have been given a 👍 reaction by a reviewer.
*   **`@pullfrog [any custom instruction]`**: Tell the agent to execute any arbitrary coding, refactoring, or planning task.

### 2. Manual Dispatch (workflow_dispatch)
You can trigger a Pullfrog run manually through the GitHub Actions tab by selecting the **Pullfrog** workflow.

The manual dispatch accepts the following input parameters:
*   **`prompt`** (Required): The instruction or task payload for the agent.
*   **`name`** (Optional): A custom run name to identify this execution in GitHub Actions logs.
*   **`model`** (Optional): Model slug override (e.g. `anthropic/claude-3-5-sonnet`, `google/gemini-2.5-pro`). If left blank, it uses the repository variable `PULLFROG_MODEL`, falling back to `anthropic/claude-sonnet`.
*   **`effort`** (Optional): Claude reasoning/thinking budget override (`low` | `medium` | `high` | `max`). Defaults to `medium` or the repository variable `PULLFROG_EFFORT`.
*   **`timeout`** (Optional): Maximum run duration (e.g., `20m`, `1h`). Default: `1h`.

---

## 🤖 Automated Triggers & Event Workflows

Pullfrog listens to various GitHub repository events to run specific agents asynchronously. Each automated feature runs in its own isolated concurrency group to prevent jobs from cancelling each other.

| Feature / Workflow | Event Trigger | Execution Mode & Behavior | Concurrency Group |
| :--- | :--- | :--- | :--- |
| **PR Review**<br>[`pullfrog-review.yml`](../workflows/pullfrog-review.yml) | PR `opened`, `ready_for_review`, or `reopened`. | Uses low/high/max review budgets (default: max), tracing affected dependencies and consumers. Submits a single review, then terminates. | `pullfrog-review-<repo>-<pr#>` |
| **Address Reviews**<br>[`pullfrog-address-reviews.yml`](../workflows/pullfrog-address-reviews.yml) | PR review `submitted` with `changes_requested`. | Triggers only if the reviewer is a **bot**. Pullfrog will implement the feedback, commit, and push updates. | `pullfrog-address-<repo>-<pr#>` |
| **CI Failure Fix**<br>[`pullfrog-ci-fix.yml`](../workflows/pullfrog-ci-fix.yml) | A check suite finishes with a `failure` status. | Active only on bot-authored PR branches. Pullfrog inspects logs, diagnoses/resolves the issue, and pushes a fix commit containing `[pullfrog-ci-fix]`. | `pullfrog-ci-fix-<repo>-<pr#\|sha>` |
| **Issue Triage**<br>[`pullfrog-issues.yml`](../workflows/pullfrog-issues.yml) | An issue is `opened`. | Pullfrog analyzes the request, writes an implementation plan as a comment, and applies up to 3 matching repository labels. | `pullfrog-issues-<repo>-<issue#>` |

---

## ⚙️ Configuration & Customization

### The Central Config File: [`config.yml`](config.yml)
You can configure global rules and feature flags in `.github/pullfrog/config.yml`.

```yaml
mention: "@pullfrog"
runner_labels: ubuntu

features:
  dispatch: true             # Allow manual dispatch
  mention_triggers: true     # Respond to @pullfrog mentions in comments
  pr_review:
    enabled: true            # Enable automatic PR reviews
    budget: max              # low | high | max; PULLFROG_REVIEW_BUDGET overrides
    include_drafts: false    # Skip reviewing draft PRs
    on_synchronize: false    # Avoid expensive re-reviews on every commit push
  issues:
    enabled: true
    mode: plan               # 'plan' for plans in comments, 'build' to attempt immediate PRs
  address_reviews:
    enabled: true
    only_bot_prs: false      # Allow Pullfrog to automatically fix human-authored PRs after a bot review
  ci_fix:
    enabled: true
    only_bot_prs: true       # Security: only auto-fix CI for bot-created PRs (avoids unchecked human writes)

status_checks: false         # Set to true to require pullfrog status checks on branch protection
```

### Agent Instruction Manuals
Under the [`instructions/`](instructions/) directory, three markdown instructions govern the agent's behavior:

1.  **[`plan.md`](instructions/plan.md)**: Governs issue enrichment. Instructs the agent to post a structured approach (goal, files affected, risks, test plan).
2.  **[`build.md`](instructions/build.md)**: Governs task execution and coding. Defines monorepo structures, package managers, and safety requirements.
3.  **[`review.md`](instructions/review.md)**: Reusable code review template with low/high/max investigation budgets, evidence requirements, repository risk checks, and a coverage/validation summary. Max is the default.

### Code Review Budgets

Set `features.pr_review.budget` in [`config.yml`](config.yml) to `low`, `high`, or
`max` (the shipped default). The repository Actions variable
`PULLFROG_REVIEW_BUDGET` overrides this setting. Resolution order is **repository
variable → config.yml → max**; an invalid selected value falls back to `max`.
The shared workflow passes the result to automatic reviews, mention-triggered
reviews, and manual review tasks. To control the budget from config.yml, leave
the repository variable unset (remove it if it was previously configured).

| Mode | Investigation calls after checkout | Depth | Inline cap / body target |
| :--- | ---: | :--- | :--- |
| `low` | 40 | Diff, risky functions, direct callers/callees, focused checks | 10 / 400 words |
| `high` | 100 | Every changed file, affected consumers/providers, subsystem checks | 20 / 800 words |
| `max` (default) | 200 | End-to-end impact, transitive dependencies, adversarial second pass, relevant regression/integration checks | 30 / 1,200 words |

These are agent investigation ceilings, not enforced token/dollar caps. The
workflow timeout still applies (automatic reviews: 60 minutes). Review budget
does not change the model or `PULLFROG_EFFORT`. Incomplete coverage must be stated.
See [the full template](instructions/review.md) for counting and output rules.

Set the default explicitly with the [GitHub CLI](https://cli.github.com/manual/gh_variable_set):

```sh
gh variable set PULLFROG_REVIEW_BUDGET --body max --repo CooLguNxDD/Whiskers-Agent
```

The workflow reads this through GitHub's
[`vars` context](https://docs.github.com/en/actions/reference/workflows-and-actions/contexts#vars-context).

---

## 🔒 Security & Guardrails

To prevent unwanted actions or code modification:
1.  **Draft Pull Requests**: Pullfrog skips automated code reviews on draft PRs unless explicitly marked `ready_for_review`.
2.  **CI Fix Protection**: The auto-fixer (`ci_fix.only_bot_prs: true`) is restricted to bot-authored PRs by default. It will never push modifications to human PRs automatically.
3.  **CI Fix Loop Prevention**: If a branch contains more than two commits containing `[pullfrog-ci-fix]`, the agent will halt and report that maximum attempt limits were reached.
4.  **No Default Branch Pushing**: Pullfrog is restricted to feature branches. It cannot push directly to `main` or `develop`.

---

## 🔑 Repository Variables & Credentials

You can set these in repository **Settings → Secrets and variables → Actions** without modifying code:

| Variable / Secret | Example Value | Purpose |
| :--- | :--- | :--- |
| **`PULLFROG_AGENT`** (Var) | `opencode` | Agent harness to execute (`opencode`, `claude`, `codex`, `cursor`, `antigravity`, `grok`). Default: `opencode`. |
| **`PULLFROG_MODEL`** (Var) | `meta/muse-spark-contributor` | Model slug to run (`meta/muse-spark-contributor`, `meta/muse-spark-1.3`, `openrouter/meta/muse-spark-1.3`). |
| **`PULLFROG_EFFORT`** (Var) | `medium` | Reasoning effort (`minimal`, `low`, `medium`, `high`, `xhigh`, `max`). |
| **`PULLFROG_REVIEW_BUDGET`** (Var) | `max` | Overrides `features.pr_review.budget` (`low`, `high`, `max`). Unset uses config; invalid uses `max`. Independent of reasoning effort. |
| **`META_MODEL_API_KEY`** (Secret) | `sk-...` | Direct API key for Meta Model API (Muse Spark). |
| **`OPENROUTER_API_KEY`** (Secret) | `sk-or-...` | OpenRouter API key for Muse Spark and other models. |
| **`OPENCODE_API_KEY`** (Secret) | `sk-...` | OpenCode Zen API key. |
| **`PULLFROG_GITHUB_TOKEN`** (Secret) | `ghp_...` | GitHub PAT with repo permissions (used if GitHub App credentials are not provided). |
| **`PULLFROG_APP_ID`** (Secret) | `123456` | GitHub App ID to mint short-lived tokens. |
| **`PULLFROG_APP_PRIVATE_KEY`** (Secret) | `-----BEGIN RSA...` | App Private Key PEM for GH API authentication. |
| **`ANTHROPIC_API_KEY`** (Secret) | `sk-ant-...` | API token for Claude models. |
| **`GEMINI_API_KEY`** (Secret) | `AIzaSy...` | API token for Google Gemini models. |
| **`ANTIGRAVITY_TOKEN`** (Secret) | `oauth-token-...` | Token for Google Antigravity CLI (`agy`) runs. See [Setup Guide](../grok_antigravity_setup.md). |
| **`GROK_AUTH_JSON`** (Secret) | `{"tokens":...}` | Credentials JSON (or Base64) for xAI Grok Build CLI runs. See [Setup Guide](../grok_antigravity_setup.md). |
| **`CURSOR_API_KEY`** (Secret) | `cur_...` | API token for Cursor CLI (`cursor-agent`) runs. |

