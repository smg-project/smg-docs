"""Expose merged release-audit corrections as hypotheses for main discovery."""

import difflib
import hashlib
import json
import re

import nightly_docs as docs


VERSION_ROOT = 'src/lib/versions/'
SHA = r'[0-9a-f]{40}'


def tree(base, prefix):
    files = {}
    for entry in docs.git('ls-tree', '-r', '-z', base, '--', prefix).split('\0'):
        if entry:
            metadata, path = entry.split('\t', 1)
            files[path] = metadata
    return files


def read(base, path, files):
    if path not in files:
        return ''
    if not files[path].startswith('100644 blob '):
        raise ValueError('Release handoff requires regular tracked files')
    return docs.git('show', f'{base}:{path}')


def owner(path):
    """One discovery owner per page; related-page corrections still share a concern."""
    name = path.removeprefix(docs.DOC_ROOT)
    if name == 'reference/configuration.md' or name.startswith('contributing/'):
        return 'deploy-config' if name == 'reference/configuration.md' else 'operations'
    if name.startswith('reference/api/'):
        return 'api-protocols'
    if any(term in name for term in ('grpc-pipeline', 'tokenization-and-parsing')):
        return 'parsers-tokenizers'
    if any(term in name for term in ('multimodal', 'grpc-workers', 'zmq-workers')):
        return 'grpc-multimodal'
    if any(term in name for term in ('mcp', 'wasm', 'external-provider')):
        return 'tools-providers'
    if (name.startswith(('concepts/data/', 'concepts/security/'))
            or any(term in name for term in ('auth', 'tls', 'data-connection', 'tenant-rate'))):
        return 'data-security'
    if any(term in name for term in ('metrics', 'monitoring', 'logging')):
        return 'operations'
    if name in ('getting-started/index.md', 'reference/index.md'):
        return 'deploy-config'
    return 'routing-workers'


def diff(before, after, old, new):
    before = before + '\n' if before else ''
    after = after + '\n' if after else ''
    return ''.join(difflib.unified_diff(before.splitlines(True), after.splitlines(True),
                                      fromfile=old, tofile=new))


def collect(context, directory):
    """Rebuild evidence from committed audit markers, never expiring run artifacts.

    The pre-audit seed was an existing release page, or main for a missing page.
    Include only audited changes still different from current main. Deliberate
    release/main differences are candidates too; discovery must dismiss them
    with current-source evidence rather than copying release text.
    """
    base, source = context['base_sha'], context['source_sha']
    if not all(re.fullmatch(SHA, sha) for sha in (base, source)):
        raise ValueError('Release handoff requires pinned revisions')
    current = tree(base, VERSION_ROOT)
    main = tree(base, docs.DOC_ROOT)
    candidates = []
    directory.mkdir(parents=True, exist_ok=True)
    markers = [p for p in current if re.fullmatch(
        re.escape(VERSION_ROOT) + r'v\d+\.\d+\.\d+/sync\.json', p)]
    for marker in sorted(markers):
        state = json.loads(read(base, marker, current))
        release = state.get('source_release', {})
        tag = marker.split('/')[-2]
        old_base = state.get('docs_base', '')
        if (release.get('tag') != tag or not re.fullmatch(SHA, release.get('sha', ''))
                or not re.fullmatch(SHA, old_base)
                or type(state.get('audited_pages')) is not int or state['audited_pages'] < 1):
            raise ValueError('Invalid committed release audit marker')
        docs.git('merge-base', '--is-ancestor', old_base, base)
        docs.source_git('merge-base', '--is-ancestor', release['sha'], source)
        prefix = VERSION_ROOT + tag + '/content/'
        old_release = tree(old_base, prefix)
        old_main = tree(old_base, docs.DOC_ROOT)
        names = {p.removeprefix(prefix) for p in current if p.startswith(prefix)}
        names.update(p.removeprefix(prefix) for p in old_release)
        names.update(p.removeprefix(docs.DOC_ROOT) for p in old_main)
        for name in sorted(names):
            path, release_path = docs.DOC_ROOT + name, prefix + name
            if not docs.doc_path(path):
                continue
            if release_path in old_release:
                before = read(old_base, release_path, old_release)
            else:
                before = read(old_base, path, old_main)
            audited = read(base, release_path, current)
            working = read(base, path, main)
            if before == audited or working == audited:
                continue
            identity = hashlib.sha256(json.dumps(
                [release['sha'], path, before, audited]).encode()).hexdigest()[:16]
            candidate = {'id': identity, 'release': tag, 'release_sha': release['sha'],
                         'doc_path': path, 'shard': owner(path), 'file': identity + '.patch'}
            evidence = {**candidate, 'docs_base': base, 'audit_base': old_base,
                        'source_sha': source}
            # Keep patch lines literal: a JSON-escaped whole diff is one enormous
            # line that model file readers truncate even when the file is small.
            (directory / candidate['file']).write_text(
                json.dumps(evidence, indent=2) + '\n\n=== audit_diff ===\n'
                + diff(before, audited, 'audit-input/' + name, tag + '/' + name)
                + '\n=== main_diff ===\n'
                + diff(working, audited, 'main/' + name, tag + '/' + name))
            candidates.append(candidate)
    return candidates


