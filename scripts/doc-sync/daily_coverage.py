"""Account for recent mainline changes without truncating discovery history."""

import nightly_docs as docs


SETTLED = {'proposed', 'covered', 'not_applicable'}
COMPLETE = SETTLED | {'blocked'}
# Assessed pairs the backlog does not need to revisit before fresh work.
LEDGER = COMPLETE | {'abandoned'}
# A selected concern that never reaches a PR (review rejection, writer no-op,
# failed publication) is re-examined this many times before maintainers are
# asked to look at it instead of spending another nightly attempt.
MAX_PUBLICATION_ATTEMPTS = 3
BACKLOG_JOBS = 8
BACKLOG_COMMITS = 25


def identity(item):
    return f"{item['area']}/{item['concern']}"


def published(item, prs):
    """Open, merged and declined PRs all settle a concern; release deferral does not."""
    return any(docs.same_concern(item, pr) for pr in prs)


def prior_checks(prior):
    return (prior.get('daily_coverage', {}).get('checks', [])
            + [{**c, 'kind': 'backlog'} for c in prior.get('backlog_coverage', {}).get('checks', [])])


def scan_key(scan):
    return scan['shard'], scan.get('backlog') or 0


def unpublished_reason(item, prior, feedback):
    deferred = {key: reason for key, reason in prior.get('deferred', [])}
    if item['key'] in deferred:
        return 'Deferred, not published: ' + deferred[item['key']] + '.'
    if any(selected['key'] == item['key'] for selected in prior.get('selected', [])):
        return feedback.get(item['key'], 'Selected, but no PR was created: the writer made no '
                            'supported change or publication failed before review.')
    return 'Proposed, but not selected for publication.'


def carry_forward(prior, prs, feedback=None):
    """Return unfinished checks, reopening proposals that never reached a PR.

    A proposed disposition only settles a commit once its concerns have a PR.
    Deferred page conflicts, review rejections and writer no-ops reopen the
    check with the reason, so the subsystem re-examines it on the next run.
    """
    feedback = feedback or {}
    attempts = dict(prior.get('publication_attempts', {}))
    for item in map(docs.validate_item, prior.get('selected', [])):
        if published(item, prs):
            attempts.pop(identity(item), None)
        else:
            attempts[identity(item)] = attempts.get(identity(item), 0) + 1
    proposals = {scan_key(scan): {p['concern']: docs.validate_item(p) for p in scan['concerns']}
                 for scan in prior.get('scans', [])}
    checks, abandoned = [], []
    for check in prior_checks(prior):
        if check['status'] not in SETTLED:
            checks.append(check)
            continue
        if check['status'] != 'proposed':
            continue
        owned = proposals.get(scan_key(check), {})
        waiting = [owned[c] for c in check['concerns'] if c in owned and not published(owned[c], prs)]
        retry = [item for item in waiting if attempts.get(identity(item), 0) < MAX_PUBLICATION_ATTEMPTS]
        abandoned.extend({'shard': check['shard'], 'source_sha': check['source_sha'],
                          'concern': identity(item), 'attempts': attempts[identity(item)],
                          'reason': unpublished_reason(item, prior, feedback)}
                         for item in waiting if item not in retry)
        if retry:
            checks.append({**check, 'status': 'unpublished', 'concerns': [i['concern'] for i in retry],
                           'blockers': [], 'depends_on': [],
                           'evidence': ' '.join(f"{identity(i)}: {unpublished_reason(i, prior, feedback)}"
                                                for i in retry)})
    return checks, attempts, abandoned


def retry_concerns(prior, prs, attempts, feedback=None):
    """Queue selected-but-unpublished concerns with the reason they did not land."""
    feedback = feedback or {}
    return [{**item, 'publication_attempts': attempts[identity(item)],
             'publication_feedback': unpublished_reason(item, prior, feedback)}
            for item in map(docs.validate_item, prior.get('selected', []))
            if not published(item, prs)
            and attempts.get(identity(item), 0) < MAX_PUBLICATION_ATTEMPTS]


