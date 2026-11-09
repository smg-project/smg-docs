"""A completion pass may finish daily work, but cannot erase earlier findings."""

import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import daily_coverage
import discovery
import discovery_followup as followup
import test_daily_coverage as fixtures
from test_nightly_docs import proposal


class FollowupTests(unittest.TestCase):
    setUp = fixtures.DailyCoverageTests.setUp
    check = fixtures.DailyCoverageTests.check

    def original(self):
        return {**self.scans[0], 'inspected_commits': [self.latest, self.recent],
                'concerns': [proposal(source_sha=self.latest)],
                'daily_commit_checks': [self.check(status='deferred', concerns=['wait-timeout'],
                    evidence='Router behavior proposed; worker launch rules remain unverified.'),
                    self.check(self.recent, status='covered')],
                'release_audit_checks': [], 'retired_concerns': [],
                'execution': {'assistant_rounds': 80}}

    def result(self, **changes):
        return json.dumps({'concerns': [], 'inspected_commits': [1], 'remaining_work': 'Done.',
                           'daily_commit_checks': {'1': {k: v for k, v in self.check(
                               status='proposed', concerns=['wait-timeout']).items() if k != 'source_sha'}},
                           'release_audit_checks': {}, 'retired_concerns': [], **changes})

    def test_context_targets_only_unfinished_daily_work_and_keeps_history_ids(self):
        original = self.original()
        before = copy.deepcopy((self.context, original))
        self.assertEqual(followup.required(self.context, original), [self.latest])
        scoped = followup.prepare(self.context, original, self.assignments)
        self.assertEqual(scoped['required_commit_ids'], [1])
        self.assertEqual(scoped['focus_commit_ids'], [1])
        self.assertEqual(len(scoped['code_history']), 3)
        self.assertEqual(scoped['release_audit_candidates'], [])
        self.assertEqual(scoped['previous_scan'], original)
        self.assertEqual(discovery.scan_schema(scoped)['properties']['daily_commit_checks']['required'], ['1'])
        self.assertEqual((self.context, original), before)
        self.assertEqual(followup.required(self.context, {**original, 'backlog': 1}), [])
        original['daily_commit_checks'][0]['status'] = 'blocked'
        self.assertEqual(followup.required(self.context, original), [])

    def test_merge_preserves_completed_findings_and_original_proposals(self):
        original = self.original()
        before = copy.deepcopy(original)
        result = followup.merge(self.context, original, self.result())
        self.assertEqual(original, before)
        self.assertEqual(result['concerns'], original['concerns'])
        self.assertEqual(result['execution'], original['execution'])
        self.assertEqual(result['daily_commit_checks'][1], original['daily_commit_checks'][1])
        self.assertTrue(daily_coverage.report(self.context, [result], ['api-protocols'])['complete'])

    def test_new_verified_gap_is_added_and_unfinished_work_still_fails_coverage(self):
        item = proposal(source_commit=1, concern='worker-launch-rules', question='Worker launch rules?')
        del item['source_sha']
        checks = {'1': {k: v for k, v in self.check(status='proposed',
                    concerns=['wait-timeout', 'worker-launch-rules']).items() if k != 'source_sha'}}
        result = followup.merge(self.context, self.original(), self.result(concerns=[item],
                                                                          daily_commit_checks=checks))
        self.assertEqual(len(result['concerns']), 2)
        checks['1'].update(status='deferred', concerns=[], evidence='Worker launch path is still unverified.')
        result = followup.merge(self.context, self.original(), self.result(daily_commit_checks=checks))
        self.assertFalse(daily_coverage.report(self.context, [result], ['api-protocols'])['complete'])

    def test_missing_original_disposition_is_added_without_dropping_other_checks(self):
        original = self.original()
        original['daily_commit_checks'] = original['daily_commit_checks'][1:]
        result = followup.merge(self.context, original, self.result())
        self.assertEqual({c['source_sha'] for c in result['daily_commit_checks']}, {self.latest, self.recent})
        self.assertEqual(result['daily_commit_checks'][0], original['daily_commit_checks'][0])

    def test_followup_rejects_changes_to_settled_checks_audits_or_queue_retirements(self):
        raw = json.loads(self.result())
        for changes in [
                {'daily_commit_checks': {}},
                {'daily_commit_checks': {'2': raw['daily_commit_checks']['1']}},
                {'release_audit_checks': {'foreign': {'status': 'covered'}}},
                {'retired_concerns': [{'concern': 'rollouts/wait-timeout', 'reason': 'Done'}]},
                {'inspected_commits': [1, 1]}]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                followup.merge(self.context, self.original(), json.dumps({**raw, **changes}))
        with self.assertRaisesRegex(ValueError, 'baseline'):
            followup.merge(self.context, {**self.original(), 'base_sha': 'f' * 40}, self.result())

    def test_followup_rejects_replacement_uninspected_and_unrelated_proposals(self):
        for concern, source, inspected in [('wait-timeout', 1, [1]), ('unrelated-gap', 3, [3]),
                                            ('worker-launch-rules', 1, []),
                                            ('release-audit-aaaa-gap', 1, [1]),
                                            ('version-context-gap', 1, [1])]:
            item = proposal(source_commit=source, concern=concern)
            del item['source_sha']
            check = {k: v for k, v in self.check(status='proposed', concerns=[concern]).items()
                     if k != 'source_sha'}
            with self.subTest(concern=concern, inspected=inspected), self.assertRaises(ValueError):
                followup.merge(self.context, self.original(), self.result(
                    concerns=[item], inspected_commits=inspected, daily_commit_checks={'1': check}))

    def test_uninspected_completed_disposition_and_false_page_blocker_still_fail(self):
        # Earlier inspection cannot stand in for this pass's fresh verification.
        check = {k: v for k, v in self.check(status='covered').items() if k != 'source_sha'}
        with self.assertRaisesRegex(ValueError, 'not inspected'):
            followup.merge(self.context, self.original(), self.result(
                inspected_commits=[], daily_commit_checks={'1': check}))
        # A page blocker must still exist in the pinned PR inventory.
        check.update(status='blocked', blockers=[{'pr': 123, 'path': proposal()['doc_paths'][0]}])
        with self.assertRaisesRegex(ValueError, 'not an open PR'):
            followup.merge(self.context, self.original(), self.result(daily_commit_checks={'1': check}))

    def test_prepare_and_merge_require_a_fresh_successful_trace(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            context_root = root / 'nightly-docs-context'
            context_root.mkdir()
            (context_root / 'context.json').write_text(json.dumps(self.context))
            (context_root / 'assignments.json').write_text(json.dumps(self.assignments))
            scan = root / 'scan.json'
            original = self.original()
            scan.write_text(json.dumps(original))
            trace = root / 'claude-execution-output.json'
            trace.write_text('stale successful first-pass trace')
            env = {'RUNNER_TEMP': directory, 'SOURCE_ROOT': directory, 'SCAN_OUTPUT': str(scan),
                   'NIGHTLY_CONTEXT': str(root / 'followup.json'), 'GITHUB_OUTPUT': str(root / 'output'),
                   'GITHUB_STEP_SUMMARY': str(root / 'summary'), 'SCAN_EXECUTION_FILE': str(trace),
                   'SCAN_ACTION_OUTCOME': 'success'}
            with patch.dict(os.environ, env), patch('sys.argv', ['followup.py', 'prepare']):
                followup.main()
            self.assertFalse(trace.exists())
            self.assertIn('needed=true', Path(env['GITHUB_OUTPUT']).read_text())
            with patch.dict(os.environ, env), patch('sys.argv', ['followup.py', 'merge']):
                with self.assertRaises((ValueError, FileNotFoundError)):
                    followup.main()
                self.assertEqual(json.loads(scan.read_text()), original)
                trace.write_text(json.dumps([
                    {'type': 'assistant', 'message': {'id': 'fresh-round'}},
                    {'type': 'result', 'subtype': 'success', 'is_error': False,
                     'num_turns': 1, 'structured_output': json.loads(self.result())}]))
                followup.main()
            merged = json.loads(scan.read_text())
            self.assertEqual(merged['daily_followup']['required_commits'], [self.latest])
            self.assertEqual(merged['daily_followup']['execution']['assistant_rounds'], 1)
            self.assertEqual(followup.required(self.context, merged), [])


if __name__ == '__main__':
    unittest.main()
