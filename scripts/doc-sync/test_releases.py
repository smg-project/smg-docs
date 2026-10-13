"""Exercise stable-release resolution, publication guards and deferred history."""

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import releases
import nightly_docs as docs
import maintenance
import discovery
from test_nightly_docs import proposal, pr, GitGuardTests
from test_maintenance import pull


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        releases.latest_stable.cache_clear()
        self.addCleanup(releases.latest_stable.cache_clear)
        self.release = {'id': 12, 'tag_name': 'v1.12.0', 'draft': False, 'prerelease': False,
                        'published_at': '2026-10-08T00:00:00Z', 'html_url': 'https://example/release',
                        'target_commitish': 'main'}

    def test_resolves_tag_not_target_branch_and_caches_snapshot(self):
        with patch.object(releases, 'api', side_effect=[self.release, {'sha': 'a' * 40}]) as api:
            self.assertEqual(releases.latest_stable()['sha'], 'a' * 40)
            self.assertEqual(releases.latest_stable()['tag'], 'v1.12.0')
        self.assertEqual(api.call_args_list[1].args[0], 'repos/smg-project/smg/commits/v1.12.0')
        self.assertEqual(api.call_count, 2)

    def test_draft_prerelease_missing_release_and_invalid_sha_fail_closed(self):
        for changes in [{'draft': True}, {'prerelease': True}, {'published_at': None}]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                releases.resolve({**self.release, **changes})
        with patch.object(releases, 'api', side_effect=RuntimeError('No releases')), self.assertRaises(RuntimeError):
            releases.latest_stable()
        with patch.object(releases, 'api', return_value={'sha': 'main'}), self.assertRaises(ValueError):
            releases.resolve(self.release)

    def test_retagging_a_pinned_release_is_rejected(self):
        pinned = {'id': 12, 'tag': 'v1.12.0', 'sha': 'a' * 40,
                  'published_at': self.release['published_at'], 'url': self.release['html_url']}
        with patch.object(releases, 'api', side_effect=[self.release, {'sha': 'b' * 40}]), self.assertRaisesRegex(ValueError, 'changed'):
            releases.verify(pinned)

    def test_ancestry_not_chronology_decides_release_inclusion(self):
        for status in ['ahead', 'identical', 'behind', 'diverged']:
            with self.subTest(status=status), patch.object(releases, 'api', return_value={'status': status}):
                self.assertEqual(releases.contains('a' * 40, 'b' * 40), status in ['ahead', 'identical'])

    def test_release_deferred_pr_can_return_but_declined_and_merged_cannot(self):
        item = docs.validate_item(proposal())
        deferred = pr(item, state='closed', release_deferred=True, number=155)
        self.assertFalse(docs.covered(item, [deferred]))
        self.assertTrue(docs.covered(item, [pr(item, state='closed')]))
        self.assertTrue(docs.covered(item, [pr(item, state='closed', merged=True)]))
        self.assertEqual(docs.publication_branch(item, [deferred]), item['branch'] + '-retry-155')
        context = {'code_history': ['a' * 40 + ' released'], 'existing_prs': [deferred]}
        self.assertEqual(len(discovery.pending_candidates([item], context)), 1)
        context['code_history'] = ['b' * 40 + ' release before change']
        self.assertEqual(discovery.pending_candidates([item], context), [])

    def test_closed_label_is_only_deferred_when_unmerged_and_counts_toward_quota(self):
        item = docs.validate_item(proposal())
        row = {'number': 1, 'title': item['title'], 'body': f'{docs.MARKER}{item["key"]} -->',
               'state': 'closed', 'created_at': '2026-10-12T00:00:00Z', 'merged_at': None,
               'head': {'ref': item['branch']}, 'labels': [{'name': docs.DEFERRED_LABEL}]}
        for merged in [None, '2026-10-12T01:00:00Z']:
            with patch.object(docs, 'pages', return_value=[{**row, 'merged_at': merged}]), patch.object(docs, 'open_pr_files', return_value={}):
                result = docs.existing_prs('test/repo')
            self.assertEqual(result[0]['release_deferred'], merged is None)
            self.assertEqual(docs.daily_remaining(result, '2026-10-12'), 99)

    def test_maintenance_skips_unreleased_before_feedback_or_retry_budget(self):
        with patch.dict(os.environ, {'GITHUB_REPOSITORY': 'smg-project/smg-docs', 'EXTRA_FEEDBACK': ''}), \
                patch.object(maintenance, 'get_pr', return_value=pull()), \
                patch.object(maintenance, 'current_base', return_value='b' * 40), \
                patch.object(maintenance, 'current_code', return_value='c' * 40), \
                patch.object(releases, 'contains', return_value=False), \
                patch.object(maintenance, 'feedback') as feedback, patch.object(maintenance, 'output') as output:
            maintenance.select(7, True)
            self.assertEqual(output.call_args.kwargs['count'], '0')
            with tempfile.TemporaryDirectory() as directory:
                maintenance.prepare(7, Path(directory), True, True)
                self.assertEqual(output.call_args.kwargs['work'], 'false')
                self.assertIn('unreleased', (Path(directory) / 'context.json').read_text())
            feedback.assert_not_called()

    def test_retry_branch_preserves_concern_authentication(self):
        p = pull()
        p['head']['ref'] += '-retry-155'
        with patch.dict(os.environ, {'GITHUB_REPOSITORY': 'smg-project/smg-docs'}):
            self.assertEqual(maintenance.eligible(p)[0], 'a' * 40)
            p['head']['ref'] += '-arbitrary'
            with self.assertRaises(ValueError):
                maintenance.eligible(p)


class ReleaseGitTests(unittest.TestCase):
    # Reuse the Git fixture without importing its test class into discovery twice.
    setUp = GitGuardTests.setUp
    cleanup = GitGuardTests.cleanup
    git = GitGuardTests.git

    def test_post_release_commit_and_wrong_checkout_cannot_reach_writer(self):
        release = {'sha': self.base}
        original = {**self.item, 'source_release': release}
        self.path.write_text('Future behavior\n')
        self.git('commit', '-qam', 'unreleased feature')
        future = self.git('rev-parse', 'HEAD')
        with patch.dict(os.environ, {'SOURCE_ROOT': os.getcwd()}), patch.object(releases, 'verify'):
            with self.assertRaisesRegex(ValueError, 'checkout'):
                docs.verify_item_release(original)
            self.git('checkout', '--detach', self.base)
            docs.verify_item_release(original)
            with self.assertRaises(subprocess.CalledProcessError):
                docs.verify_item_release({**original, 'source_sha': future})

    def test_new_unreleased_prose_is_rejected_but_plain_contract_passes(self):
        for text in ['Unreleased: new bootstrap behavior.', 'On current main, use the new flag.']:
            self.path.write_text(text + '\n')
            docs.validate_diff(self.item, self.base)
            with self.assertRaisesRegex(ValueError, 'unreleased behavior'):
                docs.validate_reader_docs(self.base)
        self.path.write_text('The shutdown grace period defaults to 180 seconds.\n')
        docs.validate_diff(self.item, self.base)
        docs.validate_reader_docs(self.base)


# unittest discovers imported TestCase classes too.
del GitGuardTests
