# Adapted from ome-projects/ome at 776fbe3b4be9e826be3404699886543d83e8e380.
# Partial-discovery recovery adapted from OME at 4221c9a9bc056fc10293e8007be0c5f8c9e1343d.
# SMG cross-repository discovery and publication changes; Apache-2.0 (LICENSE).
"""Partition discovery and fairly combine independently validated scan results."""

from datetime import datetime, timedelta, timezone
from itertools import zip_longest
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from urllib.parse import urlencode

import nightly_docs as docs
import release_handoff
import daily_coverage


# Ownership is about the user question, not where a shared API type happens to
# live. A commit may appear in several scans; the complete history stays eligible.
SHARDS = [
    ("api-protocols", "Public request/response fields, OpenAI/Anthropic compatibility and HTTP streaming; not parsing/template internals, auth, or persistence.",
     ["crates/protocols", "model_gateway/src/endpoints", "model_gateway/src/routers/http", "clients"]),
    ("routing-workers", "Load balancing, worker registration/discovery, health, prefill/decode routing and overload behavior; not public protocol fields or deployment installation.",
     ["model_gateway/src/policies", "model_gateway/src/worker", "model_gateway/src/service_discovery", "model_gateway/src/routers", "crates/kv_index", "crates/radix_tree"]),
    ("grpc-multimodal", "gRPC worker serving, multimodal transport/encoding, engine compatibility; not thinking/template semantics or HTTP API fields.",
     ["model_gateway/src/routers/grpc", "grpc_servicer", "crates/grpc_client", "crates/engine_zmq_client", "crates/multimodal", "crates/mm_rdma"]),
    ("parsers-tokenizers", "Tool and reasoning parsing, chat templates, reasoning effort, tokenization and model-specific rendering; not general public API or worker lifecycle.",
     ["crates/tool_parser", "crates/reasoning_parser", "crates/tokenizer", "model_gateway/src/routers/grpc/utils", "model_gateway/src/endpoints/parse", "model_gateway/src/endpoints/tokenize", "bindings"]),
    ("tools-providers", "MCP tools, external providers/routing, WASM extensions and agent tool execution; not local-worker routing or parser syntax.",
     ["crates/mcp", "crates/external_router", "crates/wasm", "model_gateway/src/wasm", "model_gateway/src/routers/openai", "model_gateway/src/endpoints/responses"]),
    ("data-security", "Conversation/response persistence and history backends, authentication, authorization, rate limits and security; not Helm deployment syntax.",
     ["crates/data_connector", "crates/auth", "model_gateway/src/endpoints/conversations", "model_gateway/src/endpoints/responses", "model_gateway/src/middleware", "model_gateway/src/rate_limit"]),
    ("deploy-config", "Installation, Helm/Kubernetes, Docker, CLI flags, configuration and releases; not internal runtime implementation details owned by other scans.",
     ["deploy", "docker", "model_gateway/src/config", "model_gateway/src/main.rs", "model_gateway/src/lib.rs", "model_gateway/src/cli.rs", "Cargo.toml", "bindings"]),
    ("operations", "Observability, metrics, logging, mesh, benchmarks, testing/contributor workflows and other user-facing changes outside the other responsibilities.",
     ["model_gateway/src/observability", "model_gateway/src/mesh", "model_gateway/src/mesh_discovery", "crates/mesh", "scripts", "examples", "e2e_test", ".github", "Makefile"]),
]
SHARD_NAMES = tuple(slug for slug, _, _ in SHARDS)
# Discovery reports chain the queue and commit ledger from run to run.
REPORT_DAYS = 30


def partition(context):
    history = {line.split()[0]: line for line in context["code_history"]}
    assignments = {}
    for slug, _, paths in SHARDS:
        assignments[slug] = set(docs.source_git("log", "--first-parent", "--format=%H",
                                       context["source_sha"], "--", *paths).splitlines()) & history.keys()
    # No path falls through the cracks. Operational discovery also receives
    # commits outside the named subsystems, including future directories.
    assignments["operations"].update(history.keys() - set().union(*assignments.values()))
    return {slug: [line for sha, line in history.items() if sha in assignments[slug]]
            for slug, _, _ in SHARDS}


