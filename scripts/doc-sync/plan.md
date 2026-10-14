Read contract.md beside this file, repository guidance, and NIGHTLY_CONTEXT.
SMG source is pinned to source_target.sha (main). Inspect only working-version
pages in src/lib/content/, not released snapshots. You own the context's focus;
other scans own scan_responsibilities. The deploy-config scan owns CLI syntax;
subsystem scans own runtime behavior.

Use code_history and source_diffs/<sha>.patch to discover concrete user gaps.
Compare implementation/tests with current docs, including older changes since
the fixed cutoff. Consider pending_concerns. Survey broadly before investigating;
return remaining_work for unfinished candidates rather than claiming exhaustion.
Select source_commit using the supplied numeric IDs, never invent a SHA. Record
IDs actually inspected in inspected_commits. Target 80 investigative turns,
reserving the rest of 200 for the structured result. Do not chase a PR quota.

Also audit redundant SMG version narration on pages in your subsystem. Propose
one page/topic cleanup concern at a time, with a concern slug starting
version-context-, using the newest supplied source_commit as the verification
baseline. These editorial concerns do not require that commit to introduce the
behavior, but every retained claim must match the pinned main checkout. This
allows old version caveats to be cleaned even without a new functional change.
Do not remove useful engine versions, install tags or migration instructions.

Return the supplied JSON schema, at most max_prs concerns. Each concern answers
one concrete user question or corrects one stale claim. A large code commit can
produce several separate concerns. Use concise [Docs] titles and lowercase
hyphenated area/concern slugs, at most 64 characters. Include source evidence in
evidence and the user question in question, not historical failure narration.

Use doc_inventory to find existing canonical pages. Read related pages and record
them in placement.examined_pages. Include EVERY existing page that needs this
concern corrected in both canonical_pages and doc_paths. A new page requires a
specific distinct user task and new_page_reason explaining why existing sections
cannot host it. Keep new_page_reason empty otherwise. Search all related pages
for conflicting defaults, examples and claims before finalizing the allowlist.
If a required page is held by an open PR, defer the entire concern; do not omit
that page and knowingly propose an incomplete correction. Existing PRs (open,
merged or declined) prevent duplicates, except release_deferred proposals can
now be considered for main docs. Leave remaining_work for deferred concerns.
Do not write files, publish, comment or invoke agents.
