# Adapted from ome-projects/ome-docs at 7a77080bf6eb076bebb57a8885792d47aa528494.
# Apache-2.0 (LICENSE).
"""Exercise maintenance policy and real Git publication without remote writes."""

import copy
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import maintenance as m
from test_nightly_docs import proposal


def pull():
    item = m.docs.validate_item(proposal())
    return {"number": 7, "code_sha": "a" * 40, "state": "open", "draft": True, "user": {"login": "github-actions[bot]"},
            "title": item["title"], "body": f"{m.docs.MARKER}{item['key']} -->",
            "head": {"sha": "b" * 40, "ref": item["branch"], "repo": {"full_name": "smg-project/smg-docs"}},
            "base": {"sha": "a" * 40, "ref": "main", "repo": {"full_name": "smg-project/smg-docs"}}}


class PolicyTests(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, {"GITHUB_REPOSITORY": "smg-project/smg-docs",
                                      "SOURCE_ROOT": "/nonexistent/smg", "EXTRA_FEEDBACK": ""})
        env.start()
        self.addCleanup(env.stop)
        base = patch.object(m, 'current_base', return_value='a' * 40)
        base.start()
        self.addCleanup(base.stop)
        code = patch.object(m, "current_code", return_value="a" * 40)
        code.start()
        self.addCleanup(code.stop)

    def test_only_the_documentation_repository_is_supported(self):
        self.assertEqual(m.repo(), "smg-project/smg-docs")
        for other in ["smg-project/smg", "fork/ome-docs"]:
            with self.subTest(repo=other), patch.dict(os.environ, {"GITHUB_REPOSITORY": other}), \
                    self.assertRaisesRegex(ValueError, "Unsupported"):
                m.repo()

    def test_identity_is_checked_independently_of_label(self):
        self.assertEqual(m.eligible(pull())[0], "a" * 40)
        for field, value in [("state", "closed"), ("body", "docs")]:
            pr = pull()
            pr[field] = value
            with self.assertRaises(ValueError):
                m.eligible(pr)
        for who, value in [("user", {"login": "someone"}),
                           ("head", {"sha": "b" * 40, "ref": "some-branch", "repo": {"full_name": "smg-project/smg-docs"}}),
                           ("head", {"sha": "b" * 40, "ref": pull()["head"]["ref"], "repo": {"full_name": "fork/ome"}})]:
            pr = pull()
            pr[who] = value
            with self.assertRaises(ValueError):
                m.eligible(pr)

    def test_budget_survives_own_push_and_force_is_explicit(self):
        state = {"phase": "needs-repair", "attempts": 3, "signature": "old"}
        self.assertEqual(m.decision(state, "new-head"), "needs-human")
        self.assertEqual(m.decision(state, "new-head", True), "work")
        state = {"phase": "ready", "attempts": 0, "signature": "old"}
        self.assertEqual(m.decision(state, "old"), "cached")
        self.assertEqual(m.decision(state, "new-feedback"), "work")

    def test_operational_failures_have_a_separate_bounded_budget(self):
        ctx = {'number': 7, 'attempts': 3, 'infrastructure_attempts': 2,
               'extra_feedback': '', 'run_url': 'url', 'code_sha': 'a' * 40}
        with patch.object(m, 'get_pr', return_value=pull()), \
                patch.object(m, 'save_state') as save, patch.object(m, 'record_check') as record:
            m.failure(ctx)
        state = save.call_args.args[1]
        self.assertEqual(state['attempts'], 2)
        self.assertEqual(state['infrastructure_attempts'], 2)
        self.assertEqual(state['phase'], 'retry-infrastructure')
        self.assertFalse(record.call_args.args[2])
        self.assertEqual(m.decision(state, 'new'), 'work')
        state['infrastructure_attempts'] = 3
        self.assertEqual(m.decision(state, 'new'), 'needs-human')
        self.assertEqual(m.decision(state, 'new', True), 'work')
        ctx['infrastructure_attempts'] = 3
        with patch.object(m, 'get_pr', return_value=pull()), \
                patch.object(m, 'save_state') as save, patch.object(m, 'record_check'):
            m.failure(ctx)
        self.assertEqual(save.call_args.args[1]['phase'], 'needs-human')
        self.assertEqual(save.call_args.args[1]['attempts'], 2)

    def test_prepare_reserves_infrastructure_budget_without_charging_content(self):
        ctx = {'number': 7, 'head': 'b' * 40, 'base': 'a' * 40, 'files': {},
               'state': {'attempts': 2, 'infrastructure_attempts': 1},
               'signature': 'new', 'run_url': 'url', 'extra_feedback': '', 'code_sha': 'a' * 40}
        for force in [False, True]:
            with tempfile.TemporaryDirectory() as directory, \
                    patch.object(m, 'get_pr', return_value=pull()), \
                    patch.object(m, 'context', return_value=copy.deepcopy(ctx)), \
                    patch.object(m, 'restore'), patch.object(m, 'save_state') as save, patch.object(m, 'output'):
                m.prepare(7, Path(directory), True, force)
                prepared = json.loads((Path(directory) / 'context.json').read_text())
            self.assertEqual(prepared['attempts'], 1 if force else 3)
            self.assertEqual(prepared['infrastructure_attempts'], 1 if force else 2)
            state = save.call_args.args[1]
            self.assertEqual(state['attempts'], 0 if force else 2)
            self.assertEqual(state['infrastructure_attempts'], 1 if force else 2)


    def test_closed_pr_dispatch_is_a_noop(self):
        pr = {**pull(), 'state': 'closed'}
        with patch.object(m, 'get_pr', return_value=pr), \
                patch.object(m, 'feedback') as feedback, patch.object(m, 'output') as output:
            m.select(7, False)
        feedback.assert_not_called()
        self.assertEqual(output.call_args.kwargs, {'matrix': {'include': []}, 'count': '0'})

    def test_cache_pins_head_base_and_feedback(self):
        pr = pull()
        original = m.signature(pr, {"threads": []})
        for side in ["base", "head"]:
            changed = copy.deepcopy(pr)
            changed[side]["sha"] = "c" * 40
            self.assertNotEqual(m.signature(changed, {"threads": []}), original)
        self.assertNotEqual(m.signature(pr, {"threads": [{"id": "T", "path": "metrics.md", "comments": [{"body": "fix this"}]}]}), original)

    def test_cache_ignores_thread_positions_but_tracks_feedback(self):
        thread = {'id': 'T', 'path': 'metrics.md', 'line': 12, 'number': 1,
                  'isOutdated': False, 'comments': [{'body': 'Fix this'}]}
        original = {'threads': [thread]}
        moved = {'threads': [{**thread, 'line': None, 'number': 2, 'isOutdated': True}]}
        digest = m.signature(pull(), original)
        self.assertEqual(m.signature(pull(), moved), digest)
        for field, value in [('id', 'new'), ('path', 'other.md'),
                             ('comments', [{'body': 'New feedback'}])]:
            with self.subTest(field=field):
                changed = {'threads': [{**thread, field: value}]}
                self.assertNotEqual(m.signature(pull(), changed), digest)

    def test_finish_resolves_verified_threads_after_hunk_moves(self):
        bot = {'author': {'__typename': 'Bot', 'login': 'claude'}, 'body': 'Fix link'}
        thread = {'id': 'T', 'path': 'metrics.md', 'line': 12, 'number': 1,
                  'isOutdated': False, 'comments': [bot]}
        original = {'threads': [thread], 'failed_checks': []}
        pr = pull()
        ctx = {'number': 7, 'head': 'old', 'base': pr['base']['sha'], 'feedback': original,
               'attempts': 1, 'extra_feedback': '', 'run_url': 'url', 'code_sha': pr['code_sha']}
        for change in ['position', 'reply', 'edited', 'protected', 'missing']:
            with self.subTest(change=change), tempfile.TemporaryDirectory() as directory:
                fresh = {'threads': [{**thread, 'line': None, 'number': 2, 'isOutdated': True}],
                         'failed_checks': []}
                if change == 'reply':
                    fresh['threads'][0]['comments'] = [bot, {**bot, 'body': 'Another concern'}]
                elif change == 'edited':
                    fresh['threads'][0]['comments'] = [{**bot, 'body': 'Updated concern'}]
                elif change == 'protected':
                    fresh['protected_threads'] = ['T']
                elif change == 'missing':
                    fresh['threads'] = []
                root = Path(directory)
                (root / 'review.json').write_text(json.dumps({
                    **{key: True for key in m.docs.REVIEW_GATES}, 'reason': 'Verified',
                    'addressed_threads': [1]}))
                with patch.dict(os.environ, {'BUILD_OK': 'true', 'GITHUB_STEP_SUMMARY': str(root / 'summary')}), \
                        patch.object(m, 'publish_repair', return_value=pr['head']['sha']), \
                        patch.object(m, 'published_pr', return_value=pr), patch.object(m, 'record_check'), \
                        patch.object(m, 'feedback', return_value=(fresh, {}, None)), \
                        patch.object(m, 'api') as api, patch.object(m, 'save_state') as save:
                    m.finish(ctx, root, True)
                expected = ['T'] if change == 'position' else []
                self.assertEqual(json.loads((root / 'result.json').read_text())['resolved_bot_threads'], expected)
                if expected:
                    self.assertEqual(api.call_count, 1)
                    self.assertEqual(api.call_args.args[2]['variables'], {'id': 'T'})
                    self.assertEqual(m.decision(save.call_args.args[1], m.signature(pr, {
                        'threads': [], 'failed_checks': []})), 'cached')
                else:
                    api.assert_not_called()
                    if change in ['reply', 'edited']:
                        self.assertEqual(m.decision(save.call_args.args[1], m.signature(pr, fresh)), 'work')

    def test_state_requires_bot_and_valid_budget(self):
        state = {"attempts": 2, "phase": "working", "head": "x", "base": "y", "run_url": "url"}
        comment = {"id": 99, "user": {"login": "github-actions[bot]"}, "body": m.state_body(state)}
        self.assertEqual(m.decode_state([comment]), (state, 99))
        for invalid in [-1, 4, True, '1']:
            bad = {**comment, 'body': m.state_body({**state, 'infrastructure_attempts': invalid})}
            with self.assertRaisesRegex(ValueError, 'infrastructure'):
                m.decode_state([bad])
        with self.assertRaises(ValueError):
            m.decode_state([comment, comment])
        comment["user"]["login"] = "untrusted"
        self.assertEqual(m.decode_state([comment]), ({}, None))

    def test_bot_skip_notice_is_not_a_repair_request(self):
        comment = {'user': {'login': 'coderabbitai[bot]', 'type': 'Bot'},
                   'body': '<!-- This is an auto-generated comment: summarize by coderabbit.ai -->\n'
                           '<!-- This is an auto-generated comment: skip review by coderabbit.ai -->\nReview skipped'}
        self.assertFalse(m.substantive_comment(comment))
        comment['user']['login'] = 'maintainer'
        comment['author_association'] = 'COLLABORATOR'
        self.assertTrue(m.substantive_comment(comment))
        comment['user']['login'] = 'coderabbitai[bot]'
        comment['body'] = 'Fix this incorrect example'
        self.assertTrue(m.substantive_comment(comment))

    def test_untrusted_feedback_does_not_invalidate_a_successful_review(self):
        outsider = {'login': 'claude', '__typename': 'User'}
        self.assertFalse(m.trusted_feedback(outsider, 'NONE'))
        self.assertTrue(m.trusted_feedback(outsider, 'COLLABORATOR'))
        self.assertTrue(m.trusted_feedback({'login': 'claude', '__typename': 'Bot'}, 'NONE'))
        details = {'threads': [], 'unresolved_threads': [], 'protected_threads': []}
        self.assertEqual(m.signature(pull(), details), m.signature(pull(), {
            **details, 'unresolved_threads': ['external-thread'], 'protected_threads': ['T']}))


    def test_feedback_filters_outsiders_and_protects_mixed_threads(self):
        bot = {'author': {'__typename': 'Bot', 'login': 'claude'}, 'authorAssociation': 'NONE', 'body': 'Fix link'}
        outsider = {'author': {'__typename': 'User', 'login': 'visitor'}, 'authorAssociation': 'NONE', 'body': 'Untrusted text'}
        threads = [{'id': 'T', 'isResolved': False, 'comments': {'pageInfo': {'hasNextPage': False}, 'nodes': [bot, outsider]}},
                   {'id': 'external', 'isResolved': False, 'comments': {'pageInfo': {'hasNextPage': False}, 'nodes': [outsider]}}]
        response = {'data': {'repository': {'pullRequest': {'reviewThreads': {
            'nodes': threads, 'pageInfo': {'hasNextPage': False}}}}}}
        comments = [{'id': 1, 'user': {'login': 'visitor', 'type': 'User'}, 'author_association': 'NONE', 'body': 'Spend budget'}]
        reviews = [{'id': 2, 'user': {'login': 'visitor', 'type': 'User'}, 'author_association': 'NONE',
                    'body': 'External review', 'state': 'CHANGES_REQUESTED', 'commit_id': 'b' * 40}]
        with patch.object(m.docs, 'pages', side_effect=[comments, reviews]), \
                patch.object(m, 'api', return_value=response), patch.object(m, 'check_runs', return_value=[]):
            details, _, _ = m.feedback(pull())
        self.assertEqual(details['comments'], [])
        self.assertEqual(details['reviews'], [])
        self.assertEqual(details['threads'][0]['comments'], [bot])
        self.assertEqual(details['unresolved_threads'], ['T', 'external'])
        self.assertEqual(m.checked_threads({'addressed_threads': [1]}, {'feedback': details}), [])

    def test_manual_apply_and_scheduled_defaults(self):
        for event in ['schedule', 'issue_comment', 'workflow_run', 'workflow_dispatch', 'push']:
            for number in ['', '1072']:
                self.assertFalse(m.apply_mode({'apply': False, 'pr_number': number}, event))
                self.assertTrue(m.apply_mode({'apply': True, 'pr_number': number}, event))
            for raw in ['{}', 'null']:
                self.assertEqual(m.apply_mode(json.loads(raw), event), event == 'schedule')
        with self.assertRaises(ValueError):
            m.apply_mode({'apply': 'false'}, 'schedule')

    def test_bulk_dispatch_rejects_single_pr_options(self):
        with patch.dict(os.environ, {'EXTRA_FEEDBACK': 'Fix one thing'}), self.assertRaisesRegex(ValueError, 'PR number'):
            m.select(0, False)
        with patch.dict(os.environ, {'EXTRA_FEEDBACK': ''}), self.assertRaisesRegex(ValueError, 'PR number'):
            m.select(0, True)

    def test_bad_feedback_does_not_abort_other_prs(self):
        prs = [{**pull(), 'number': 1}, {**pull(), 'number': 2}]
        def feedback(pr):
            if pr['number'] == 1:
                raise ValueError('Feedback requires triage')
            return {}, {}, None
        with patch.object(m.docs, 'pages', return_value=prs), \
                patch.object(m, 'feedback', side_effect=feedback), patch.object(m, 'output') as output:
            m.select(0, False)
        self.assertEqual(output.call_args.kwargs['matrix']['include'], [{'number': 2}])

    def test_secret_content_and_oversized_status_are_handled(self):
        for text in ['sk-ant-' + 'x' * 40, 'ghs_' + 'x' * 40, 'prefix private-test-key suffix']:
            with patch.dict(os.environ, {'ANTHROPIC_API_KEY': 'private-test-key'}), self.assertRaisesRegex(ValueError, 'Credential'):
                m.reject_secrets(text)
        state = {'phase': 'needs-repair', 'attempts': 1, 'head': 'a', 'base': 'b', 'run_url': 'url',
                 'reason': '<&😀' * 100000, 'extra_feedback': '😀' * 100000}
        self.assertLess(len(m.state_body(state).encode()), 65536)

    def test_publication_waits_only_for_our_own_head(self):
        ctx = {'number': 7, 'head': 'b' * 40, 'base': 'a' * 40, 'code_sha': 'a' * 40}
        old, new = pull(), pull()
        new['head']['sha'] = 'c' * 40
        with patch.object(m, 'get_pr', side_effect=[old, old, new]), patch.object(m.time, 'sleep') as sleep:
            self.assertEqual(m.published_pr(ctx, 'c' * 40), new)
            self.assertEqual(sleep.call_count, 2)
        with patch.object(m, 'get_pr', return_value=new), patch.object(m.time, 'sleep') as sleep:
            with self.assertRaises(ValueError):
                m.published_pr(ctx, 'd' * 40)
            sleep.assert_not_called()

    def test_prepare_scope_rejection_is_terminal_without_model_work(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'GITHUB_RUN_ID': '1'}), \
                patch.object(m, 'get_pr', return_value=pull()), \
                patch.object(m, 'context', side_effect=ValueError('Scope violation')), \
                patch.object(m.docs, 'pages', return_value=[]), patch.object(m, 'save_state') as save, \
                patch.object(m, 'record_check') as record, self.assertRaisesRegex(ValueError, 'Scope'):
            m.prepare(7, Path(directory), True, False)
        self.assertEqual(save.call_args.args[1]['phase'], 'needs-human')
        self.assertEqual(save.call_args.args[1]['attempts'], 3)
        self.assertFalse(record.call_args.args[2])

    def test_inaccurate_review_cannot_publish_even_valid_single_concern(self):
        ctx = {'number': 7, 'head': 'b' * 40, 'base': 'a' * 40, 'feedback': {'threads': []},
               'attempts': 1, 'extra_feedback': '', 'run_url': 'url', 'code_sha': 'a' * 40}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.dict(os.environ, {'BUILD_OK': 'true',
                    'GITHUB_STEP_SUMMARY': str(root / 'summary'),
                    'REVIEW_JSON': json.dumps({'placement_appropriate': True, 'related_docs_consistent': True, 'single_concern': True, 'accurate': False, 'reason': 'Wrong behavior'})}), \
                    patch.object(m, 'live_match'), patch.object(m, 'published_pr', return_value=pull()), \
                    patch.object(m, 'publish_repair') as publish, patch.object(m, 'record_check') as record, \
                    patch.object(m, 'save_state') as save:
                m.finish(ctx, root, True)
            publish.assert_not_called()
            self.assertEqual(save.call_args.args[1]['attempts'], 1)
            self.assertEqual(save.call_args.args[1]['infrastructure_attempts'], 0)
            self.assertFalse(record.call_args.args[2])
            self.assertFalse(json.loads((root / 'result.json').read_text())['published'])
            self.assertIn('no repair published', (root / 'summary').read_text())
    def test_human_threads_are_never_resolved(self):
        bot = {"author": {"__typename": "Bot", "login": "claude"}, "body": "fix link"}
        human = {"author": {"__typename": "User", "login": "claude"}, "body": "also clarify"}
        ctx = {"feedback": {"threads": [{"id": "bot", "comments": [bot]},
                                         {"id": "human", "comments": [bot, human]}]}}
        self.assertEqual(m.checked_threads({"addressed_threads": [1, 2]}, ctx), ["bot"])
        for numbers in [[True], [3], [1, 1], "1"]:
            with self.assertRaises(ValueError):
                m.checked_threads({"addressed_threads": numbers}, ctx)

    def test_source_advance_preserves_round_but_invalidates_next_sweep(self):
        pr = pull()
        details = {"threads": [], "failed_checks": []}
        original = copy.deepcopy(pr)
        ctx = {"number": 7, "head": pr["head"]["sha"], "base": pr["base"]["sha"],
               "code_sha": pr["code_sha"], "signature": m.signature(pr, details),
               "extra_feedback": "", "feedback": details, "attempts": 1, "run_url": "url"}
        pr["code_sha"] = "c" * 40
        with patch.object(m, "get_pr", return_value=pr), \
                patch.object(m, "feedback", return_value=(details, {}, None)):
            m.live_match(ctx)
            self.assertEqual(m.published_pr(ctx, ctx['head']), pr)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'review.json').write_text(json.dumps({
                **{key: True for key in m.docs.REVIEW_GATES}, 'reason': 'Verified pinned source',
                'addressed_threads': []}))
            with patch.dict(os.environ, {'BUILD_OK': 'true', 'GITHUB_STEP_SUMMARY': str(root / 'summary')}), \
                    patch.object(m, 'publish_repair', return_value=ctx['head']), \
                    patch.object(m, 'published_pr', return_value=pr), \
                    patch.object(m, 'record_check'), patch.object(m, 'save_state') as save:
                m.finish(ctx, root, True)
            state = save.call_args.args[1]
            self.assertEqual(state['code_sha'], ctx['code_sha'])
            self.assertEqual(m.decision(state, m.signature(original, details)), 'cached')
            self.assertEqual(m.decision(state, m.signature(pr, details)), 'work')
            self.assertEqual(json.loads((root / 'result.json').read_text())['code_sha'], ctx['code_sha'])

    def test_stale_writer_cannot_publish(self):
        pr = pull()
        details = {"threads": []}
        ctx = {"number": 7, "head": pr["head"]["sha"], "base": pr["base"]["sha"],
               "signature": m.signature(pr, details), "extra_feedback": "", "code_sha": pr["code_sha"]}
        with patch.object(m, "get_pr", return_value=pr), patch.object(m, "feedback", return_value=(details, {}, None)):
            self.assertEqual(m.live_match(ctx), pr)
            pr["head"]["sha"] = "c" * 40
            with self.assertRaisesRegex(ValueError, "stale"):
                m.live_match(ctx)


    def test_feedback_arriving_during_publication_is_not_marked_reviewed(self):
        thread = {"id": "T", "number": 1, "comments": [{"author": {"__typename": "Bot", "login": "claude"}}]}
        original = {"threads": [thread], "comments": [], "failed_checks": []}
        fresh = copy.deepcopy(original)
        fresh['threads'][0]['comments'].append({'author': {'__typename': 'User', 'login': 'reviewer'},
                                                'body': 'New concern after publication'})
        pr = pull()
        ctx = {"number": 7, "head": "old", "base": pr['base']['sha'], "feedback": original,
               "attempts": 1, "extra_feedback": "", "run_url": "https://example.test/run", "code_sha": "a" * 40}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.dict(os.environ, {"BUILD_OK": "true",
                    "REVIEW_JSON": json.dumps({"placement_appropriate": True, "related_docs_consistent": True, "accurate": True, "single_concern": True, "reason": "Verified",
                                                "addressed_threads": [1]}),
                    "GITHUB_STEP_SUMMARY": str(root / 'summary')}), \
                    patch.object(m, 'live_match'), patch.object(m, 'publish_repair', return_value=pr['head']['sha']), \
                    patch.object(m, 'record_check'), patch.object(m, 'get_pr', return_value=pr), \
                    patch.object(m, 'feedback', return_value=(fresh, {}, None)), \
                    patch.object(m, 'save_state') as save:
                m.finish(ctx, root, True)
            state = save.call_args.args[1]
            self.assertEqual(m.decision(state, m.signature(pr, fresh)), 'work')
            self.assertEqual(json.loads((root / 'result.json').read_text())['resolved_bot_threads'], [])


class PublicationTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.original = os.getcwd()
        self.addCleanup(os.chdir, self.original)
        self.root = Path(directory.name)
        # SMG source and documentation have independent pinned revisions.
        self.code = self.root / 'smg'
        self.code.mkdir()
        self.code_git('init', '-q')
        self.code_git('config', 'user.name', 'Test')
        self.code_git('config', 'user.email', 'test@users.noreply.github.com')
        self.source = self.code_commit('package example\n')
        env = patch.dict(os.environ, {'SOURCE_ROOT': str(self.code)})
        env.start()
        self.addCleanup(env.stop)
        self.remote = self.root / 'remote.git'
        subprocess.run(['git', 'init', '--bare', '-q', str(self.remote)], check=True)
        working = self.root / 'working'
        working.mkdir()
        os.chdir(working)
        self.git('init', '-q')
        self.git('config', 'user.name', 'Test')
        self.git('config', 'user.email', 'test@users.noreply.github.com')
        self.git('remote', 'add', 'origin', str(self.remote))
        self.path = Path(proposal()['doc_paths'][0])
        self.path.parent.mkdir(parents=True)
        self.path.write_text('Original\n')
        self.git('add', '.')
        self.git('commit', '-qm', 'base')
        self.base = self.git('rev-parse', 'HEAD')
        self.item = m.docs.validate_item(proposal(source_sha=self.source))
        self.git('checkout', '-qb', self.item['branch'])
        self.path.write_text('Unfixed PR\n')
        self.git('commit', '-qam', 'original PR')
        self.head = self.git('rev-parse', 'HEAD')
        self.git('push', '-q', 'origin', 'HEAD')
        self.git('checkout', '--detach', self.base)
        Path('source.go').write_text('package newer\n')
        self.git('add', 'source.go')
        self.git('commit', '-qm', 'advance main')
        self.base = self.git('rev-parse', 'HEAD')
        self.ctx = {'head': self.head, 'base': self.base, 'item': self.item, 'number': 7, 'code_sha': self.source}

    def git(self, *args):
        return subprocess.check_output(['git', *args], text=True, stderr=subprocess.DEVNULL).strip()

    def code_git(self, *args):
        return subprocess.check_output(['git', '-C', str(self.code), *args], text=True,
                                       stderr=subprocess.DEVNULL).strip()

    def code_commit(self, content):
        (self.code / 'source.go').write_text(content)
        self.code_git('add', '.')
        self.code_git('commit', '-qm', 'code change')
        return self.code_git('rev-parse', 'HEAD')

    def pull(self, item, head):
        pr = pull()
        pr['body'] = f"{m.docs.MARKER}{item['key']} -->"
        pr['head'].update(sha=head, ref=item['branch'])
        pr['base']['sha'] = self.base
        pr['code_sha'] = self.source
        return pr


    def test_original_citations_can_be_loaded_but_not_republished(self):
        ctx = {**self.ctx, 'files': {str(self.path): 'See smg-project/smg#2924.\n'}}
        self.assertTrue(m.restore(ctx))
        bundle = json.dumps({'base_sha': self.base, 'key': self.item['key'], 'files': ctx['files']})
        with self.assertRaisesRegex(ValueError, 'code-change references'):
            m.restore(ctx, bundle)
        clean = json.dumps({'base_sha': self.base, 'key': self.item['key'],
                            'files': {str(self.path): 'Set the cache byte budget.\n'}})
        self.assertTrue(m.restore(ctx, clean))

    def test_publication_preserves_history_and_exact_reviewed_tree(self):
        self.path.write_text('Fixed PR\n\nSecond paragraph.\n')
        m.docs.validate_diff(self.item, self.base)
        tree = self.git('write-tree')
        with patch.object(m, 'live_match'):
            result = m.publish_repair(self.ctx)
        self.assertEqual(self.git('rev-parse', result + '^{tree}'), tree)
        self.git('merge-base', '--is-ancestor', self.head, result)
        self.git('merge-base', '--is-ancestor', self.base, result)
        self.assertIn('Signed-off-by:', self.git('show', '-s', '--format=%B'))
        self.assertEqual(self.path.read_text(), 'Fixed PR\n\nSecond paragraph.\n')

    def test_competing_commit_rejects_push(self):
        self.git('checkout', '--detach', self.head)
        self.path.write_text('Human repair\n')
        self.git('commit', '-qam', 'human repair')
        self.git('push', '-q', 'origin', 'HEAD:refs/heads/' + self.item['branch'])
        self.git('checkout', '--detach', self.base)
        self.path.write_text('Stale repair\n')
        m.docs.validate_diff(self.item, self.base)
        with patch.object(m, 'live_match'), self.assertRaises(subprocess.CalledProcessError):
            m.publish_repair(self.ctx)

    def test_incoming_code_and_executable_docs_are_rejected(self):
        for path, mode in [('code.py', 0o644), (str(self.path), 0o755)]:
            self.git('checkout', '--detach', self.head)
            Path(path).write_text('Untrusted content\n')
            Path(path).chmod(mode)
            self.git('add', path)
            self.git('commit', '-qm', 'invalid PR edit')
            pr = pull()
            pr['body'] = f"{m.docs.MARKER}{self.item['key']} -->"
            pr['head'].update(sha=self.git('rev-parse', 'HEAD'), ref=self.item['branch'])
            pr['base']['sha'] = self.base
            pr['code_sha'] = self.source
            with patch.dict(os.environ, {'GITHUB_REPOSITORY': 'smg-project/smg-docs'}), \
                    patch.object(m.docs, 'mutate_git'), self.assertRaises(ValueError):
                m.context(pr)


    def test_context_and_restore_pin_the_source_separately(self):
        pr = self.pull(self.item, self.head)
        with patch.dict(os.environ, {'GITHUB_REPOSITORY': 'smg-project/smg-docs',
                'GITHUB_SHA': 'c' * 40, 'GITHUB_RUN_ID': '1'}), \
                patch.object(m.docs, 'mutate_git'), \
                patch.object(m, 'feedback', return_value=({'threads': []}, {}, None)):
            ctx = m.context(pr)
        self.assertEqual(ctx['code_sha'], self.source)
        self.assertIn('+' + 'package example', ctx['source_patch'])
        self.assertEqual(ctx['item']['branch'], self.item['branch'])
        self.assertEqual(ctx['item']['placement']['canonical_pages'], [str(self.path)])
        self.assertTrue(m.restore(ctx))
        self.assertEqual(self.path.read_text(), 'Unfixed PR\n')
        self.assertEqual(self.code_git('rev-parse', 'HEAD'), self.source)

    def test_entire_pr_size_guard_applies_before_repair(self):
        self.git('checkout', '--detach', self.head)
        self.path.write_text('line\n' * 1000)
        self.git('commit', '-qam', 'oversized original')
        pr = self.pull(self.item, self.git('rev-parse', 'HEAD'))
        with patch.dict(os.environ, {'GITHUB_REPOSITORY': 'smg-project/smg-docs',
                'GITHUB_SHA': 'c' * 40, 'GITHUB_RUN_ID': '1'}), \
                patch.object(m.docs, 'mutate_git'), \
                patch.object(m, 'feedback', return_value=({'threads': []}, {}, None)):
            ctx = m.context(pr)
        with self.assertRaisesRegex(ValueError, 'under 1000'):
            m.restore(ctx)

    def test_new_source_invalidates_cached_success(self):
        pr = self.pull(self.item, self.head)
        digest = m.signature(pr, {})
        pr['code_sha'] = 'f' * 40
        self.assertNotEqual(m.signature(pr, {}), digest)

    def test_bot_commit_identity(self):
        self.path.write_text('Repaired\n')
        m.docs.validate_diff(self.item, self.base)
        with patch.object(m, 'live_match'):
            m.publish_repair(self.ctx)
        identity = 'github-actions[bot] <41898282+github-actions[bot]@users.noreply.github.com>'
        self.assertEqual(self.git('show', '-s', '--format=%an <%ae>'), identity)
        self.assertEqual(self.git('show', '-s', '--format=%cn <%ce>'), identity)
        self.assertIn('Signed-off-by: ' + identity, self.git('show', '-s', '--format=%B'))

    def test_edited_pr_description_invalidates_cached_success(self):
        pr = self.pull(self.item, self.head)
        digest = m.signature(pr, {})
        pr['body'] += '\nChanged concern evidence'
        self.assertNotEqual(m.signature(pr, {}), digest)