def resolve_scan(raw, history):
    """Resolve small model-selected IDs through the trusted source index."""
    result = json.loads(raw)
    commits = [line.split()[0] for line in history]

    def resolve(number):
        if type(number) is not int or number < 1 or number > len(commits):
            raise ValueError("Source commit ID is outside this scan's index")
        return commits[number - 1]

    concerns = []
    for proposal in result["concerns"]:
        item = dict(proposal)
        if "source_sha" in item:
            raise ValueError("The model must select a commit ID, not supply a hash")
        item["source_sha"] = resolve(item.pop("source_commit"))
        concerns.append(item)
    daily = result.get('daily_commit_checks', [])
    if isinstance(daily, dict):
        keyed = []
        for number, check in daily.items():
            if not number.isdecimal() or str(int(number)) != number:
                raise ValueError('Daily commit key must be a canonical numeric source ID')
            if not isinstance(check, dict):
                raise ValueError('Daily commit assessment must be an object')
            if 'source_commit' in check or 'source_sha' in check:
                raise ValueError('Keyed daily checks must select source only through the key')
            keyed.append({**check, 'source_commit': int(number)})
        daily = keyed
    return json.dumps({"concerns": concerns,
                       "inspected_commits": [resolve(number) for number in result["inspected_commits"]],
                       "daily_commit_checks": [{**{k: v for k, v in check.items() if k != 'source_commit'},
                                                'source_sha': resolve(check['source_commit'])}
                                               for check in daily],
                       "release_audit_checks": result.get("release_audit_checks", []),
                       "remaining_work": result["remaining_work"]})


def validate_scan(raw, context, slug, history, backlog=0):
    result = json.loads(raw)
    inspected = result["inspected_commits"]
    candidates = {line.split()[0] for line in history}
    if (not isinstance(inspected, list) or any(not isinstance(sha, str) for sha in inspected)
            or len(inspected) != len(set(inspected)) or not set(inspected) <= candidates):
        raise ValueError("Invalid inspected-commit report")
    if not isinstance(result["remaining_work"], str) or not result["remaining_work"].strip():
        raise ValueError("Missing remaining-work report")
    concerns = [{**item, "title": docs.normalize_title(item["title"])}
                for item in result["concerns"]]
    # Validate each independently. Competing proposals are deferred centrally,
    # not an operational failure that discards a whole scan's useful results.
    uninspected = []
    for item in concerns:
        docs.plan(json.dumps({"concerns": [item]}), {**context, "code_history": history})
        if item["source_sha"] not in inspected:
            uninspected.append(f"{item['area']}/{item['concern']} ({item['source_sha']})")
    if uninspected:
        raise ValueError('Concern source was not reported as inspected: ' + '; '.join(uninspected))
    if len(concerns) > docs.MAX_PRS:
        raise ValueError("Scan exceeds proposal limit")
    expected, audit_context = None, context
    if backlog:
        # Backlog scans assess only their assigned older commits; release audits
        # stay with the subsystem's daily scan, which owns their candidates.
        expected = daily_coverage.batch_commits(context, slug, backlog)
        if not expected:
            raise ValueError("Unknown backlog scan")
        audit_context = {**context, 'release_audit_candidates': []}
    checks = release_handoff.validate_checks({**result, 'concerns': concerns}, audit_context, slug)
    return {"shard": slug, "base_sha": context["base_sha"], "concerns": concerns,
            "daily_commit_checks": daily_coverage.validate(result, context, slug, SHARD_NAMES, expected),
            "release_audit_checks": checks,
            "inspected_commits": inspected, "remaining_work": result["remaining_work"],
            **({"backlog": backlog} if backlog else {})}


def scan_names(context):
    """Select all scans by default, or one explicitly requested validation scan."""
    names = [slug for slug, _, _ in SHARDS]
    requested = context.get("discovery_shard", "")
    if requested and requested not in names:
        raise ValueError("Unknown discovery shard")
    return [requested] if requested else names