def assigned(context, slug):
    return [c for c in context.get('release_audit_candidates', []) if c['shard'] == slug]


def validate_checks(result, context, slug):
    expected = {c['id']: c for c in assigned(context, slug)}
    checks = result.get('release_audit_checks', [])
    if not isinstance(checks, list):
        raise ValueError('Missing release audit dispositions')
    proposals = {p['concern']: p for p in result['concerns']}
    seen, owned = set(), set()
    for check in checks:
        identity = check.get('candidate_id')
        if identity not in expected or identity in seen:
            raise ValueError('Unknown or duplicate release audit candidate')
        seen.add(identity)
        status = check.get('status')
        if status not in ('proposed', 'covered', 'not_applicable', 'deferred'):
            raise ValueError('Invalid release audit disposition')
        if not isinstance(check.get('evidence'), str) or not check['evidence'].strip():
            raise ValueError('Release audit disposition needs current-source evidence')
        concerns = check.get('concerns')
        if (not isinstance(concerns, list) or any(not isinstance(c, str) for c in concerns)
                or len(set(concerns)) != len(concerns)
                or (status == 'proposed' and not concerns)
                or (status in ('covered', 'not_applicable') and concerns)):
            raise ValueError('Invalid release audit concern references')
        for concern in concerns:
            if sum(p['concern'] == concern for p in result['concerns']) > 1:
                raise ValueError(f'Ambiguous release audit reference: duplicate concern slug {concern}')
            proposal = proposals.get(concern)
            if not proposal or expected[identity]['doc_path'] not in proposal['doc_paths']:
                raise ValueError(f'Release audit candidate {identity} references missing or unrelated concern {concern}')
            # A disposition can reference an existing ordinary proposal (keeping
            # its original source/identity) or a shared correction owned by another
            # candidate. validate_scan checks every proposal's source, inspected
            # history and page scope before checking these references.
            if concern.startswith('release-audit-'):
                if proposal['source_sha'] != context['source_sha']:
                    raise ValueError(f'Release audit concern {concern} must use the pinned main source')
                if concern.startswith('release-audit-' + identity + '-'):
                    owned.add(concern)
    if seen != set(expected):
        raise ValueError('Every assigned release audit candidate needs a disposition; defer unfinished work')
    # A cross-page reference cannot replace the owning candidate's evidence.
    if {c for c in proposals if c.startswith('release-audit-')} != owned:
        raise ValueError('Release audit concern lacks verified owning candidate evidence')
    return checks


def report(context, scans):
    checked = {c['candidate_id']: c for scan in scans for c in scan.get('release_audit_checks', [])}
    return [{**candidate, **checked.get(candidate['id'], {
        'candidate_id': candidate['id'], 'status': 'deferred', 'concerns': [],
        'evidence': 'No validated scan disposition; remains eligible next night.'})}
        for candidate in context.get('release_audit_candidates', [])]
