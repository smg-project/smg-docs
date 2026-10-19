"""Audit one published release snapshot without trusting model-written tooling."""

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tempfile

import nightly_docs as docs
import releases

REGISTRY = 'src/lib/config/docs-releases.json'
MARKER = '<!-- docs-release-sync:'
SECTIONS = {'getting-started', 'concepts', 'reference', 'contributing'}
BATCH_SIZE = 2
MAX_BATCH_BYTES = 48 * 1024
REVIEW_GATES = ('accurate', 'complete', 'reader_focused', 'related_docs_consistent')
# Releases already documented before version-aware automation was introduced.
INITIAL_RELEASE = 'v1.12.0'


def version_key(tag):
    if not isinstance(tag, str) or not re.fullmatch(r'v\d+\.\d+\.\d+', tag):
        raise ValueError('Expected a stable vX.Y.Z version')
    return tuple(map(int, tag[1:].split('.')))


def root(tag):
    version_key(tag)
    return f'src/lib/versions/{tag}/'


def page_path(path):
    p = PurePosixPath(path)
    return (isinstance(path, str) and str(p) == path and not p.is_absolute()
            and '..' not in p.parts and bool(p.parts) and p.parts[0] in SECTIONS and p.suffix == '.md')


def branch(release):
    version_key(release['tag'])
    if not re.fullmatch(r'[0-9a-f]{40}', release['sha']):
        raise ValueError('Expected release commit SHA')
    return f'docs/release-{release["tag"]}-{release["sha"][:12]}'


def release_marker(release):
    return f'{MARKER}{release["tag"]}:{release["sha"]} -->'


def pending_release(release, registry, sync, prs):
    """A prepared current/future folder is not evidence that its release was audited."""
    key = version_key(release['tag'])
    if key <= version_key(INITIAL_RELEASE) and not sync:
        return False  # Historical baseline, not a claim of an automated audit.
    if sync.get('source_release') == release:
        return False
    return not any(pr.get('state') == 'open' and
                   release_marker(release) in (pr.get('body') or '') for pr in prs)


def read_json(path):
    return json.loads(Path(path).read_text())


def write_json(path, value):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(value, indent=2) + '\n')


def outputs(**values):
    with open(os.environ['GITHUB_OUTPUT'], 'a') as out:
        for key, value in values.items():
            out.write(f'{key}={value if isinstance(value, str) else json.dumps(value)}\n')


def detect():
    release = releases.latest_stable()
    registry = read_json(REGISTRY)
    state_path = Path(root(release['tag'])) / 'sync.json'
    state = read_json(state_path) if state_path.exists() else {}
    prs = docs.pages(f'repos/{os.environ["GITHUB_REPOSITORY"]}/pulls?state=open&per_page=100')
    needed = pending_release(release, registry, state, prs)
    outputs(needed=str(needed).lower(), release=release)
    print(f'{release["tag"]}: {"release reconciliation needed" if needed else "continue main documentation"}')


def tracked_bytes(base, prefix):
    result = {}
    for entry in docs.git('ls-tree', '-r', '-z', base, '--', prefix).split('\0'):
        if not entry:
            continue
        metadata, path = entry.split('\t', 1)
        if not metadata.startswith('100644 blob '):
            raise ValueError('Snapshot inputs must be regular non-executable files')
        result[path] = subprocess.check_output(['git', 'show', f'{base}:{path}'])
    return result


def make_seed(base, release, manifest):
    """Preserve authored snapshot corrections; copy only missing main pages/assets."""
    prefix = root(release['tag'])
    images = f'static/versions/{release["tag"]}/images/'
    seed = tracked_bytes(base, prefix)
    seed.update(tracked_bytes(base, images))
    old_manifest = json.loads(seed[prefix + 'manifest.json']) if prefix + 'manifest.json' in seed else None
    # Freeze existing routes/nav, adding routes newly present in main. Page-level
    # auditors can remove unsupported pages; navigation is filtered at assembly.
    final_manifest = json.loads(json.dumps(old_manifest or manifest))
    for route, file in manifest['pages'].items():
        final_manifest['pages'].setdefault(route, file)
    for file in final_manifest['pages'].values():
        if not page_path(file):
            raise ValueError('Invalid manifest documentation path')
        path = prefix + 'content/' + file
        if path not in seed:
            seed[path] = subprocess.check_output(['git', 'show', f'{base}:{docs.DOC_ROOT}{file}'])
    # Include new navigation entries from main without replacing existing labels.
    if old_manifest:
        for section, nav in manifest['navigation'].items():
            current = final_manifest['navigation'][section]
            links = {item['href'] for group in current['sections'] for item in group['items']}
            for group in nav['sections']:
                added = [item for item in group['items'] if item['href'] not in links]
                if added:
                    current['sections'].append({**group, 'items': added})
                    links.update(item['href'] for item in added)
    seed[prefix + 'manifest.json'] = (json.dumps(final_manifest, indent='\t') + '\n').encode()
    for path, content in tracked_bytes(base, 'static/images/').items():
        seed.setdefault(images + path.removeprefix('static/images/'), content)
    registry = json.loads(docs.git('show', f'{base}:{REGISTRY}'))
    registry = sorted(set(registry + [release['tag']]), key=version_key, reverse=True)
    seed[REGISTRY] = (json.dumps(registry) + '\n').encode()
    return seed