def prioritized(scans, context):
    """Order proposals so required commit coverage claims contested pages first.

    Tiers: concerns for required recent commits, previously queued or retried
    concerns, older backlog commits, then audits and other discovery. Round
    robin within each tier prevents one large subsystem from consuming the cap.
    """
    pending = {(p['area'], p['concern']) for p in context.get('pending_concerns', [])}

    def tier(scan, proposal):
        referenced = any(proposal['concern'] in check.get('concerns', [])
                         for check in scan.get('daily_commit_checks', []))
        if referenced and not scan.get('backlog'):
            return 0
        if (proposal['area'], proposal['concern']) in pending:
            return 1
        return 2 if referenced else 3

    ordered = []
    for level in range(4):
        lanes = [[p for p in scan['concerns'] if tier(scan, p) == level] for scan in scans]
        ordered.extend(p for row in zip_longest(*lanes) for p in row if p is not None)
    return ordered


def combine(scans, context, allow_partial=False):
    expected = scan_names(context)
    batches = [(b['shard'], b['backlog']) for b in context.get('backlog_batches', [])]
    by_id = {daily_coverage.scan_key(scan): scan for scan in scans}
    daily = {slug for slug, batch in by_id if not batch}
    if (len(by_id) != len(scans) or not daily <= set(expected)
            or not {key for key in by_id if key[1]} <= set(batches)):
        raise ValueError("Unknown or duplicate discovery scans")
    # Backlog scans are best-effort; only the subsystem scans are mandatory.
    if not allow_partial and daily != set(expected):
        raise ValueError("Missing or duplicate discovery scans")
    for (slug, batch), scan in by_id.items():
        if scan["base_sha"] != context["base_sha"]:
            raise ValueError("Discovery baseline mismatch")
        validate_scan(json.dumps(scan), context, slug, context["code_history"], batch)
    ordered = ([by_id[(slug, 0)] for slug in expected if (slug, 0) in by_id]
               + [by_id[key] for key in batches if key in by_id])
    selected, occupied, keys, identities, questions, deferred = [], set(), set(), set(), set(), []
    for proposal in prioritized(ordered, context):
        proposal = {**proposal, "title": docs.normalize_title(proposal["title"])}
        item = docs.plan(json.dumps({"concerns": [proposal]}), context)
        if not item:
            deferred.append((docs.validate_item(proposal)["key"], "existing PR"))
            continue
        item = item[0]
        identity = (item["area"], item["concern"])
        question = " ".join(item["question"].lower().split())
        if item["key"] in keys or identity in identities or question in questions:
            reason = "duplicate concern"
        elif occupied.intersection(item["doc_paths"]):
            reason = "overlapping documentation files"
        elif len(selected) >= context.get("max_prs", docs.MAX_PRS):
            reason = "UTC daily/run PR cap"
        else:
            selected.append(item)
            keys.add(item["key"])
            identities.add(identity)
            questions.add(question)
            occupied.update(item["doc_paths"])
            continue
        deferred.append((item["key"], reason))
    return selected, deferred


def deferred_queue(scans, deferred, prs):
    """Queue file-blocked concerns without reviving merged or declined work."""
    queued = {key for key, reason in deferred
              if reason in {'overlapping documentation files', 'UTC daily/run PR cap', 'existing PR'}}
    queued_concerns = []
    for scan in scans:
        for proposal in scan['concerns']:
            item = docs.validate_item({**proposal, 'title': docs.normalize_title(proposal['title'])})
            if item['key'] not in queued:
                continue
            # Keep file-blocked work, but never queue an already-open,
            # merged, or deliberately declined instance of this concern.
            if any(docs.same_concern(item, pr)
                   for pr in prs):
                continue
            queued_concerns.append(item)
    return queued_concerns


