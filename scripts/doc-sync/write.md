Read contract.md beside this file, repository guidance, NIGHTLY_ITEM and the
supplied source patch. SOURCE_ROOT is the immutable SMG main checkout. Inspect
implementation, tests, helpers and callers; discovery evidence is a hypothesis.
Update only doc_paths for exactly the planned concern. Correct all canonical
pages and all same-concern mentions, including tables, introductions and examples.
If required corrections lie outside the allowlist, leave the tree unchanged and
explain which paths are missing. Never knowingly export an incomplete fix.

For version-context-* concerns, verify behavior against the pinned checkout and
remove redundant release framing; the chosen source commit is the verification
baseline, not necessarily the introducing change. Otherwise verify the primary
source patch actually introduces the documented behavior. Unsupported, already
fixed or incomplete features require no change.

Follow existing Markdown/front matter. Keep fewer than 1,000 changed lines and
one concern. Do not delete pages or edit non-documentation files. Target 60 turns
investigating/editing within the 120-turn ceiling. Before finishing, reread every
edited section as a service user, not as an implementation report.
