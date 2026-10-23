import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import discovery
import nightly_docs as docs
import release_handoff as handoff
from test_nightly_docs import proposal, pr


class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo, self.source = self.root / 'docs', self.root / 'source'
        for repo in (self.repo, self.source):
            repo.mkdir()
            self.git(repo, 'init', '-q')
            self.git(repo, 'config', 'user.name', 'test')
            self.git(repo, 'config', 'user.email', 'test@example.com')
        (self.source / 'code').write_text('released\n')
        self.source_release = self.commit(self.source)
        (self.source / 'code').write_text('new behavior\n')
        self.source_main = self.commit(self.source)
        self.path = docs.DOC_ROOT + 'reference/configuration.md'
        self.release_path = 'src/lib/versions/v1.13.0/content/reference/configuration.md'
        self.marker = 'src/lib/versions/v1.13.0/sync.json'
        self.write(self.path, 'Old claim\n')
        self.write(self.release_path, 'Old claim\n')
        self.old = self.commit(self.repo)
        self.state = {'source_release': {'tag': 'v1.13.0', 'sha': self.source_release},
                      'docs_base': self.old, 'audited_pages': 1}
        self.write(self.marker, json.dumps(self.state))
        self.write(self.release_path, 'Correct released claim\n')
        self.commit(self.repo)
        self.addCleanup(patch.stopall)
        patch.object(docs, 'git', side_effect=lambda *a: self.git(self.repo, *a)).start()
        patch.object(docs, 'source_git', side_effect=lambda *a: self.git(self.source, *a)).start()

    def git(self, repo, *args):
        return subprocess.check_output(['git', '-c', 'core.hooksPath=/dev/null', '-C', str(repo),
                                        *args], text=True, stderr=subprocess.PIPE).strip()

    def commit(self, repo):
        self.git(repo, 'add', '-A')
        self.git(repo, 'commit', '-qm', 'fixture')
        return self.git(repo, 'rev-parse', 'HEAD')

    def write(self, path, value):
        target = self.repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(value)

    def collect(self):
        context = {'base_sha': self.git(self.repo, 'rev-parse', 'HEAD'), 'source_sha': self.source_main}
        return handoff.collect(context, self.root / 'evidence')

    def test_committed_audit_exports_both_diffs_and_stable_identity(self):
        item, = self.collect()
        self.assertEqual(item['shard'], 'deploy-config')
        evidence = (self.root / 'evidence' / item['file']).read_text()
        self.assertIn('=== audit_diff ===\n', evidence)
        self.assertIn('\n-Old claim\n', evidence)
        self.assertIn('\n+Correct released claim\n', evidence)
        self.assertIn(self.source_main, evidence)
        self.write(self.path, 'Newer main behavior; still needs the correction\n')
        self.commit(self.repo)
        self.assertEqual(self.collect()[0]['id'], item['id'])

    def test_prepared_and_unmerged_markers_are_ignored(self):
        (self.repo / self.marker).unlink()
        self.commit(self.repo)
        self.write(self.marker, json.dumps(self.state))
        self.assertEqual(self.collect(), [])

    def test_large_evidence_retains_readable_lines_and_final_correction(self):
        self.write(self.release_path, ''.join(f'Correct claim {i}\n' for i in range(1000)))
        self.commit(self.repo)
        item, = self.collect()
        evidence = (self.root / 'evidence' / item['file']).read_text()
        self.assertIn('\n+Correct claim 999\n', evidence)
        self.assertLess(max(map(len, evidence.splitlines())), 200)

    def test_corrected_main_stops_generating_candidate(self):
        self.write(self.path, 'Correct released claim\n')
        self.commit(self.repo)
        self.assertEqual(self.collect(), [])

    def test_unchanged_release_page_is_not_a_correction(self):
        self.write(self.release_path, 'Old claim\n')
        self.write(self.path, 'Intentional newer main behavior\n')
        self.commit(self.repo)
        self.assertEqual(self.collect(), [])

    def test_new_version_seed_uses_original_main_and_keeps_removed_page_evidence(self):
        name = 'concepts/new.md'
        self.write(docs.DOC_ROOT + name, 'Unsupported feature\n')
        base = self.commit(self.repo)
        self.state['docs_base'] = base
        self.write(self.marker, json.dumps(self.state))
        self.commit(self.repo)
        items = self.collect()
        item, = [i for i in items if i['doc_path'] == docs.DOC_ROOT + name]
        evidence = (self.root / 'evidence' / item['file']).read_text()
        self.assertIn('\n-Unsupported feature\n', evidence)

    def test_invalid_source_ancestry_and_marker_fail_closed(self):
        with patch.object(docs, 'source_git', side_effect=ValueError('not ancestor')):
            with self.assertRaisesRegex(ValueError, 'not ancestor'):
                self.collect()
        self.state['source_release']['tag'] = 'v1.12.0'
        self.write(self.marker, json.dumps(self.state))
        self.commit(self.repo)
        with self.assertRaisesRegex(ValueError, 'Invalid committed'):
            self.collect()

    def test_symlink_evidence_is_rejected(self):
        (self.repo / self.release_path).unlink()
        (self.repo / self.release_path).symlink_to('/etc/passwd')
        self.commit(self.repo)
        with self.assertRaisesRegex(ValueError, 'regular tracked'):
            self.collect()


