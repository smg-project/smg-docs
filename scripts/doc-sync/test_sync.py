import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

spec = importlib.util.spec_from_file_location("sync", Path(__file__).with_name("sync.py"))
sync = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync)
PAGE = "src/lib/content/reference/configuration.md"


class ScopeTests(unittest.TestCase):
    def test_one_concern_edit(self):
        changed, diff, count = sync.make_changes(
            [{"path": PAGE, "old": "old\n", "new": "new\n"}], {PAGE: "old\n"}, [PAGE], 999)
        self.assertEqual(changed, {PAGE: "new\n"})
        self.assertEqual(count, 2)
        self.assertIn("-old", diff)

    def test_cannot_escape_docs_or_scope(self):
        for path in [".github/workflows/ci.yml", "src/lib/content/../../evil.md",
                     "src/lib/content/new.md", "src/lib/content//reference/configuration.md"]:
            with self.subTest(path=path), self.assertRaises(ValueError):
                sync.make_changes([{"path": path, "old": "x", "new": "y"}],
                                  {path: "x"}, [PAGE], 999)

    def test_ambiguous_and_stale_replacements_rejected(self):
        for original in ["repeat repeat", "not present"]:
            with self.assertRaises(ValueError):
                sync.make_changes([{"path": PAGE, "old": "repeat", "new": "new"}],
                                  {PAGE: original}, [PAGE], 999)

    def test_999_changed_lines_allowed_but_1000_rejected(self):
        originals = {PAGE: "old\n"}
        edits = [{"path": PAGE, "old": "old\n", "new": "line\n" * 998}]
        self.assertEqual(sync.make_changes(edits, originals, [PAGE], 999)[2], 999)
        edits[0]["new"] = "line\n" * 999
        with self.assertRaises(ValueError):
            sync.make_changes(edits, originals, [PAGE], 999)

    def test_page_count_is_unlimited_within_single_concern(self):
        pages = [f"src/lib/content/{n}.md" for n in range(20)]
        edits = [{"path": p, "old": "old\n", "new": "new\n"} for p in pages]
        changed, _, count = sync.make_changes(edits, {p: "old\n" for p in pages}, pages, 999)
        self.assertEqual(len(changed), 20)
        self.assertEqual(count, 40)
        concern = {"slug": "routing", "title": "docs: routing", "area": "routing",
                   "concern": "one behavior across its guides", "evidence": "source.rs", "doc_paths": pages}
        sync.validate_plan({"decision": "concerns", "reason": "one gap", "concerns": [concern]}, set(pages))
        self.assertNotIn("maxItems", sync.CONCERN_SCHEMA["properties"]["doc_paths"])

    def test_header_like_changed_content_counts_toward_limit(self):
        originals = {PAGE: "--old\n"}
        edits = [{"path": PAGE, "old": "--old\n", "new": "++new\n" * 999}]
        with self.assertRaises(ValueError):
            sync.make_changes(edits, originals, [PAGE], 999)

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

    def test_first_ledger_checkpoint_is_atomic(self):
        with patch.object(sync, "GitHub") as gh:
            gh.api.return_value = []
            ledger = sync.Ledger(gh, "base", "2026-06-27", False)
            self.assertEqual(gh.api.call_count, 1)  # No empty branch at construction.
            gh.api.side_effect = [{"tree": {"sha": "base-tree"}},
                                  {"sha": "state-tree", "tree": [{"path": sync.STATE_PATH, "sha": "state-blob"}]},
                                  {"sha": "state-commit"}, {}]
            ledger.save()
            self.assertEqual(gh.api.call_args.args, ("git/refs", "POST", {
                "ref": "refs/heads/" + sync.STATE_BRANCH, "sha": "state-commit"}))
            self.assertEqual(ledger.file_sha, "state-blob")
            initial_commit = gh.api.call_args_list[-2].args[2]
            self.assertEqual(initial_commit["author"], sync.commit_metadata("")["author"])
            self.assertEqual(initial_commit["committer"], initial_commit["author"])

    def test_large_ledger_uses_blob_endpoint(self):
        import base64
        data = {"version": 1, "since": "2026-06-27", "commits": {"already-checked": {}}}
        with patch.object(sync, "GitHub") as gh:
            gh.api.side_effect = [[{"ref": "refs/heads/" + sync.STATE_BRANCH}],
                                  {"sha": "blob-sha", "encoding": "none", "content": ""},
                                  {"encoding": "base64", "content": base64.b64encode(json.dumps(data).encode()).decode()}]
            ledger = sync.Ledger(gh, "base", "2026-06-27", True)
            self.assertIn("already-checked", ledger.data["commits"])
            self.assertEqual(gh.api.call_args.args[0], "git/blobs/blob-sha")

    def test_ledger_load_failure_is_not_treated_as_empty_history(self):
        with patch.object(sync, "GitHub") as gh:
            gh.api.side_effect = [[{"ref": "refs/heads/" + sync.STATE_BRANCH}], RuntimeError("HTTP 403")]
            with self.assertRaises(RuntimeError):
                sync.Ledger(gh, "base", "2026-06-27", False)


