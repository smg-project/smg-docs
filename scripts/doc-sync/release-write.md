Read contract.md beside this file and release-docs/context.json and batch.json.
Use focused searches in release-history.txt and excluded-main-history.txt in the
supplied evidence directory for the assigned pages' subjects.
SOURCE_ROOT is exactly the published release tag. The target snapshot has been
seeded from main only where pages were missing; existing authored release content
was preserved. It is NOT already verified release documentation.

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
paragraph. No code-change links or implementation history in public prose.

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