class DiscoveryHandoffTests(unittest.TestCase):
    def setUp(self):
        self.identity = '1234567890abcdef'
        self.path = proposal()['doc_paths'][0]
        self.item = proposal(concern='release-audit-' + self.identity + '-routing')
        self.context = {'source_sha': self.item['source_sha'], 'release_audit_candidates': [
            {'id': self.identity, 'shard': 'routing-workers', 'doc_path': self.path}]}
        self.check = {'candidate_id': self.identity, 'status': 'proposed',
                      'concerns': [self.item['concern']], 'evidence': 'Current callers and tests verify this claim.'}
        self.result = {'concerns': [self.item], 'release_audit_checks': [self.check]}

    def test_all_assigned_candidates_need_an_explicit_disposition(self):
        self.assertEqual(handoff.validate_checks(self.result, self.context, 'routing-workers'), [self.check])
        for checks in [[], [self.check, self.check], [{**self.check, 'candidate_id': 'other'}]]:
            with self.subTest(checks=checks), self.assertRaises(ValueError):
                handoff.validate_checks({**self.result, 'release_audit_checks': checks}, self.context, 'routing-workers')

    def test_proposals_require_candidate_page_current_source_and_evidence(self):
        for changes in [{'source_sha': 'f' * 40}, {'doc_paths': [docs.DOC_ROOT + 'other.md']},
                        {'concern': 'unrelated'}]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                handoff.validate_checks({**self.result, 'concerns': [{**self.item, **changes}]},
                                        self.context, 'routing-workers')
        with self.assertRaises(ValueError):
            handoff.validate_checks({**self.result, 'release_audit_checks': [{**self.check, 'evidence': ''}]},
                                    self.context, 'routing-workers')

    def test_deferred_and_intentional_version_differences_do_not_authorize_edits(self):
        for status in ['deferred', 'covered', 'not_applicable']:
            result = {'concerns': [], 'release_audit_checks': [{**self.check, 'status': status, 'concerns': []}]}
            self.assertEqual(handoff.validate_checks(result, self.context, 'routing-workers'), result['release_audit_checks'])
            with self.assertRaises(ValueError):
                handoff.validate_checks({**result, 'concerns': [self.item]}, self.context, 'routing-workers')

    def test_missing_scan_keeps_candidates_visible(self):
        result, = handoff.report(self.context, [])
        self.assertEqual(result['status'], 'deferred')
        self.assertEqual(result['candidate_id'], self.identity)

    def test_partial_candidate_can_propose_one_concern_and_defer_the_rest(self):
        result = {**self.result, 'release_audit_checks': [{**self.check, 'status': 'deferred'}]}
        self.assertEqual(handoff.validate_checks(result, self.context, 'routing-workers'),
                         result['release_audit_checks'])

    def validate_scan(self, result):
        history = [self.context['source_sha'] + ' current', 'b' * 40 + ' original change']
        context = {**self.context, 'base_sha': 'c' * 40, 'code_history': history,
                   'existing_prs': [], 'doc_inventory': [{'path': self.path}]}
        raw = json.dumps({**result, 'inspected_commits': [line.split()[0] for line in history],
                          'remaining_work': 'Other corrections remain deferred.'})
        return discovery.validate_scan(raw, context, 'routing-workers', history)

    def test_existing_concern_references_preserve_source_and_dedup_identity(self):
        for slug, sha in [('version-context-routing', self.context['source_sha']),
                          ('bootstrap-port-probe-at-registration', 'b' * 40)]:
            with self.subTest(slug=slug):
                item = {**self.item, 'concern': slug, 'source_sha': sha}
                check = {**self.check, 'status': 'deferred', 'concerns': [slug]}
                scan = self.validate_scan({'concerns': [item], 'release_audit_checks': [check]})
                self.assertEqual(scan['concerns'], [item])
                self.assertEqual(scan['release_audit_checks'], [check])
                original = docs.validate_item(item)
                self.assertTrue(docs.covered(docs.validate_item(scan['concerns'][0]),
                                            [pr(original, state='closed')]))

    def test_ordinary_reference_cannot_bypass_source_or_page_validation(self):
        item = {**self.item, 'concern': 'existing-correction'}
        check = {**self.check, 'concerns': [item['concern']]}
        for changes in [{'source_sha': 'f' * 40}, {'doc_paths': [docs.DOC_ROOT + 'other.md']},
                        {'doc_paths': ['src/lib/versions/v1.13.0/content/other.md']}]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.validate_scan({'concerns': [{**item, **changes}], 'release_audit_checks': [check]})

    def test_queued_audit_keeps_full_identity_when_main_advances(self):
        old = docs.validate_item({**self.item, 'source_sha': 'b' * 40})
        self.context['pending_concerns'] = [old]
        unrelated = {**self.item, 'concern': 'ordinary-change'}
        result = {**self.result, 'concerns': [old, unrelated]}
        scan = self.validate_scan(result)
        self.assertEqual(scan['concerns'], [old, unrelated])
        self.assertEqual(scan['release_audit_checks'], [self.check])
        self.assertEqual(docs.validate_item(scan['concerns'][0])['key'], old['key'])

    def test_old_audit_source_requires_exact_queued_identity(self):
        old = docs.validate_item({**self.item, 'source_sha': 'b' * 40})
        result = {**self.result, 'concerns': [old]}
        for pending in [[], [{**old, 'source_sha': 'c' * 40}],
                        [{**old, 'area': 'other'}], [{**old, 'concern': old['concern'] + '-other'}]]:
            with self.subTest(pending=pending), self.assertRaisesRegex(ValueError, 'queued identity'):
                # Stale/forged key metadata cannot replace matching the fields.
                self.context['pending_concerns'] = pending
                self.validate_scan(result)

    def test_queued_audit_still_requires_inspected_history_page_and_owner(self):
        old = docs.validate_item({**self.item, 'source_sha': 'b' * 40})
        context = {**self.context, 'pending_concerns': [old], 'base_sha': 'c' * 40,
                   'existing_prs': [], 'doc_inventory': [{'path': self.path}]}
        result = {**self.result, 'concerns': [old], 'inspected_commits': ['a' * 40, 'b' * 40],
                  'remaining_work': 'Done'}
        history = ['a' * 40 + ' current', 'b' * 40 + ' original']
        variants = [({**result, 'inspected_commits': ['a' * 40]}, history),
                    ({**result, 'inspected_commits': ['a' * 40]}, history[:1]),
                    ({**result, 'concerns': [{**old, 'doc_paths': [docs.DOC_ROOT + 'other.md']}]}, history),
                    ({**result, 'release_audit_checks': [{**self.check, 'status': 'deferred', 'concerns': []}]}, history)]
        for raw, commits in variants:
            with self.subTest(raw=raw, commits=commits), self.assertRaises(ValueError):
                discovery.validate_scan(json.dumps(raw), context, 'routing-workers', commits)

    def shared_result(self):
        second = {'id': 'fedcba0987654321', 'shard': 'routing-workers',
                  'doc_path': docs.DOC_ROOT + 'reference/routing.md'}
        context = {**self.context, 'release_audit_candidates': [
            *self.context['release_audit_candidates'], second]}
        item = {**self.item, 'doc_paths': [self.path, second['doc_path']]}
        reference = {**self.check, 'candidate_id': second['id'], 'status': 'deferred'}
        result = {'concerns': [item], 'release_audit_checks': [self.check, reference]}
        return result, context

    def test_one_owned_concern_can_cover_multiple_candidate_pages(self):
        result, context = self.shared_result()
        for checks in [result['release_audit_checks'], list(reversed(result['release_audit_checks']))]:
            self.assertEqual(handoff.validate_checks({**result, 'release_audit_checks': checks},
                                                    context, 'routing-workers'), checks)

    def test_cross_page_reference_cannot_replace_owner_or_pinned_source(self):
        result, context = self.shared_result()
        owner, reference = result['release_audit_checks']
        with self.assertRaisesRegex(ValueError, 'owning candidate'):
            handoff.validate_checks({**result, 'release_audit_checks': [
                {**owner, 'status': 'deferred', 'concerns': []}, reference]}, context, 'routing-workers')
        with self.assertRaisesRegex(ValueError, 'pinned main source'):
            handoff.validate_checks({**result, 'concerns': [
                {**result['concerns'][0], 'source_sha': 'b' * 40}]}, context, 'routing-workers')
        with self.assertRaisesRegex(ValueError, 'unrelated concern'):
            handoff.validate_checks({**result, 'concerns': [self.item]}, context, 'routing-workers')
        foreign = {**context, 'release_audit_candidates': [
            {**context['release_audit_candidates'][0], 'shard': 'api-protocols'},
            context['release_audit_candidates'][1]]}
        with self.assertRaisesRegex(ValueError, 'owning candidate'):
            handoff.validate_checks({**result, 'release_audit_checks': [reference]}, foreign, 'routing-workers')

    def test_duplicate_slugs_cannot_make_reference_ambiguous(self):
        with self.assertRaisesRegex(ValueError, 'duplicate concern slug'):
            handoff.validate_checks({**self.result, 'concerns': [self.item, dict(self.item)]},
                                    self.context, 'routing-workers')

    def test_resolution_preserves_dispositions_and_dedup_survives_new_main(self):
        item = dict(self.item)
        del item['source_sha']
        resolved = discovery.resolve_scan(json.dumps({**self.result,
            'concerns': [{**item, 'source_commit': 1}], 'inspected_commits': [1], 'remaining_work': 'Done'}),
            [self.context['source_sha'] + ' main'])
        self.assertEqual(json.loads(resolved)['release_audit_checks'], [self.check])
        old = docs.validate_item(self.item)
        new = docs.validate_item({**self.item, 'source_sha': 'f' * 40})
        for state in ['open', 'closed']:
            self.assertTrue(docs.same_concern(new, pr(old, state=state)))
        different = docs.validate_item({**self.item, 'concern': self.item['concern'] + '-other'})
        self.assertFalse(docs.same_concern(different, pr(old)))


if __name__ == '__main__':
    unittest.main()