def ledger(prior, carried, abandoned, history):
    """Remember assessed commit/subsystem pairs so backlog work never repeats them."""
    commits = {line.split()[0] for line in history}
    result = {sha: dict(entry) for sha, entry in prior.get('commit_ledger', {}).items()
              if sha in commits}
    # Reports written before the ledger existed still contribute their checks.
    for check in prior_checks(prior):
        if check['status'] in COMPLETE and check['source_sha'] in commits:
            result.setdefault(check['source_sha'], {})[check['shard']] = check['status']
    for check in carried:
        if check['status'] != 'blocked':
            result.get(check['source_sha'], {}).pop(check['shard'], None)
    for item in abandoned:
        if item['source_sha'] in commits:
            result.setdefault(item['source_sha'], {})[item['shard']] = 'abandoned'
    return {sha: entry for sha, entry in result.items() if entry}


def record(commit_ledger, checks):
    result = {sha: dict(entry) for sha, entry in commit_ledger.items()}
    for check in checks:
        entry = result.setdefault(check['source_sha'], {})
        if check['status'] in COMPLETE:
            entry[check['shard']] = check['status']
        else:
            entry.pop(check['shard'], None)
    return {sha: entry for sha, entry in result.items() if entry}


def assign(context, prior, assignments, carried=None):
    """Require the past day, gaps between runs, and unfinished prior checks."""
    history = [line.split()[0] for line in context['code_history']]
    required = set(context['daily_window']['commits'])
    # A delayed/failed schedule can leave more than 24 hours between snapshots.
    # This extends the daily obligation; it never narrows the historical index.
    previous = prior.get('source_sha')
    if previous in history:
        required.update(history[:history.index(previous)])
    if carried is None:
        carried = [c for c in prior_checks(prior) if c['status'] not in SETTLED]
    pending = {(c['shard'], c['source_sha']) for c in carried if c.get('kind') != 'backlog'}
    assigned = {line.split()[0] for lines in assignments.values() for line in lines}
    if not required <= assigned:
        raise ValueError('Required daily commits lack discovery assignments')
    return {slug: [line.split()[0] for line in lines
                   if line.split()[0] in required or (slug, line.split()[0]) in pending]
            for slug, lines in assignments.items()}


def backlog(context, commit_ledger, carried, assignments, shards, jobs=BACKLOG_JOBS, size=BACKLOG_COMMITS):
    """Spread older never-assessed commit/subsystem pairs over bounded scans.

    Reopened backlog checks come first, then fresh pairs newest-first, then
    pairs still blocked by open PRs. Each job goes to the subsystem with the
    most remaining work, so the largest backlogs drain in parallel.
    """
    daily = context.get('daily_assignments', {})
    queues = {}
    for slug in shards:
        required = set(daily.get(slug, []))
        lines = [line.split()[0] for line in assignments.get(slug, [])]
        retry = {c['source_sha'] for c in carried
                 if c.get('kind') == 'backlog' and c['shard'] == slug and c['status'] != 'blocked'}
        status = {sha: commit_ledger.get(sha, {}).get(slug) for sha in lines}
        queues[slug] = ([sha for sha in lines if sha in retry and sha not in required]
                        + [sha for sha in lines if sha not in retry and sha not in required
                           and status[sha] is None]
                        + [sha for sha in lines if sha not in retry and sha not in required
                           and status[sha] == 'blocked'])
    remaining = {slug: list(queue) for slug, queue in queues.items() if queue}
    batches, counts = [], {}
    for _ in range(jobs):
        if not remaining:
            break
        slug = max(remaining, key=lambda name: (len(remaining[name]), -shards.index(name)))
        counts[slug] = counts.get(slug, 0) + 1
        batches.append({'shard': slug, 'backlog': counts[slug], 'commits': remaining[slug][:size]})
        remaining[slug] = remaining[slug][size:]
        if not remaining[slug]:
            del remaining[slug]
    return batches, {slug: len(queue) for slug, queue in queues.items()}


