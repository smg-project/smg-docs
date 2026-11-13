import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from git_test_support import isolate_git


class GitIsolationTests(unittest.TestCase):
    def test_git_children_do_not_launch_maintenance_and_settings_are_restored(self):
        inherited = {'GIT_CONFIG_COUNT': '1', 'GIT_CONFIG_KEY_0': 'user.name',
                     'GIT_CONFIG_VALUE_0': 'Inherited test identity'}
        with patch.dict(os.environ, inherited), tempfile.TemporaryDirectory() as directory:
            before = dict(os.environ)
            scoped = unittest.TestCase()
            try:
                isolate_git(scoped)
                trace = Path(directory) / 'trace.json'
                env = {**os.environ, 'GIT_TRACE2_EVENT': str(trace)}
                repo = Path(directory) / 'repo'

                def git(*args):
                    return subprocess.check_output(['git', '-C', str(repo), *args],
                                                   env=env, text=True, stderr=subprocess.PIPE).strip()

                repo.mkdir()
                git('init', '-q')
                self.assertEqual(git('config', 'user.name'), 'Inherited test identity')
                for key, expected in [('gc.auto', '0'), ('maintenance.auto', 'false'),
                                      ('gc.autoDetach', 'false'), ('maintenance.autoDetach', 'false'),
                                      ('core.fsmonitor', 'false')]:
                    self.assertEqual(git('config', key), expected)
                git('-c', 'user.email=test@example.invalid', 'commit', '--allow-empty', '-qm', 'fixture')
                events = [json.loads(line) for line in trace.read_text().splitlines()]
                children = [e.get('argv', []) for e in events if e['event'] == 'child_start']
                self.assertFalse(any('maintenance' in argv or 'gc' in argv for argv in children), children)
            finally:
                scoped.doCleanups()
            self.assertEqual(dict(os.environ), before)
