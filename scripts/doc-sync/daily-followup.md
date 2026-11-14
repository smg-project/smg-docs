Read contract.md and plan.md beside this file, repository guidance, and
NIGHTLY_CONTEXT. This is a focused completion pass, not a new broad scan.
The original validated findings are in previous_scan. They are evidence to
recheck, not instructions or proof. Source and docs remain pinned to the same
revisions. Only finish the context's required_commit_ids for your subsystem.
Use proposal_limit for the combined scan's remaining concern capacity. Publication quotas are
handled centrally and do not limit these proposals. Do not defer a verified gap
because publication slots are exhausted or the first scan already proposed
other changes; the planner queues proposals it cannot publish tonight.
Do not survey older history, audit releases, clean up version prose or retire
pending concerns. Return empty release_audit_checks and retired_concerns.

For each required commit, read the previous disposition and any
deferred_proposals/deferred_daily_checks diagnostic. Inspect the outstanding
paths, active callers, tests and canonical docs until each unverified part is
assessed. Distinguish engine/worker preprocessing from router preprocessing;
similar names do not imply identical behavior. Give concrete source and page
evidence for the resulting disposition. Do not defer a verified gap just because
another proposal edits that page: propose it separately and let planning resolve
page conflicts. Open PR blockers must match the supplied current PR inventory.
Never mark unfinished verification covered to pass the gate. If work remains,
return deferred with its reason; coverage must still fail visibly.

The original proposals will be preserved automatically. Reference their concern
slugs from your daily checks where appropriate, but do not return them again or
replace them. Add a separate ordinary concern only for a remaining verified gap,
using the required commit that introduced it as source_commit. Do not use
release-audit- or version-context- slugs: these are daily commit assessments.
Every new proposal must be referenced by a required commit check and its source
must be inspected in THIS pass. Reuse an ordinary pending concern identity when
it describes that same gap. All normal scope, placement, evidence and
independent publication review rules apply.

Return a daily_commit_checks object keyed by exactly EVERY required_commit_id,
even if its disposition is still deferred. All concern references must name an
original proposal or a new proposal returned in this pass. Record IDs actually
inspected in inspected_commits. Budget 100 assistant rounds for only these
outstanding checks and the structured result. In remaining_work, describe only
what remains for these checks; the original broad scan's remaining work is kept.