def build_report(scans, context):
    """Keep validated partial work while explicitly reporting missing coverage."""
    selected, deferred = combine(scans, context, allow_partial=True)
    expected = scan_names(context)
    received = {scan['shard'] for scan in scans if not scan.get('backlog')}
    missing = [name for name in expected if name not in received]
    daily = daily_coverage.report(context, scans, expected)
    backlog = daily_coverage.backlog_report(context, scans)
    queued = deferred_queue(scans, deferred, context['existing_prs'])
    # Daily quota can reduce each scan's proposal budget even when every scan
    # succeeds. Such a run cannot retire prior work merely by omitting it.
    quota_limited = context.get('max_prs', docs.MAX_PRS) < context.get('requested_max_prs', docs.MAX_PRS)
    if missing or not daily['complete'] or quota_limited:
        selected_ids = {(item['area'], item['concern']) for item in selected}
        prior = [item for item in context.get('pending_concerns', [])
                 if (item['area'], item['concern']) not in selected_ids]
        queued = prior + queued
    queued = pending_candidates(queued, context)
    return {'base_sha': context['base_sha'], 'source_sha': context['source_sha'],
            'source_release': context.get('source_release'), 'source_target': context.get('source_target'),
            'source_repo': docs.SOURCE_REPO, 'initial_since': docs.INITIAL_SINCE,
            'eligible_commits': len(context['code_history']),
            'available_pr_slots': context.get('max_prs', docs.MAX_PRS),
            'requested_max_prs': context.get('requested_max_prs', docs.MAX_PRS),
            'discovery_shard': context.get('discovery_shard', ''),
            'scans': scans, 'selected': selected,
            'release_audit_checks': release_handoff.report(context, scans),
            'daily_coverage': daily, 'backlog_coverage': backlog,
            # The ledger outlives the 24-hour window, so older commits are
            # assessed once and then leave the backlog.
            'commit_ledger': daily_coverage.record(context.get('commit_ledger', {}),
                                                   daily['checks'] + backlog['checks']),
            'publication_attempts': context.get('publication_attempts', {}),
            'abandoned_concerns': context.get('abandoned_concerns', []),
            'deferred': deferred, 'queued_concerns': queued[:docs.MAX_PRS],
            'queue_overflow': [item['key'] for item in queued[docs.MAX_PRS:]],
            'expected_shards': expected, 'missing_shards': missing,
            'complete': not missing and daily['complete'],
            'doc_inventory': context.get('doc_inventory', []),
            'dry_run': context.get('dry_run', False), 'max_prs': context.get('max_prs', docs.MAX_PRS)}


def pending_candidates(proposals, context):
    """Validate and deduplicate pending evidence before applying the queue cap."""
    history = {line.split()[0] for line in context['code_history']}
    result, seen = [], set()
    for proposal in proposals:
        item = docs.validate_item(proposal)
        identity = (item['area'], item['concern'])
        if any(docs.same_concern(item, pr)
               for pr in context.get('existing_prs', [])):
            continue
        if item['source_sha'] in history and identity not in seen:
            seen.add(identity)
            result.append(item)
    return result


def pending_from_report(report, context):
    """Carry a bounded queue as evidence for fresh discovery."""
    return pending_candidates(report.get('queued_concerns', []), context)[:docs.MAX_PRS]


