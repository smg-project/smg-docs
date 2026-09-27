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
   current implementation/tests, and current documentation. Each has 100 Claude
   turns and reports inspected commits, proposals, and remaining work.
3. **Plan:** round-robin the scans, validate source IDs, remove duplicates, defer
   overlapping pages, and allocate the remaining daily PR slots.
4. **Write:** at most four independent writers, each with 120 turns, update only
   its concern's allowlisted Markdown files. Export documentation text as JSON.
5. **Publish:** fresh jobs import only that text, reapply path/size guards, run an
   independent read-only accuracy/scope review, run `pnpm check` and `pnpm build`,
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
  changes are rejected.
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
- Generated commits use **XinyueZhang369 <zoeyzhang369@gmail.com>** as author and
  committer, with that DCO `Signed-off-by` identity. This is a DCO sign-off, not a
  cryptographic signature. Existing remote branches are never force-overwritten.

## Manual runs and reports

Dispatch `nightly-doc-sync.yml` on a working branch to test its automation against
current main docs/source. `dry_run` defaults to true; set it to false to publish.
`max_prs` accepts 1–100 and cannot override the shared daily cap. The old
`max_commits` input is removed so the full initial window stays available.
The optional `DOC_SYNC_TOKEN` is used only by the trusted publication step;
otherwise `GITHUB_TOKEN` publishes. GitHub Actions must be allowed to create PRs.
PRs created with `GITHUB_TOKEN` do not trigger ordinary PR CI, so publication
performs its own type check and production build.

The `nightly-docs-discovery-report` artifact records pinned revisions, eligible
commits, per-scan coverage, selected concerns, and deferred work. Scan contexts and
writer bundles are retained for two days; the discovery report for fourteen.
The old `automation/doc-sync-state` branch is retained as history and is no longer
written. Eligibility comes from the fixed source window and live PR history, so
legacy incomplete work remains discoverable.

Run guards locally with:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s scripts/doc-sync -p 'test_*.py' -v
```

The adapted OME files retain Apache-2.0 licensing in [LICENSE](LICENSE).
