# Nightly documentation sync

`.github/workflows/nightly-doc-sync.yml` runs at **09:43 UTC every day** on
`smg-org-runner-cpu`. It audits `smg-project/smg` against the current `smg-docs`
content and opens **draft PRs, one user-facing concern per PR**. It never merges.

The initial source window starts **2026-06-27 UTC** (90 days before setup). This is
a fixed date, not a rolling lookback: incomplete work cannot silently age out.
Each run alternates the oldest backlog and newest commits. The default has no
commit-count cap (`max_commits: 0`); a run works within a 600-model-call and
65-minute model-work budget, reserving half for drafting/review. Unfinished work
remains pending. Budget-limited completion is reported explicitly and does not
mean the entire 90-day history has been audited.
Discovery, documentation drafting, and independent review all use
`claude-fable-5` through the Anthropic Messages API with the runner's
`ANTHROPIC_API_KEY`; they run only on `smg-org-runner-cpu`.
The separate PR CI jobs use `ubuntu-latest` for mocked unit tests, lint, type
checks, and builds. Those checks make no model calls and need no model credential.
All generated documentation and ledger commits use
`XinyueZhang369 <zoeyzhang369@gmail.com>` as author, committer, and DCO
`Signed-off-by` identity.

## Scope and validation

- Split independent behaviors even when they came from one source commit or edit
  the same page. Do not accumulate multiple source commits into a catch-all PR.
- Read current code and docs before proposing anything. Skip changes already
  documented, reverted/superseded behavior, and changes without user-facing impact.
- A PR can edit **any number of existing Markdown pages** under `src/lib/content/`
  for its one concern, with **fewer than 1,000 added + removed lines** (999 maximum). It cannot change workflow/code/config
  files. Work needing a new page/navigation entry is explicitly deferred.
- An independent model call reviews the patch for one-concern scope, accuracy,
  and whether it is already documented, including callers/conditional paths and
  release availability. A rejection gets one correction attempt with the review
  feedback and rejected diff. Feedback persists for subsequent runs if unresolved.
  Hard line/path guards then apply. Source on `main` is not evidence that a fix
  has shipped: preserve valid release warnings and identify unreleased fixes.
- `git diff --check`, `pnpm check`, and a production `pnpm build` must pass before
  publication. A generated PR records its exact source and docs revisions.
- At most **100 new PRs per UTC day**, shared across scheduled runs, retries,
  and manual dispatches. Closed and merged PRs still count for their creation day;
  the former 10-open-PR ceiling is removed. Any open PR
  (including a human's) touching a target page defers that concern until a later
  night. Independent pages can yield multiple PRs the same day.
- The model only receives public repository evidence and read/search tools. It
  cannot run shell commands, fetch arbitrary URLs, or access tokens. Trusted
  Python code validates proposals and performs GitHub writes.

Edit `config.json` to tune runtime/model budgets or the model. Manual dispatch
can set `max_commits` to a positive batch size for verification, or `0` for the
entire pending backlog within budget. `max_prs` cannot exceed the configured cap.

## Durable state and duplicate prevention

The `automation/doc-sync-state` branch stores `doc-sync-state.json`. It is separate
from `main` and from documentation PRs, so routine checkpoints do not deploy the
site. Source commit IDs and per-commit concern slugs identify work permanently.
A plan is saved before any PR work; a prepared commit is saved before creating its
branch/PR. Interrupted publication resumes from that exact commit only when the
source/docs snapshots and validation version still match. Otherwise it regenerates
and reviews the draft on a new revision branch, retaining the old branch and
checkpoint for inspection. Bump `VALIDATION_VERSION` when changing validation
requirements. Human-modified branches block this regeneration. Existing open,
merged, and human-closed PRs are terminal for that concern; the bot never force
pushes, reopens rejected PRs, or rewrites human edits. Later changes to the same
behavior are independently checked against the current docs.

Deferred work and errors remain unprocessed/pending, appear in the JSON artifact,
and make the run fail visibly when the audit/drafting attempt cannot resolve them.
Expected model-call/runtime limits preserve the backlog and set `budget_limited`
in the report without recording an error. Deferred concerns may need a human to split a large
change or add navigation. To retry a deliberately closed PR, a maintainer must
explicitly remove its ledger entry **and** resolve/rename the old branch; there is
no automatic reopening. Do not edit the ledger while the workflow is running.
Do not change `source_since` without explicitly migrating the ledger.

## GitHub permissions and first run

The job requests `contents: write` and `pull-requests: write`. In repository Actions
settings, **Allow GitHub Actions to create and approve pull requests** must be
allowed when using `GITHUB_TOKEN`. If organization policy blocks it, a repository
admin must enable it or configure a dedicated `DOC_SYNC_TOKEN` with contents and
pull-request write permissions for **smg-docs only**. Never reuse ARC's registration
credential or put a personal token in code.
PR-creation permission failures stop further publication/model work immediately;
known GitHub policy denials are diagnosed without logging arbitrary API responses
or credentials. The job logs which token source is selected, never its value.

PRs created with `GITHUB_TOKEN` do not trigger normal PR workflows. The nightly
publisher therefore runs checks/build itself for each proposed patch; the PR body
records that validation. A dedicated bot token can trigger the regular PR checks.
Draft PRs still require human review and must not be auto-merged.

Start with **Actions → Nightly SMG Documentation Sync → Run workflow**, keep
`dry_run: true`, and lower `max_commits`/`max_prs` for a smoke test. Dry run performs
the audit, scope review and site validation, but writes no GitHub branches, PRs or
ledger checkpoints. Its `doc-sync-report.json` artifact includes proposed diffs
and reasons for skipped/deferred work. The scheduled trigger publishes by default.

For workflow fixes, dispatch the development branch: the workflow loads automation
and its config from that ref, but separately checks out documentation `main` and
source `main`. Generated documentation PRs therefore contain only approved page
edits, never the unmerged automation changes. Both branch and scheduled runs share
the same concurrency group and durable ledger.

Run the deterministic tests locally (no tokens or model calls):

```sh
python3 -m unittest discover -s scripts/doc-sync -p 'test_*.py' -v
```

The audit is probabilistic: review draft PRs for factual accuracy. Unusually large
source diffs are deferred rather than truncated and incorrectly declared covered.
