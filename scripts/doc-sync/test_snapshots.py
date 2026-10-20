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


class AuditBatchTests(unittest.TestCase):
    def test_diagnostic_requires_dry_run_and_trusted_workflow_origin(self):
        run = {'head_repository': {'full_name': 'smg-project/smg-docs'},
               'path': '.github/workflows/nightly-doc-sync.yml', 'event': 'workflow_dispatch'}
        env = {'DIAGNOSTIC_RUN_ID': '123', 'DRY_RUN': 'true',
               'GITHUB_REPOSITORY': 'smg-project/smg-docs', 'DIAGNOSTIC_BATCHES': '1'}
        with patch.dict(os.environ, env), patch.object(s.releases, 'api', return_value=run) as api, \
                patch.object(s, 'import_diagnostic', return_value=({'include': []}, 'a' * 64)) as imported, \
                patch.object(s, 'outputs'):
            for override in [{'DRY_RUN': 'false'}, {'DIAGNOSTIC_RUN_ID': '../123'}]:
                with patch.dict(os.environ, override), self.assertRaisesRegex(ValueError, 'dry-run'):
                    s.prepare_diagnostic({}, Path('/tmp/release-docs'))
            api.assert_not_called()
            for invalid in [dict(run, head_repository={'full_name': 'other/fork'}),
                            dict(run, path='.github/workflows/other.yml'),
                            dict(run, event='pull_request')]:
                api.return_value = invalid
                with self.assertRaisesRegex(ValueError, 'this repository nightly'):
                    s.prepare_diagnostic({}, Path('/tmp/release-docs'))
            imported.assert_not_called()
            api.return_value = run
            s.prepare_diagnostic({}, Path('/tmp/release-docs'))
            imported.assert_called_once_with({}, Path('/tmp/release-docs'),
                Path('/tmp/release-checkpoint-context'), Path('/tmp/release-checkpoint-bundles'), '1', 'write')

            with patch.dict(os.environ, {'DIAGNOSTIC_STAGE': 'repair-followup'}):
                s.prepare_diagnostic({}, Path('/tmp/release-docs'))
            self.assertEqual(imported.call_args.args[-1], 'repair-followup')

    def test_diagnostic_selection_cannot_expand_or_duplicate_scopes(self):
        ctx = {'batches': [['one.md'], ['two.md']], 'matrix': {'include': [
            {'batch': 0, 'max_turns': 180}, {'batch': 1, 'max_turns': 300}]}}
        self.assertEqual(s.diagnostic_matrix(ctx, '1'),
                         {'include': [{'batch': 1, 'max_turns': 300}]})
        for invalid in ['', '-1', '2', '0,0', '0,1,0', '0; echo bad', '0, 1']:
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                s.diagnostic_matrix(ctx, invalid)

    def test_diagnostic_includes_related_batches_without_changing_their_scopes_or_budgets(self):
        ctx = {'batches': [[f'page-{i}.md'] for i in range(10)],
               'matrix': {'include': [{'batch': i, 'max_turns': 300 if i == 6 else 180}
                                      for i in range(10)]}}
        original = json.loads(json.dumps(ctx))
        for selected in [[0, 2, 4, 6, 8], list(range(8))]:
            self.assertEqual(s.diagnostic_matrix(ctx, ','.join(map(str, selected))),
                             {'include': [ctx['matrix']['include'][i] for i in selected]})
        for invalid in ['0,1,2,3,4,5,6,7,8', '0,1,2,3,4,5,6,6', '0,1,2,3,4,5,6,10']:
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                s.diagnostic_matrix(ctx, invalid)
        self.assertEqual(ctx, original)

    def test_every_page_is_assigned_once_in_stable_order(self):
        pages = {f'page-{i:02}.md': (b'doc', b'main') for i in reversed(range(59))}
        batches = s.audit_batches(pages)
        self.assertEqual([p for batch in batches for p in batch], sorted(pages))
        self.assertTrue(all(0 < len(batch) <= 2 for batch in batches))

    def test_reading_budget_splits_batches_before_the_page_cap(self):
        page = b'x' * (s.MAX_BATCH_BYTES // 2 + 1)
        self.assertEqual(s.audit_batches({'a.md': (page,), 'b.md': (page,)}),
                         [['a.md'], ['b.md']])

    def test_large_page_is_isolated_without_losing_coverage(self):
        self.assertEqual(s.audit_batches({'a.md': (b'small',),
                         'b.md': (b'x' * (s.MAX_BATCH_BYTES + 1),), 'c.md': (b'small',)}),
                         [['a.md'], ['b.md'], ['c.md']])

    def test_large_main_counterpart_counts_even_when_snapshot_is_short(self):
        large = b'x' * s.MAX_BATCH_BYTES
        self.assertEqual(s.audit_batches({'a.md': (b'small', large),
                                         'b.md': (b'small', b'small')}),
                         [['a.md'], ['b.md']])

    def test_turn_budget_grows_only_for_isolated_oversized_pages(self):
        pages = {'a.md': (b'x' * s.MAX_BATCH_BYTES,),
                 'b.md': (b'small', b'x' * (s.MAX_BATCH_BYTES + 1)),
                 'c.md': (b'small',)}
        batches = s.audit_batches(pages)
        self.assertEqual(s.audit_matrix(pages, batches), {'include': [
            {'batch': 0, 'max_turns': 180}, {'batch': 1, 'max_turns': 300},
            {'batch': 2, 'max_turns': 180}]})


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


class RegressionCatalogTests(unittest.TestCase):
    def test_checked_in_cases_have_unique_ids_and_scoped_source_evidence(self):
        cases = s.read_json(Path(s.__file__).with_name('release-regressions.json'))
        self.assertEqual(len(cases), len({case['id'] for case in cases}))
        for case in cases:
            self.assertRegex(case['source_sha'], r'^[a-f0-9]{40}$')
            self.assertTrue(case['pages'] and all(s.page_path(p) for p in case['pages']))
            self.assertTrue(case['source_paths'] and case['check'].strip())
            for path in case['source_paths']:
                self.assertFalse(Path(path).is_absolute())
                self.assertNotIn('..', Path(path).parts)

    def test_cases_follow_exact_source_and_assigned_pages_not_version_name(self):
        case = s.read_json(Path(s.__file__).with_name('release-regressions.json'))[0]
        ctx = {'release': {**RELEASE, 'sha': case['source_sha']},
               'batches': [[s.root(RELEASE['tag']) + 'content/' + case['pages'][0]],
                           [s.root(RELEASE['tag']) + 'content/contributing/index.md']]}
        self.assertIn(case, s.regression_checks(ctx, 0))
        self.assertEqual(s.regression_checks(ctx, 1), [])
        ctx['release']['sha'] = 'f' * 40
        self.assertEqual(s.regression_checks(ctx, 0), [])


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
        return {'batch': 0, 'bundle_sha': s.bundle_hash(value),
                'snapshot_sha': s.snapshot_hash([value]), 'verdict': {
            'accurate': True, 'complete': True, 'reader_focused': True, 'related_docs_consistent': True,
            'reviewed_pages': self.paths, 'reason': 'Source checked', 'regression_checks': [], **changes}}

    def test_batch_evidence_exposes_inherited_omissions_and_deleted_pages(self):
        ctx, _ = self.context()
        directory = Path(self.evidence.name)
        Path(self.paths[1]).write_text('# Snapshot without the released feature\n')
        s.batch_evidence(ctx, 0, directory)
        diff = (directory / 'main-to-snapshot.diff').read_text()
        self.assertIn('-# Main feature', diff)
        self.assertIn('+# Snapshot without the released feature', diff)
        self.assertIn('src/lib/content/reference/feature.md', diff)
        self.assertEqual(s.read_json(directory / 'batch.json')['paths'], self.paths)
        Path(self.paths[1]).unlink()
        s.batch_evidence(ctx, 0, directory)
        self.assertIn('-# Main feature', (directory / 'main-to-snapshot.diff').read_text())

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


class RepairTests(unittest.TestCase):
    setUp = SnapshotGitTests.setUp
    cleanup = SnapshotGitTests.cleanup
    git = SnapshotGitTests.git
    context = SnapshotGitTests.context

    def checkpoint(self, ctx, directory):
        checkpoint = directory / 'prior-context'
        checkpoint.mkdir()
        s.write_json(checkpoint / 'context.json', ctx)
        destination = directory / 'diagnostic'
        destination.mkdir()
        bundles = directory / 'prior-bundles'
        for index in range(len(ctx['batches'])):
            s.write_json(bundles / f'release-bundle-{index}' / f'bundle-{index}.json',
                         s.read_json(directory / f'bundle-{index}.json'))
        # Old approval artifacts must never enter the new evidence directory.
        s.write_json(bundles / 'release-review-0' / 'review-0.json', {'accepted': True})
        return checkpoint, destination, bundles

    def test_diagnostic_import_keeps_full_context_but_never_imports_approvals(self):
        ctx, directory, values = self.proposal(rejected=())
        checkpoint, destination, bundles = self.checkpoint(ctx, directory)
        matrix, before = s.import_diagnostic(ctx, destination, checkpoint, bundles, '1')
        self.assertEqual([v['batch'] for v in matrix['include']], [1])
        self.assertEqual(s.proposal_hash(ctx, destination), before)
        self.assertEqual(len(list(destination.glob('bundle-*.json'))), len(values))
        self.assertEqual(list(destination.glob('review-*.json')), [])
        with self.assertRaisesRegex(ValueError, 'unchanged'):
            s.require_repair_change(ctx, destination, before)
        values[1]['files'][ctx['batches'][1][0]] = '# Source-verified correction\n'
        s.write_json(destination / 'bundle-1.json', values[1])
        s.require_repair_change(ctx, destination, before)

    def test_diagnostic_rejects_different_source_base_seed_and_page_inventory(self):
        ctx, directory, _ = self.proposal()
        checkpoint, destination, bundles = self.checkpoint(ctx, directory)
        variants = [dict(ctx, base='b' * 40), dict(ctx, seed_digest='other'),
                    dict(ctx, release={**ctx['release'], 'sha': 'c' * 40}),
                    dict(ctx, batches=list(reversed(ctx['batches'])))]
        for different in variants:
            s.write_json(checkpoint / 'context.json', different)
            with self.assertRaisesRegex(ValueError, 'frozen release inputs'):
                s.import_diagnostic(ctx, destination, checkpoint, bundles, '1')
        self.assertEqual(list(destination.iterdir()), [])

    def test_diagnostic_uses_a_complete_corrected_stage_without_old_drafts_or_approvals(self):
        ctx, directory, values = self.proposal(rejected=())
        checkpoint, destination, bundles = self.checkpoint(ctx, directory)
        for stage in ('repair', 'repair-followup'):
            prefix = s.DIAGNOSTIC_ARTIFACT_PREFIXES[stage]
            corrected = json.loads(json.dumps(values))
            for i, value in enumerate(corrected):
                value['files'][ctx['batches'][i][0]] = f'# Corrected {stage} page {i}\n'
                s.write_json(bundles / f'{prefix}-{i}' / f'bundle-{i}.json', value)
            stage_output = destination / stage
            _, before = s.import_diagnostic(ctx, stage_output, checkpoint, bundles, '1', stage)
            self.assertEqual(before, s.snapshot_hash(corrected))
            self.assertNotEqual(before, s.snapshot_hash(values))
            self.assertEqual(s.proposal_hash(ctx, stage_output), before)
            self.assertEqual(s.read_json(stage_output / 'diagnostic.json')['stage'], stage)
            self.assertEqual(list(stage_output.glob('*review*')), [])
            for i, value in enumerate(corrected):
                self.assertEqual(s.read_json(stage_output / f'bundle-{i}.json'), value)

    def test_diagnostic_rejects_incomplete_or_invalid_corrected_stage_without_fallback(self):
        ctx, directory, values = self.proposal(rejected=())
        checkpoint, destination, bundles = self.checkpoint(ctx, directory)
        s.write_json(bundles / 'release-followup-bundle-0' / 'bundle-0.json', values[0])
        with self.assertRaises(FileNotFoundError):
            s.import_diagnostic(ctx, destination, checkpoint, bundles, '1', 'repair-followup')
        self.assertEqual(list(destination.iterdir()), [])
        invalid = {**values[1], 'release': {**RELEASE, 'sha': 'f' * 40}}
        s.write_json(bundles / 'release-followup-bundle-1' / 'bundle-1.json', invalid)
        with self.assertRaises(ValueError):
            s.import_diagnostic(ctx, destination, checkpoint, bundles, '1', 'repair-followup')
        self.assertEqual(list(destination.iterdir()), [])
        for stage in ('unknown', '../release-review', 'write; echo bad'):
            with self.subTest(stage=stage), self.assertRaisesRegex(ValueError, 'checkpoint stage'):
                s.import_diagnostic(ctx, destination, checkpoint, bundles, '1', stage)

    def test_diagnostic_rejects_missing_and_out_of_scope_checkpoint_data(self):
        ctx, directory, values = self.proposal()
        checkpoint, destination, bundles = self.checkpoint(ctx, directory)
        (bundles / 'release-bundle-0' / 'bundle-0.json').unlink()
        with self.assertRaises(FileNotFoundError):
            s.import_diagnostic(ctx, destination, checkpoint, bundles, '1')
        values[0]['files']['.github/workflows/ci.yml'] = 'unsafe tooling'
        s.write_json(bundles / 'release-bundle-0' / 'bundle-0.json', values[0])
        with self.assertRaisesRegex(ValueError, 'assigned page'):
            s.import_diagnostic(ctx, destination, checkpoint, bundles, '1')
        self.assertEqual(list(destination.iterdir()), [])

    def test_diagnostic_results_cannot_authorize_finalization_or_publication(self):
        ctx, directory, _ = self.proposal(rejected=())
        s.write_json(directory / 'diagnostic.json', {})
        raw = s.read_json(directory / 'review-0.json')['verdict']
        with patch.dict(os.environ, {'REVIEW_JSON': json.dumps(raw)}):
            s.review(ctx, 0, directory)
        self.assertTrue(s.read_json(directory / 'review-0.json')['diagnostic'])
        with self.assertRaisesRegex(ValueError, 'cannot finalize'):
            s.finalize(ctx, directory)
        (directory / 'diagnostic.json').unlink()
        with self.assertRaisesRegex(ValueError, 'cannot authorize'):
            s.rejected_batches(ctx, directory)
        with patch.dict(os.environ, {'DIAGNOSTIC_RUN_ID': '123'}):
            with self.assertRaisesRegex(ValueError, 'cannot publish'):
                s.publish(ctx)

    def test_diagnostic_rejects_correct_local_pages_when_related_pages_contradict_them(self):
        ctx, directory, values = self.proposal(rejected=())
        s.write_json(directory / 'diagnostic.json', {})
        raw = s.read_json(directory / 'review-0.json')['verdict']
        raw.update(related_docs_consistent=False,
                   reason='Assigned page is correct; the related checkpoint page has a stale default')
        with patch.dict(os.environ, {'REVIEW_JSON': json.dumps(raw)}):
            with self.assertRaisesRegex(ValueError, 'related checkpoint page'):
                s.review(ctx, 0, directory)
        result = s.read_json(directory / 'review-0.json')
        self.assertTrue(result['diagnostic'])
        self.assertEqual(result['verdict'], raw)
        self.assertEqual(result['snapshot_sha'], s.snapshot_hash(values))

    def proposal(self, rejected=(1,)):
        ctx, _ = self.context()
        ctx['batches'] = [[p] for p in self.paths]
        ctx['matrix'] = {'include': [{'batch': 0, 'max_turns': 180},
                                      {'batch': 1, 'max_turns': 300}]}
        directory = Path(self.evidence.name)
        values = [{'base': self.base, 'release': RELEASE, 'batch': i,
                   'files': {p: f'# Writer proposal {i}\n'}} for i, p in enumerate(self.paths)]
        for i, value in enumerate(values):
            s.write_json(directory / f'bundle-{i}.json', value)
        self.records(ctx, directory, values, rejected)
        return ctx, directory, values

    def records(self, ctx, directory, values, rejected=()):
        for i, value in enumerate(values):
            verdict = {gate: i not in rejected for gate in s.REVIEW_GATES}
            verdict.update(reviewed_pages=ctx['batches'][i], reason='Specific release evidence', regression_checks=[])
            s.write_json(directory / f'review-{i}.json', {
                'batch': i, 'bundle_sha': s.bundle_hash(value),
                'snapshot_sha': s.snapshot_hash(values), 'verdict': verdict})

    def test_negative_regression_blocks_publication_despite_positive_general_gates(self):
        ctx, directory, _ = self.proposal(rejected=())
        check = {'id': 'released-feature', 'satisfied': False,
                 'evidence': 'feature.rs::render enables it; reference/feature.md omits it'}
        raw = s.read_json(directory / 'review-1.json')['verdict']
        raw['regression_checks'] = [check]
        with patch.object(s, 'regression_checks', side_effect=lambda ctx, i: [check] if i == 1 else []):
            with patch.dict(os.environ, {'REVIEW_JSON': json.dumps(raw)}):
                s.review(ctx, 1, directory, require_accepted=False)
            self.assertEqual(s.rejected_batches(ctx, directory), [1])
            with self.assertRaisesRegex(ValueError, 'rejected full snapshot'):
                s.finalize(ctx, directory)
            self.assertEqual(s.repair_findings(ctx, directory)[0]['verdict']['regression_checks'], [check])

    def test_regression_evidence_requires_exact_coverage_and_explicit_results(self):
        ctx, directory, _ = self.proposal(rejected=())
        raw = s.read_json(directory / 'review-1.json')['verdict']
        check = {'id': 'released-feature', 'satisfied': True,
                 'evidence': 'feature.rs::render and reference/feature.md agree'}
        with patch.object(s, 'regression_checks', return_value=[check]):
            for invalid in [None, [], [check, check], [{**check, 'id': 'unknown'}],
                            [{**check, 'satisfied': 'true'}], [{**check, 'evidence': ''}], [None]]:
                with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                    s.validate_review(ctx, 1, {**raw, 'regression_checks': invalid})
            self.assertTrue(s.validate_review(ctx, 1, {**raw, 'regression_checks': [check]}))

    def test_record_rejection_is_repairable_but_cannot_finalize(self):
        ctx, directory, _ = self.proposal()
        raw = s.read_json(directory / 'review-1.json')['verdict']
        with patch.dict(os.environ, REVIEW_JSON=json.dumps(raw)):
            s.review(ctx, 1, directory, require_accepted=False)
            with self.assertRaisesRegex(ValueError, 'rejected'):
                s.review(ctx, 1, directory)
        with self.assertRaisesRegex(ValueError, 'rejected'):
            s.finalize(ctx, directory)

    def test_plan_selects_only_rejected_batches_with_their_frozen_budget(self):
        ctx, directory, _ = self.proposal()
        finding = s.read_json(directory / 'review-1.json')
        finding['verdict']['related_docs_consistent'] = True
        s.write_json(directory / 'review-1.json', finding)
        with patch.object(s, 'outputs') as out:
            s.plan_repair(ctx, directory)
        out.assert_called_once_with(needed='true', matrix={
            'include': [{'batch': 1, 'max_turns': 300}]},
            snapshot_sha=s.proposal_hash(ctx, directory))

    def test_cross_page_finding_reaches_a_batch_its_own_reviewer_accepted(self):
        ctx, directory, values = self.proposal()
        finding = s.read_json(directory / 'review-1.json')
        finding['verdict'].update(
            accurate=True, complete=True, reader_focused=True,
            reason=f'{self.paths[0]} contradicts this page: ZMQ does not use shared memory.')
        s.write_json(directory / 'review-1.json', finding)
        with patch.object(s, 'outputs') as out:
            s.plan_repair(ctx, directory)
        out.assert_called_once_with(needed='true', matrix=ctx['matrix'],
                                    snapshot_sha=s.proposal_hash(ctx, directory))
        self.assertEqual(s.repair_findings(ctx, directory), [finding])
        # Sharing another review never expands the writer's original file scope.
        s.assemble(ctx, directory)
        Path(self.paths[0]).write_text('# ZMQ uses inline tensors\n')
        s.export(ctx, 0, s.assembled_data(ctx, directory), directory / 'repaired.json')
        result = s.read_json(directory / 'repaired.json')
        self.assertEqual(set(result['files']), {self.paths[0]})
        s.write_json(directory / 'bundle-0.json', result)
        with self.assertRaisesRegex(ValueError, 'stale snapshot review'):
            s.finalize(ctx, directory)
        values[0] = result
        self.records(ctx, directory, values)
        s.finalize(ctx, directory)

    def test_all_accepted_reviews_skip_repair(self):
        ctx, directory, _ = self.proposal(rejected=())
        with patch.object(s, 'outputs') as out:
            s.plan_repair(ctx, directory)
        out.assert_called_once_with(needed='false', matrix={'include': []},
                                    snapshot_sha=s.proposal_hash(ctx, directory))
        s.finalize(ctx, directory)

    def test_unchanged_rejected_proposal_cannot_get_another_review(self):
        ctx, directory, values = self.proposal()
        before = s.proposal_hash(ctx, directory)
        with self.assertRaisesRegex(ValueError, 'unchanged'):
            s.require_repair_change(ctx, directory, before)
        for invalid in ['', 'bad', 'a' * 40]:
            with self.assertRaisesRegex(ValueError, 'trusted pre-repair'):
                s.require_repair_change(ctx, directory, invalid)
        values[1]['files'][self.paths[1]] = '# Corrected released behavior\n'
        s.write_json(directory / 'bundle-1.json', values[1])
        s.require_repair_change(ctx, directory, before)
        with self.assertRaisesRegex(ValueError, 'stale'):
            s.finalize(ctx, directory)

    def test_new_cross_page_findings_after_repair_require_correction_and_fresh_reviews(self):
        ctx, directory, values = self.proposal()
        before = s.proposal_hash(ctx, directory)
        values[1]['files'][self.paths[1]] = '# First correction\n'
        s.write_json(directory / 'bundle-1.json', values[1])
        s.require_repair_change(ctx, directory, before)
        self.records(ctx, directory, values, rejected=(0,))
        new_finding = s.read_json(directory / 'review-0.json')
        new_finding['verdict'].update(
            accurate=True, complete=True, reader_focused=True,
            reason=f'{self.paths[1]} uses an example path the release validator rejects.')
        s.write_json(directory / 'review-0.json', new_finding)
        self.assertEqual(s.repair_findings(ctx, directory), [new_finding])
        with patch.object(s, 'outputs') as out:
            s.plan_repair(ctx, directory)
        out.assert_called_once_with(needed='true', matrix=ctx['matrix'],
                                    snapshot_sha=s.proposal_hash(ctx, directory))
        before = s.proposal_hash(ctx, directory)
        s.assemble(ctx, directory)
        Path(self.paths[1]).write_text('# Corrected example path\n')
        s.export(ctx, 1, s.assembled_data(ctx, directory), directory / 'next.json')
        values[1] = s.read_json(directory / 'next.json')
        s.write_json(directory / 'bundle-1.json', values[1])
        s.require_repair_change(ctx, directory, before)
        with self.assertRaisesRegex(ValueError, 'stale'):
            s.finalize(ctx, directory)
        self.records(ctx, directory, values, rejected=(0,))
        with self.assertRaisesRegex(ValueError, 'rejected'):
            s.finalize(ctx, directory)
        self.records(ctx, directory, values)
        s.finalize(ctx, directory)
        self.assertEqual(Path(self.paths[1]).read_text(), '# Corrected example path\n')

    def test_missing_or_malformed_review_is_fatal_not_repairable(self):
        ctx, directory, values = self.proposal()
        (directory / 'review-0.json').unlink()
        with self.assertRaises(FileNotFoundError):
            s.plan_repair(ctx, directory)
        self.records(ctx, directory, values)
        result = s.read_json(directory / 'review-0.json')
        result['verdict']['accurate'] = 'true'
        s.write_json(directory / 'review-0.json', result)
        with self.assertRaisesRegex(ValueError, 'Invalid snapshot review'):
            s.plan_repair(ctx, directory)
        with self.assertRaisesRegex(ValueError, 'Invalid snapshot review'):
            s.repair_findings(ctx, directory)

    def test_repair_export_preserves_other_writers_edits(self):
        ctx, directory, _ = self.proposal()
        s.assemble(ctx, directory)
        original = Path(self.paths[0]).read_bytes()
        Path(self.paths[1]).write_text('# Correct released contract\n')
        s.export(ctx, 1, s.assembled_data(ctx, directory), directory / 'repaired.json')
        result = s.read_json(directory / 'repaired.json')
        self.assertEqual(result['files'], {self.paths[1]: '# Correct released contract\n'})
        self.assertEqual(Path(self.paths[0]).read_bytes(), original)

    def test_repair_export_preserves_other_batch_deletion_and_filtered_nav(self):
        ctx, directory, values = self.proposal(rejected=(0,))
        values[1]['files'][self.paths[1]] = None
        s.write_json(directory / 'bundle-1.json', values[1])
        s.assemble(ctx, directory)
        Path(self.paths[0]).write_text('# Correct overview\n')
        s.export(ctx, 0, s.assembled_data(ctx, directory), directory / 'repaired.json')
        self.assertFalse(Path(self.paths[1]).exists())
        manifest = s.read_json(self.prefix + 'manifest.json')
        self.assertNotIn('reference/feature', manifest['pages'])

    def test_repair_cannot_change_accepted_pages_main_or_metadata(self):
        ctx, directory, _ = self.proposal()
        for forbidden in [self.paths[0], docs.DOC_ROOT + 'reference/index.md',
                          self.prefix + 'manifest.json']:
            s.assemble(ctx, directory)
            old = Path(forbidden).read_bytes()
            Path(forbidden).write_text('Unrelated change\n')
            with self.assertRaisesRegex(ValueError, 'outside'):
                s.export(ctx, 1, s.assembled_data(ctx, directory), directory / 'repaired.json')
            Path(forbidden).write_bytes(old)

    def test_any_repair_invalidates_every_earlier_consistency_review(self):
        ctx, directory, values = self.proposal()
        values[1]['files'][self.paths[1]] = '# Correct released contract\n'
        s.write_json(directory / 'bundle-1.json', values[1])
        # A fresh review of the repaired page cannot preserve an old verdict for
        # an unchanged page: that verdict checked the previous related content.
        old_unchanged = s.read_json(directory / 'review-0.json')
        self.records(ctx, directory, values)
        s.write_json(directory / 'review-0.json', old_unchanged)
        with self.assertRaisesRegex(ValueError, 'stale snapshot review'):
            s.finalize(ctx, directory)
        self.records(ctx, directory, values)
        s.finalize(ctx, directory)
        self.assertEqual(Path(self.paths[1]).read_text(), '# Correct released contract\n')

    def test_record_review_binds_the_entire_assembled_proposal(self):
        ctx, directory, values = self.proposal(rejected=())
        raw = s.read_json(directory / 'review-0.json')['verdict']
        with patch.dict(os.environ, REVIEW_JSON=json.dumps(raw)):
            s.review(ctx, 0, directory, require_accepted=False)
        record = s.read_json(directory / 'review-0.json')
        self.assertEqual(record['snapshot_sha'], s.snapshot_hash(values))
        raw['reviewed_pages'] *= 2
        with patch.dict(os.environ, REVIEW_JSON=json.dumps(raw)):
            with self.assertRaisesRegex(ValueError, 'every assigned'):
                s.review(ctx, 0, directory, require_accepted=False)


    def test_reader_rules_override_accepting_model_and_plan_repair(self):
        ctx, directory, values = self.proposal(rejected=())
        values[1]['files'][self.paths[1]] = '# Feature\nInherited claim (smg-project/smg#2584).\n'
        s.write_json(directory / 'bundle-1.json', values[1])
        self.records(ctx, directory, values)
        raw = s.read_json(directory / 'review-1.json')['verdict']
        with patch.dict(os.environ, REVIEW_JSON=json.dumps(raw)):
            s.review(ctx, 1, directory, require_accepted=False)
        recorded = s.read_json(directory / 'review-1.json')['verdict']
        self.assertFalse(recorded['reader_focused'])
        self.assertIn('code-change-reference', recorded['reason'])
        self.assertIn('"line": 2', recorded['reason'])
        with patch.object(s, 'outputs') as out:
            s.plan_repair(ctx, directory)
        out.assert_called_once_with(needed='true', matrix={
            'include': [{'batch': 1, 'max_turns': 300}]},
            snapshot_sha=s.proposal_hash(ctx, directory))

    def test_finalization_rechecks_reader_rules_even_if_review_claims_acceptance(self):
        ctx, directory, values = self.proposal(rejected=())
        values[1]['files'][self.paths[1]] = '# Feature\nSee https://github.com/smg-project/smg/pull/1.\n'
        s.write_json(directory / 'bundle-1.json', values[1])
        self.records(ctx, directory, values)
        with self.assertRaisesRegex(ValueError, 'rejected'):
            s.finalize(ctx, directory)
        raw = s.read_json(directory / 'review-1.json')['verdict']
        with patch.dict(os.environ, REVIEW_JSON=json.dumps(raw)):
            with self.assertRaisesRegex(ValueError, 'code-change-reference'):
                s.review(ctx, 1, directory)

    def test_reader_repair_requires_clean_prose_and_fresh_reviews(self):
        ctx, directory, values = self.proposal(rejected=())
        values[1]['files'][self.paths[1]] = '# Feature\nThis unreleased change is in current main.\n'
        s.write_json(directory / 'bundle-1.json', values[1])
        self.records(ctx, directory, values)
        self.assertEqual(s.rejected_batches(ctx, directory), [1])
        values[1]['files'][self.paths[1]] = '# Feature\nSet the delay to configure the grace period.\n'
        s.write_json(directory / 'bundle-1.json', values[1])
        with self.assertRaisesRegex(ValueError, 'stale'):
            s.finalize(ctx, directory)
        self.records(ctx, directory, values)
        s.finalize(ctx, directory)
        self.assertEqual(Path(self.paths[1]).read_text(), values[1]['files'][self.paths[1]])

    def test_reader_rules_preserve_operational_versions_and_cover_reference_forms(self):
        content = ('pip install smg==1.13.0\n'
                   'docker run ghcr.io/sgl-project/smg:v1.13.0\n'
                   'The worker requires vLLM 0.11.0 or later.\n')
        self.assertEqual(s.reader_bundle_violations({'files': {'page.md': content}}), [])
        for citation in ['smg-project/smg#123', 'PR #123', 'issue #123',
                         'https://github.com/smg-project/smg/commit/abc',
                         'https://github.com/smg-project/smg/blob/main/file.rs']:
            self.assertEqual(s.reader_bundle_violations({'files': {'page.md': citation}}),
                             [{'path': 'page.md', 'line': 1, 'rule': 'code-change-reference'}])


class RefreshBaseTests(unittest.TestCase):
    git = SnapshotGitTests.git
    cleanup = SnapshotGitTests.cleanup
    context = SnapshotGitTests.context
    bundle = SnapshotGitTests.bundle
    verdict = SnapshotGitTests.verdict

    def setUp(self):
        SnapshotGitTests.setUp(self)
        self.context()
        self.git('add', '.'); self.git('commit', '-qm', 'authored release snapshot')
        self.base = self.git('rev-parse', 'HEAD')
        self.remote = str(Path(self.evidence.name) / 'remote.git')
        self.upstream = str(Path(self.evidence.name) / 'upstream')
        self.git('init', '--bare', '-q', self.remote)
        self.git('remote', 'add', 'origin', self.remote)
        self.git('push', '-q', 'origin', 'HEAD:refs/heads/main')
        self.git('clone', '-q', '--branch', 'main', self.remote, self.upstream)
        self.git('-C', self.upstream, 'config', 'user.name', 'Test')
        self.git('-C', self.upstream, 'config', 'user.email', 'test@example.com')
        mocker = patch.dict(os.environ, {'GITHUB_REPOSITORY': 'smg-project/smg-docs'})
        mocker.start(); self.addCleanup(mocker.stop)

    def stage_reviewed_snapshot(self):
        ctx, _ = self.context()
        Path(self.paths[1]).write_text('# Independently reviewed release contract\n')
        value = self.bundle(ctx)
        directory = Path(self.evidence.name)
        s.write_json(directory / 'bundle-0.json', value)
        s.write_json(directory / 'review-0.json', self.verdict(ctx, value))
        s.finalize(ctx, directory)
        return ctx, value

    def advance_main(self, path, text):
        output = Path(self.upstream) / path
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text)
        self.git('-C', self.upstream, 'add', '.')
        self.git('-C', self.upstream, 'commit', '-qm', 'concurrent main update')
        self.git('-C', self.upstream, 'push', '-q', 'origin', 'HEAD:refs/heads/main')
        return self.git('-C', self.upstream, 'rev-parse', 'HEAD')

    def refresh(self, ctx, live):
        with patch.object(releases, 'api', return_value={'sha': live}), patch.object(s, 'outputs') as out:
            s.refresh_base(ctx)
            out.assert_called_once_with(base=live)

    def test_working_page_edit_keeps_reviewed_bytes_and_uses_new_base(self):
        ctx, value = self.stage_reviewed_snapshot()
        live = self.advance_main(docs.DOC_ROOT + 'reference/index.md', '# Updated main docs\n')
        self.refresh(ctx, live)
        self.assertEqual(self.git('rev-parse', 'HEAD'), live)
        self.assertEqual(Path(docs.DOC_ROOT + 'reference/index.md').read_text(), '# Updated main docs\n')
        self.assertEqual(Path(self.paths[1]).read_text(), value['files'][self.paths[1]])
        self.assertEqual(s.bundle_hash(s.read_json(Path(self.evidence.name) / 'bundle-0.json')),
                         s.bundle_hash(value))
        self.assertFalse(any(p.startswith(docs.DOC_ROOT) for p in
                             self.git('diff', '--cached', '--name-only').splitlines()))
        with patch.dict(os.environ, {'DOCS_PUBLISH_BASE': live, 'DRY_RUN': 'true'}), \
                patch.object(releases, 'api', return_value={'sha': live}), \
                patch.object(releases, 'verify'), patch.object(docs, 'existing_prs', return_value=[]):
            s.publish(ctx)

    def test_concurrent_release_page_edit_blocks_refresh(self):
        ctx, value = self.stage_reviewed_snapshot()
        live = self.advance_main(self.paths[1], '# Concurrent release fix\n')
        with self.assertRaisesRegex(ValueError, 'inputs changed'):
            self.refresh(ctx, live)
        self.assertEqual(self.git('rev-parse', 'HEAD'), ctx['base'])
        self.assertEqual(Path(self.paths[1]).read_text(), value['files'][self.paths[1]])

    def test_new_main_page_requires_fresh_coverage(self):
        ctx, _ = self.stage_reviewed_snapshot()
        live = self.advance_main(docs.DOC_ROOT + 'reference/new.md', '# New main page\n')
        with self.assertRaisesRegex(ValueError, 'inputs changed'):
            self.refresh(ctx, live)

    def test_tooling_change_requires_fresh_validation(self):
        ctx, _ = self.stage_reviewed_snapshot()
        live = self.advance_main('package.json', '{}\n')
        with self.assertRaisesRegex(ValueError, 'inputs changed'):
            self.refresh(ctx, live)

    def test_main_edit_that_changes_copied_snapshot_seed_is_rejected(self):
        self.git('rm', self.paths[1]); self.git('commit', '-qm', 'incomplete prepared snapshot')
        self.base = self.git('rev-parse', 'HEAD')
        self.git('push', '-q', 'origin', 'HEAD:refs/heads/main')
        self.git('-C', self.upstream, 'fetch', '-q', 'origin')
        self.git('-C', self.upstream, 'reset', '--hard', 'origin/main')
        ctx, _ = self.stage_reviewed_snapshot()
        live = self.advance_main(docs.DOC_ROOT + 'reference/feature.md', '# Changed seed source\n')
        with self.assertRaisesRegex(ValueError, 'seed changed'):
            self.refresh(ctx, live)

    def test_race_after_refresh_still_blocks_publication(self):
        ctx, _ = self.stage_reviewed_snapshot()
        path = docs.DOC_ROOT + 'reference/index.md'
        live = self.advance_main(path, '# Main edit\n')
        self.refresh(ctx, live)
        newer = self.advance_main(path, '# Another main edit\n')
        with patch.dict(os.environ, {'DOCS_PUBLISH_BASE': live, 'DRY_RUN': 'true'}), \
                patch.object(releases, 'api', return_value={'sha': newer}), patch.object(releases, 'verify'):
            with self.assertRaisesRegex(ValueError, 'Docs main changed'):
                s.publish(ctx)


