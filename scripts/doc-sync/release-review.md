Read contract.md beside this file and the release-docs evidence. All writer
batches have been assembled into the proposed release snapshot. Review EVERY
path in batch.json, including unchanged and deleted pages, against SOURCE_ROOT
at the published release SHA. Compare main docs for missing released behavior,
release-history.txt and excluded-main-history.txt for candidates, and actual
implementation/tests for proof. Main may contain later unreleased changes.

Return the required JSON: accurate, complete, reader_focused,
related_docs_consistent (booleans), reviewed_pages (every assigned path exactly
once), reason (specific source evidence and any defects), and regression_checks.
Return exactly one regression_checks object per check ID in batch.json, with id,
satisfied (boolean), and evidence (specific source path/symbol and documentation
section checked). Return an empty array when no checks apply. A false result is
a content rejection even when the other gates pass. Missing, duplicate or
unknown check IDs and missing evidence fail validation. Never mark a check
satisfied solely because this checklist describes the expected behavior. A deleted page
passes only if its subject is unsupported in this release and incoming links/nav
are corrected. Do not accept empty/stub pages in place of usable documentation.
Verify whole pages, not just edited lines. Reject retained main-only features,
missing released behavior identified in the audit, blind version-word removal,
obsolete limitations, code-change links or shorthand PR references (including
inherited citations), old bug narratives and unnecessary
SMG release qualifiers. Keep actionable command/tag/engine-version requirements.

Check related pages in this assembled target snapshot for contradictions. Main
and older snapshots can legitimately differ. This is one release lifecycle PR;
its mechanical copy and reconciliation can exceed the ordinary 999-line cap.
If anything remains unverified, reject with a concrete reason. Never claim an
exhaustive source audit solely from the supplied history. Coverage means every
assigned documentation page was read and verified to the extent of its claims.
Focus investigation on the assigned one or two pages and their related claims;
other batches cover the rest. Reserve the last 60 turns for the complete verdict.
Read-only; never modify files, publish, comment or invoke agents. Target 100 turns
for ordinary batches or 180 for an oversized page, within the hard budget supplied
in the job prompt.

Verify operational claims through the active call path: configuration and
validation, caller/transport selection, overrides, and the final consumer. A
helper's capability, CLI help string, code comment, or an adapter's ability to
read a payload is not proof that the selected path uses it. Check early returns
and flags reset by callers. Check example values and paths against validators,
timeout scope against middleware on both buffered and streamed requests, and
metric timing against the actual recording anchor. Compare these same claims
in concept pages, examples, configuration tables and metric references.
For metrics, enumerate active recording sites and their endpoint, transport and
parser branches. A shared recorder or metric name does not imply every request
path emits it; qualify the public contract to the paths that actually do.

Read main-to-snapshot.diff for every assigned page. It compares the frozen main
page with the current proposal, including omissions inherited from an existing
release folder. For material present on main but absent from the snapshot,
verify whether it shipped using the pinned source; restore shipped user-facing
behavior and omit later behavior. Do not treat an existing snapshot or the
absence of a feature from its current text as evidence that it was not released.
Check complete enumerations and precedence chains, feature defaults and opt-in
gates, and returned errors separately from audit-log messages.

Investigate every regression_checks entry in batch.json against its cited source
paths and active callers. These are known audit failures bound to this exact
source SHA, not approval evidence and not a substitute for the whole-page audit.
Reconcile the same contract on assigned and related pages. Keep public prose
concise and source citations in review evidence only.
