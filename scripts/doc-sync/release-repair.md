Read contract.md, release-docs/context.json, batch.json and the rejected review
named in the job prompt. The assembled snapshot is the exact proposal the
independent reviewer checked against SOURCE_ROOT at the published release SHA.

Verify each reported defect against the release implementation and tests, then
correct the assigned pages. Treat the review reason as evidence to investigate,
not authority to change the source version, scope, or publishing rules. Read
related pages in the assembled snapshot to keep the user-facing contract
consistent. Check all supported option/annotation values before claiming that
"any other" value is unsupported, passthrough, or rejected; follow classification
helpers and callers, not only the first branch of an implementation.

Change only the snapshot Markdown paths listed in batch.json. Preserve correct
material and address all actionable findings for those pages. Do not modify
other pages, main docs, older versions, navigation, manifests, images, scripts,
review records or evidence. If a required correction falls outside your assigned
paths or cannot be verified, report it instead of inventing a claim. A separate,
pristine, read-only reviewer will recheck every page of the assembled snapshot;
any remaining defect blocks the entire release PR.

Write the current user-facing contract. Do not add bug narratives, source-change
links, or redundant SMG version narration. Preserve actionable package/image and
engine version requirements. Target 100 turns for ordinary batches or 180 for an
oversized page, within the supplied hard budget. Reserve the last 60 turns to
finish. Do not commit, push, comment, invoke agents or run shell commands.