def safe_write(path, content):
    p = Path(path)
    if p.is_absolute() or '..' in p.parts or any(q.is_symlink() for q in (p, *p.parents)):
        raise ValueError('Unsafe snapshot output path')
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(content)
    p.chmod(0o644)


def seed_context(ctx):
    releases.verify(ctx['release'])
    if docs.git('rev-parse', 'HEAD') != ctx['base']:
        raise ValueError('Snapshot documentation base changed')
    if docs.source_git('rev-parse', 'HEAD') != ctx['release']['sha']:
        raise ValueError('Snapshot source must be the pinned published tag')
    # Rebuild from trusted Git blobs in every job, never import writer configs.
    seed = make_seed(ctx['base'], ctx['release'], ctx['main_manifest'])
    digest = hashlib.sha256(b''.join(p.encode() + b'\0' + v for p, v in sorted(seed.items()))).hexdigest()
    if digest != ctx['seed_digest']:
        raise ValueError('Snapshot seed changed')
    for path, content in seed.items():
        safe_write(path, content)
    return seed


def audit_batches(pages):
    """Bound both page count and reading size; audit large pages on their own."""
    batches, current, size = [], [], 0
    for path, variants in sorted(pages.items()):
        # Both snapshot and main must be checked. A short stale snapshot must
        # not hide the cost of reviewing its much larger current counterpart.
        weight = max(map(len, variants))
        if current and (len(current) >= BATCH_SIZE or size + weight > MAX_BATCH_BYTES):
            batches.append(current)
            current, size = [], 0
        current.append(path)
        size += weight
    if current:
        batches.append(current)
    return batches


def audit_matrix(pages, batches):
    """Keep ordinary audits bounded; give isolated oversized pages headroom."""
    return {'include': [
        {'batch': index, 'max_turns': 300 if sum(max(map(len, pages[path]))
         for path in batch) > MAX_BATCH_BYTES else 180}
        for index, batch in enumerate(batches)
    ]}


def prepare(directory):
    release = json.loads(os.environ['RELEASE_JSON'])
    releases.verify(release)
    base = docs.git('rev-parse', 'HEAD')
    manifest_file = directory / 'main-manifest.json'
    subprocess.run(['node', str(Path(__file__).with_name('export-manifest.mjs')), str(manifest_file)], check=True)
    manifest = read_json(manifest_file)
    seed = make_seed(base, release, manifest)
    prefix = root(release['tag']) + 'content/'
    paths = sorted(path for path in seed if path.startswith(prefix) and path.endswith('.md'))
    main_pages = tracked_bytes(base, docs.DOC_ROOT)
    pages = {path: (seed[path], main_pages.get(
        docs.DOC_ROOT + path.removeprefix(prefix), b'')) for path in paths}
    batches = audit_batches(pages)
    if not batches or len(batches) > 256:
        raise ValueError('Invalid release audit coverage')
    matrix = audit_matrix(pages, batches)
    ctx = {'base': base, 'release': release, 'main_manifest': manifest, 'batches': batches,
           'matrix': matrix,
           'seed_digest': hashlib.sha256(b''.join(p.encode() + b'\0' + v for p, v in sorted(seed.items()))).hexdigest()}
    write_json(directory / 'context.json', ctx)
    # Files remain on disk for focused reads; never truncate source evidence.
    history = docs.source_git('log', '--first-parent', '--since=' + docs.INITIAL_SINCE,
                              '--format=%H %cs %s', release['sha'])
    (directory / 'release-history.txt').write_text(history + '\n')
    main_sha = releases.main_source()['sha']
    docs.source_git('fetch', '--no-tags', 'origin', main_sha)
    excluded = docs.source_git('log', '--format=%H %cs %s', f'{release["sha"]}..{main_sha}')
    (directory / 'excluded-main-history.txt').write_text(excluded + '\n')
    outputs(matrix=matrix, base=base)


