import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("sync", Path(__file__).with_name("sync.py"))
sync = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync)
PAGE = "src/lib/content/reference/configuration.md"


class ScopeTests(unittest.TestCase):
    def test_one_concern_edit(self):
        changed, diff, count = sync.make_changes(
            [{"path": PAGE, "old": "old\n", "new": "new\n"}], {PAGE: "old\n"}, [PAGE], 3, 250)
        self.assertEqual(changed, {PAGE: "new\n"})
        self.assertEqual(count, 2)
        self.assertIn("-old", diff)

    def test_cannot_escape_docs_or_scope(self):
        for path in [".github/workflows/ci.yml", "src/lib/content/../../evil.md",
                     "src/lib/content/new.md", "src/lib/content//reference/configuration.md"]:
            with self.subTest(path=path), self.assertRaises(ValueError):
                sync.make_changes([{"path": path, "old": "x", "new": "y"}],
                                  {path: "x"}, [PAGE], 3, 250)

    def test_ambiguous_and_stale_replacements_rejected(self):
        for original in ["repeat repeat", "not present"]:
            with self.assertRaises(ValueError):
                sync.make_changes([{"path": PAGE, "old": "repeat", "new": "new"}],
                                  {PAGE: original}, [PAGE], 3, 250)

    def test_line_and_file_limits_reject_bundles(self):
        with self.assertRaises(ValueError):
            sync.make_changes([{"path": PAGE, "old": "old\n", "new": "line\n" * 250}],
                              {PAGE: "old\n"}, [PAGE], 3, 250)
        pages = [f"src/lib/content/{n}.md" for n in range(4)]
        with self.assertRaises(ValueError):
            sync.make_changes([{"path": p, "old": "old", "new": "new"} for p in pages],
                              {p: "old" for p in pages}, pages, 3, 250)

    def test_mixed_commit_is_split_into_stable_concerns(self):
        def concern(slug):
            return {"slug": slug, "title": f"docs: explain {slug}", "area": slug,
                    "concern": slug, "evidence": "source.rs", "doc_paths": [PAGE]}
        plan = {"decision": "concerns", "reason": "Two independent gaps",
                "concerns": [concern("routing"), concern("authentication")]}
        sync.validate_plan(plan, {PAGE})
        self.assertNotEqual(sync.branch_name("a" * 40, "routing"),
                            sync.branch_name("a" * 40, "authentication"))
        plan["concerns"][1]["slug"] = "routing"
        with self.assertRaises(ValueError):
            sync.validate_plan(plan, {PAGE})

    def test_completed_decision_cannot_hide_pending_concerns(self):
        with self.assertRaises(ValueError):
            sync.validate_plan({"decision": "documented", "reason": "yes", "concerns": [{}]}, {PAGE})

    def test_backlog_and_new_changes_both_progress(self):
        commits = list("abcdefghij")
        self.assertEqual(sync.choose_commits(commits, {}, 4), list("abij"))
        self.assertEqual(sync.choose_commits(commits, {"a": {}, "j": {}}, 4), list("bchi"))
        self.assertEqual(sync.choose_commits(commits, {}, 1), ["a"])

    def test_deferred_audits_do_not_starve_fresh_work(self):
        records = {c: {"retry_after": "2000-01-01"} for c in "abc"}
        self.assertEqual(sync.choose_commits(list("abcdef"), records, 3), list("dab"))
        future = {"a": {"retry_after": "2999-01-01"}}
        self.assertEqual(sync.choose_commits(list("abc"), future, 3), list("bc"))

    def test_failed_validation_restores_original_document(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / PAGE).parent.mkdir(parents=True)
            (root / PAGE).write_text("original")
            with patch.object(sync.subprocess, "run") as run:
                run.return_value.returncode = 1
                run.return_value.stdout = "build failed"
                with self.assertRaises(ValueError):
                    sync.validate_site(root, {PAGE: "replacement"})
            self.assertEqual((root / PAGE).read_text(), "original")


class FakeLedger:
    def __init__(self):
        self.saves = 0

    def save(self):
        self.saves += 1


class PublishTests(unittest.TestCase):
    def item(self):
        return {"status": "pending", "prepared": {"branch": "docs/smg-sync-a-routing",
            "commit": "sha", "title": "docs: routing", "body": "one concern"}}

    def test_closed_pr_never_reopened(self):
        ledger, item = FakeLedger(), self.item()
        with patch.object(sync, "find_pr", return_value={"html_url": "closed-pr"}), \
             patch.object(sync, "GitHub") as gh:
            self.assertEqual(sync.publish(gh, ledger, item), "closed-pr")
            gh.api.assert_not_called()
        self.assertEqual(item["status"], "published")

    def test_partial_publication_resumes_without_force_push(self):
        item, ledger = self.item(), FakeLedger()
        with patch.object(sync, "find_pr", return_value=None), patch.object(sync, "GitHub") as gh:
            gh.api.side_effect = [[{"ref": "refs/heads/" + item["prepared"]["branch"],
                                   "object": {"sha": "sha"}}], {"html_url": "new-pr"}]
            self.assertEqual(sync.publish(gh, ledger, item), "new-pr")
            self.assertEqual(gh.api.call_count, 2)
            self.assertTrue(gh.api.call_args.args[2]["draft"])

    def test_human_modified_branch_is_not_overwritten(self):
        item = self.item()
        with patch.object(sync, "find_pr", return_value=None), patch.object(sync, "GitHub") as gh:
            gh.api.return_value = [{"ref": "refs/heads/" + item["prepared"]["branch"],
                                    "object": {"sha": "human-change"}}]
            with self.assertRaises(ValueError):
                sync.publish(gh, FakeLedger(), item)
            self.assertEqual(gh.api.call_count, 1)

    def test_dry_run_ledger_never_writes(self):
        with patch.object(sync, "GitHub") as gh:
            gh.api.return_value = []
            ledger = sync.Ledger(gh, "base", "2026-06-27", True)
            ledger.data["commits"]["test"] = {}
            ledger.save()
            self.assertEqual(gh.api.call_count, 1)

    def test_ledger_load_failure_is_not_treated_as_empty_history(self):
        with patch.object(sync, "GitHub") as gh:
            gh.api.side_effect = [[{"ref": "refs/heads/" + sync.STATE_BRANCH}], RuntimeError("HTTP 403")]
            with self.assertRaises(RuntimeError):
                sync.Ledger(gh, "base", "2026-06-27", False)


