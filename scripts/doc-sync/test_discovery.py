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

    def test_invalid_commit_ids_and_model_supplied_hashes_fail_closed(self):
        for number in [0, -1, 3, '1', True, 1.0]:
            with self.subTest(number=number), self.assertRaises(ValueError):
                discovery.resolve_scan(json.dumps({'concerns': [], 'inspected_commits': [number],
                                                   'remaining_work': 'Done'}), self.history)
        with self.assertRaisesRegex(ValueError, 'not supply a hash'):
            discovery.resolve_scan(json.dumps({'concerns': [proposal(source_commit=1)],
                                               'inspected_commits': [1], 'remaining_work': 'Done'}), self.history)

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
