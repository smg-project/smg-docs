# Adapted from ome-projects/ome at 776fbe3b4be9e826be3404699886543d83e8e380.
# Partial-discovery recovery adapted from OME at 4221c9a9bc056fc10293e8007be0c5f8c9e1343d.
# SMG cross-repository discovery and publication changes; Apache-2.0 (LICENSE).
"""Partition discovery and fairly combine independently validated scan results."""

from datetime import datetime, timedelta, timezone
from itertools import zip_longest
import json
import os
from pathlib import Path
import sys
import tempfile
from urllib.parse import urlencode

import nightly_docs as docs


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
    return json.dumps({"concerns": concerns,
                       "inspected_commits": [resolve(number) for number in result["inspected_commits"]],
                       "remaining_work": result["remaining_work"]})


def validate_scan(raw, context, slug, history):
    result = json.loads(raw)
    inspected = result["inspected_commits"]
    candidates = {line.split()[0] for line in history}
    if (not isinstance(inspected, list) or any(not isinstance(sha, str) for sha in inspected)
            or len(inspected) != len(set(inspected)) or not set(inspected) <= candidates):
        raise ValueError("Invalid inspected-commit report")
    if not isinstance(result["remaining_work"], str) or not result["remaining_work"].strip():
        raise ValueError("Missing remaining-work report")
    concerns = result["concerns"]
    # Validate each independently. Competing proposals are deferred centrally,
    # not an operational failure that discards a whole scan's useful results.
    for item in concerns:
        docs.plan(json.dumps({"concerns": [item]}), {**context, "code_history": history})
        if item["source_sha"] not in inspected:
            raise ValueError("Concern source was not reported as inspected")
    if len(concerns) > docs.MAX_PRS:
        raise ValueError("Scan exceeds proposal limit")
    return {"shard": slug, "base_sha": context["base_sha"], "concerns": concerns,
            "inspected_commits": inspected, "remaining_work": result["remaining_work"]}


def scan_names(context):
    """Select all scans by default, or one explicitly requested validation scan."""
    names = [slug for slug, _, _ in SHARDS]
    requested = context.get("discovery_shard", "")
    if requested and requested not in names:
        raise ValueError("Unknown discovery shard")
    return [requested] if requested else names


def combine(scans, context, allow_partial=False):
    expected = scan_names(context)
    by_slug = {scan["shard"]: scan for scan in scans}
    if len(by_slug) != len(scans) or not set(by_slug) <= set(expected):
        raise ValueError("Unknown or duplicate discovery scans")
    if not allow_partial and set(by_slug) != set(expected):
        raise ValueError("Missing or duplicate discovery scans")
    assignments = partition(context)
    for slug, scan in by_slug.items():
        if scan["base_sha"] != context["base_sha"]:
            raise ValueError("Discovery baseline mismatch")
        validate_scan(json.dumps(scan), context, slug, context["code_history"])
    selected, occupied, keys, identities, questions, deferred = [], set(), set(), set(), set(), []
    # Round robin prevents the first large subsystem from consuming the cap.
    for row in zip_longest(*(by_slug[slug]["concerns"] for slug in expected if slug in by_slug)):
        for proposal in row:
            if proposal is None:
                continue
            title = proposal["title"]
            proposal = {**proposal, "title": title if title.startswith("[Docs] ") else "[Docs] " + title}
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
            title = proposal['title']
            item = docs.validate_item({**proposal, 'title': title if title.startswith('[Docs] ') else '[Docs] ' + title})
            if item['key'] not in queued:
                continue
            # Keep file-blocked work, but never queue an already-open,
            # merged, or deliberately declined instance of this concern.
            if any(f"{docs.MARKER}{item['key']} -->" in pr['body'] or item['branch'] == pr['branch']
                   for pr in prs):
                continue
            queued_concerns.append(item)
    return queued_concerns


def build_report(scans, context):
    """Keep validated partial work while explicitly reporting missing coverage."""
    selected, deferred = combine(scans, context, allow_partial=True)
    expected = scan_names(context)
    received = {scan['shard'] for scan in scans}
    missing = [name for name in expected if name not in received]
    queued = deferred_queue(scans, deferred, context['existing_prs'])
    # Daily quota can reduce each scan's proposal budget even when every scan
    # succeeds. Such a run cannot retire prior work merely by omitting it.
    quota_limited = context.get('max_prs', docs.MAX_PRS) < context.get('requested_max_prs', docs.MAX_PRS)
    if missing or quota_limited:
        selected_ids = {(item['area'], item['concern']) for item in selected}
        prior = [item for item in context.get('pending_concerns', [])
                 if (item['area'], item['concern']) not in selected_ids]
        queued = prior + queued
    queued = pending_candidates(queued, context)
    return {'base_sha': context['base_sha'], 'source_sha': context['source_sha'],
            'source_repo': docs.SOURCE_REPO, 'initial_since': docs.INITIAL_SINCE,
            'eligible_commits': len(context['code_history']),
            'available_pr_slots': context.get('max_prs', docs.MAX_PRS),
            'requested_max_prs': context.get('requested_max_prs', docs.MAX_PRS),
            'discovery_shard': context.get('discovery_shard', ''),
            'scans': scans, 'selected': selected,
            'deferred': deferred, 'queued_concerns': queued[:docs.MAX_PRS],
            'queue_overflow': [item['key'] for item in queued[docs.MAX_PRS:]],
            'expected_shards': expected, 'missing_shards': missing, 'complete': not missing,
            'doc_inventory': context.get('doc_inventory', []),
            'dry_run': context.get('dry_run', False), 'max_prs': context.get('max_prs', docs.MAX_PRS)}


