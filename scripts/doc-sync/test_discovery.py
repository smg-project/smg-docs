# Adapted from ome-projects/ome at 776fbe3b4be9e826be3404699886543d83e8e380.
# SMG cross-repository discovery and publication changes; Apache-2.0 (LICENSE).
import json
import unittest
from unittest.mock import patch

import discovery
import nightly_docs as docs
from test_nightly_docs import proposal


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.history = ['a' * 40 + ' old change', 'b' * 40 + ' new change']
        self.context = {'base_sha': 'c' * 40, 'source_sha': 'd' * 40, 'code_history': self.history, 'existing_prs': [], 'doc_inventory': [{'path': p} for p in proposal()['doc_paths']]}
        self.assignments = {slug: self.history for slug, _, _ in discovery.SHARDS}
        self.scans = [{'shard': slug, 'base_sha': self.context['base_sha'], 'concerns': [],
                       'inspected_commits': [], 'remaining_work': 'No more supported candidates.'}
                      for slug, _, _ in discovery.SHARDS]

    def combine(self):
        self.context['doc_inventory'] = [{'path': path} for scan in self.scans
                                        for item in scan['concerns']
                                        for path in item['placement']['examined_pages']]
        with patch.object(discovery, 'partition', return_value=self.assignments):
            return discovery.combine(self.scans, self.context)

    def test_model_commit_ids_resolve_to_exact_trusted_hashes(self):
        item = proposal()
        del item['source_sha']
        item['source_commit'] = 2
        raw = json.dumps({'concerns': [item], 'inspected_commits': [2], 'remaining_work': 'Done'})
        resolved = json.loads(discovery.resolve_scan(raw, self.history))
        self.assertEqual(resolved['concerns'][0]['source_sha'], 'b' * 40)
        self.assertEqual(resolved['inspected_commits'], ['b' * 40])
        self.assertNotIn('source_commit', resolved['concerns'][0])

    def test_placement_schema_only_allows_existing_canonical_and_examined_pages(self):
        schema = discovery.scan_schema({**self.context, 'required_commit_ids': [1]})
        existing = schema['definitions']['existing_page']['enum']
        self.assertEqual(existing, proposal()['doc_paths'])
        item = schema['properties']['concerns']['items']['properties']
        for name in ('canonical_pages', 'examined_pages'):
            self.assertEqual(item['placement']['properties'][name]['items'],
                             {'$ref': '#/definitions/existing_page'})
        self.assertNotIn('enum', item['doc_paths']['items'])  # Justified new pages remain allowed.

    def test_bad_audit_reference_defers_gap_without_discarding_valid_scan(self):
        path = proposal()['doc_paths'][0]
        candidate = {'id': 'audit1', 'shard': 'api-protocols', 'doc_path': path}
        other = proposal(concern='other-gap', doc_paths=['src/lib/content/other.md'])
        context = {**self.context, 'release_audit_candidates': [candidate],
                   'doc_inventory': self.context['doc_inventory'] + [{'path': other['doc_paths'][0]}]}
        scan = {**self.scans[0], 'concerns': [proposal(), other], 'inspected_commits': ['a' * 40],
                'release_audit_checks': [{'candidate_id': 'audit1', 'status': 'proposed',
                    'concerns': ['wait-timeout', 'other-gap', 'missing'], 'evidence': 'Verified gap remains.'}]}
        raw, diagnostic = discovery.defer_invalid_audit_references(json.dumps(scan), context, 'api-protocols')
        result = discovery.validate_scan(raw, context, 'api-protocols', self.history)
        self.assertEqual(result['concerns'], scan['concerns'])
        self.assertEqual(result['release_audit_checks'][0]['status'], 'deferred')
        self.assertEqual(result['release_audit_checks'][0]['concerns'], ['wait-timeout'])
        self.assertIn('missing', diagnostic[0]['reason'])
        self.assertIn('Verified gap remains.', result['release_audit_checks'][0]['evidence'])

    def test_audit_proposal_losing_owning_page_reference_is_withheld(self):
        context = {**self.context, 'release_audit_candidates': [
            {'id': 'audit1', 'shard': 'api-protocols', 'doc_path': 'src/lib/content/missing.md'}]}
        item = proposal(concern='release-audit-audit1-gap', source_sha=context['source_sha'])
        scan = {**self.scans[0], 'concerns': [item], 'inspected_commits': [context['source_sha']],
                'release_audit_checks': [{'candidate_id': 'audit1', 'status': 'proposed',
                    'concerns': [item['concern']], 'evidence': 'Different page needs correction.'}]}
        raw, _ = discovery.defer_invalid_audit_references(json.dumps(scan), context, 'api-protocols')
        raw, withheld = discovery.defer_unsupported_proposals(raw, context)
        self.assertEqual(json.loads(raw)['concerns'], [])
        self.assertEqual(len(withheld), 1)
        self.assertEqual(json.loads(raw)['release_audit_checks'][0]['status'], 'deferred')

    def test_invalid_commit_ids_and_model_supplied_hashes_fail_closed(self):
        for number in [0, -1, 3, '1', True, 1.0]:
            with self.subTest(number=number), self.assertRaises(ValueError):
                discovery.resolve_scan(json.dumps({'concerns': [], 'inspected_commits': [number],
                                                   'remaining_work': 'Done'}), self.history)
        with self.assertRaisesRegex(ValueError, 'not supply a hash'):
            discovery.resolve_scan(json.dumps({'concerns': [proposal(source_commit=1)],
                                               'inspected_commits': [1], 'remaining_work': 'Done'}), self.history)

    def test_overlong_model_titles_survive_scan_combine_and_deferred_queue(self):
        titles = [
            '[Docs] An unavailable history store answers 503 storage_unavailable; every Postgres/Redis store call is bounded (5 s default)',
            '[Docs] Postgres and Redis history stores are contacted at startup with a bounded verdict; Redis no longer fails on first use']
        concerns = [proposal(title=title, concern=f'history-{i}', question=f'History question {i}')
                    for i, title in enumerate(titles)]
        scan = {**self.scans[0], 'concerns': concerns, 'inspected_commits': ['a' * 40]}
        validated = discovery.validate_scan(json.dumps(scan), self.context, scan['shard'], self.history)
        self.assertEqual(len(validated['concerns']), 2)
        self.assertTrue(all(len(item['title']) <= 120 for item in validated['concerns']))
        self.scans[0] = validated
        selected, deferred = self.combine()
        self.assertEqual(len(selected), 1)
        self.assertEqual(deferred[0][1], 'overlapping documentation files')
        queued = discovery.deferred_queue(self.scans, deferred, [])
        self.assertEqual(len(queued), 1)
        self.assertLessEqual(len(queued[0]['title']), 120)
        self.assertEqual(queued[0]['evidence'], concerns[1]['evidence'])
        # A long title must not conceal an invalid source or path elsewhere.
        for field, value in [('source_sha', 'f' * 40), ('doc_paths', ['scripts/bad.py'])]:
            invalid = {**scan, 'concerns': [{**concerns[0], field: value}]}
            with self.assertRaises(ValueError):
                discovery.validate_scan(json.dumps(invalid), self.context, scan['shard'], self.history)

    def test_partition_keeps_unmatched_history_and_order(self):
        with patch.object(docs, 'source_git', side_effect=['a' * 40] + [''] * 7):
            result = discovery.partition(self.context)
        self.assertEqual(result['api-protocols'], self.history[:1])
        self.assertEqual(result['operations'], self.history[1:])
        self.assertEqual(set().union(*(set(lines) for lines in result.values())), set(self.history))

    def test_round_robin_global_cap_does_not_starve_later_scans(self):
        for n, scan in enumerate(self.scans):
            scan['inspected_commits'] = ['a' * 40]
            scan['concerns'] = [proposal(concern=f'concern-{n}-{i}', question=f'Question {n} {i}',
                                        doc_paths=[docs.DOC_ROOT + f'tasks/{n}-{i}.md']) for i in range(20)]
        selected, deferred = self.combine()
        self.assertEqual(len(selected), 100)
        self.assertEqual(len(deferred), 60)
        self.assertEqual([item['concern'] for item in selected[:8]], [f'concern-{n}-0' for n in range(8)])
        self.assertTrue(all(reason == 'UTC daily/run PR cap' for _, reason in deferred))

    def test_cross_cutting_commit_uses_global_id_outside_priority_paths(self):
        slug = self.scans[0]['shard']
        self.assignments[slug] = self.history[:1]
        scoped = discovery.scan_context(self.context, slug, self.assignments)
        self.assertEqual(scoped['focus_commit_ids'], [1])
        self.assertEqual(scoped['code_history'][1], '2: ' + self.history[1])
        item = proposal(source_sha='b' * 40)
        self.scans[0].update(inspected_commits=['b' * 40], concerns=[item])
        selected, _ = self.combine()
        self.assertEqual(selected[0]['source_sha'], 'b' * 40)
        del item['source_sha']
        item['source_commit'] = 2
        raw = json.dumps({'concerns': [item], 'inspected_commits': [2], 'remaining_work': 'Done'})
        self.assertEqual(json.loads(discovery.resolve_scan(raw, self.history))['concerns'][0]['source_sha'],
                         'b' * 40)

    def test_remaining_daily_slots_limit_parallel_publish_matrix(self):
        self.context["max_prs"] = 2
        for n, scan in enumerate(self.scans):
            scan.update(inspected_commits=['a' * 40], concerns=[proposal(concern=f'gap-{n}',
                        question=f'Question {n}', doc_paths=[docs.DOC_ROOT + f'{n}.md'])])
        selected, deferred = self.combine()
        self.assertEqual(len(selected), 2)
        self.assertEqual(len(deferred), 6)
        self.context["max_prs"] = 0
        self.assertEqual(self.combine()[0], [])

    def test_pending_audit_identity_is_supplied_only_to_its_owner(self):
        own = proposal(concern='release-audit-aaaa-same-gap')
        foreign = proposal(concern='release-audit-bbbb-foreign-gap')
        ordinary = proposal()
        self.context['pending_concerns'] = [own, foreign, ordinary]
        self.context['release_audit_candidates'] = [
            {'id': 'aaaa', 'shard': 'api-protocols'}, {'id': 'bbbb', 'shard': 'operations'}]
        scoped = discovery.scan_context(self.context, 'api-protocols', self.assignments)
        self.assertEqual(scoped['pending_concerns'], [own, ordinary])
        self.assertEqual(self.context['pending_concerns'], [own, foreign, ordinary])
        scoped = discovery.scan_context(self.context, 'operations', self.assignments)
        self.assertEqual(scoped['pending_concerns'], [foreign, ordinary])

    def test_overlap_and_semantic_identity_are_deferred_not_failed(self):
        for scan in self.scans[:3]:
            scan['inspected_commits'] = ['a' * 40, 'b' * 40]
        self.scans[0]['concerns'] = [proposal()]
        self.scans[1]['concerns'] = [proposal(source_sha='b' * 40, doc_paths=[docs.DOC_ROOT + 'tasks/other.md'])]
        self.scans[2]['concerns'] = [proposal(concern='other', question='Different question')]
        selected, deferred = self.combine()
        self.assertEqual(len(selected), 1)
        self.assertEqual([reason for _, reason in deferred], ['duplicate concern', 'overlapping documentation files'])

    def test_covered_proposal_does_not_reserve_other_files(self):
        from test_nightly_docs import pr
        item = docs.validate_item(proposal())
        self.context['existing_prs'] = [pr(item, state='closed')]
        self.scans[0].update(inspected_commits=['a' * 40], concerns=[proposal()])
        self.scans[1].update(inspected_commits=['b' * 40], concerns=[proposal(source_sha='b' * 40,
                            concern='later-fix', question='New behavior after declined fix')])
        selected, deferred = self.combine()
        self.assertEqual(selected[0]['concern'], 'later-fix')
        self.assertEqual(deferred[0][1], 'existing PR')

    def test_missing_duplicate_or_wrong_baseline_scan_fails_closed(self):
        for mutate in [lambda: self.scans.pop(), lambda: self.scans.append(self.scans[0]),
                       lambda: self.scans[0].update(base_sha='d' * 40)]:
            original = json.loads(json.dumps(self.scans))
            mutate()
            with self.assertRaises(ValueError):
                self.combine()
            self.scans = original

    def test_unknown_or_uninspected_source_is_rejected(self):
        scan = self.scans[0]
        for changes in [dict(inspected_commits=['d' * 40]),
                        dict(inspected_commits=[], concerns=[proposal()]),
                        dict(inspected_commits=['a' * 40, 'a' * 40]),
                        dict(remaining_work='')]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                discovery.validate_scan(json.dumps({**scan, **changes}), self.context,
                                        scan['shard'], self.history)

    def test_recent_inspection_does_not_approve_uninspected_backlog_proposals(self):
        scan = {**self.scans[0], 'inspected_commits': ['b' * 40],
                'concerns': [proposal(), proposal(concern='other-gap')]}
        with self.assertRaisesRegex(ValueError, 'Concern source was not reported as inspected') as error:
            discovery.validate_scan(json.dumps(scan), self.context, scan['shard'], self.history)
        self.assertIn('rollouts/wait-timeout (' + 'a' * 40 + ')', str(error.exception))
        self.assertIn('rollouts/other-gap (' + 'a' * 40 + ')', str(error.exception))
        scan['inspected_commits'].append('a' * 40)
        self.assertEqual(len(discovery.validate_scan(json.dumps(scan), self.context,
                                                    scan['shard'], self.history)['concerns']), 2)


class BatchedPRFilesTests(unittest.TestCase):
    def test_batches_open_prs_and_paginates_large_diffs(self):
        def respond(*args):
            query = args[4]
            data = {}
            for n in range(1, 28):
                if f'p{n}: pullRequest' in query:
                    data[f'p{n}'] = {'files': {'nodes': [{'path': f'{n}.md'}],
                                              'pageInfo': {'hasNextPage': n == 2}}}
            return json.dumps({'data': {'repository': data}})
        with patch.object(docs, 'run', side_effect=respond) as run, \
                patch.object(docs, 'pages', return_value=[{'filename': 'large.md'}]) as pages:
            result = docs.open_pr_files('owner/repo', list(range(1, 28)))
        self.assertEqual(run.call_count, 2)
        pages.assert_called_once_with('repos/owner/repo/pulls/2/files?per_page=100')
        self.assertEqual(result[2], ['large.md'])
        self.assertEqual(result[27], ['27.md'])

    def test_incomplete_graphql_response_fails_closed(self):
        with patch.object(docs, 'run', return_value='{"data":{"repository":{}}}'):
            with self.assertRaises(KeyError):
                docs.open_pr_files('owner/repo', [1])
