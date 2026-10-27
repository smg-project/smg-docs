"""Recent coverage is mandatory, independently of historical PR selection."""

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import daily_coverage as daily
import discovery
import nightly_docs as docs
import test_nightly_docs as fixtures
from test_nightly_docs import proposal, pr


class DailyCoverageTests(unittest.TestCase):
    def setUp(self):
        self.old, self.recent, self.latest = (c * 40 for c in 'abc')
        self.history = [self.latest + ' latest', self.recent + ' recent', self.old + ' old']
        self.context = {'code_history': self.history, 'source_sha': self.latest,
                        'base_sha': 'd' * 40, 'existing_prs': [],
                        'daily_window': {'start': '2026-10-25T10:00:00+00:00',
                                         'end': '2026-10-26T10:00:00+00:00',
                                         'commits': [self.latest, self.recent]},
                        'doc_inventory': [{'path': p} for p in proposal()['doc_paths']]}
        self.assignments = {name: [] for name, _, _ in discovery.SHARDS}
        self.assignments['api-protocols'] = self.history
        self.assignments['operations'] = self.history[:1]
        self.context['daily_assignments'] = daily.assign(self.context, {}, self.assignments)
        self.scans = [{'shard': name, 'base_sha': self.context['base_sha'],
                       'concerns': [], 'inspected_commits': [], 'remaining_work': 'Older backlog.'}
                      for name, _, _ in discovery.SHARDS]

    def check(self, sha=None, **changes):
        return {'source_sha': sha or self.latest, 'status': 'not_applicable', 'concerns': [],
                'evidence': 'The patch only refactors internal tests; no public behavior changed.', **changes}

    def test_recent_checks_do_not_remove_historical_or_cross_cutting_evidence(self):
        scoped = discovery.scan_context(self.context, 'api-protocols', self.assignments)
        self.assertEqual(scoped['required_commit_ids'], [1, 2])
        self.assertEqual(scoped['focus_commit_ids'], [1, 2, 3])
        self.assertEqual(len(scoped['code_history']), 3)
        self.assertEqual(self.context['daily_assignments']['operations'], [self.latest])
        self.assertEqual(self.context['code_history'], self.history)

    def test_delayed_run_and_unfinished_checks_extend_window(self):
        self.context['daily_window']['commits'] = []
        prior = {'source_sha': self.recent, 'daily_coverage': {'checks': [
            {'shard': 'api-protocols', **self.check(self.old, status='deferred')},
            {'shard': 'api-protocols', **self.check(self.recent, status='covered')},
            {'shard': 'operations', **self.check('f' * 40, status='uninspected')}]}}
        assigned = daily.assign(self.context, prior, self.assignments)
        self.assertEqual(assigned['api-protocols'], [self.latest, self.old])
        self.assertEqual(assigned['operations'], [self.latest])

    def test_missing_assigned_recent_commit_is_an_error(self):
        with self.assertRaisesRegex(ValueError, 'lack discovery assignments'):
            daily.assign(self.context, {}, {'operations': []})

    def test_pending_checks_are_not_truncated_at_the_pr_cap(self):
        history = [f'{n:040x} change' for n in range(120)]
        context = {**self.context, 'code_history': history, 'daily_window': {'commits': []}}
        prior = {'daily_coverage': {'checks': [
            {'shard': 'operations', **self.check(line.split()[0], status='deferred')}
            for line in history]}}
        self.assertEqual(len(daily.assign(context, prior, {'operations': history})['operations']), 120)

    def test_one_commit_can_reference_several_concerns_and_shared_sources(self):
        checks = [self.check(status='proposed', concerns=['first', 'second'])]
        scan = {**self.scans[0], 'inspected_commits': [self.latest],
                'daily_commit_checks': checks,
                'concerns': [proposal(concern='first'), proposal(concern='second')]}
        self.assertEqual(daily.validate(scan, self.context, 'api-protocols'), checks)

    def test_rejects_false_or_ambiguous_accounting(self):
        scan = {**self.scans[0], 'inspected_commits': [self.latest], 'concerns': [proposal()]}
        bad = [self.check(self.old), self.check(status='done'), self.check(evidence=''),
               self.check(self.recent), self.check(status='proposed'),
               self.check(concerns=['wait-timeout']),
               self.check(status='proposed', concerns=['missing']),
               self.check(status='proposed', concerns=['wait-timeout', 'wait-timeout'])]
        for check in bad:
            with self.subTest(check=check), self.assertRaises(ValueError):
                daily.validate({**scan, 'daily_commit_checks': [check]}, self.context, 'api-protocols')
        for checks in [[self.check(), self.check()], None]:
            with self.assertRaises(ValueError):
                daily.validate({**scan, 'daily_commit_checks': checks}, self.context, 'api-protocols')
        with self.assertRaisesRegex(ValueError, 'ambiguous'):
            daily.validate({**scan, 'concerns': [proposal(), proposal(area='other')],
                            'daily_commit_checks': [self.check(status='proposed', concerns=['wait-timeout'])]},
                           self.context, 'api-protocols')

    def test_missing_or_deferred_accounting_keeps_good_proposals_and_prior_queue(self):
        for checks in [[], [self.check(status='deferred')]]:
            with self.subTest(checks=checks):
                self.context['pending_concerns'] = [proposal(concern='older-pending')]
                self.scans[0].update(concerns=[proposal()], inspected_commits=[self.old],
                                     daily_commit_checks=checks)
                report = discovery.build_report(self.scans, self.context)
                self.assertFalse(report['complete'])
                self.assertEqual(report['missing_shards'], [])
                self.assertEqual(len(report['selected']), 1)
                self.assertEqual(report['queued_concerns'][0]['concern'], 'older-pending')
                self.assertEqual(len(report['daily_coverage']['checks']), 3)

    def test_missing_reference_diagnostic_identifies_commit_and_slug(self):
        scan = {**self.scans[0], 'inspected_commits': [self.latest],
                'concerns': [proposal()], 'daily_commit_checks': [
                    self.check(status='proposed', concerns=['rollouts/wait-timeout'])]}
        with self.assertRaisesRegex(ValueError, 'missing or ambiguous') as error:
            daily.validate(scan, self.context, 'api-protocols')
        self.assertIn(self.latest, str(error.exception))
        self.assertIn("'rollouts/wait-timeout' (0 proposals)", str(error.exception))

    def test_verified_open_pr_blocker_counts_as_assessed_but_carries_forward(self):
        page = proposal()['doc_paths'][0]
        self.context['existing_prs'] = [pr(docs.validate_item(proposal()), number=338)]
        check = self.check(status='blocked', blockers=[{'pr': 338, 'path': page}],
                           evidence='The source adds a parameter missing from the held reference page.')
        self.scans[0].update(inspected_commits=[self.latest, self.recent],
                             daily_commit_checks=[check, self.check(self.recent)])
        self.context['discovery_shard'] = 'api-protocols'
        report = discovery.build_report(self.scans[:1], self.context)
        self.assertTrue(report['complete'])
        self.assertEqual(report['selected'], [])
        # Even with no fresh commits next night, the blocked finding is required.
        self.context['daily_window']['commits'] = []
        assigned = daily.assign(self.context, report, self.assignments)
        self.assertEqual(assigned['api-protocols'], [self.latest])
        self.assertEqual(assigned['operations'], [])

    def test_blockers_cannot_hide_an_uninspected_commit_or_invent_pr_coverage(self):
        page = proposal()['doc_paths'][0]
        scan = {**self.scans[0], 'inspected_commits': [self.latest]}
        self.context['existing_prs'] = [pr(docs.validate_item(proposal()), number=338)]
        bad = [self.check(status='blocked'),
               self.check(self.recent, status='blocked', blockers=[{'pr': 338, 'path': page}]),
               self.check(status='blocked', blockers=[{'pr': 339, 'path': page}]),
               self.check(status='blocked', blockers=[{'pr': 338, 'path': docs.DOC_ROOT+'other.md'}]),
               self.check(status='covered', blockers=[{'pr': 338, 'path': page}])]
        for check in bad:
            with self.subTest(check=check), self.assertRaises(ValueError):
                daily.validate({**scan, 'daily_commit_checks': [check]}, self.context, 'api-protocols')
        self.context['existing_prs'][0]['state'] = 'closed'
        with self.assertRaisesRegex(ValueError, 'not an open PR'):
            daily.validate({**scan, 'daily_commit_checks': [self.check(
                status='blocked', blockers=[{'pr': 338, 'path': page}])]}, self.context, 'api-protocols')

    def test_no_scan_records_every_obligation_as_uninspected(self):
        report = discovery.build_report([], self.context)
        self.assertFalse(report['complete'])
        checks = report['daily_coverage']['checks']
        self.assertEqual(len(checks), 3)
        self.assertTrue(all(c['status'] == 'uninspected' for c in checks))
        self.assertEqual(daily.assign(self.context, report, self.assignments),
                         self.context['daily_assignments'])

    def test_all_checks_complete_or_targeted_run_scope(self):
        self.scans[0].update(inspected_commits=[self.latest, self.recent],
                             daily_commit_checks=[self.check(), self.check(self.recent)])
        # Passing one side of a cross-cutting commit does not cover the other.
        self.assertFalse(discovery.build_report(self.scans, self.context)['complete'])
        self.scans[-1].update(inspected_commits=[self.latest], daily_commit_checks=[self.check()])
        self.assertTrue(discovery.build_report(self.scans, self.context)['complete'])
        self.context['discovery_shard'] = 'api-protocols'
        report = discovery.build_report(self.scans[:1], self.context)
        self.assertTrue(report['complete'])
        self.assertEqual(len(report['daily_coverage']['checks']), 2)

    def test_no_recent_commits_still_permits_backlog(self):
        self.context['daily_assignments'] = {}
        self.context['daily_window']['commits'] = []
        self.scans[0].update(concerns=[proposal()], inspected_commits=[self.old])
        report = discovery.build_report(self.scans, self.context)
        self.assertTrue(report['complete'])
        self.assertEqual(len(report['selected']), 1)

    def test_numeric_ids_resolve_in_daily_checks(self):
        raw = {'concerns': [], 'inspected_commits': [1], 'remaining_work': 'Backlog',
               'daily_commit_checks': [{'source_commit': 1, 'status': 'covered', 'concerns': [],
                                        'evidence': 'The API reference already describes the field.'}]}
        resolved = json.loads(discovery.resolve_scan(json.dumps(raw), self.history))
        self.assertEqual(resolved['daily_commit_checks'][0]['source_sha'], self.latest)
        discovery.validate_scan(json.dumps(resolved), self.context, 'api-protocols', self.history)
        for value in [0, 4, '1', True]:
            raw['daily_commit_checks'][0]['source_commit'] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                discovery.resolve_scan(json.dumps(raw), self.history)

    def test_keyed_daily_checks_resolve_without_model_supplied_hashes(self):
        check = {'status': 'covered', 'concerns': [], 'blockers': [],
                 'evidence': 'The API reference already describes the field.'}
        raw = {'concerns': [], 'inspected_commits': [1], 'remaining_work': 'Backlog',
               'daily_commit_checks': {'1': check}}
        resolved = json.loads(discovery.resolve_scan(json.dumps(raw), self.history))
        self.assertEqual(resolved['daily_commit_checks'], [{**check, 'source_sha': self.latest}])
        discovery.validate_scan(json.dumps(resolved), self.context, 'api-protocols', self.history)
        for key, value in [('0', check), ('4', check), ('01', check), ('+1', check),
                           ('1.0', check), (self.latest, check), ('١', check),
                           ('1', None), ('1', {**check, 'source_commit': 2}),
                           ('1', {**check, 'source_sha': self.recent})]:
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                discovery.resolve_scan(json.dumps({**raw, 'daily_commit_checks': {key: value}}),
                                       self.history)

    def test_schema_requires_every_assigned_commit_even_if_already_inspected(self):
        # Run 40453649191 inspected global ID 81 but omitted its disposition.
        # The output contract must require it separately from inspected_commits.
        for ids in [[1, 81, 113], []]:
            schema = discovery.scan_schema({'required_commit_ids': ids})
            checks = schema['properties']['daily_commit_checks']
            self.assertEqual(checks['type'], 'object')
            self.assertEqual(checks['required'], [str(i) for i in ids])
            self.assertEqual(set(checks['properties']), set(checks['required']))
            self.assertFalse(checks['additionalProperties'])
            for definition in checks['properties'].values():
                self.assertEqual(definition, {'$ref': '#/definitions/daily_check'})
            self.assertNotIn('source_commit', schema['definitions']['daily_check']['properties'])
            self.assertIn('evidence', schema['definitions']['daily_check']['required'])

    def test_large_execution_output_is_read_from_file_without_shell_interpolation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'execution.json'
            payload = {'remaining_work': "$(exit 1) `exit 1` '" + 'x' * 200000}
            result = {'type': 'result', 'subtype': 'success', 'is_error': False, 'structured_output': payload}
            assistant = {'type': 'assistant', 'message': {'id': 'msg-1'}}
            path.write_text(json.dumps([assistant, result]))
            self.assertEqual(json.loads(discovery.read_scan_output(path)), payload)
            for messages in [[], {}, [result, {'type': 'assistant'}],
                             [{**result, 'is_error': True}], [{**result, 'subtype': 'error_max_turns'}],
                             [{**result, 'structured_output': None}]]:
                path.write_text(json.dumps(messages))
                with self.subTest(messages=str(messages)[:100]), self.assertRaises(ValueError):
                    discovery.read_scan_output(path)

    def test_turn_counter_recovery_requires_success_and_verifiable_bounded_rounds(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'execution.json'
            assistant = lambda identity: {'type': 'assistant', 'message': {'id': identity}}
            result = {'type': 'result', 'subtype': 'success', 'is_error': False,
                      'num_turns': 201, 'structured_output': {'concerns': []}}
            # Several content blocks from one API response share its message ID.
            trace = [assistant('a'), assistant('a'), assistant('b'), result]
            path.write_text(json.dumps(trace))
            self.assertEqual(json.loads(discovery.read_scan_output(path, 'failure', 200)),
                             result['structured_output'])
            for messages, outcome, limit in [
                    (trace, 'failure', 1), (trace, 'success', 1),
                    (trace, 'cancelled', 200), (trace, 'skipped', 200),
                    (trace, 'failure', 201), (trace, 'failure', 0),
                    ([result], 'failure', 200),
                    ([assistant(None), result], 'failure', 200),
                    ([assistant(''), result], 'failure', 200),
                    ([assistant('a'), {**result, 'subtype': 'error_max_turns'}], 'failure', 200),
                    ([assistant('a'), {**result, 'is_error': True}], 'failure', 200),
                    ([assistant('a'), {**result, 'structured_output': None}], 'failure', 200),
                    ([assistant('a'), {**result, 'num_turns': '201'}], 'failure', 200)]:
                path.write_text(json.dumps(messages))
                with self.subTest(outcome=outcome, limit=limit, messages=messages), self.assertRaises(ValueError):
                    discovery.read_scan_output(path, outcome, limit)

    def test_partition_scan_and_combine_keep_daily_accounting_end_to_end(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            context_root = root / 'nightly-docs-context'
            context_root.mkdir()
            self.context['discovery_shard'] = 'api-protocols'
            (context_root / 'context.json').write_text(json.dumps(self.context))
            scans = root / 'scans'
            scans.mkdir()
            env = {'RUNNER_TEMP': directory, 'SOURCE_ROOT': directory, 'SHARD': 'api-protocols',
                   'DEFAULT_BRANCH': 'main', 'GITHUB_REPOSITORY': 'test/repo',
                   'NIGHTLY_CONTEXT': str(root / 'scoped.json'), 'SCAN_DIR': str(scans),
                   'SCAN_OUTPUT': str(scans / 'api.json'), 'REPORT_OUTPUT': str(root / 'report.json'),
                   'SCAN_EXECUTION_FILE': str(root / 'execution.json'),
                   'SCAN_ACTION_OUTCOME': 'failure', 'DISCOVERY_MAX_ROUNDS': '200',
                   'GITHUB_OUTPUT': str(root / 'output'), 'GITHUB_STEP_SUMMARY': str(root / 'summary')}
            result = {'concerns': [], 'inspected_commits': [1, 2], 'remaining_work': 'Old history remains.',
                      'release_audit_checks': [], 'daily_commit_checks': {
                          str(i): {'status': 'not_applicable', 'concerns': [], 'blockers': [],
                                   'evidence': 'Test-only changes, no changed user contract.'}
                          for i in [1, 2]}}
            Path(env['SCAN_EXECUTION_FILE']).write_text(json.dumps([
                {'type': 'assistant', 'message': {'id': 'msg-1'}}, {
                'type': 'result', 'subtype': 'success', 'is_error': False,
                'num_turns': 201, 'structured_output': result}]))
            prior = {'daily_coverage': {'checks': [
                {'shard': 'api-protocols', **self.check(status='blocked',
                   blockers=[{'pr': 338, 'path': proposal()['doc_paths'][0]}])}]}}
            with patch.dict(os.environ, env), \
                    patch.object(discovery, 'previous_report', return_value=prior), \
                    patch.object(discovery.release_handoff, 'collect', return_value=[]), \
                    patch.object(discovery, 'partition', return_value=self.assignments):
                for command in ['partition', 'context', 'scan', 'combine']:
                    with patch('sys.argv', ['discovery.py', command]):
                        discovery.main()
            scoped = json.loads(Path(env['NIGHTLY_CONTEXT']).read_text())
            report = json.loads(Path(env['REPORT_OUTPUT']).read_text())
            self.assertEqual(scoped['required_commit_ids'], [1, 2])
            self.assertEqual(scoped['pending_commit_checks'], prior['daily_coverage']['checks'])
            output = dict(line.split('=', 1) for line in Path(env['GITHUB_OUTPUT']).read_text().splitlines())
            schema = json.loads(output['schema'])
            self.assertEqual(schema['properties']['daily_commit_checks']['required'], ['1', '2'])
            self.assertEqual(len(scoped['code_history']), 3)
            self.assertTrue(report['complete'])
            self.assertEqual(len(report['daily_coverage']['checks']), 2)
            self.assertTrue(report['scans'][0]['execution']['recovered'])
            self.assertEqual(report['scans'][0]['execution']['assistant_rounds'], 1)
            self.assertIn('uncovered=0\n', Path(env['GITHUB_OUTPUT']).read_text())
            self.assertIn('2 not_applicable', Path(env['GITHUB_STEP_SUMMARY']).read_text())


class DailyWindowGitTests(unittest.TestCase):
    setUp = fixtures.GitGuardTests.setUp
    cleanup = fixtures.GitGuardTests.cleanup
    git = fixtures.GitGuardTests.git

    def commit_at(self, timestamp):
        with patch.dict(os.environ, {'GIT_COMMITTER_DATE': timestamp,
                                     'GIT_AUTHOR_DATE': '2020-01-01T00:00:00Z'}):
            self.git('commit', '--allow-empty', '-qm', 'fixture')
        return self.git('rev-parse', 'HEAD')

    def test_window_uses_pinned_clock_and_committer_dates_without_losing_backlog(self):
        with patch.dict(os.environ, {'GIT_COMMITTER_DATE': '2026-06-01T00:00:00Z'}):
            self.git('commit', '--amend', '--no-edit', '-q')
        old = self.commit_at('2026-08-01T00:00:00Z')
        before = self.commit_at('2026-10-25T09:59:59Z')
        boundary = self.commit_at('2026-10-25T10:00:00Z')
        # An out-of-order timestamp must not stop traversal at the cutoff.
        self.commit_at('2026-06-20T00:00:00Z')
        latest = self.commit_at('2026-10-26T09:00:00Z')
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(docs, 'existing_prs', return_value=[]), \
                patch.dict(os.environ, {'SOURCE_ROOT': os.getcwd(),
                    'SMG_SOURCE_JSON': json.dumps({'version': 'main', 'sha': latest}),
                    'SMG_SOURCE_CAPTURED_AT': '2026-10-26T10:00:00+00:00'}):
            output = Path(directory) / 'context.json'
            docs.prepare('test/repo', output)
            context = json.loads(output.read_text())
        self.assertEqual(context['daily_window']['commits'], [latest, boundary])
        self.assertEqual(context['daily_window']['start'], '2026-10-25T10:00:00+00:00')
        self.assertEqual({line.split()[0] for line in context['code_history']}, {old, before, boundary, latest})

    def test_mainline_merge_patch_includes_topic_branch_changes(self):
        branch = self.git('branch', '--show-current')
        self.git('switch', '-qc', 'topic')
        Path('topic.rs').write_text('pub const NEW_FLAG: bool = true;\n')
        self.git('add', 'topic.rs')
        topic = self.commit_at('2026-10-24T09:00:00Z')
        self.git('switch', '-q', branch)
        with patch.dict(os.environ, {'GIT_COMMITTER_DATE': '2026-10-26T09:00:00Z'}):
            self.git('merge', '--no-ff', '-qm', 'Merge topic', 'topic')
        latest = self.git('rev-parse', 'HEAD')
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(docs, 'existing_prs', return_value=[]), \
                patch.dict(os.environ, {'SOURCE_ROOT': os.getcwd(),
                    'SMG_SOURCE_JSON': json.dumps({'version': 'main', 'sha': latest}),
                    'SMG_SOURCE_CAPTURED_AT': '2026-10-26T10:00:00+00:00'}):
            output = Path(directory) / 'context.json'
            docs.prepare('test/repo', output)
            context = json.loads(output.read_text())
            self.assertIn(latest, context['daily_window']['commits'])
            self.assertNotIn(topic, context['daily_window']['commits'])
            self.assertIn('+pub const NEW_FLAG', (Path(context['source_diffs']) / f'{latest}.patch').read_text())


if __name__ == '__main__':
    unittest.main()
