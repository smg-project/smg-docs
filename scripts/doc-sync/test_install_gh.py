"""Exercise release-asset retries and ensure failed downloads install nothing."""

import io
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class InstallGhTests(unittest.TestCase):
    def test_transient_download_is_retried_before_installing(self):
        self.install(failures=1)

    def test_failed_download_does_not_install_partial_binary(self):
        self.install(failures=100)

    def install(self, failures):
        archive = io.BytesIO()
        with tarfile.open(fileobj=archive, mode='w:gz') as bundle:
            content = b'#!/bin/sh\necho "gh version fixture"\n'
            info = tarfile.TarInfo('gh_2.74.0_linux_amd64/bin/gh')
            info.mode, info.size = 0o755, len(content)
            bundle.addfile(info, io.BytesIO(content))
        requests = []

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                requests.append(self.path)
                self.send_response(504 if len(requests) <= failures else 200)
                self.end_headers()
                self.wfile.write(b'temporary failure' if len(requests) <= failures else archive.getvalue())

            def log_message(self, *_args):
                pass

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                bindir = root / 'bin'
                bindir.mkdir()
                for name in ('bash', 'mktemp', 'rm', 'tar', 'gzip', 'install', 'mkdir'):
                    (bindir / name).symlink_to(shutil.which(name))
                (bindir / 'uname').write_text('#!/bin/sh\necho x86_64\n')
                # Route the real curl retry logic to a local server, shortening
                # only the delay so exhausted retries do not slow the suite.
                curl = shutil.which('curl')
                (bindir / 'curl').write_text(f'''#!/bin/bash
args=()
while [ "$#" -gt 0 ]; do
  case "$1" in
    --retry-delay) shift; args+=(--retry-delay 0);;
    https://github.com/*) args+=("http://127.0.0.1:{server.server_port}/gh.tar.gz");;
    *) args+=("$1");;
  esac
  shift
done
exec {curl} "${{args[@]}}" --retry-max-time 3
''')
                for name in ('uname', 'curl'):
                    (bindir / name).chmod(0o755)
                env = {**os.environ, 'PATH': str(bindir), 'HOME': str(root / 'home'),
                       'GITHUB_PATH': str(root / 'github-path'), 'TMPDIR': str(root)}
                result = subprocess.run(['/bin/bash', str(Path(__file__).with_name('install-gh.sh'))],
                                        env=env, capture_output=True, text=True)
                installed = root / 'home/.local/bin/gh'
                if failures == 1:
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(len(requests), 2)
                    self.assertTrue(installed.exists())
                    self.assertEqual(Path(env['GITHUB_PATH']).read_text().strip(), str(installed.parent))
                else:
                    self.assertNotEqual(result.returncode, 0)
                    self.assertGreater(len(requests), 1)
                    self.assertFalse(installed.exists())
                    self.assertFalse(Path(env['GITHUB_PATH']).exists())
                self.assertEqual(list(root.glob('tmp.*')), [])
        finally:
            server.shutdown()
            thread.join()
            server.server_close()


if __name__ == '__main__':
    unittest.main()
