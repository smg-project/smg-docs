"""Version selection and trusted snapshot assembly against actual Git fixtures."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import nightly_docs as docs
import releases
import snapshots as s
import test_nightly_docs as fixtures


RELEASE = {'id': 13, 'tag': 'v1.13.0', 'sha': 'a' * 40,
           'published_at': '2026-10-14T18:00:00Z', 'url': 'https://example/v1.13.0'}


class SelectionTests(unittest.TestCase):
    def test_no_new_release_leaves_future_prepared_snapshot_alone(self):
        older = {**RELEASE, 'tag': 'v1.12.0'}
        self.assertFalse(s.pending_release(older, ['v1.13.0', 'v1.12.0'], {}, []))

    def test_future_prepared_folder_cannot_hide_a_new_release(self):
        self.assertTrue(s.pending_release(RELEASE, ['v1.14.0', 'v1.12.0'], {}, []))

    def test_existing_folder_without_audit_still_needs_reconciliation(self):
        self.assertTrue(s.pending_release(RELEASE, ['v1.13.0', 'v1.12.0'], {}, []))
        self.assertFalse(s.pending_release(RELEASE, ['v1.13.0'], {'source_release': RELEASE}, []))

    def test_new_release_and_changed_tag_require_audit(self):
        self.assertTrue(s.pending_release(RELEASE, ['v1.12.0'], {}, []))
        self.assertTrue(s.pending_release(RELEASE, ['v1.13.0'],
                        {'source_release': {**RELEASE, 'sha': 'b' * 40}}, []))
        self.assertGreater(s.version_key('v1.13.0'), s.version_key('v1.9.0'))

    def test_open_release_pr_does_not_repeat_but_closed_unmerged_can_retry(self):
        pr = {'state': 'open', 'body': s.release_marker(RELEASE)}
        self.assertFalse(s.pending_release(RELEASE, ['v1.12.0'], {}, [pr]))
        self.assertTrue(s.pending_release(RELEASE, ['v1.12.0'], {}, [{**pr, 'state': 'closed'}]))

    def test_release_metadata_and_path_injection_rejected(self):
        for tag in ['../main', 'v1.13.0-rc1', 'v1.13.0/foo', 'main']:
            with self.assertRaises(ValueError):
                s.root(tag)
        for path in ['', '../reference/a.md', 'reference/../../a.md', '/reference/a.md', 'reference/x.py']:
            self.assertFalse(s.page_path(path))

    def test_main_source_is_pinned_once_not_resolved_as_a_release(self):
        releases.main_source.cache_clear()
        self.addCleanup(releases.main_source.cache_clear)
        with patch.object(releases, 'api', return_value={'sha': 'c' * 40}) as api:
            self.assertEqual(releases.main_source(), {'version': 'main', 'sha': 'c' * 40})
            releases.main_source()
            api.assert_called_once_with('repos/smg-project/smg/commits/main')


class SnapshotGitTests(unittest.TestCase):
    def setUp(self):
        self.old = os.getcwd()
        self.temp = tempfile.TemporaryDirectory()
        self.evidence = tempfile.TemporaryDirectory()
        self.addCleanup(self.cleanup)
        os.chdir(self.temp.name)
        self.git('init', '-q')
        self.git('config', 'user.name', 'Test')
        self.git('config', 'user.email', 'test@example.com')
        for path, text in {
            docs.DOC_ROOT + 'reference/index.md': '# Reference\n',
            docs.DOC_ROOT + 'reference/feature.md': '# Main feature\n',
            'static/images/a.svg': '<svg/>', s.REGISTRY: '["v1.12.0"]\n',
            'src/lib/versions/v1.12.0/content/reference/index.md': '# Frozen older docs\n'
        }.items():
            s.safe_write(path, text.encode())
        self.git('add', '.')
        self.git('commit', '-qm', 'baseline')
        self.base = self.git('rev-parse', 'HEAD')
        self.manifest = {'navigation': {'reference': {'indexHref': 'reference', 'sections': [
            {'label': 'Reference', 'items': [{'label': 'Feature', 'href': 'reference/feature'}]}]}},
            'pages': {'reference': 'reference/index.md', 'reference/feature': 'reference/feature.md'}}
        self.prefix = s.root(RELEASE['tag'])
        self.paths = [self.prefix + 'content/' + p for p in self.manifest['pages'].values()]

    def cleanup(self):
        os.chdir(self.old)
        self.temp.cleanup()
        self.evidence.cleanup()

    def git(self, *args):
        return subprocess.check_output(['git', *args], text=True, stderr=subprocess.DEVNULL).strip()

    def context(self):
        seed = s.make_seed(self.base, RELEASE, self.manifest)
        digest = hashlib.sha256(b''.join(p.encode()+b'\0'+v for p,v in sorted(seed.items()))).hexdigest()
        ctx = {'base': self.base, 'release': RELEASE, 'main_manifest': self.manifest,
               'batches': [self.paths], 'seed_digest': digest}
        for path, content in seed.items():
            s.safe_write(path, content)
        return ctx, seed

    def bundle(self, ctx, **changes):
        return {'base': self.base, 'release': RELEASE, 'batch': 0,
                'files': {p: Path(p).read_text() for p in self.paths}, **changes}

    def verdict(self, ctx, value, **changes):
        return {'batch': 0, 'bundle_sha': s.bundle_hash(value), 'verdict': {
            'accurate': True, 'complete': True, 'reader_focused': True, 'related_docs_consistent': True,
            'reviewed_pages': self.paths, 'reason': 'Source checked', **changes}}

    def test_new_snapshot_copies_main_manifest_assets_and_preserves_old_release(self):
        ctx, seed = self.context()
        self.assertEqual(Path(self.paths[1]).read_text(), '# Main feature\n')
        self.assertIn('static/versions/v1.13.0/images/a.svg', seed)
        self.assertEqual(s.read_json(s.REGISTRY), ['v1.13.0', 'v1.12.0'])
        self.assertEqual(Path('src/lib/versions/v1.12.0/content/reference/index.md').read_text(), '# Frozen older docs\n')

    def test_existing_snapshot_is_not_overwritten_but_missing_page_is_seeded(self):
        s.safe_write(self.paths[0], b'# Release-specific correction\n')
        s.safe_write(self.prefix+'manifest.json', json.dumps({'navigation': self.manifest['navigation'],
                      'pages': {'reference': 'reference/index.md'}}).encode())
        self.git('add', '.'); self.git('commit', '-qm', 'prepared version')
        self.base = self.git('rev-parse', 'HEAD')
        _, seed = self.context()
        self.assertEqual(seed[self.paths[0]], b'# Release-specific correction\n')
        self.assertEqual(seed[self.paths[1]], b'# Main feature\n')

    def test_seed_is_bound_to_source_and_docs_revision(self):
        ctx, _ = self.context()
        with patch.object(releases, 'verify'), patch.object(docs, 'source_git', return_value=RELEASE['sha']):
            s.seed_context(ctx)
            with self.assertRaisesRegex(ValueError, 'seed changed'):
                s.seed_context({**ctx, 'seed_digest': 'bad'})
        with patch.object(releases, 'verify'), patch.object(docs, 'source_git', return_value='b'*40):
            with self.assertRaisesRegex(ValueError, 'published tag'):
                s.seed_context(ctx)

    def test_writer_cannot_modify_main_older_version_or_tooling(self):
        ctx, seed = self.context()
        path = Path(docs.DOC_ROOT+'reference/index.md')
        path.write_text('# Bad cross-version edit\n')
        with self.assertRaisesRegex(ValueError, 'outside'):
            s.export(ctx, 0, seed, Path(self.evidence.name)/'bundle-0.json')

    def test_bundle_cannot_escape_version_omit_pages_or_change_context(self):
        ctx, _ = self.context()
        for changes in [{'base':'b'*40}, {'release':{**RELEASE,'sha':'b'*40}}, {'batch':1},
                        {'files': {docs.DOC_ROOT+'reference/index.md':'bad'}}, {'files': {}}]:
            with self.assertRaises(ValueError):
                s.validate_bundle(ctx, 0, self.bundle(ctx, **changes))
        bad = self.bundle(ctx); bad['files'][self.paths[0]] = None
        with self.assertRaisesRegex(ValueError, 'overview'):
            s.validate_bundle(ctx, 0, bad)

    def test_missing_review_or_rejected_batch_cannot_finalize(self):
        ctx, _ = self.context(); directory = Path(self.evidence.name)
        value = self.bundle(ctx)
        s.write_json(directory/'bundle-0.json', value)
        with self.assertRaises(FileNotFoundError): s.finalize(ctx, directory)
        s.write_json(directory/'review-0.json', self.verdict(ctx, value, complete=False))
        with self.assertRaisesRegex(ValueError, 'review'): s.finalize(ctx, directory)
        s.write_json(directory/'review-0.json', self.verdict(ctx, value))
        value['files'][self.paths[1]] = 'Changed after review'
        s.write_json(directory/'bundle-0.json', value)
        with self.assertRaisesRegex(ValueError, 'review'): s.finalize(ctx, directory)

    def test_all_accepted_pages_assemble_and_unsupported_page_leaves_nav(self):
        ctx, _ = self.context(); directory = Path(self.evidence.name)
        value = self.bundle(ctx); value['files'][self.paths[1]] = None
        s.write_json(directory/'bundle-0.json', value)
        s.write_json(directory/'review-0.json', self.verdict(ctx, value))
        s.finalize(ctx, directory)
        self.assertFalse(Path(self.paths[1]).exists())
        manifest = s.read_json(self.prefix+'manifest.json')
        self.assertNotIn('reference/feature', manifest['pages'])
        self.assertEqual(manifest['navigation']['reference']['sections'], [])
        self.assertEqual(s.read_json(self.prefix+'sync.json')['source_release'], RELEASE)
        changed = self.git('diff','--cached','--name-only').splitlines()
        self.assertFalse(any(p.startswith(docs.DOC_ROOT) for p in changed))
        self.assertFalse(any('v1.12.0/' in p for p in changed))


class MainSourceGitTests(unittest.TestCase):
    setUp = fixtures.GitGuardTests.setUp
    cleanup = fixtures.GitGuardTests.cleanup
    git = fixtures.GitGuardTests.git

    def test_main_can_document_a_post_release_commit_but_not_an_unpinned_head(self):
        self.path.write_text('Merged new behavior\n'); self.git('commit','-qam','new feature')
        current = self.git('rev-parse','HEAD')
        item = {**self.item, 'source_sha': current, 'source_target': {'version':'main','sha':current}}
        with patch.dict(os.environ, {'SOURCE_ROOT': os.getcwd()}), patch.object(releases,'verify') as verify:
            docs.verify_item_release(item)
            verify.assert_not_called()
            self.git('checkout','--detach',self.base)
            with self.assertRaisesRegex(ValueError,'pinned main'):
                docs.verify_item_release(item)
