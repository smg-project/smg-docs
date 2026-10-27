"""Resolve published stable SMG releases and prove source-change inclusion."""

from functools import lru_cache
from datetime import datetime, timezone
import json
import os
import re
import subprocess
from urllib.parse import quote

REPO = 'smg-project/smg'


def api(endpoint):
    return json.loads(subprocess.check_output(['gh', 'api', endpoint], text=True))


def resolve(release):
    if (release.get('draft') is not False or release.get('prerelease') is not False
            or not release.get('published_at') or not release.get('tag_name')
            or type(release.get('id')) is not int):
        raise ValueError('A published stable SMG release is required')
    # target_commitish may say main even for an old release; resolve the tag itself.
    sha = api(f'repos/{REPO}/commits/{quote(release["tag_name"], safe="")}')['sha']
    if not re.fullmatch(r'[0-9a-f]{40}', sha):
        raise ValueError('Release tag did not resolve to an immutable commit')
    return {'id': release['id'], 'tag': release['tag_name'], 'sha': sha,
            'published_at': release['published_at'], 'url': release['html_url']}


@lru_cache(maxsize=1)
def latest_stable():
    # GitHub's latest release excludes draft/prerelease releases. Never fall back to main.
    return resolve(api(f'repos/{REPO}/releases/latest'))


def verify(release):
    current = resolve(api(f'repos/{REPO}/releases/{release["id"]}'))
    if current != release:
        raise ValueError('Pinned stable release changed; start a fresh run')


def contains(source, release_sha):
    if not all(re.fullmatch(r'[0-9a-f]{40}', sha) for sha in (source, release_sha)):
        raise ValueError('Release comparison requires immutable commits')
    result = api(f'repos/{REPO}/compare/{source}...{release_sha}')
    return result['status'] in {'ahead', 'identical'}


@lru_cache(maxsize=1)
def main_source():
    sha = api(f'repos/{REPO}/commits/main')['sha']
    if not re.fullmatch(r'[0-9a-f]{40}', sha):
        raise ValueError('SMG main did not resolve to an immutable commit')
    return {'version': 'main', 'sha': sha}


if __name__ == '__main__':
    import sys
    if '--main' in sys.argv:
        captured_at = datetime.now(timezone.utc).isoformat()
        target = main_source()
        with open(os.environ['GITHUB_OUTPUT'], 'a') as out:
            out.write(f'sha={target["sha"]}\n')
        with open(os.environ['GITHUB_ENV'], 'a') as out:
            out.write('SMG_SOURCE_JSON=' + json.dumps(target) + '\n')
            out.write('SMG_SOURCE_CAPTURED_AT=' + captured_at + '\n')
        print(f'Pinned SMG main: {target["sha"]}')
        sys.exit(0)
    release = latest_stable()
    with open(os.environ['GITHUB_OUTPUT'], 'a') as out:
        out.write(f'sha={release["sha"]}\n')
    with open(os.environ['GITHUB_ENV'], 'a') as out:
        out.write('SMG_RELEASE_JSON=' + json.dumps(release) + '\n')
    print(f'Pinned published stable release {release["tag"]}: {release["sha"]}')
