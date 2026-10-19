# Nightly SMG documentation sync

The editable `src/lib/content/` tree is **main** documentation, backed by a
pinned SMG main commit, including merged changes that have not shipped yet.
Release snapshots under `src/lib/versions/` have an independent release audit.
The selector/banner supplies version context; behavior prose describes the
selected version directly. See [versioning](../../README.md#documentation-versions).

The workflow runs at **09:43 UTC daily** (01:43 PST / 02:43 PDT), on
`smg-org-runner-cpu`. Discovery, writing, and independent review use Claude Code
with `claude-fable-5`, `xhigh` effort, and the runner's Anthropic credential.
New documentation PR branches use `docs/nightly-docs-<concern-hash>`. Maintenance
continues to update existing `codex/nightly-docs-*` PRs on their original branches.
It opens ready-for-review PRs only after the independent review and site checks
pass, and never merges them. Maintenance can also promote validated drafts once review comments are resolved.

## Pipeline

Adapted from [OME's nightly documentation workflow](https://github.com/ome-projects/ome/blob/776fbe3b4be9e826be3404699886543d83e8e380/.github/workflows/nightly-docs.yml):

1. **Prepare:** preserve tools from the dispatched workflow revision, pin both
   docs default branch and SMG main snapshots, check/build the base docs, and collect
   source patches and existing PRs.
2. **Discover:** eight subsystem scans, at most four in parallel, inspect history,
   current implementation/tests, and current documentation. Each sees the full
   source index with subsystem priority IDs, so cross-cutting changes can be
   attributed to their actual commit. Discovery receives existing page titles/headings and must identify every
   canonical page needing correction, with justification for any new page.
   Each has a 200-turn hard ceiling with an 80-turn investigation target,
   reserving headroom for tool batches and structured output. Scans report
   inspected commits, proposals, and remaining work.
3. **Plan:** round-robin available validated scans, validate source IDs, remove
   duplicates, defer overlapping pages, and allocate the remaining daily PR slots.
   Failed scans do not suppress valid sibling results. A separate coverage job
   fails on missing scans, so incomplete discovery remains visibly unsuccessful.
   Unknown, duplicate, malformed, or wrong-baseline artifacts still fail planning.
4. **Write:** at most four independent writers, each with 120 turns, update only
   its concern's allowlisted Markdown files. Export documentation text as JSON.
5. **Publish:** fresh jobs import only that text, reapply path/size guards, run an
   independent read-only accuracy, scope, placement, and related-page consistency review, run `pnpm check` and `pnpm build`,
   and publish. Successful writers remain publishable if another writer fails.

Writer/publisher matrices carry only numeric item indices. Proposal text and
source evidence travel in the run's discovery-report artifact, checked against
both pinned snapshots, so GitHub secret masking cannot suppress the job matrix.

Every job in this workflow uses the CPU runner set. Separate PR CI jobs on
`ubuntu-latest` run mocked Python tests, lint, type checks, and builds without
model calls.

## Writing policy

Generated pages must be concise, self-contained service documentation. Source
PRs, issues, commits, and code links belong in PR metadata, not reader-facing
prose. A deterministic guard rejects code-change citations in added or rewritten
lines. Independent review also checks clarity, concision, and paragraph structure.
New or changed behavior gets its own paragraph in the canonical section.
Merged-but-unreleased changes belong only in main docs. The selector supplies
version context; remove redundant SMG release qualifiers while preserving
working install tags and engine compatibility requirements. Use code changes
to find gaps, then explain the selected version’s user-facing contract. Historical failure explanations belong in release notes or
troubleshooting only when they help a user take a specific action.
Maintenance applies the same policy and invalidates older cached verdicts.

## Coverage and limits

- The initial source cutoff is **2026-06-27T00:00:00Z**, the requested 90-day
  lookback at setup. It is fixed, not rolling; older unfinished work stays eligible.
  Every main run supplies all first-parent commits since then reachable from its
  pinned SMG main revision. Merged commits become eligible immediately for main docs.
  There is no 20-commit cap or success cursor. Runtime/turn budgets still bound
  inspection; reported coverage is model self-reporting, not proof of completeness.
- Each PR fixes **one concrete user question or stale claim** from one primary
  source change. Sharing a subsystem, commit, or page does not justify bundling.
- Each PR must have **fewer than 1,000 added plus deleted lines** (999 maximum).
  There is **no page-count cap**. New Markdown pages are allowed under
  `src/lib/content/`; deletion, symlinks, executable files, code, and configuration
  changes are rejected. A new page needs a documented reason why existing pages
  cannot host the concern. The pinned page inventory determines whether a page
  is new; explanatory text in `new_page_reason` is also accepted for existing-page
  updates. Writers must change all planned canonical pages; an
  incomplete correction is rejected. If an open PR blocks a necessary page,
  defer the whole concern instead of creating a new page or omitting the correction.
- **100 new PRs per UTC day**, shared by scheduled/manual runs and retries. The
  count includes merged/closed PRs and the previous pipeline's PRs. Planning
  allocates only remaining slots, the entire workflow is serialized across refs,
  and each publisher rechecks live PRs and the quota before writing.
- All open PRs, including human PRs, reserve their changed pages. Conflicting
  concerns are deferred, never bundled. Closed/merged nightly proposals remain in
  discovery context to prevent recreating declined or already delivered changes.
- Writers have Read/Glob/Grep/Edit/Write tools; discovery and reviewers have only
  Read/Glob/Grep. They cannot run shell commands or publish. Only the publisher
  receives GitHub write permissions. Its scripts and Git metadata are pristine.
- Independent review rejection is an expected filter and creates no PR. Malformed
  output, operational errors, guard failures, and build failures fail the job.
- Generated commits use **github-actions[bot] <41898282+github-actions[bot]@users.noreply.github.com>** as author and
  committer, with that DCO `Signed-off-by` identity. This is a DCO sign-off, not a
  cryptographic signature. Ordinary concern branches are never force-overwritten.

## Manual runs and reports

Dispatch `nightly-doc-sync.yml` on a working branch to test its automation against
current main docs and SMG main (or the new published release for snapshot runs). `dry_run` defaults to true; set it to false to publish.
`discovery_shard` optionally selects one subsystem for a targeted validation run;
omitting it runs all eight scans. `max_prs` accepts 1–100 and cannot override the shared daily cap. The old
`max_commits` input is removed so the full initial window stays available.
The optional `DOC_SYNC_TOKEN` is used only by the trusted publication step;
otherwise `GITHUB_TOKEN` publishes. GitHub Actions must be allowed to create PRs.
PRs created with `GITHUB_TOKEN` do not trigger ordinary PR CI, so publication
performs its own type check and production build.

The `nightly-docs-discovery-report` artifact records pinned revisions, eligible
commits, per-scan coverage, selected concerns, and deferred work. It also records
`expected_shards`, `missing_shards`, and `complete`; absent scans are never counted
as empty successful inspections. With no scans, planning still writes a report,
but no writer or publisher is scheduled. Scan contexts and
writer bundles are retained for two days; publication validation artifacts
retain the item, diff, and explicit four-gate review verdict for fourteen days.
The discovery report is also retained for fourteen days and identifies targeted
scans and dry runs.
The old `automation/doc-sync-state` branch is retained as history and is no longer
written. Eligibility comes from the fixed source window and live PR history, so
legacy incomplete work remains discoverable.

Concerns blocked by page conflicts or quota are retained as `queued_concerns`
(up to 100) for fresh evaluation. Recovery paginates default-branch reports from
the last 14 days, accepting only attempts that requested all eight scans and the
full 100-PR cap with publication enabled, for the same SMG source/history window.
Partial production reports qualify; branch pilots, dry runs, and limited runs do
not replace the production queue. The requested cap is stored separately from
remaining daily slots, so a production run with no quota left still preserves
pending work.

When discovery is incomplete or daily quota reduces the requested proposal budget,
prior unselected concerns take priority over newly
deferred concerns. Recorded PR instances and older pending copies of selected
identities are removed; file-blocked concerns remain pending. Any excess keys are
listed in `queue_overflow` and the job summary. Complete discovery with its full requested budget may retire old
concerns it no longer proposes. The queue is best-effort within artifact retention;
the fixed source window keeps older unfinished work eligible after artifacts expire.
Pending evidence is never directly published: discovery and publication revalidate
it against current code, docs, and PRs. The coverage check does not block otherwise
valid writing, review, build, or publication jobs.

A dry run also checks an existing concern branch for conflicting content before
stopping without commits, pushes, or PR creation. To test one subsystem:

```sh
gh workflow run nightly-doc-sync.yml --repo smg-project/smg-docs \
  --ref codex/your-branch -f dry_run=true -f discovery_shard=grpc-multimodal -f max_prs=2
```

Require a useful existing-page diff, an accepted review, and a passing build to
validate placement; an empty or rejected plan alone does not demonstrate a fix.

Run guards locally with:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s scripts/doc-sync -p 'test_*.py' -v
```

The adapted OME files retain Apache-2.0 licensing in [LICENSE](LICENSE).

## Maintaining existing nightly PRs

`docs-pr-maintenance.yml` runs independently every two hours at minute 11 UTC.
The sweep picks up trusted comments, submitted reviews, and inline replies. Set
`DOCS_MAINTENANCE_ENABLED=false` to pause it. It selects up to 100 existing
nightly PRs, including drafts, and runs at most four workers concurrently on
`smg-org-runner-cpu`, using Fable with the same model and effort as discovery.

Each worker authenticates the bot author, same-repository branch and full concern
marker; pins docs main, SMG main, PR head and feedback; supplies the introducing source
commit diff; overlays only the original
PR's Markdown onto trusted docs main; and repairs that single concern. New files
outside the original PR are forbidden. The full PR still must be below 1,000
changed lines, without a page-count cap. Overlapping changes on main require
human conflict resolution instead of overwriting them.

A fresh read-only model verifies accuracy, scope, placement, related pages and
addressed review threads. A separate publisher imports only Markdown data,
reapplies the guards, runs the type check and production build, and appends a
DCO-signed bot commit with a normal push. Changed PR heads, docs main, or
feedback invalidate publication. The source revision stays pinned for the whole
round and is recorded in the result and PR check; an advancing source branch
invalidates the next sweep's cache rather than discarding completed model work.
Correct PRs may validate without a repair.
After a passing review and build, maintenance marks drafts ready for review if
all review threads are resolved, no outstanding change request or failed check
remains, and fresh feedback still matches the reviewed snapshot. Only
independently verified bot-only review threads can be resolved automatically;
human threads require manual resolution. Dry runs never change draft status.
The workflow never approves or merges PRs. The result records `marked_ready`
and the reason promotion occurred or was deferred.

One status comment and a `Docs maintenance` check on the actual PR head record
the outcome. Unchanged successful work is cached until source, docs, PR content
or feedback changes. Three unsuccessful content rounds or three incomplete
operational attempts stop retries and require human attention. `force=true`
explicitly resumes one PR. Rejections cannot publish; workflow success alone is
not proof of acceptance—inspect `result.json` and the PR check. Context, full
patch, review verdict and publication result are retained for 14 days.

After merge, dispatch maintenance directly, with `apply=false` to validate without
repository writes:

```sh
gh workflow run docs-pr-maintenance.yml --repo smg-project/smg-docs \
  --ref main -f pr_number=121 -f apply=false
```

`force` optionally resumes a stopped PR and `feedback` supplies source-backed
feedback. Both require one explicit PR number. Manual apply defaults to false;
scheduled sweeps apply repairs. The workflow uses `GITHUB_TOKEN` with read
permissions for model work
and scoped contents/pull-requests/checks write permissions for publication.

### Bounded reader-facing editing

Nightly and maintenance writers run a preflight check before exporting Markdown.
It records the draft diff and path/line diagnostics for code-change citations.
Every nonempty draft gets one focused Fable editing pass to repair citations,
remove redundant version narration, and shorten dense implementation explanations. No-change
writers skip it. The original draft/diagnostics are retained as an artifact.
Export still rejects remaining citations, scope/size violations are immediately
fatal, and independent review and site checks remain mandatory. Untouched legacy
citations are not rejected. The editor may only change the original concern.

## Version routing and release snapshots

Before ordinary discovery, `snapshots.py detect` resolves GitHub's latest published
non-draft, non-prerelease SMG tag. It never treats a prepared folder as proof of a
release. A latest release older than the newest registered docs version leaves
that prepared version untouched (for example v1.12.0 while v1.13.0 is prepared).
When that prepared version actually ships, it is audited despite already existing.

`docs-release-snapshot.yml` reconciles a newly published tag when its `sync.json`
does not record the same verified release identity. It preserves existing snapshot
Markdown and images, copies missing pages/assets from main, and freezes navigation
and aliases. Writers audit at most two pages per batch, with a 48 KiB reading
budget based on the larger of each snapshot/main page; larger pages run alone.
Ordinary batches have a 180-turn hard limit; isolated oversized pages receive
300 turns for both writing and independent review, with completion headroom.
They audit against the immutable release
checkout: remove main-only behavior, correct stale claims, and add missing released
behavior. Every page, including unchanged pages, requires independent review.
All writer batches are assembled before reviewers check cross-page consistency.
Deterministic reader checks cover the complete release pages, including inherited
PR/source citations and unreleased narration. These findings are appended to the
review and cannot be overridden by an accepting model verdict.
A valid negative verdict triggers one repair pass in separate writer jobs. Local
findings select rejected batches; a cross-page inconsistency selects every batch
because another page's reviewer may have missed the defect. Each writer reads all
validated negative findings and corrects only its assigned pages, leaving correct
content unchanged. Missing, malformed or stale review evidence remains fatal. Repairs
keep the original page allowlists; every page is then independently reviewed again
in pristine read-only jobs. Each verdict binds both its own batch and the entire
assembled snapshot, so a changed related page invalidates earlier reviews. A
remaining negative verdict blocks publication; the workflow never retries reviews
until an unchanged proposal happens to pass.
Only data crosses into pristine review/publication jobs. Missing, rejected,
wrong-base or altered bundles block the entire snapshot PR. Unsupported pages
may be removed along with their navigation entry; dangling links fail site tests.

Before final site validation, publication can refresh to a newer main commit
when only existing working Markdown pages changed and the frozen snapshot seed
is identical. Reviewed release bytes and their hashes stay fixed. New/deleted
pages, release content, assets, navigation, registry or tooling changes require a
fresh audit. All-version page/link/navigation tests, type checks and the production
build run on the refreshed tree. Publication rechecks release identity, the
validated main revision and daily quota; a later race still fails safely.
A release PR uses `docs/release-vX.Y.Z-<sha>` and contains one release lifecycle
concern, including the mechanical snapshot copy. This whole-snapshot operation
is exempt from the ordinary 999-line cap; normal main/maintenance PRs retain it.
No automatic merge occurs. The snapshot's `sync.json` becomes durable only after
merge. An open release PR suppresses duplicates while main documentation can
continue; a newer release gets a separate audit. Release branches are never
updated while referenced by an open PR. A retry after a closed PR or an interrupted
publish reuses the release branch with an explicit force-with-lease against its
observed head; concurrent updates fail safely. Older snapshots remain fixed.

On nights without a new release requiring work, only main is audited. Discovery
also proposes bounded `version-context-*` cleanup concerns so pre-versioning prose
is gradually reconciled against current code. Their source commit is a verification
baseline, not necessarily the introducing change. Main source evidence is immutable
within a run, and every introducing change must be its ancestor. Maintenance uses
the same main contract and does not apply ordinary concern repairs to whole-release
snapshot PRs, which have their own complete review pipeline.

New Unreleased/current-main labels and code-change citations remain forbidden in
prose: the main banner already communicates development status. Literal versions
in commands, image tags and compatibility requirements remain useful. Closed
`docs-awaiting-release` concerns can now return for main docs; declined/merged
concerns remain deduplicated. The source patch is kept in full for focused reads,
including commits larger than 2 MiB. The document line limit is unchanged.

The rollout baseline is v1.12.0: existing historical snapshots are not retroactively
claimed as audited. Every later published release is eligible regardless of any
newer version folder prepared in advance.