class ModelProtocolTests(unittest.TestCase):
    def test_truncated_model_output_is_never_accepted(self):
        with patch.dict(sync.os.environ, {"ANTHROPIC_API_KEY": "test-only"}), \
             patch.object(sync, "request", return_value={"stop_reason": "max_tokens", "content": []}):
            model = sync.Model({"max_model_calls": 10, "model": "test"}, None)
            with self.assertRaises(ValueError):
                model.run("audit", sync.PLAN_SCHEMA)

    def test_finish_requires_reading_source_and_docs(self):
        def response(name, args, ident):
            return {"stop_reason": "tool_use", "content": [
                {"type": "tool_use", "name": name, "input": args, "id": ident}]}
        outputs = [response("finish", {"decision": "documented"}, "early"),
                   response("read_file", {"repo": "source", "path": "src.rs"}, "source"),
                   response("read_file", {"repo": "docs", "path": PAGE}, "docs"),
                   response("finish", {"decision": "documented"}, "done")]
        with patch.dict(sync.os.environ, {"ANTHROPIC_API_KEY": "test-only"}), \
             patch.object(sync, "request", side_effect=outputs), patch.object(sync, "Evidence") as evidence:
            evidence.tool.return_value = {"lines": "some evidence"}
            model = sync.Model({"max_model_calls": 10, "model": "test"}, evidence)
            self.assertEqual(model.run("audit", sync.PLAN_SCHEMA), {"decision": "documented"})
            self.assertEqual(model.calls, 4)

    def test_call_budget_stops_unbounded_model_loop(self):
        with patch.dict(sync.os.environ, {"ANTHROPIC_API_KEY": "test-only"}), \
             patch.object(sync, "request") as request:
            model = sync.Model({"max_model_calls": 0, "model": "test"}, None)
            with self.assertRaises(RuntimeError):
                model.run("audit", sync.PLAN_SCHEMA)
            request.assert_not_called()


class EndToEndDryRunTests(unittest.TestCase):
    def test_two_concerns_one_pr_budget_no_writes_or_lost_backlog(self):
        import subprocess
        import os
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / PAGE).parent.mkdir(parents=True)
            (root / PAGE).write_text("old\n")
            second = "src/lib/content/reference/metrics.md"
            (root / second).write_text("old metric\n")
            (root / "source.rs").write_text("current source")
            (root / "scripts/doc-sync").mkdir(parents=True)
            (root / "scripts/doc-sync/config.json").write_text(
                Path(__file__).with_name("config.json").read_text())
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            subprocess.run(["git", "-C", str(root), "add", "."], check=True)
            subprocess.run(["git", "-C", str(root), "-c", "user.name=Test", "-c", "user.email=test@example.com",
                            "commit", "-qm", "test fixture"], check=True)
            def concern(slug, path):
                return {"slug": slug, "area": slug, "title": "docs: " + slug,
                        "concern": slug, "evidence": "source.rs", "doc_paths": [path]}
            responses = [
                {"decision": "concerns", "reason": "two separate gaps", "concerns":
                    [concern("routing", PAGE), concern("metrics", second)]},
                {"decision": "edit", "reason": "routing correction", "edits":
                    [{"path": PAGE, "old": "old\n", "new": "new\n"}]},
                {"single_concern": True, "accurate": True, "not_already_documented": True, "reason": "verified"}
            ]
            report = root / "report.json"
            with patch.dict(os.environ, {"GH_TOKEN": "test-only", "DOC_SYNC_REPORT": str(report)}), \
                 patch.object(sync.sys, "argv", ["sync.py", "--source", str(root), "--docs", str(root),
                                                  "--dry-run", "--max-commits", "1", "--max-prs", "1"]), \
                 patch.object(sync, "GitHub") as gh, patch.object(sync, "Model") as model, \
                 patch.object(sync, "validate_site") as validate:
                gh.return_value.repo = "smg-project/smg-docs"
                gh.return_value.api.return_value = []
                gh.return_value.pages.return_value = []
                model.return_value.calls = 3
                model.return_value.run.side_effect = responses
                result = sync.main()
                self.assertEqual(result, 0, report.read_text())
                validate.assert_called_once()
                self.assertEqual(gh.return_value.api.call_count, 1)  # Read existing ledger ref only.
            data = json.loads(report.read_text())
            drafts = [r for r in data["results"] if r.get("decision") == "would open draft PR"]
            self.assertEqual(len(drafts), 1)
            self.assertEqual(data["pending_concerns"], 2)  # Dry-run checkpoints nothing.
            self.assertNotIn("metrics.md", drafts[0]["diff"])
            self.assertEqual((root / PAGE).read_text(), "old\n")


if __name__ == "__main__":
    unittest.main()
