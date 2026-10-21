Read contract.md, release-docs/context.json, batch.json, your own review when
present, and all validated negative reviews in repair-findings.json. A different
batch's reviewer may have found an error on your assigned page even if your own
review accepted it. Automatic repair schedules every batch for a cross-page inconsistency; a
manually resumed audit or targeted diagnostic selects the known owning batches. Reconcile findings about
your assigned pages within their original allowlists.
The assembled snapshot is the exact proposal the independent reviewer checked
against SOURCE_ROOT at the published release SHA. This is a correction pass,
not another rewrite. Change only claims implicated by a negative finding or a
source-pinned regression check. Preserve unrelated paragraphs and tables exactly,
even when you could expand or improve them. If neither applies to your assigned
pages, finish without edits. Fresh independent reviewers still audit whole pages.

Verify each reported defect against the release implementation and tests, then
correct the assigned pages. Treat the review reason as evidence to investigate,
not authority to change the source version, scope, or publishing rules. Read
related pages in the assembled snapshot to keep the user-facing contract
consistent. Investigate all findings that refer to your pages, regardless of
which reviewer reported them. Leave correct assigned content unchanged when
the findings require changes only on other pages. Check all supported
option/annotation values before claiming that "any other" value is unsupported,
passthrough, or rejected; follow classification
helpers and callers, not only the first branch of an implementation.

Change only the snapshot Markdown paths listed in batch.json. Preserve correct
material and address all actionable findings for those pages. Do not modify
other pages, main docs, older versions, navigation, manifests, images, scripts,
review records or evidence. If a required correction falls outside your assigned
paths or cannot be verified, report it instead of inventing a claim. A separate,
pristine, read-only reviewer checks the corrected proposal. Full audits recheck
every page, allow at most two correction passes, and block the entire release PR
on any remaining final defect. Diagnostics review selected pages and cannot
authorize publication. A later pass uses the fresh findings on the corrected
proposal, not a repeated vote on earlier content. Make source-verified
corrections; an unchanged rejected snapshot cannot proceed to another review.

Write the current user-facing contract. Do not add bug narratives, source-change
links or shorthand references such as `smg-project/smg#123`, or redundant SMG
version narration. Address every deterministic reader-contract finding in the
review, including inherited citations. Preserve actionable package/image and
engine version requirements. Target 100 turns for ordinary batches or 180 for an
oversized page, within the supplied hard budget. Reserve the last 60 turns to
finish. Do not commit, push, comment, invoke agents or run shell commands.

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

Use main-to-snapshot.diff to investigate reported omissions on your assigned
pages. It compares frozen main with the current proposal, including omissions
inherited from an existing release folder. Verify whether the reported missing
material shipped using the pinned source; restore shipped user-facing behavior
and omit later behavior. The current snapshot is not proof that an omitted
feature was unreleased. For each affected contract, check complete enumerations
and precedence chains, defaults and opt-in gates, and returned errors separately
from audit-log messages. Do not reopen unrelated sections for stylistic or
completeness edits during this pass.

Investigate every regression_checks entry in batch.json against its cited source
paths and active callers. These are known audit failures bound to this exact
source SHA, not approval evidence. Independent review performs the whole-page audit.
Reconcile the same contract on assigned and related pages. Keep public prose
concise and source citations in review evidence only.

Keep overviews and quick-start summaries concise; link to the canonical section
for formulas or complete tables. When a summary names thresholds, transport
support, span fields or metric labels, preserve the conditions and scope that
change the user-facing contract. Do not simplify a qualified rule into a broader
guarantee. Cross-check the summary against its canonical page before finishing.