def batch_paths(ctx, index):
    if type(index) is not int or not 0 <= index < len(ctx['batches']):
        raise ValueError('Invalid release audit batch')
    return ctx['batches'][index]


def validate_bundle(ctx, index, value):
    paths = batch_paths(ctx, index)
    if value.get('base') != ctx['base'] or value.get('release') != ctx['release'] or value.get('batch') != index:
        raise ValueError('Snapshot bundle does not match its pinned context')
    files = value.get('files')
    if not isinstance(files, dict) or set(files) != set(paths):
        raise ValueError('Every assigned page must be returned exactly once')
    for path, content in files.items():
        if content is not None and (not isinstance(content, str) or '\0' in content or len(content.encode()) > 2 * 1024 * 1024):
            raise ValueError('Invalid snapshot Markdown')
        if content is None and path.endswith('/index.md'):
            raise ValueError('Keep section overview pages')
    return files


def export(ctx, index, seed, output):
    paths = batch_paths(ctx, index)
    changed = set(docs.git('diff', '--name-only', 'HEAD').splitlines())
    changed.update(docs.git('ls-files', '--others', '--exclude-standard').splitlines())
    for path in changed - set(paths):
        p = Path(path)
        expected = seed.get(path)
        unchanged = (not p.exists() and not p.is_symlink()) if expected is None else (
            p.is_file() and not p.is_symlink() and p.read_bytes() == expected)
        if path not in seed or not unchanged:
            raise ValueError('Release writer changed a file outside its page allowlist')
    if docs.git('rev-parse', 'HEAD') != ctx['base']:
        raise ValueError('Release writer must not commit')
    files = {}
    for path in paths:
        p = Path(path)
        if any(q.is_symlink() for q in (p, *p.parents)) or (p.exists() and p.stat().st_mode & 0o111):
            raise ValueError('Unsafe snapshot Markdown file')
        files[path] = p.read_text() if p.exists() else None
    value = {'base': ctx['base'], 'release': ctx['release'], 'batch': index, 'files': files}
    validate_bundle(ctx, index, value)
    write_json(output, value)


def bundle_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def snapshot_hash(values):
    return hashlib.sha256(''.join(bundle_hash(value) for value in values).encode()).hexdigest()


def proposal_hash(ctx, directory):
    values = []
    for index in range(len(ctx['batches'])):
        value = read_json(directory / f'bundle-{index}.json')
        validate_bundle(ctx, index, value)
        values.append(value)
    return snapshot_hash(values)


def assembled_data(ctx, directory):
    """Derive the entire proposal from trusted seed data and scoped Markdown bundles."""
    data = make_seed(ctx['base'], ctx['release'], ctx['main_manifest'])
    for index in range(len(ctx['batches'])):
        value = read_json(directory / f'bundle-{index}.json')
        for path, content in validate_bundle(ctx, index, value).items():
            data[path] = None if content is None else content.encode()
    prefix = root(ctx['release']['tag'])
    manifest = json.loads(data[prefix + 'manifest.json'])
    manifest['pages'] = {route: file for route, file in manifest['pages'].items()
                         if data.get(prefix + 'content/' + file) is not None}
    for nav in manifest['navigation'].values():
        for group in nav['sections']:
            group['items'] = [item for item in group['items'] if item['href'] in manifest['pages']]
        nav['sections'] = [group for group in nav['sections'] if group['items']]
    data[prefix + 'manifest.json'] = (json.dumps(manifest, indent='\t') + '\n').encode()
    return data


def assemble(ctx, directory):
    for path, content in assembled_data(ctx, directory).items():
        if content is None:
            Path(path).unlink(missing_ok=True)
        else:
            safe_write(path, content)


def validate_review(ctx, index, raw):
    paths = batch_paths(ctx, index)
    reviewed = raw.get('reviewed_pages')
    if not isinstance(reviewed, list) or len(reviewed) != len(paths) or set(reviewed) != set(paths):
        raise ValueError('Independent review did not cover every assigned snapshot page')
    if any(type(raw.get(gate)) is not bool for gate in REVIEW_GATES):
        raise ValueError('Invalid snapshot review')
    if not isinstance(raw.get('reason'), str) or not raw['reason'].strip():
        raise ValueError('Missing snapshot review evidence')
    return all(raw[gate] for gate in REVIEW_GATES)


