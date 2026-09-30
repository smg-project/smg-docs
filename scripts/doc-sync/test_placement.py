"""Regression coverage for existing-page corrections and justified new pages."""

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import discovery
import nightly_docs as docs
import test_nightly_docs as fixtures
from test_nightly_docs import pr


class PlacementTests(unittest.TestCase):
    setUp = fixtures.GitGuardTests.setUp
    cleanup = fixtures.GitGuardTests.cleanup
    git = fixtures.GitGuardTests.git
    origin = fixtures.GitGuardTests.origin

    def context(self, prs=None):
        return {"code_history": [self.base + " source change"], "existing_prs": prs or [],
                "doc_inventory": docs.doc_inventory(self.base)}

    def test_inventory_uses_pinned_pages_not_writer_additions_or_symlinks(self):
        self.path.write_text('---\ntitle: "Rollout waits"\n---\n## Timeout\n')
        self.git("add", ".")
        self.git("commit", "-qm", "Add headings")
        base = self.git("rev-parse", "HEAD")
        self.path.write_text("Changed by writer\n")
        (self.path.parent / "new.md").write_text("Uncommitted page\n")
        link = self.path.parent / "link.md"
        link.symlink_to(self.path.name)
        self.git("add", str(link))
        self.git("commit", "-qm", "Add symlink")
        pages = docs.doc_inventory(base)
        self.assertEqual(pages, [{"path": str(self.path), "title": "Rollout waits",
                                 "headings": ["Timeout"]}])
        self.assertEqual(docs.doc_inventory("HEAD"), pages)

    def test_missing_or_fabricated_placement_fails_before_writing(self):
        for decision in [None, {}, {"examined_pages": [], "canonical_pages": [], "new_page_reason": ""},
                         {"examined_pages": [docs.DOC_ROOT + "missing.md"],
                          "canonical_pages": [], "new_page_reason": "New reference"}]:
            with self.subTest(decision=decision), self.assertRaises(ValueError):
                docs.plan(json.dumps({"concerns": [{**self.item, "placement": decision}]}), self.context())

    def test_new_page_needs_reason_and_cannot_omit_planned_canonical_correction(self):
        new = str(self.path.parent / "new.md")
        item = {**self.item, "doc_paths": [new]}
        with self.assertRaisesRegex(ValueError, "Canonical pages"):
            docs.plan(json.dumps({"concerns": [item]}), self.context())
        item["placement"] = {"examined_pages": [str(self.path)], "canonical_pages": [],
                             "new_page_reason": ""}
        with self.assertRaisesRegex(ValueError, "New pages require"):
            docs.plan(json.dumps({"concerns": [item]}), self.context())
        item["placement"]["new_page_reason"] = "A distinct reader task; the examined reference covers another task."
        self.assertEqual(len(docs.plan(json.dumps({"concerns": [item]}), self.context())), 1)

    def test_writer_and_fresh_publisher_reject_new_page_without_canonical_edit(self):
        new = self.path.parent / "new.md"
        item = {**self.item, "doc_paths": [str(self.path), str(new)],
                "placement": {**self.item["placement"], "new_page_reason": "Separate example needs context."}}
        new.write_text("Correct new example, but old page is still wrong.\n")
        with self.assertRaisesRegex(ValueError, "canonical-page correction unchanged"):
            docs.validate_diff(item, self.base)
        new.unlink()
        bundle = json.dumps({"base_sha": self.base, "key": item["key"],
                             "files": {str(new): "Correct new example.\n"}})
        with self.assertRaisesRegex(ValueError, "canonical-page correction unchanged"):
            docs.import_bundle(item, self.base, bundle)
        self.path.write_text("Corrected existing page.\n")
        self.assertTrue(docs.validate_diff(item, self.base))

    def test_blocked_canonical_page_defers_whole_concern_including_new_page(self):
        item = {**self.item, "doc_paths": [str(self.path), str(self.path.parent / "new.md")],
                "placement": {**self.item["placement"], "new_page_reason": "Related example."}}
        human = pr(self.item, body="Manual update", branch="docs/manual")
        self.assertEqual(docs.plan(json.dumps({"concerns": [item]}), self.context([human])), [])

    def test_each_placement_review_gate_must_pass_and_missing_gates_fail_closed(self):
        good = {key: True for key in docs.REVIEW_GATES}
        good["reason"] = "Checked canonical and related pages."
        docs.review_passes(json.dumps(good))
        for key in ("placement_appropriate", "related_docs_consistent"):
            for value in (False, "true", None):
                with self.subTest(key=key, value=value), tempfile.TemporaryDirectory() as directory:
                    verdict = {**good, key: value}
                    if value is None:
                        verdict.pop(key)
                    raw = json.dumps(verdict)
                    with self.assertRaises(ValueError):
                        docs.review_passes(raw)
                    output = Path(directory) / "output"
                    with patch.dict(os.environ, {"GITHUB_OUTPUT": str(output),
                                                 "GITHUB_STEP_SUMMARY": str(Path(directory) / "summary")}):
                        if value is False:
                            docs.record_review(raw)
                            self.assertEqual(output.read_text(), "accepted=false\n")
                        else:
                            with self.assertRaises(ValueError):
                                docs.record_review(raw)
                            self.assertFalse(output.exists())

    def test_dry_run_checks_remote_conflicts_without_publishing(self):
        self.origin()
        self.path.write_text("Corrected existing page.\n")
        with patch.object(docs, "existing_prs", return_value=[]), patch.dict(os.environ, {"DRY_RUN": "true"}):
            docs.publish(self.item, "test/repo", self.base, "main")
            self.assertEqual(self.git("rev-parse", "HEAD"), self.base)
            self.assertEqual(self.git("ls-remote", "--heads", "origin"), "")
            self.git("push", "origin", f'HEAD:refs/heads/{self.item["branch"]}')
            with self.assertRaisesRegex(ValueError, "Existing branch"):
                docs.publish(self.item, "test/repo", self.base, "main")
            self.assertTrue(self.git("ls-remote", "--heads", "origin").startswith(self.base))

    def test_targeted_scan_is_explicit_and_default_still_requires_all_eight(self):
        slug = "grpc-multimodal"
        context = {**self.context(), "base_sha": self.base, "discovery_shard": slug}
        scan = {"shard": slug, "base_sha": self.base, "concerns": [self.item],
                "inspected_commits": [self.base], "remaining_work": "Done"}
        with patch.object(discovery, "partition", return_value={}):
            selected, deferred = discovery.combine([scan], context)
            self.assertEqual(len(selected), 1)
            self.assertEqual(deferred, [])
            with self.assertRaisesRegex(ValueError, "Missing or duplicate"):
                discovery.combine([scan], {**context, "discovery_shard": ""})
            with self.assertRaisesRegex(ValueError, "Unknown discovery"):
                discovery.combine([scan], {**context, "discovery_shard": "unknown"})
        self.assertEqual(len(discovery.scan_names({})), 8)


if __name__ == "__main__":
    unittest.main()