def batch_commits(context, slug, batch):
    return next((b['commits'] for b in context.get('backlog_batches', [])
                 if b['shard'] == slug and b['backlog'] == batch), [])


def validate(result, context, slug, shards=(), expected=None):
    if expected is None:
        expected = context.get('daily_assignments', {}).get(slug, [])
    expected = set(expected)
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
        depends = check.get('depends_on', [])
        if (not isinstance(blockers, list) or not isinstance(depends, list)
                or (status == 'blocked') != bool(blockers or depends)):
            raise ValueError('Blocked daily checks need explicit open-PR page or subsystem blockers')
        if (len(set(map(str, depends))) != len(depends)
                or any(name not in shards or name == slug for name in depends)):
            raise ValueError('Daily commit dependency must name other discovery subsystems')
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


def dependency(check, scan, scans, context):
    """Accept a cross-subsystem wait only while the other side has live work.

    The owning subsystem's proposal for the same commit must exist tonight, in
    the queue, or as an open PR. Otherwise the wait could never end, so the
    check stays unfinished and coverage fails visibly.
    """
    if check['status'] != 'blocked' or not check.get('depends_on'):
        return check
    sha, own = check['source_sha'], {p['concern'] for p in scan['concerns']}
    marker = f'{docs.MARKER}{sha}:'
    for name in check['depends_on']:
        if not (any(p['source_sha'] == sha for other in scans if other['shard'] == name
                    for p in other['concerns'])
                or any(p['source_sha'] == sha and p['concern'] not in own
                       for p in context.get('pending_concerns', []))
                or any(pr['state'] == 'open' and marker in pr['body']
                       for pr in context['existing_prs'])):
            return {**check, 'status': 'deferred', 'evidence': check['evidence']
                    + f' [Unverified dependency: {name} has no proposed, queued or open'
                    ' documentation work for this commit.]'}
    return check


def report(context, scans, expected):
    daily = [scan for scan in scans if not scan.get('backlog')]
    checked = {(scan['shard'], c['source_sha']): dependency(c, scan, scans, context)
               for scan in daily for c in scan.get('daily_commit_checks', [])}
    checks = []
    for slug in expected:
        for sha in context.get('daily_assignments', {}).get(slug, []):
            checks.append({**checked.get((slug, sha), {
                'source_sha': sha, 'status': 'uninspected', 'concerns': [],
                'evidence': 'No validated disposition; must be checked next run.'}),
                'shard': slug, 'source_sha': sha})
    return {'window': context.get('daily_window'), 'checks': checks,
            'complete': all(c['status'] in COMPLETE for c in checks)}


def backlog_report(context, scans):
    """Best-effort older coverage: unfinished pairs return without failing the run."""
    checked = {(*scan_key(scan), c['source_sha']): dependency(c, scan, scans, context)
               for scan in scans if scan.get('backlog') for c in scan.get('daily_commit_checks', [])}
    received = {scan_key(scan) for scan in scans if scan.get('backlog')}
    checks = []
    for batch in context.get('backlog_batches', []):
        for sha in batch['commits']:
            checks.append({**checked.get((batch['shard'], batch['backlog'], sha), {
                'source_sha': sha, 'status': 'uninspected', 'concerns': [],
                'evidence': 'No validated disposition; remains in the backlog.'}),
                'shard': batch['shard'], 'backlog': batch['backlog'], 'source_sha': sha})
    return {'remaining_before_run': context.get('backlog_remaining', {}),
            'batches': [{'shard': b['shard'], 'backlog': b['backlog'], 'commits': len(b['commits'])}
                        for b in context.get('backlog_batches', [])],
            'missing': [f"{b['shard']}/backlog-{b['backlog']}" for b in context.get('backlog_batches', [])
                        if (b['shard'], b['backlog']) not in received],
            'checks': checks}
