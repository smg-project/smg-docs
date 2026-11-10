from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ('nightly-doc-sync.yml', 'docs-release-snapshot.yml')
COPY = 'cp -R .github/actions/download-artifact-retry .nightly-action/download'


def jobs(text):
    """Split a workflow into (job name, job text) by its two-space job keys."""
    body = text.split('\njobs:\n', 1)[1]
    parts = re.split(r'^  ([a-z][a-z0-9-]*):\s*$', body, flags=re.M)
    return list(zip(parts[1::2], parts[2::2]))


class WorkflowDownloadTests(unittest.TestCase):
    # Run 42793291666 lost two release review-repair jobs to a dropped
    # connection that download-artifact does not retry.
    def test_audit_and_nightly_downloads_retry(self):
        for name in WORKFLOWS:
            text = (ROOT / '.github/workflows' / name).read_text()
            with self.subTest(workflow=name):
                self.assertNotIn('actions/download-artifact@', text)
                for job, steps in jobs(text):
                    if './.nightly-action/download' in steps:
                        # The setup checks out the docs base, so the action must
                        # be copied from the workflow commit before that switch.
                        self.assertIn(COPY, steps.split('./.nightly-action/download', 1)[0], job)

    def test_retry_action_makes_three_attempts(self):
        action = (ROOT / '.github/actions/download-artifact-retry/action.yml').read_text()
        self.assertEqual(action.count('uses: actions/download-artifact@'), 3)
        self.assertEqual(action.count('continue-on-error: true'), 2)


if __name__ == '__main__':
    unittest.main()
