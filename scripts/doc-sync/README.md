# Nightly documentation sync

`.github/workflows/nightly-doc-sync.yml` runs at **09:43 UTC every day** on
`smg-org-runner-cpu`. It audits `smg-project/smg` against the current `smg-docs`
content and opens **draft PRs, one user-facing concern per PR**. It never merges.

The initial source window starts **2026-06-27 UTC** (90 days before setup). This is
a fixed date, not a rolling lookback: incomplete work cannot silently age out.
Each run shares its audit capacity between the oldest backlog and newest commits.
The workflow reads the runner's `ANTHROPIC_API_KEY`; it needs no new model secret.

## Scope and validation

- Split independent behaviors even when they came from one source commit or edit
  the same page. Do not accumulate multiple source commits into a catch-all PR.
- Read current code and docs before proposing anything. Skip changes already
  documented, reverted/superseded behavior, and changes without user-facing impact.
- A PR edits at most **3 existing Markdown pages** under `src/lib/content/`, with
  at most **250 added + removed lines**. It cannot change workflow/code/config
  files. Work needing a new page/navigation entry is explicitly deferred.
- An independent model call reviews the patch for one-concern scope, accuracy,
  and whether it is already documented. Hard file/line/path guards then apply.
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

Edit `config.json` to tune bounded limits or the model. Manual dispatch can lower
`max_commits` and `max_prs`, but cannot raise the configured limits.

## Durable state and duplicate prevention

The `automation/doc-sync-state` branch stores `doc-sync-state.json`. It is separate
from `main` and from documentation PRs, so routine checkpoints do not deploy the
site. Source commit IDs and per-commit concern slugs identify work permanently.
A plan is saved before any PR work; a prepared commit is saved before creating its
branch/PR. Interrupted publication resumes from that exact commit. Existing open,
merged, and human-closed PRs are terminal for that concern; the bot never force
pushes, reopens rejected PRs, or rewrites human edits. Later changes to the same
behavior are independently checked against the current docs.

Deferred work and errors remain unprocessed/pending, appear in the JSON artifact,
and make the run fail visibly. Deferred concerns may need a human to split a large
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

PRs created with `GITHUB_TOKEN` do not trigger normal PR workflows. The nightly
publisher therefore runs checks/build itself for each proposed patch; the PR body
records that validation. A dedicated bot token can trigger the regular PR checks.
Draft PRs still require human review and must not be auto-merged.

Start with **Actions → Nightly SMG Documentation Sync → Run workflow**, keep
`dry_run: true`, and lower `max_commits`/`max_prs` for a smoke test. Dry run performs
the audit, scope review and site validation, but writes no GitHub branches, PRs or
ledger checkpoints. Its `doc-sync-report.json` artifact includes proposed diffs
and reasons for skipped/deferred work. The scheduled trigger publishes by default.

Run the deterministic tests locally (no tokens or model calls):

```sh
python3 -m unittest discover -s scripts/doc-sync -p 'test_*.py' -v
```

The audit is probabilistic: review draft PRs for factual accuracy. Unusually large
source diffs are deferred rather than truncated and incorrectly declared covered.
