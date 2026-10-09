# Nightly SMG documentation sync

The workflow runs at **09:43 UTC daily** (01:43 PST / 02:43 PDT), on
`smg-org-runner-cpu`. Discovery, writing, and independent review use Claude Code
with `claude-fable-5`, `xhigh` effort, and the runner's Anthropic credential.
It opens draft PRs and never merges them.

## Pipeline

Adapted from [OME's nightly documentation workflow](https://github.com/ome-projects/ome/blob/776fbe3b4be9e826be3404699886543d83e8e380/.github/workflows/nightly-docs.yml):

1. **Prepare:** preserve tools from the dispatched workflow revision, pin both
   repositories' default-branch snapshots, check/build the base docs, and collect
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

Every job in this workflow uses the CPU runner set. Separate PR CI jobs on
`ubuntu-latest` run mocked Python tests, lint, type checks, and builds without
model calls.

## Coverage and limits

- The initial source cutoff is **2026-06-27T00:00:00Z**, the requested 90-day
  lookback at setup. It is fixed, not rolling; older unfinished work stays eligible.
  Every run supplies all first-parent commits since then, plus all new commits.
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
  cryptographic signature. Existing remote branches are never force-overwritten.

## Manual runs and reports

Dispatch `nightly-doc-sync.yml` on a working branch to test its automation against
current main docs/source. `dry_run` defaults to true; set it to false to publish.
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
Draft status stays unchanged; the workflow never approves or merges PRs. Only
independently verified bot-only review threads can be resolved automatically.

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
