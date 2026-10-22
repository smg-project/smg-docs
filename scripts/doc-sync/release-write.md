Read contract.md beside this file and release-docs/context.json and batch.json.
Use focused searches in release-history.txt and excluded-main-history.txt in the
supplied evidence directory for the assigned pages' subjects.
SOURCE_ROOT is exactly the published release tag. The target snapshot has been
seeded from main only where pages were missing; existing authored release content
was preserved. In a diagnostic run it is a saved writer proposal from an earlier
audit. Neither input is already verified release documentation.

Audit EVERY path in batch.json against the release implementation, helpers,
callers and tests. Compare the corresponding src/lib/content page for missing
material, but do not assume main content shipped. Use release history to find
missing released behavior; excluded-main-history is a discovery aid for removing
post-release behavior. The checkout is the final source of truth.

Correct the snapshot's user-facing contract, remove unsupported main-only
features and obsolete version narration, and add missing released behavior to
existing sections. Keep exact versions in working commands, image tags and engine
compatibility requirements. Never blindly strip version numbers or preserve a
false claim after deleting its caveat. New behavior gets a separate concise
paragraph. No code-change links, shorthand references such as `smg-project/smg#123`,
or implementation history in public prose. Remove inherited citations too; this
is a whole-page audit.

Change only the assigned snapshot Markdown paths. Read other snapshot pages for
consistency; never change main docs, another release, manifests, images, scripts,
metadata or evidence. If an assigned non-index page is entirely unsupported in
the release, delete it; the trusted assembler removes its manifest/nav entry.
Correct references to unsupported pages in assigned pages as needed. If a required
fix cannot be made, explain it; the independent reviewer must reject an incomplete
audit. Keep all index pages useful. Do not create new file paths.

Work only on the assigned one or two pages; the other batches cover the rest of
the snapshot. Start with the page's claims and use focused searches in the source
and histories. Do not read the entire release history or audit unrelated subsystems.
For option and annotation tables, verify the complete supported value set in
classification helpers and callers before writing "any other" or "unsupported".
Check linked pages for the same contract after updating such tables.
Reserve the last 60 turns to finish verified edits and report any unresolved gaps.

This job is one batch of a whole-release snapshot audit, not an ordinary concern
PR. The ordinary 999-line cap does not apply to the snapshot lifecycle PR. Keep
changes minimal and relevant to this release. Target 100 turns for ordinary
batches or 180 for an oversized page, within the hard budget supplied in the job
prompt. Do not commit, push, comment, invoke agents or run shell commands.

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

Keep overviews and quick-start summaries concise; link to the canonical section
for formulas or complete tables. When a summary names thresholds, transport
support, span fields or metric labels, preserve the conditions and scope that
change the user-facing contract. Do not simplify a qualified rule into a broader
guarantee. Cross-check the summary against its canonical page before finishing.