class CommitIdentityTests(unittest.TestCase):
    def test_documentation_commit_metadata_uses_requested_identity(self):
        metadata = sync.commit_metadata("docs: explain routing")
        identity = {"name": "XinyueZhang369", "email": "zoeyzhang369@gmail.com"}
        self.assertEqual(metadata["author"], identity)
        self.assertEqual(metadata["committer"], identity)
        self.assertEqual(metadata["message"], "docs: explain routing\n\nSigned-off-by: XinyueZhang369 <zoeyzhang369@gmail.com>")

    def test_ledger_updates_use_same_identity_and_signoff(self):
        with patch.object(sync, "GitHub") as gh:
            gh.api.return_value = []
            ledger = sync.Ledger(gh, "base", "2026-06-27", False)
            ledger.file_sha = "previous-blob"
            gh.api.return_value = {"content": {"sha": "next-blob"}}
            ledger.save()
            payload = gh.api.call_args.args[2]
            self.assertEqual(payload["author"], sync.commit_metadata("")["author"])
            self.assertEqual(payload["committer"], payload["author"])
            self.assertIn("Signed-off-by: XinyueZhang369 <zoeyzhang369@gmail.com>", payload["message"])



class DailyLimitTests(unittest.TestCase):
    def pr(self, date="2026-09-25", branch="docs/smg-sync-a-routing", state="open", repo="smg-project/smg-docs"):
        return {"created_at": date + "T10:00:00Z", "state": state,
                "head": {"ref": branch, "repo": {"full_name": repo}}}

    def test_closed_and_merged_prs_still_count_today(self):
        with patch.object(sync, "GitHub") as gh:
            gh.repo = "smg-project/smg-docs"
            gh.pages.return_value = [self.pr(), self.pr(state="closed"), self.pr(state="closed")]
            self.assertEqual(sync.daily_pr_budget(gh, 100, "2026-09-25"), 97)
            self.assertIn("state=all", gh.pages.call_args.args[0])

    def test_prior_days_unrelated_and_fork_prs_do_not_count(self):
        with patch.object(sync, "GitHub") as gh:
            gh.repo = "smg-project/smg-docs"
            gh.pages.return_value = [self.pr(branch="docs/manual-fix"), self.pr(repo="someone/fork"),
                                    self.pr(), self.pr(date="2026-09-24")]
            self.assertEqual(sync.daily_pr_budget(gh, 100, "2026-09-25"), 99)

    def test_reruns_cannot_exceed_daily_cap(self):
        with patch.object(sync, "GitHub") as gh:
            gh.repo = "smg-project/smg-docs"
            gh.pages.return_value = [self.pr()] * 99
            self.assertEqual(sync.daily_pr_budget(gh, 100, "2026-09-25"), 1)
            gh.pages.return_value = [self.pr()] * 100
            self.assertEqual(sync.daily_pr_budget(gh, 100, "2026-09-25"), 0)
            gh.pages.return_value = [self.pr()] * 101
            self.assertEqual(sync.daily_pr_budget(gh, 100, "2026-09-25"), 0)