def review(ctx, index, directory, require_accepted=True):
    raw = json.loads(os.environ['REVIEW_JSON'])
    accepted = validate_review(ctx, index, raw)
    value = read_json(directory / f'bundle-{index}.json')
    validate_bundle(ctx, index, value)
    write_json(directory / f'review-{index}.json', {'batch': index, 'bundle_sha': bundle_hash(value),
               'snapshot_sha': proposal_hash(ctx, directory), 'verdict': raw})
    if not accepted and require_accepted:
        raise ValueError('Release snapshot review rejected this batch: ' + raw['reason'])


def rejected_batches(ctx, directory):
    """Missing/malformed/stale evidence is fatal, not a repairable content finding."""
    rejected = []
    snapshot_sha = proposal_hash(ctx, directory)
    for index in range(len(ctx['batches'])):
        value = read_json(directory / f'bundle-{index}.json')
        validate_bundle(ctx, index, value)
        result = read_json(directory / f'review-{index}.json')
        if (result.get('batch') != index or result.get('bundle_sha') != bundle_hash(value)
                or result.get('snapshot_sha') != snapshot_sha):
            raise ValueError('Missing or stale snapshot review')
        if not validate_review(ctx, index, result['verdict']):
            rejected.append(index)
    return rejected


def plan_repair(ctx, directory):
    rejected = rejected_batches(ctx, directory)
    matrix = {'include': [item for item in ctx['matrix']['include'] if item['batch'] in rejected]}
    if {item['batch'] for item in matrix['include']} != set(rejected):
        raise ValueError('Repair plan did not cover every rejected batch')
    outputs(needed=str(bool(rejected)).lower(), matrix=matrix)
    print(f'{len(rejected)} of {len(ctx["batches"])} batches need a bounded content repair')


def finalize(ctx, directory):
    """Require all reviewed data; models cannot forge release metadata or scripts."""
    if rejected_batches(ctx, directory):
        raise ValueError('Missing or rejected full snapshot review')
    assemble(ctx, directory)
    safe_write(root(ctx['release']['tag']) + 'sync.json', (json.dumps({
        'source_release': ctx['release'], 'docs_base': ctx['base'],
        'audited_pages': sum(map(len, ctx['batches']))}, indent='\t') + '\n').encode())
    allowed = {REGISTRY}
    allowed.update(p for p in docs.git('ls-files').splitlines()
                   if p.startswith((root(ctx['release']['tag']), f'static/versions/{ctx["release"]["tag"]}/')))
    # Seed generation and bundle validation already constrain new paths.
    allowed.update(make_seed(ctx['base'], ctx['release'], ctx['main_manifest']))
    allowed.add(root(ctx['release']['tag']) + 'sync.json')
    changed = set(docs.git('diff', '--name-only', 'HEAD').splitlines())
    changed.update(docs.git('ls-files', '--others', '--exclude-standard').splitlines())
    if not changed <= allowed:
        raise ValueError('Snapshot changes outside the selected version')
    docs.mutate_git('add', '--', *sorted(changed))
    docs.mutate_git('diff', '--cached', '--check')


def refresh_base(ctx):
    """Keep reviewed release data while accepting unrelated working-page edits."""
    repo = os.environ['GITHUB_REPOSITORY']
    live = releases.api(f'repos/{repo}/commits/main')['sha']
    if not re.fullmatch(r'[0-9a-f]{40}', live):
        raise ValueError('Invalid documentation base')
    if docs.git('rev-parse', 'HEAD') != ctx['base']:
        raise ValueError('Release audit base changed before refresh')
    if live != ctx['base']:
        docs.mutate_git('fetch', '--no-tags', 'origin', live)
        docs.git('merge-base', '--is-ancestor', ctx['base'], live)
        changes = docs.git('diff', '--name-status', ctx['base'], live).splitlines()
        for line in changes:
            status, path = line.split('\t', 1)
            if status != 'M' or not path.startswith(docs.DOC_ROOT) or not path.endswith('.md'):
                raise ValueError('Release audit inputs changed; rerun on a fresh base')
        seed = make_seed(live, ctx['release'], ctx['main_manifest'])
        digest = hashlib.sha256(b''.join(p.encode() + b'\0' + v for p, v in sorted(seed.items()))).hexdigest()
        if digest != ctx['seed_digest']:
            raise ValueError('Release snapshot seed changed; rerun on a fresh base')
        patch = subprocess.check_output(['git', '-c', 'core.hooksPath=/dev/null',
                                         'diff', '--cached', '--binary', ctx['base']])
        docs.mutate_git('reset', '--hard', live)
        if patch:
            subprocess.run(['git', '-c', 'core.hooksPath=/dev/null', 'apply', '--index'],
                           input=patch, check=True)
        print('Refreshed working-document changes; reviewed release data is unchanged')
    outputs(base=live)