def previous_report(repo, branch, context):
    """Read the latest retained main-branch plan, never another branch's pilot."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=REPORT_DAYS)).date().isoformat()
    query = urlencode({'branch': branch, 'per_page': 100, 'created': '>=' + cutoff})
    run_pages = json.loads(docs.run('gh', 'api',
        f'repos/{repo}/actions/workflows/nightly-doc-sync.yml/runs?{query}', '--paginate', '--slurp'))
    for run in (run for page in run_pages for run in page['workflow_runs']):
        if (run['status'] != 'completed' or run['head_branch'] != branch
                or run['head_repository']['full_name'] != repo):
            continue
        pages = json.loads(docs.run('gh', 'api',
            f"repos/{repo}/actions/runs/{run['id']}/artifacts?per_page=100", '--paginate', '--slurp'))
        artifacts = [artifact for page in pages for artifact in page['artifacts']]
        if not any(a['name'] == 'nightly-docs-discovery-report' and not a['expired'] for a in artifacts):
            continue
        with tempfile.TemporaryDirectory() as directory:
            docs.run('gh', 'run', 'download', str(run['id']), '--repo', repo,
                     '--name', 'nightly-docs-discovery-report', '--dir', directory)
            report = json.loads(Path(directory, 'nightly-docs-discovery-report.json').read_text())
        # A full production attempt may carry a partial recovery report.
        # Filtered/dry-run pilots still cannot replace the production queue.
        if (report.get('dry_run') is not False or report.get('requested_max_prs') != docs.MAX_PRS
                or report.get('source_repo') != docs.SOURCE_REPO
                or report.get('initial_since') != docs.INITIAL_SINCE
                or set(report.get('expected_shards', []))
                != {name for name, _, _ in SHARDS}):
            continue
        # Publication outcomes are separate artifacts of the same run.
        return {**report, 'run_id': run['id']}
    return {}


def publication_feedback(repo, report):
    """Read the prior run's review verdicts so unpublished concerns return with a reason."""
    if not report.get('run_id') or not report.get('selected'):
        return {}
    feedback = {}
    with tempfile.TemporaryDirectory() as directory:
        try:
            docs.run('gh', 'run', 'download', str(report['run_id']), '--repo', repo,
                     '--pattern', 'nightly-docs-validation-*', '--dir', directory)
        except subprocess.CalledProcessError:
            return {}
        for path in Path(directory).rglob('nightly-docs-item.json'):
            try:
                key = json.loads(path.read_text())['key']
            except (OSError, ValueError, KeyError, TypeError):
                continue  # Feedback is advisory; a damaged artifact only loses the reason.
            try:
                verdict = docs.review_verdict(path.with_name('nightly-docs-review.json').read_text())
            except (OSError, ValueError):
                feedback[key] = 'Imported for review, but no valid review verdict was recorded.'
                continue
            failed = [gate for gate in docs.REVIEW_GATES if not verdict[gate]]
            feedback[key] = ('Independent review rejected it (failed: ' + ', '.join(failed) + '): '
                             + verdict['reason'][:3000] if failed else
                             'Review accepted it, but the site build or publication did not complete.')
    return feedback


def backlog_limits():
    jobs = int(os.environ.get('BACKLOG_JOBS') or daily_coverage.BACKLOG_JOBS)
    size = int(os.environ.get('BACKLOG_COMMITS') or daily_coverage.BACKLOG_COMMITS)
    if not 0 <= jobs <= 16 or not 1 <= size <= 40:
        raise ValueError('backlog_jobs must be 0-16 and backlog_commits 1-40')
    return jobs, size


def previous_pending(repo, branch, context):
    return pending_from_report(previous_report(repo, branch, context), context)


def scan_context(context, slug, assignments, backlog=0):
    # Path ownership is a prioritization hint, not a source-attribution boundary.
    # Cross-cutting wiring (e.g. app_context.rs enabling MCP config) can introduce
    # subsystem behavior without touching that subsystem's directory.
    focus = next(focus for name, focus, _ in SHARDS if name == slug)
    primary = {line.split()[0] for line in assignments[slug]}
    history = context["code_history"]
    if backlog:
        required = set(daily_coverage.batch_commits(context, slug, backlog))
        audit_candidates = []
    else:
        required = set(context.get('daily_assignments', {}).get(slug, []))
        audit_candidates = release_handoff.assigned(context, slug)
    # A queued audit identity can only be re-proposed by its owning scan, which
    # receives the candidate evidence required by validate_checks.
    pending = [p for p in context.get('pending_concerns', [])
               if not p['concern'].startswith('release-audit-') or any(
                   p['concern'].startswith('release-audit-' + c['id'] + '-') for c in audit_candidates)]
    # The ledger and backlog plan are bookkeeping, not discovery evidence.
    shared = {k: v for k, v in context.items()
              if k not in ('commit_ledger', 'backlog_batches', 'publication_attempts')}
    return {**shared, "code_history": [f"{i}: {line}" for i, line in enumerate(history, 1)],
            "release_audit_candidates": audit_candidates,
            "pending_concerns": pending,
            "pending_commit_checks": [c for c in context.get('pending_commit_checks', [])
                                      if c['shard'] == slug and c['source_sha'] in required],
            "required_commit_ids": [i for i, line in enumerate(history, 1)
                                    if line.split()[0] in required],
            "focus_commit_ids": [i for i, line in enumerate(history, 1) if line.split()[0] in primary],
            "scan_mode": "backlog" if backlog else "daily",
            "shard": slug, "focus": focus,
            "scan_responsibilities": {name: focus for name, focus, _ in SHARDS}}