class TransportFailureTests(unittest.TestCase):
    def test_get_recovers_from_transient_dns_failure(self):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b'{"ok": true}'
        with patch.object(sync.urllib.request, "urlopen", side_effect=[
                sync.urllib.error.URLError("sensitive DNS details"), response]) as open_url, \
             patch.object(sync.time, "sleep") as sleep:
            self.assertEqual(sync.request("https://api.github.com/test", "secret"), {"ok": True})
            self.assertEqual(open_url.call_count, 2)
            sleep.assert_called_once()

    def test_anthropic_recovers_from_response_read_timeout(self):
        responses = [MagicMock(), MagicMock()]
        responses[0].__enter__.return_value.read.side_effect = TimeoutError("secret diagnostic")
        responses[1].__enter__.return_value.read.return_value = b'{"ok": true}'
        with patch.object(sync.urllib.request, "urlopen", side_effect=responses) as open_url, \
             patch.object(sync.time, "sleep"):
            self.assertEqual(sync.request("https://api.anthropic.com/v1/messages", "secret", "POST",
                                          {"model": "test"}, anthropic=True), {"ok": True})
            self.assertEqual(open_url.call_count, 2)

    def test_exhausted_transport_retries_are_bounded_and_sanitized(self):
        for exc in (sync.urllib.error.URLError("secret diagnostic"),
                    TimeoutError("secret diagnostic"), ConnectionResetError("secret diagnostic")):
            with self.subTest(error=type(exc).__name__), \
                 patch.object(sync.urllib.request, "urlopen", side_effect=exc) as open_url, \
                 patch.object(sync.time, "sleep") as sleep:
                with self.assertRaises(RuntimeError) as raised:
                    sync.request("https://api.github.com/test?token=secret", "secret")
                self.assertEqual(open_url.call_count, 4)
                self.assertEqual(sleep.call_count, 3)
                self.assertEqual(str(raised.exception), "GET /test: " + type(exc).__name__)
                self.assertNotIn("secret", str(raised.exception))

    def test_github_writes_are_not_retried_after_ambiguous_failure(self):
        for method in ("POST", "PUT", "PATCH"):
            with self.subTest(method=method), \
                 patch.object(sync.urllib.request, "urlopen", side_effect=TimeoutError("secret")) as open_url, \
                 patch.object(sync.time, "sleep") as sleep:
                with self.assertRaises(RuntimeError):
                    sync.request("https://api.github.com/test", "secret", method, {"write": True})
                self.assertEqual(open_url.call_count, 1)
                sleep.assert_not_called()


class MalformedPlanTests(unittest.TestCase):
    def test_non_object_concerns_raise_validation_errors(self):
        for concern in ("routing", None, [], 42):
            with self.subTest(concern=concern), self.assertRaises(ValueError):
                sync.validate_plan({"decision": "concerns", "reason": "a gap", "concerns": [concern]}, {PAGE})

    def test_non_object_plans_raise_validation_errors(self):
        for plan in (None, [], "routing"):
            with self.subTest(plan=plan), self.assertRaises(ValueError):
                sync.validate_plan(plan, {PAGE})

    def test_non_string_slugs_raise_validation_errors(self):
        for slug in (None, [], 42):
            with self.subTest(slug=slug), self.assertRaises(ValueError):
                sync.validate_plan({"decision": "concerns", "reason": "a gap", "concerns": [{"slug": slug}]}, {PAGE})

    def test_bad_item_is_deferred_without_stopping_next_commit(self):
        failures = [{"decision": "concerns", "reason": "a gap", "concerns": ["routing"]},
                    RuntimeError("POST /v1/messages: TimeoutError")]
        for failed_result in failures:
            with self.subTest(failure=failed_result), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root / "scripts/doc-sync").mkdir(parents=True)
                (root / "scripts/doc-sync/config.json").write_text(Path(__file__).with_name("config.json").read_text())
                report = root / "report.json"
                with patch.dict(sync.os.environ, {"GH_TOKEN": "test-only", "DOC_SYNC_REPORT": str(report)}), \
                     patch.object(sync.sys, "argv", ["sync.py", "--source", str(root), "--docs", str(root),
                                                      "--dry-run", "--max-commits", "2"]), \
                     patch.object(sync, "GitHub") as gh, patch.object(sync, "Model") as model, \
                     patch.object(sync, "Evidence") as evidence, patch.object(sync, "git") as git:
                    gh.return_value.repo = "smg-project/smg-docs"
                    gh.return_value.api.return_value = []
                    gh.return_value.pages.return_value = []
                    evidence.return_value.refs = {"source": "source-head", "docs": "docs-head"}
                    evidence.return_value.doc_paths = {PAGE}
                    git.side_effect = ["first\nsecond\n", "first patch", "second patch"]
                    model.return_value.calls = 2
                    model.return_value.run.side_effect = [failed_result,
                        {"decision": "documented", "reason": "already covered", "concerns": []}]
                    self.assertEqual(sync.main(), 1)
                data = json.loads(report.read_text())
                self.assertEqual(len(data["errors"]), 1)
                self.assertEqual(data["errors"][0]["commit"], "first")
                self.assertEqual(data["results"][0]["commit"], "second")
                self.assertEqual(data["results"][0]["decision"], "documented")
                self.assertEqual(data["unreviewed_commits"], 1)



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

    def test_non_object_finish_result_is_rejected_after_evidence_reads(self):
        outputs = [{"stop_reason": "tool_use", "content": [
            {"type": "tool_use", "id": "source", "name": "read_file", "input": {"repo": "source", "path": "src.rs"}},
            {"type": "tool_use", "id": "docs", "name": "read_file", "input": {"repo": "docs", "path": PAGE}}]},
            {"stop_reason": "tool_use", "content": [
                {"type": "tool_use", "id": "done", "name": "finish", "input": ["malformed"]}]}]
        with patch.dict(sync.os.environ, {"ANTHROPIC_API_KEY": "test-only"}), \
             patch.object(sync, "request", side_effect=outputs), patch.object(sync, "Evidence") as evidence:
            evidence.tool.return_value = {"lines": "some evidence"}
            model = sync.Model({"max_model_calls": 10, "model": "test"}, evidence)
            with self.assertRaisesRegex(ValueError, "must be an object"):
                model.run("audit", sync.PLAN_SCHEMA)

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
            # The docs snapshot can have an old/broken automation config: the
            # dispatched automation's own config must be used instead.
            (root / "scripts/doc-sync/config.json").write_text("invalid old docs config")
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
            self.assertIn("## Concern\n\nrouting correction", drafts[0]["body"])
            self.assertEqual((root / PAGE).read_text(), "old\n")