def publish(ctx):
    """Publish a reviewed snapshot; reuse idle branches with a compare-and-swap push."""
    releases.verify(ctx['release'])
    repo = os.environ['GITHUB_REPOSITORY']
    live = releases.api(f'repos/{repo}/commits/main')['sha']
    base = os.environ.get('DOCS_PUBLISH_BASE', ctx['base'])
    if live != base or docs.git('rev-parse', 'HEAD') != base:
        raise ValueError('Docs main changed during the release audit; rerun on a fresh base')
    name = branch(ctx['release'])
    ref = f'refs/heads/{name}'
    remote = docs.git('ls-remote', '--heads', 'origin', ref)
    expected = remote.split()[0] if remote else ''
    prs = docs.existing_prs(repo)
    if docs.daily_remaining(prs) == 0 or any(
            pr['state'] == 'open' and (release_marker(ctx['release']) in pr['body']
                                      or pr.get('branch') == name) for pr in prs):
        print('Release PR already open or daily quota reached; no duplicate published')
        return
    if os.environ.get('DRY_RUN') == 'true':
        print('Dry run: complete release snapshot validated; no PR published')
        return
    docs.mutate_git('switch', '-c', name)
    docs.git('config', 'user.name', 'github-actions[bot]')
    docs.git('config', 'user.email', '41898282+github-actions[bot]@users.noreply.github.com')
    docs.mutate_git('commit', '-s', '-m', f'docs: reconcile {ctx["release"]["tag"]} snapshot')
    # Also require an absent branch to remain absent when this is a first push.
    docs.mutate_git('push', f'--force-with-lease={ref}:{expected}', 'origin', f'HEAD:{ref}')
    body = (release_marker(ctx['release']) + '\n\nReconcile documentation with the published '
            + ctx['release']['tag'] + ' source. Every snapshot page was independently reviewed; '
            'version isolation, links, type checks and the production build passed.\n\n'
            'This release snapshot includes the mechanical content/navigation/image copy when needed. '
            'Ordinary concern updates retain their 999-line cap.\n\nSource: ' + ctx['release']['url'])
    with tempfile.NamedTemporaryFile(mode='w', suffix='.md') as f:
        f.write(body); f.flush()
        print(docs.run('gh', 'pr', 'create', '--repo', repo, '--base', 'main', '--head', name,
                       '--title', f'docs: reconcile {ctx["release"]["tag"]} documentation', '--body-file', f.name))


def main():
    command = sys.argv[1]
    if command == 'detect':
        detect(); return
    directory = Path(os.environ['RUNNER_TEMP']) / 'release-docs'
    directory.mkdir(exist_ok=True)
    if command == 'prepare':
        prepare(directory); return
    ctx = read_json(directory / 'context.json')
    index = int(os.environ.get('BATCH', '0'))
    if command == 'seed':
        seed_context(ctx)
        write_json(directory / 'batch.json', {'paths': batch_paths(ctx, index), 'release': ctx['release']})
    elif command == 'export':
        # Do not re-seed here: it would overwrite the writer's work.
        seed = make_seed(ctx['base'], ctx['release'], ctx['main_manifest'])
        export(ctx, index, seed, directory / f'bundle-{index}.json')
    elif command == 'assemble':
        seed_context(ctx); assemble(ctx, directory)
        write_json(directory / 'batch.json', {'paths': batch_paths(ctx, index), 'release': ctx['release']})
    elif command in ('review', 'record-review'):
        review(ctx, index, directory, require_accepted=command == 'review')
    elif command == 'plan-repair':
        plan_repair(ctx, directory)
    elif command == 'export-repair':
        # Reconstruct the pre-repair proposal without overwriting the edits.
        export(ctx, index, assembled_data(ctx, directory), directory / f'bundle-{index}.json')
    elif command == 'finalize':
        seed_context(ctx); finalize(ctx, directory)
    elif command == 'refresh':
        refresh_base(ctx)
    elif command == 'publish':
        publish(ctx)
    else:
        raise ValueError('Unknown snapshot command')


if __name__ == '__main__':
    main()