def scan_schema(scoped):
    """Make every frozen daily obligation a required output field.

    A free-form array can silently omit an inspected commit. Required keys let
    the SDK correct that omission before returning; our independent coverage
    and evidence guards still decide whether the assessment is complete.
    """
    schema = json.loads(Path(__file__).with_name('scan-schema.json').read_text())
    ids = [str(number) for number in scoped['required_commit_ids']]
    schema['properties']['daily_commit_checks'].update(
        properties={number: {'$ref': '#/definitions/daily_check'} for number in ids}, required=ids)
    schema['definitions']['daily_check']['properties']['depends_on']['items']['enum'] = [
        name for name in SHARD_NAMES if name != scoped.get('shard')]
    return schema


def main():
    command = sys.argv[1]
    root = Path(os.environ["RUNNER_TEMP"]) / "nightly-docs-context"
    context = json.loads((root / "context.json").read_text())
    context["source_root"] = os.environ["SOURCE_ROOT"]
    context["source_diffs"] = str(root / "nightly-docs-sources")
    context['release_audit_evidence'] = str(root / 'release-audits')
    if command == "partition":
        names = scan_names(context)  # Reject invalid manual inputs before network work.
        jobs, size = backlog_limits()
        repo = os.environ['GITHUB_REPOSITORY']
        prior = previous_report(repo, os.environ['DEFAULT_BRANCH'], context)
        feedback = publication_feedback(repo, prior)
        prs = context['existing_prs']
        carried, attempts, abandoned = daily_coverage.carry_forward(prior, prs, feedback)
        # Rejected or unpublished selections return first, with the reason.
        context['pending_concerns'] = pending_candidates(
            daily_coverage.retry_concerns(prior, prs, attempts, feedback)
            + pending_from_report(prior, context), context)[:docs.MAX_PRS]
        context['publication_attempts'] = attempts
        context['abandoned_concerns'] = abandoned
        assignments = partition(context)
        context['daily_assignments'] = daily_coverage.assign(context, prior, assignments, carried)
        context['commit_ledger'] = daily_coverage.ledger(prior, carried, abandoned, context['code_history'])
        context['backlog_batches'], context['backlog_remaining'] = daily_coverage.backlog(
            context, context['commit_ledger'], carried, assignments, names, jobs, size)
        assigned = {(slug, sha) for slug, shas in context['daily_assignments'].items() for sha in shas}
        assigned.update((b['shard'], sha) for b in context['backlog_batches'] for sha in b['commits'])
        context['pending_commit_checks'] = [c for c in carried if (c['shard'], c['source_sha']) in assigned]
        candidates = release_handoff.collect(context, root / 'release-audits')
        checked = {c['candidate_id']: c['status'] for c in prior.get('release_audit_checks', [])}
        # Unfinished and newly discovered corrections get attention first. Never
        # reuse an old disposition as approval against a different main revision.
        context['release_audit_candidates'] = sorted(candidates, key=lambda c: (
            checked.get(c['id']) not in (None, 'deferred', 'proposed'), c['release'], c['doc_path']))
        (root / 'context.json').write_text(json.dumps(context, indent=2))
        (root / "assignments.json").write_text(json.dumps(assignments))
        with open(os.environ["GITHUB_OUTPUT"], "a") as output:
            output.write("matrix=" + json.dumps({"include": [{"shard": slug} for slug in names] + [
                {"shard": b['shard'], "backlog": b['backlog']} for b in context['backlog_batches']]}) + "\n")
    elif command == "context":
        slug, backlog = os.environ["SHARD"], int(os.environ.get("BACKLOG_BATCH") or 0)
        assignments = json.loads((root / "assignments.json").read_text())
        scoped = scan_context(context, slug, assignments, backlog)
        Path(os.environ["NIGHTLY_CONTEXT"]).write_text(json.dumps(scoped, indent=2))
        encoded = json.dumps(scan_schema(scoped), separators=(',', ':'))
        # Fail explicitly rather than silently dropping mandatory IDs or hitting
        # Linux's per-argument limit when the action starts.
        if len(encoded.encode()) > 120000:
            raise ValueError('Daily coverage schema is too large for one scan; split the scan before retrying')
        with open(os.environ['GITHUB_OUTPUT'], 'a') as output:
            output.write('schema=' + encoded + '\n')
    elif command == "scan":
        slug, backlog = os.environ["SHARD"], int(os.environ.get("BACKLOG_BATCH") or 0)
        assignments = json.loads((root / "assignments.json").read_text())
        execution = {}
        raw = resolve_scan(read_scan_output(
            os.environ['SCAN_EXECUTION_FILE'], os.environ.get('SCAN_ACTION_OUTCOME', 'success'),
            int(os.environ.get('DISCOVERY_MAX_ROUNDS', '200')), execution), context["code_history"])
        scan = validate_scan(raw, context, slug, context["code_history"], backlog)
        scan['execution'] = execution
        Path(os.environ["SCAN_OUTPUT"]).write_text(json.dumps(scan))
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as summary:
            label = (f"{slug} backlog {backlog}: {len(daily_coverage.batch_commits(context, slug, backlog))} older"
                     if backlog else f"{slug}: {len(assignments[slug])} priority")
            summary.write(f"{label} commits; "
                          f"{len(scan['inspected_commits'])} self-reported inspected; "
                          f"{len(scan['concerns'])} proposed concerns.\n")
            summary.write(f"Verified {execution['assistant_rounds']} assistant rounds "
                          f"within the {execution['max_rounds']}-round limit. "
                          f"Upstream turn-counter recovery: {execution['recovered']}.\n")
    elif command == "combine":
        scans = [json.loads(path.read_text()) for path in Path(os.environ["SCAN_DIR"]).glob("*.json")]
        report = build_report(scans, context)
        selected, deferred = report['selected'], report['deferred']
        Path(os.environ["REPORT_OUTPUT"]).write_text(json.dumps(report, indent=2))
        with open(os.environ["GITHUB_OUTPUT"], "a") as output:
            output.write("matrix=" + json.dumps(docs.item_matrix(selected)) + "\n")
            output.write(f"count={len(selected)}\n")
            output.write(f"complete={str(report['complete']).lower()}\n")
            output.write("missing=" + ", ".join(report['missing_shards']) + "\n")
            output.write(f"uncovered={sum(c['status'] not in daily_coverage.COMPLETE for c in report['daily_coverage']['checks'])}\n")
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as summary:
            summary.write(f"Selected {len(selected)} independent concerns; deferred {len(deferred)}.\n\n")
            if report['missing_shards']:
                summary.write("**Incomplete discovery**: missing validated scans from "
                              + ", ".join(report['missing_shards']) + ".\n\n")
            summary.write(f"Retained {len(report['queued_concerns'])} pending concerns.\n\n")
            daily = report['daily_coverage']
            if daily['window']:
                window = daily['window']
                summary.write(f"Daily source window: {window['start']} to {window['end']} "
                              f"at `{context['source_sha']}`; {len(window['commits'])} recent mainline commits.\n\n")
            summary.write(f"Required commit/subsystem checks: {len(daily['checks'])}; "
                          + ', '.join(f"{sum(c['status'] == status for c in daily['checks'])} {status}"
                                      for status in ('proposed', 'covered', 'not_applicable', 'blocked', 'deferred', 'uninspected'))
                          + '. Includes unfinished checks and changes between runs.\n\n')
            for check in daily['checks']:
                if check['status'] not in daily_coverage.SETTLED:
                    summary.write(f"- Carry forward: `{check['source_sha']}` ({check['shard']}, {check['status']})\n")
            reopened = [c for c in context.get('pending_commit_checks', []) if c['status'] == 'unpublished']
            if reopened:
                summary.write(f"\nReopened {len(reopened)} commit checks whose proposals never reached a PR "
                              "(page conflict, review rejection, or no change).\n\n")
            backlog = report['backlog_coverage']
            summary.write(f"Older-commit backlog: {sum(backlog['remaining_before_run'].values())} unassessed "
                          f"commit/subsystem pairs before this run; {len(backlog['checks'])} assigned to "
                          f"{len(backlog['batches'])} backlog scans: "
                          + ', '.join(f"{sum(c['status'] == status for c in backlog['checks'])} {status}"
                                      for status in ('proposed', 'covered', 'not_applicable', 'blocked', 'deferred', 'uninspected'))
                          + '. Unfinished backlog pairs return next run without failing coverage.\n\n')
            if backlog['missing']:
                summary.write('Missing backlog scans (retried next run): ' + ', '.join(backlog['missing']) + '.\n\n')
            if report['abandoned_concerns']:
                summary.write(f"**Needs maintainer attention** (no PR after {daily_coverage.MAX_PUBLICATION_ATTEMPTS} "
                              "selections; no longer retried automatically):\n"
                              + ''.join(f"- `{a['concern']}` for `{a['source_sha']}` ({a['shard']}): "
                                        f"{a['reason'][:300]}\n" for a in report['abandoned_concerns']) + '\n')
            checks = report['release_audit_checks']
            summary.write(f"Release-audit handoff: {len(checks)} candidate pages; "
                          + ', '.join(f"{sum(c['status'] == status for c in checks)} {status}"
                                      for status in ('proposed', 'covered', 'not_applicable', 'deferred'))
                          + '. Dispositions are discovery evidence, not publication approval.\n\n')
            if report['queue_overflow']:
                summary.write("**Queue overflow** (still eligible through source history):\n"
                              + "".join(f"- `{key}`\n" for key in report['queue_overflow']) + "\n")
            summary.write("| Scan | Priority commits | Reported inspected | Proposals |\n| --- | ---: | ---: | ---: |\n")
            assignments = partition(context)
            for scan in scans:
                name, priority = scan['shard'], len(assignments[scan['shard']])
                if scan.get('backlog'):
                    name = f"{name}/backlog-{scan['backlog']}"
                    priority = len(daily_coverage.batch_commits(context, scan['shard'], scan['backlog']))
                summary.write(f"| {name} | {priority} | "
                              f"{len(scan['inspected_commits'])} | {len(scan['concerns'])} |\n")
    else:
        raise ValueError("Unknown discovery command")