class RecoveryTests(unittest.TestCase):
    def item(self):
        return {"status": "pending", "concern": {"slug": "routing", "concern": "hypothesis",
                "doc_paths": [PAGE]}, "prepared": {"branch": "docs/smg-sync-old-routing",
                "commit": "old", "title": "docs: routing", "body": "old claims"}}

    def test_legacy_draft_is_regenerated_without_modifying_old_branch(self):
        item, ledger = self.item(), FakeLedger()
        with patch.object(sync, "GitHub") as gh:
            gh.api.return_value = [{"ref": "refs/heads/" + item["prepared"]["branch"],
                                    "object": {"sha": "old"}}]
            sync.refresh_prepared(gh, ledger, item, {"source": "s", "docs": "d"})
            gh.api.assert_called_once()
            self.assertEqual(len(gh.api.call_args.args), 1)  # Only reads GitHub.
        self.assertNotIn("prepared", item)
        self.assertEqual(item["superseded"][0]["commit"], "old")
        self.assertEqual(sync.candidate_branch("a" * 40, item), "docs/smg-sync-aaaaaaaaaaaa-routing-r1")
        self.assertIn("release availability", item["last_error"])
        self.assertEqual(ledger.saves, 1)

    def test_human_changes_block_regeneration(self):
        item, ledger = self.item(), FakeLedger()
        with patch.object(sync, "GitHub") as gh:
            gh.api.return_value = [{"ref": "refs/heads/" + item["prepared"]["branch"],
                                    "object": {"sha": "human"}}]
            with self.assertRaisesRegex(ValueError, "modified"):
                sync.refresh_prepared(gh, ledger, item, {"source": "s", "docs": "d"})
        self.assertIn("prepared", item)
        self.assertEqual(ledger.saves, 0)

    def test_current_validation_and_snapshots_resume_without_regeneration(self):
        item, ledger = self.item(), FakeLedger()
        refs = {"source": "s", "docs": "d"}
        item["prepared"].update(validation_version=sync.VALIDATION_VERSION, refs=refs)
        with patch.object(sync, "GitHub") as gh:
            sync.refresh_prepared(gh, ledger, item, refs)
            gh.api.assert_not_called()
        self.assertIn("prepared", item)
        self.assertEqual(ledger.saves, 0)

    def test_source_or_docs_change_invalidates_saved_validation(self):
        for refs in ({"source": "new", "docs": "d"}, {"source": "s", "docs": "new"}):
            item, ledger = self.item(), FakeLedger()
            item["prepared"].update(validation_version=sync.VALIDATION_VERSION,
                                    refs={"source": "s", "docs": "d"})
            with patch.object(sync, "GitHub") as gh:
                gh.api.return_value = []
                sync.refresh_prepared(gh, ledger, item, refs)
            self.assertNotIn("prepared", item)

    def test_rejected_patch_feedback_reaches_correction_attempt(self):
        item, ledger = self.item(), FakeLedger()
        edits = {"decision": "edit", "reason": "accurate summary", "edits": [
            {"path": PAGE, "old": "old", "new": "new"}]}
        reject = {"single_concern": True, "accurate": False, "not_already_documented": True,
                  "reason": "Only the logprob path forces JSON; inspect the caller"}
        accept = {**reject, "accurate": True, "reason": "Verified conditional scope"}
        with patch.object(sync, "Model") as model:
            model.run.side_effect = [edits, reject, edits, accept]
            result = sync.draft_changes(model, {"max_changed_lines_per_pr": 999}, ledger,
                                        item, "sha", {PAGE: "old"})
            self.assertIn(reject["reason"], model.run.call_args_list[2].args[0])
            self.assertIn("Previous rejected patch:", model.run.call_args_list[2].args[0])
            self.assertEqual(result[1], {PAGE: "new"})
            self.assertEqual(model.run.call_count, 4)
        self.assertEqual(ledger.saves, 1)

    def test_repeated_rejection_preserves_feedback_and_fails(self):
        item, ledger = self.item(), FakeLedger()
        edit = {"decision": "edit", "edits": [{"path": PAGE, "old": "old", "new": "new"}]}
        reject = {"single_concern": True, "accurate": False, "not_already_documented": True,
                  "reason": "Unverified release claim"}
        with patch.object(sync, "Model") as model:
            model.run.side_effect = [edit, reject, edit, reject]
            with self.assertRaisesRegex(ValueError, "Unverified release claim"):
                sync.draft_changes(model, {"max_changed_lines_per_pr": 999}, ledger,
                                   item, "sha", {PAGE: "old"})
            self.assertEqual(model.run.call_count, 4)
        self.assertIn("Unverified release claim", item["last_error"])
        self.assertIn("+new", item["last_diff"])
        self.assertEqual(ledger.saves, 2)

    def test_runtime_budget_does_not_call_model_after_deadline(self):
        with patch.dict(sync.os.environ, {"ANTHROPIC_API_KEY": "test-only"}), \
             patch.object(sync.time, "monotonic", side_effect=[100, 161]), \
             patch.object(sync, "request") as request:
            model = sync.Model({"max_model_calls": 100, "max_runtime_minutes": 1}, None)
            with self.assertRaises(sync.BudgetExhausted):
                model.run("audit", sync.PLAN_SCHEMA)
            request.assert_not_called()

    def test_uncapped_history_includes_every_commit_and_prioritizes_old_and_new(self):
        commits = [str(n) for n in range(563)]
        selected = sync.choose_commits(commits, {}, 0)
        self.assertEqual(len(selected), 563)
        self.assertEqual(set(selected), set(commits))
        self.assertEqual(selected[:4], ["0", "562", "1", "561"])

    def test_budget_stop_keeps_audit_unreviewed_without_marking_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, gh, model, evidence = Path(tmp), MagicMock(), MagicMock(), MagicMock()
            report = root / "report.json"
            gh.api.return_value = []
            gh.pages.return_value = []
            evidence.refs = {"source": "source", "docs": "docs"}
            evidence.doc_paths = {PAGE}
            model.calls = 0
            model.run.side_effect = sync.BudgetExhausted("time budget")
            with patch.dict(sync.os.environ, {"GH_TOKEN": "test", "DOC_SYNC_REPORT": str(report)}), \
                 patch.object(sync.sys, "argv", ["sync.py", "--source", tmp, "--docs", tmp, "--dry-run"]), \
                 patch.object(sync, "GitHub", return_value=gh), \
                 patch.object(sync, "Model", return_value=model), \
                 patch.object(sync, "Evidence", return_value=evidence), \
                 patch.object(sync, "git", side_effect=["first\n", "patch"]):
                self.assertEqual(sync.main(), 0)
            data = json.loads(report.read_text())
            self.assertEqual(data["unreviewed_commits"], 1)
            self.assertTrue(data["budget_limited"])
            self.assertEqual(data["errors"], [])


if __name__ == "__main__":
    unittest.main()
