"""Account for recent mainline changes without truncating discovery history."""


SETTLED = {'proposed', 'covered', 'not_applicable'}
COMPLETE = SETTLED | {'blocked'}


def assign(context, prior, assignments):
    """Require the past day, gaps between runs, and unfinished prior checks."""
    history = [line.split()[0] for line in context['code_history']]
    required = set(context['daily_window']['commits'])
    # A delayed/failed schedule can leave more than 24 hours between snapshots.
    # This extends the daily obligation; it never narrows the historical index.
    previous = prior.get('source_sha')
    if previous in history:
        required.update(history[:history.index(previous)])
    pending = {(c['shard'], c['source_sha'])
               for c in prior.get('daily_coverage', {}).get('checks', [])
               if c['status'] not in SETTLED}
    assigned = {line.split()[0] for lines in assignments.values() for line in lines}
    if not required <= assigned:
        raise ValueError('Required daily commits lack discovery assignments')
    return {slug: [line.split()[0] for line in lines
                   if line.split()[0] in required or (slug, line.split()[0]) in pending]
            for slug, lines in assignments.items()}


def validate(result, context, slug):
    expected = set(context.get('daily_assignments', {}).get(slug, []))
    checks = result.get('daily_commit_checks', [])
    if not isinstance(checks, list):
        raise ValueError('Invalid daily commit checks')
    seen = set()
    for check in checks:
        sha = check.get('source_sha')
        if sha not in expected or sha in seen:
            raise ValueError('Unknown or duplicate daily commit check')
        seen.add(sha)
        status = check.get('status')
        if status not in COMPLETE | {'deferred'}:
            raise ValueError('Invalid daily commit disposition')
        if status in COMPLETE and sha not in result['inspected_commits']:
            raise ValueError('Daily commit was not inspected')
        if not isinstance(check.get('evidence'), str) or not check['evidence'].strip():
            raise ValueError('Daily commit check needs source/documentation evidence')
        blockers = check.get('blockers', [])
        if not isinstance(blockers, list) or (status == 'blocked') != bool(blockers):
            raise ValueError('Blocked daily checks need explicit open-PR page blockers')
        for blocker in blockers:
            number, path = blocker.get('pr'), blocker.get('path')
            if (type(number) is not int or not isinstance(path, str)
                    or not path.startswith('src/lib/content/')
                    or not any(pr['number'] == number and pr['state'] == 'open'
                               and path in pr['files'] for pr in context['existing_prs'])):
                raise ValueError('Daily commit blocker is not an open PR holding that page')
        concerns = check.get('concerns')
        if (not isinstance(concerns, list) or any(not isinstance(c, str) for c in concerns)
                or len(concerns) != len(set(concerns))
                or (status == 'proposed' and not concerns)
                or (status in {'covered', 'not_applicable'} and concerns)):
            raise ValueError('Invalid daily commit concern references')
        for concern in concerns:
            matches = sum(p['concern'] == concern for p in result['concerns'])
            if matches != 1:
                raise ValueError('Daily commit check references missing or ambiguous concern: '
                                 f'{sha}, {concern!r} ({matches} proposals)')
    # Missing dispositions remain incomplete in the report, without discarding
    # otherwise valid proposals from this scan.
    return checks


def report(context, scans, expected):
    checked = {(scan['shard'], c['source_sha']): c for scan in scans
               for c in scan.get('daily_commit_checks', [])}
    checks = []
    for slug in expected:
        for sha in context.get('daily_assignments', {}).get(slug, []):
            checks.append({**checked.get((slug, sha), {
                'source_sha': sha, 'status': 'uninspected', 'concerns': [],
                'evidence': 'No validated disposition; must be checked next run.'}),
                'shard': slug, 'source_sha': sha})
    return {'window': context.get('daily_window'), 'checks': checks,
            'complete': all(c['status'] in COMPLETE for c in checks)}
