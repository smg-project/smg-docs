"""Keep temporary test repositories free of detached Git writers."""

import os
from unittest.mock import patch


def isolate_git(test_case):
    # Apply to every Git child, including production helpers and bare remotes.
    # Fixture-local config alone misses those subprocesses. Preserve inherited
    # command-scope settings and restore the environment after each test.
    settings = {'gc.auto': '0', 'maintenance.auto': 'false',
                'gc.autoDetach': 'false', 'maintenance.autoDetach': 'false',
                'core.fsmonitor': 'false'}
    start = int(os.environ.get('GIT_CONFIG_COUNT', '0'))
    env = {'GIT_CONFIG_COUNT': str(start + len(settings))}
    for index, (key, value) in enumerate(settings.items(), start):
        env[f'GIT_CONFIG_KEY_{index}'] = key
        env[f'GIT_CONFIG_VALUE_{index}'] = value
    scoped = patch.dict(os.environ, env)
    scoped.start()
    test_case.addCleanup(scoped.stop)