def read_scan_output(path, action_outcome='success', max_rounds=200, details=None):
    """Read a successful, bounded trace; never infer approval from step status.

    The pinned action's post-hoc guard compares tool-result counts with SDK
    API rounds (anthropics/claude-code-action#1795). Recover only that mismatch,
    after checking distinct assistant message IDs and all ordinary result gates.
    The workflow clears the known execution path before invoking the action.
    """
    messages = json.loads(Path(path).read_text())
    if not isinstance(messages, list) or not messages:
        raise ValueError('Missing discovery execution result')
    result = messages[-1]
    if (result.get('type') != 'result' or result.get('subtype') != 'success'
            or result.get('is_error') is not False
            or not isinstance(result.get('structured_output'), dict)):
        raise ValueError('Discovery did not finish with successful structured output')
    assistants = [m.get('message', {}).get('id') for m in messages if m.get('type') == 'assistant']
    if (not assistants or any(not isinstance(i, str) or not i for i in assistants)
            or type(max_rounds) is not int or max_rounds < 1):
        raise ValueError('Discovery trace lacks a verifiable assistant-round count')
    rounds = len(set(assistants))
    if rounds > max_rounds:
        raise ValueError(f'Discovery exceeded its API-round budget: {rounds} > {max_rounds}')
    reported = result.get('num_turns')
    if action_outcome != 'success':
        if (action_outcome != 'failure' or type(reported) is not int or reported <= max_rounds):
            raise ValueError('Discovery action failed without the known turn-counter mismatch')
        print(f'Recovered upstream turn-counter mismatch: {reported} reported turns, '
              f'{rounds} assistant rounds within the {max_rounds}-round limit.')
    else:
        print(f'Validated discovery trace: {rounds} assistant rounds within the {max_rounds}-round limit.')
    if details is not None:
        details.update(assistant_rounds=rounds, max_rounds=max_rounds, reported_turns=reported,
                       action_outcome=action_outcome, recovered=action_outcome == 'failure')
    return json.dumps(result['structured_output'])


if __name__ == "__main__":
    main()