def pending_candidates(proposals, context):
    """Validate and deduplicate pending evidence before applying the queue cap."""
    history = {line.split()[0] for line in context['code_history']}
    result, seen = [], set()
    for proposal in proposals:
        item = docs.validate_item(proposal)
        identity = (item['area'], item['concern'])
        if any(f"{docs.MARKER}{item['key']} -->" in pr['body'] or item['branch'] == pr['branch']
               for pr in context.get('existing_prs', [])):
            continue
        if item['source_sha'] in history and identity not in seen:
            seen.add(identity)
            result.append(item)
    return result


def pending_from_report(report, context):
    """Carry a bounded queue as evidence for fresh discovery."""
    return pending_candidates(report.get('queued_concerns', []), context)[:docs.MAX_PRS]


def previous_pending(repo, branch, context):
    """Read the latest retained main-branch plan, never another branch's pilot."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=14)).date().isoformat()
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
        return pending_from_report(report, context)
    return []


def scan_context(context, slug, assignments):
    # Path ownership is a prioritization hint, not a source-attribution boundary.
    # Cross-cutting wiring (e.g. app_context.rs enabling MCP config) can introduce
    # subsystem behavior without touching that subsystem's directory.
    focus = next(focus for name, focus, _ in SHARDS if name == slug)
    primary = {line.split()[0] for line in assignments[slug]}
    history = context["code_history"]
    return {**context, "code_history": [f"{i}: {line}" for i, line in enumerate(history, 1)],
            "focus_commit_ids": [i for i, line in enumerate(history, 1) if line.split()[0] in primary],
            "shard": slug, "focus": focus,
            "scan_responsibilities": {name: focus for name, focus, _ in SHARDS}}


def main():
    command = sys.argv[1]
    root = Path(os.environ["RUNNER_TEMP"]) / "nightly-docs-context"
    context = json.loads((root / "context.json").read_text())
    context["source_root"] = os.environ["SOURCE_ROOT"]
    context["source_diffs"] = str(root / "nightly-docs-sources")
    if command == "partition":
        scan_names(context)  # Reject invalid manual inputs before network work.
        context['pending_concerns'] = previous_pending(
            os.environ['GITHUB_REPOSITORY'], os.environ['DEFAULT_BRANCH'], context)
        (root / 'context.json').write_text(json.dumps(context, indent=2))
        assignments = partition(context)
        (root / "assignments.json").write_text(json.dumps(assignments))
        with open(os.environ["GITHUB_OUTPUT"], "a") as output:
            output.write("matrix=" + json.dumps({"include": [{"shard": slug} for slug in scan_names(context)]}) + "\n")
    elif command == "context":
        slug = os.environ["SHARD"]
        assignments = json.loads((root / "assignments.json").read_text())
        scoped = scan_context(context, slug, assignments)
        Path(os.environ["NIGHTLY_CONTEXT"]).write_text(json.dumps(scoped, indent=2))
    elif command == "scan":
        slug = os.environ["SHARD"]
        assignments = json.loads((root / "assignments.json").read_text())
        raw = resolve_scan(os.environ["PLAN_JSON"], context["code_history"])
        scan = validate_scan(raw, context, slug, context["code_history"])
        Path(os.environ["SCAN_OUTPUT"]).write_text(json.dumps(scan))
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as summary:
            summary.write(f"{slug}: {len(assignments[slug])} priority commits; "
                          f"{len(scan['inspected_commits'])} self-reported inspected; "
                          f"{len(scan['concerns'])} proposed concerns.\n")
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
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as summary:
            summary.write(f"Selected {len(selected)} independent concerns; deferred {len(deferred)}.\n\n")
            if report['missing_shards']:
                summary.write("**Incomplete discovery**: missing validated scans from "
                              + ", ".join(report['missing_shards']) + ".\n\n")
            summary.write(f"Retained {len(report['queued_concerns'])} pending concerns.\n\n")
            if report['queue_overflow']:
                summary.write("**Queue overflow** (still eligible through source history):\n"
                              + "".join(f"- `{key}`\n" for key in report['queue_overflow']) + "\n")
            summary.write("| Scan | Priority commits | Reported inspected | Proposals |\n| --- | ---: | ---: | ---: |\n")
            assignments = partition(context)
            for scan in scans:
                summary.write(f"| {scan['shard']} | {len(assignments[scan['shard']])} | "
                              f"{len(scan['inspected_commits'])} | {len(scan['concerns'])} |\n")
    else:
        raise ValueError("Unknown discovery command")


if __name__ == "__main__":
    main()
