"""Finish unresolved daily assessments without repeating broad discovery."""

import json
import os
from pathlib import Path
import sys

import daily_coverage
import discovery


def required(context, scan):
    if scan.get('backlog'):
        return []
    checked = {c['source_sha']: c for c in scan.get('daily_commit_checks', [])}
    return [sha for sha in context.get('daily_assignments', {}).get(scan['shard'], [])
            if checked.get(sha, {}).get('status') not in daily_coverage.COMPLETE]


def prepare(context, scan, assignments):
    commits = required(context, scan)
    scoped = discovery.scan_context(context, scan['shard'], assignments)
    ids = [i for i, line in enumerate(context['code_history'], 1) if line.split()[0] in commits]
    return {**scoped, 'proposal_limit': discovery.docs.MAX_PRS - len(scan['concerns']),
            'scan_mode': 'daily-followup', 'required_commit_ids': ids,
            'focus_commit_ids': ids, 'daily_assignments': {scan['shard']: commits},
            'release_audit_candidates': [],
            'pending_concerns': [p for p in scoped['pending_concerns']
                                 if p['source_sha'] in commits
                                 and not p['concern'].startswith('release-audit-')],
            'pending_commit_checks': [c for c in scoped['pending_commit_checks']
                                      if c['source_sha'] in commits],
            'previous_scan': scan}


def merge(context, scan, raw):
    if scan['base_sha'] != context['base_sha']:
        raise ValueError('Follow-up documentation baseline changed')
    commits = required(context, scan)
    result = json.loads(discovery.resolve_scan(raw, context['code_history']))
    checks = result['daily_commit_checks']
    shas = [c['source_sha'] for c in checks]
    if len(shas) != len(set(shas)) or set(shas) != set(commits):
        raise ValueError('Follow-up must assess exactly the unfinished daily commits')
    if result['release_audit_checks'] or result['retired_concerns']:
        raise ValueError('Follow-up cannot change release audits or retire queued concerns')
    inspected = result['inspected_commits']
    if len(inspected) != len(set(inspected)):
        raise ValueError('Duplicate follow-up inspected commit')
    existing = {daily_coverage.identity(p) for p in scan['concerns']}
    referenced = {name for c in checks for name in c['concerns']}
    for item in result['concerns']:
        identity = daily_coverage.identity(item)
        if identity in existing:
            raise ValueError('Follow-up cannot replace an existing proposal')
        existing.add(identity)
        if (item['source_sha'] not in commits or item['source_sha'] not in inspected
                or item['concern'] not in referenced
                or item['concern'].startswith(('release-audit-', 'version-context-'))):
            raise ValueError('Follow-up proposal must address an inspected unfinished daily commit')
    daily_coverage.validate({**result, 'concerns': scan['concerns'] + result['concerns']},
                            context, scan['shard'], discovery.SHARD_NAMES, commits)
    # Preserve all completed findings, original proposal identities, audit
    # dispositions and queue retirements. Revalidate the entire merged scan.
    replacement = {c['source_sha']: c for c in checks}
    combined = {**scan, 'concerns': scan['concerns'] + result['concerns'],
                'inspected_commits': list(dict.fromkeys(scan['inspected_commits'] + inspected)),
                'daily_commit_checks': [replacement.pop(c['source_sha'], c)
                                        for c in scan.get('daily_commit_checks', [])],
                'remaining_work': scan['remaining_work'] + '\nDaily follow-up: ' + result['remaining_work']}
    combined['daily_commit_checks'].extend(replacement.values())
    validated = discovery.validate_scan(json.dumps(combined), context, scan['shard'], context['code_history'])
    return {**scan, **validated}


def main():
    root = Path(os.environ['RUNNER_TEMP']) / 'nightly-docs-context'
    context = json.loads((root / 'context.json').read_text())
    context.update(source_root=os.environ['SOURCE_ROOT'], source_diffs=str(root / 'nightly-docs-sources'),
                   release_audit_evidence=str(root / 'release-audits'))
    path = Path(os.environ['SCAN_OUTPUT'])
    scan = json.loads(path.read_text())
    if sys.argv[1] == 'prepare':
        commits = required(context, scan)
        with open(os.environ['GITHUB_OUTPUT'], 'a') as output:
            output.write(f'needed={str(bool(commits)).lower()}\n')
            if not commits:
                return
            assignments = json.loads((root / 'assignments.json').read_text())
            scoped = prepare(context, scan, assignments)
            schema = discovery.scan_schema(scoped)
            schema['properties']['retired_concerns']['maxItems'] = 0
            encoded = json.dumps(schema, separators=(',', ':'))
            if len(encoded.encode()) > 120000:
                raise ValueError('Follow-up schema is too large')
            Path(os.environ['NIGHTLY_CONTEXT']).write_text(json.dumps(scoped, indent=2))
            output.write('schema=' + encoded + '\n')
        # A missing output must never reuse the first pass's successful trace.
        (Path(os.environ['RUNNER_TEMP']) / 'claude-execution-output.json').unlink(missing_ok=True)
    elif sys.argv[1] == 'merge':
        execution = {}
        raw = discovery.read_scan_output(os.environ['SCAN_EXECUTION_FILE'],
                                         os.environ['SCAN_ACTION_OUTCOME'], 100, execution)
        combined = merge(context, scan, raw)
        combined['daily_followup'] = {'required_commits': required(context, scan), 'execution': execution,
                                     'remaining_work': json.loads(raw)['remaining_work']}
        path.write_text(json.dumps(combined))
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as summary:
            summary.write(f"Daily follow-up: {len(required(context, scan))} unfinished checks; "
                          f"{len(required(context, combined))} still unfinished after "
                          f"{execution['assistant_rounds']} assistant rounds.\n")
    else:
        raise ValueError('Unknown follow-up command')


if __name__ == '__main__':
    main()
