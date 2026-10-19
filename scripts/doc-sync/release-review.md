Read contract.md beside this file and the release-docs evidence. All writer
batches have been assembled into the proposed release snapshot. Review EVERY
path in batch.json, including unchanged and deleted pages, against SOURCE_ROOT
at the published release SHA. Compare main docs for missing released behavior,
release-history.txt and excluded-main-history.txt for candidates, and actual
implementation/tests for proof. Main may contain later unreleased changes.

Return the required JSON: accurate, complete, reader_focused,
related_docs_consistent (booleans), reviewed_pages (every assigned path exactly
once) and reason (specific source evidence and any defects). A deleted page
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