class ReleasePublishTests(unittest.TestCase):
    """Exercise retry publication and races against an actual local bare remote."""
    git = SnapshotGitTests.git
    cleanup = SnapshotGitTests.cleanup

    def setUp(self):
        SnapshotGitTests.setUp(self)
        self.remote = str(Path(self.evidence.name) / 'remote.git')
        self.git('init', '--bare', '-q', self.remote)
        self.git('remote', 'add', 'origin', self.remote)
        self.git('push', '-q', 'origin', 'HEAD:refs/heads/main')
        self.name = s.branch(RELEASE)
        self.ref = 'refs/heads/' + self.name
        self.ctx = {'base': self.base, 'release': RELEASE}
        Path(s.REGISTRY).write_text('["v1.13.0", "v1.12.0"]\n')
        self.git('add', s.REGISTRY)
        self.tree = self.git('write-tree')
        self.prs = []
        for mocker in [patch.dict(os.environ, {'GITHUB_REPOSITORY': 'smg-project/smg-docs', 'DRY_RUN': 'false'}),
                       patch.object(releases, 'verify'),
                       patch.object(releases, 'api', return_value={'sha': self.base}),
                       patch.object(docs, 'existing_prs', side_effect=lambda _: self.prs)]:
            mocker.start(); self.addCleanup(mocker.stop)
        original = docs.run
        self.created = []

        def run(*args):
            if args[:3] == ('gh', 'pr', 'create'):
                self.created.append(args)
                return 'https://example/pull/1'
            return original(*args)
        mocker = patch.object(docs, 'run', side_effect=run)
        mocker.start(); self.addCleanup(mocker.stop)

    def old_branch(self):
        head = self.git('commit-tree', self.base + '^{tree}', '-p', self.base, '-m', 'previous snapshot')
        self.git('push', '-q', 'origin', head + ':' + self.ref)
        return head

    def remote_head(self):
        return self.git('ls-remote', '--heads', 'origin', self.ref).split()[0]

    def assert_published(self):
        s.publish(self.ctx)
        self.assertEqual(self.git('rev-parse', self.remote_head() + '^{tree}'), self.tree)
        self.assertEqual(len(self.created), 1)
        self.assertEqual(self.created[0][self.created[0].index('--head') + 1], self.name)

    def test_first_publication(self):
        self.assert_published()

    def test_closed_pr_reuses_retained_branch_for_new_snapshot(self):
        before = self.old_branch()
        self.prs[:] = [{'state': 'closed', 'branch': self.name, 'body': s.release_marker(RELEASE)}]
        self.assert_published()
        self.assertNotEqual(self.remote_head(), before)

    def test_orphaned_push_can_retry_pr_creation(self):
        self.old_branch()
        self.assert_published()

    def test_open_pr_blocks_replacement_even_if_marker_was_removed(self):
        before = self.old_branch()
        self.prs[:] = [{'state': 'open', 'branch': self.name, 'body': 'Edited by a reviewer'}]
        s.publish(self.ctx)
        self.assertEqual(self.remote_head(), before)
        self.assertEqual(self.git('rev-parse', 'HEAD'), self.base)
        self.assertFalse(self.created)

    def test_concurrent_remote_update_rejects_lease_without_creating_pr(self):
        before = self.old_branch()
        competing = self.git('commit-tree', self.base + '^{tree}', '-p', before, '-m', 'concurrent change')
        mutate = docs.mutate_git

        def race(*args):
            if args[0] == 'push':
                self.git('push', '-q', 'origin', competing + ':' + self.ref)
            return mutate(*args)
        with patch.object(docs, 'mutate_git', side_effect=race):
            with self.assertRaises(subprocess.CalledProcessError):
                s.publish(self.ctx)
        self.assertEqual(self.remote_head(), competing)
        self.assertFalse(self.created)

    def test_concurrent_branch_creation_is_not_overwritten(self):
        competing = self.git('commit-tree', self.base + '^{tree}', '-p', self.base, '-m', 'concurrent branch')
        mutate = docs.mutate_git

        def race(*args):
            if args[0] == 'push':
                self.git('push', '-q', 'origin', competing + ':' + self.ref)
            return mutate(*args)
        with patch.object(docs, 'mutate_git', side_effect=race):
            with self.assertRaises(subprocess.CalledProcessError):
                s.publish(self.ctx)
        self.assertEqual(self.remote_head(), competing)
        self.assertFalse(self.created)

    def test_dry_run_keeps_existing_branch_untouched(self):
        before = self.old_branch()
        with patch.dict(os.environ, {'DRY_RUN': 'true'}):
            s.publish(self.ctx)
        self.assertEqual(self.remote_head(), before)
        self.assertEqual(self.git('rev-parse', 'HEAD'), self.base)
        self.assertFalse(self.created)

    def test_stale_base_and_daily_limit_prevent_retry(self):
        before = self.old_branch()
        with patch.object(releases, 'api', return_value={'sha': 'b' * 40}):
            with self.assertRaisesRegex(ValueError, 'Docs main changed'):
                s.publish(self.ctx)
        with patch.object(docs, 'daily_remaining', return_value=0):
            s.publish(self.ctx)
        self.assertEqual(self.remote_head(), before)
        self.assertFalse(self.created)


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
