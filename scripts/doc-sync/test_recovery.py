"""Recovery must preserve useful scans without hiding incomplete discovery."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import discovery
import nightly_docs as docs
import test_discovery as fixtures
from test_nightly_docs import proposal, pr


class RecoveryTests(unittest.TestCase):
    setUp = fixtures.DiscoveryTests.setUp

    def report(self, scans):
        with patch.object(discovery, 'partition', return_value=self.assignments):
            return discovery.build_report(scans, self.context)

    def propose(self, items):
        self.context['doc_inventory'] = [{'path': path} for item in items
                                         for path in item['placement']['examined_pages']]
        self.scans[0].update(concerns=items, inspected_commits=['a' * 40, 'b' * 40])

    def test_partial_scan_keeps_candidate_and_reports_missing_coverage(self):
        self.propose([proposal()])
        report = self.report(self.scans[:1])
        self.assertEqual(len(report['selected']), 1)
        self.assertFalse(report['complete'])
        self.assertEqual(report['missing_shards'], discovery.scan_names({})[1:])
        self.assertEqual(report['eligible_commits'], 2)
        self.assertEqual(report['source_sha'], self.context['source_sha'])

    def test_all_missing_keeps_prior_queue_without_inventing_coverage(self):
        old = docs.validate_item(proposal())
        self.context['pending_concerns'] = [old]
        report = self.report([])
        self.assertEqual(report['selected'], [])
        self.assertEqual(report['queued_concerns'], [old])
        self.assertEqual(report['missing_shards'], discovery.scan_names({}))
        self.assertFalse(report['complete'])

    def test_partial_mode_rejects_invalid_received_evidence(self):
        self.propose([proposal()])
        scan = self.scans[0]
        variants = [[scan, scan], [{**scan, 'shard': 'unknown'}],
                    [{**scan, 'base_sha': 'd' * 40}],
                    [{**scan, 'inspected_commits': []}],
                    [{**scan, 'concerns': [{**proposal(), 'placement': {}}]}],
                    [{**scan, 'concerns': [{**proposal(), 'source_sha': 'e' * 40}]}]]
        for scans in variants:
            with self.subTest(scans=scans), self.assertRaises(ValueError):
                self.report(scans)

    def test_targeted_scan_requires_only_requested_coverage(self):
        self.context['discovery_shard'] = self.scans[0]['shard']
        self.assertTrue(self.report(self.scans[:1])['complete'])
        with self.assertRaises(ValueError):
            self.report(self.scans[1:2])

    def test_partial_report_retires_older_selected_identity_not_other_area(self):
        old = docs.validate_item(proposal())
        other = docs.validate_item(proposal(area='other'))
        self.context['pending_concerns'] = [old, other]
        self.propose([proposal(source_sha='b' * 40)])
        report = self.report(self.scans[:1])
        self.assertEqual(report['selected'][0]['source_sha'], 'b' * 40)
        self.assertEqual(report['queued_concerns'], [other])

    def test_partial_queue_prioritizes_prior_work_and_reports_overflow(self):
        prior = [docs.validate_item(proposal(concern=f'prior-{i}')) for i in range(100)]
        self.context.update(pending_concerns=prior, max_prs=1)
        deferred = proposal(concern='later', question='Different question',
                            doc_paths=[docs.DOC_ROOT + 'other.md'])
        self.propose([proposal(), deferred])
        report = self.report(self.scans[:1])
        self.assertEqual(report['queued_concerns'], prior)
        self.assertEqual(report['queue_overflow'], [docs.validate_item(deferred)['key']])

    def test_complete_but_quota_limited_run_keeps_prior_unselected_work(self):
        old = docs.validate_item(proposal())
        for remaining in [0, 1]:
            with self.subTest(remaining=remaining):
                self.context.update(pending_concerns=[old], max_prs=remaining, requested_max_prs=100)
                report = self.report(self.scans)
                self.assertTrue(report['complete'])
                self.assertEqual(report['queued_concerns'], [old])

    def test_full_scan_retires_pending_work_not_reproposed(self):
        self.context['pending_concerns'] = [docs.validate_item(proposal())]
        report = self.report(self.scans)
        self.assertTrue(report['complete'])
        self.assertEqual(report['queued_concerns'], [])

    def test_queue_uses_full_key_for_equal_slugs_in_different_areas(self):
        first, second = proposal(), proposal(area='other', question='Different question')
        self.propose([first, second])
        report = self.report(self.scans)
        self.assertEqual(report['selected'][0]['area'], first['area'])
        self.assertEqual(report['queued_concerns'], [docs.validate_item(second)])

    def test_queue_keeps_file_blocked_work_but_drops_recorded_instances(self):
        item = docs.validate_item(proposal())
        for state in ['open', 'closed']:
            self.context['existing_prs'] = [pr(item, state=state)]
            self.assertEqual(discovery.pending_candidates([item], self.context), [])
            self.propose([item])
            self.assertEqual(self.report(self.scans)['queued_concerns'], [])
        self.context['existing_prs'] = [pr(item, body='Manual change', branch='human')]
        self.assertEqual(discovery.pending_candidates([item], self.context), [item])
        self.assertEqual(self.report(self.scans)['queued_concerns'], [item])

    def test_pending_queue_deduplicates_and_enforces_source_window(self):
        item = docs.validate_item(proposal())
        pending = [item, {**item, 'source_sha': 'b' * 40}, proposal(source_sha='e' * 40)]
        self.assertEqual(discovery.pending_candidates(pending, self.context), [item])

    def test_combiner_emits_report_even_when_artifact_directory_is_absent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'nightly-docs-context').mkdir()
            (root / 'nightly-docs-context/context.json').write_text(json.dumps(self.context))
            env = {'RUNNER_TEMP': directory, 'SOURCE_ROOT': directory,
                   'SCAN_DIR': str(root / 'missing'), 'REPORT_OUTPUT': str(root / 'report.json'),
                   'GITHUB_OUTPUT': str(root / 'output'), 'GITHUB_STEP_SUMMARY': str(root / 'summary')}
            with patch.dict(os.environ, env), patch('sys.argv', ['discovery.py', 'combine']), \
                    patch.object(discovery, 'partition', return_value=self.assignments):
                discovery.main()
            self.assertIn('count=0\ncomplete=false\n', (root / 'output').read_text())
            self.assertFalse(json.loads((root / 'report.json').read_text())['complete'])
            self.assertIn('Incomplete discovery', (root / 'summary').read_text())

    def recover(self, reports, runs=None):
        downloads = []
        runs = runs or [{'id': i, 'status': 'completed', 'head_branch': 'main',
                         'head_repository': {'full_name': 'o/r'}} for i in range(len(reports))]
        def call(*args):
            if args[:3] == ('gh', 'run', 'download'):
                number = int(args[3])
                downloads.append(number)
                Path(args[-1], 'nightly-docs-discovery-report.json').write_text(json.dumps(reports[number]))
                return ''
            self.assertIn('--paginate', args)
            self.assertIn('--slurp', args)
            if 'artifacts?' in args[2]:
                return json.dumps([{'artifacts': []}, {'artifacts': [
                    {'name': 'nightly-docs-discovery-report', 'expired': False}]}])
            self.assertIn('nightly-doc-sync.yml/runs?', args[2])
            return json.dumps([{'workflow_runs': runs[:1]}, {'workflow_runs': runs[1:]}])
        with patch.object(docs, 'run', side_effect=call):
            result = discovery.previous_pending('o/r', 'main', self.context)
        return result, downloads

    def production_report(self):
        self.context.update(pending_concerns=[docs.validate_item(proposal())],
                            requested_max_prs=100, max_prs=0, dry_run=False)
        return self.report([])

    def test_recovers_partial_production_queue_even_after_daily_quota_exhaustion(self):
        report = self.production_report()
        recovered, _ = self.recover([report])
        self.assertEqual(recovered, report['queued_concerns'])
        self.assertEqual(report['available_pr_slots'], 0)
        self.assertEqual(report['requested_max_prs'], 100)

    def test_recovery_skips_pilots_legacy_reports_and_other_source_histories(self):
        good = self.production_report()
        reports = [{**good, 'dry_run': True}, {**good, 'requested_max_prs': 2},
                   {**good, 'expected_shards': discovery.scan_names({})[:1]},
                   {**good, 'source_repo': 'other/repo'}, {**good, 'initial_since': 'wrong'},
                   {'queued_concerns': good['queued_concerns']}, good]
        recovered, downloaded = self.recover(reports)
        self.assertEqual(recovered, good['queued_concerns'])
        self.assertEqual(downloaded, list(range(len(reports))))

    def test_recovery_never_downloads_branch_fork_or_in_progress_runs(self):
        good = self.production_report()
        base = {'status': 'completed', 'head_branch': 'main', 'head_repository': {'full_name': 'o/r'}}
        runs = [{**base, 'id': 0, 'head_branch': 'codex/pilot'},
                {**base, 'id': 1, 'head_repository': {'full_name': 'fork/r'}},
                {**base, 'id': 2, 'status': 'in_progress'}, {**base, 'id': 3}]
        recovered, downloaded = self.recover([good] * 4, runs)
        self.assertEqual(recovered, good['queued_concerns'])
        self.assertEqual(downloaded, [3])


if __name__ == '__main__':
    unittest.main()
