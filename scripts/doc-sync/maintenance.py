# Adapted from ome-projects/ome-docs at 7a77080bf6eb076bebb57a8885792d47aa528494.
# SMG cross-repository maintenance changes; Apache-2.0 (LICENSE).
"""Reconcile existing nightly documentation PRs without bypassing review policy."""

import base64
from concurrent.futures import ThreadPoolExecutor
import hashlib
import html
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

import nightly_docs as docs

STATE = "<!-- docs-maintenance-state:"
CHECK = "Docs maintenance"
BOTS = {"claude", "coderabbitai"}
TRUSTED_ASSOCIATIONS = {'OWNER', 'MEMBER', 'COLLABORATOR'}
MAX_ATTEMPTS = 3
MAX_INFRASTRUCTURE_ATTEMPTS = 3


def reject_secrets(text):
    """Reject known live credentials and recognizable token literals without echoing them."""
    values = [os.getenv(name, '') for name in ('ANTHROPIC_API_KEY', 'GH_TOKEN', 'GITHUB_TOKEN')]
    if (any(len(value) >= 8 and value in text for value in values)
            or re.search(r'(?:sk-ant-|gh[pousr]_|github_pat_)[A-Za-z0-9_-]{20,}', text)):
        raise ValueError('Credential-like content detected; refusing public output')


def trusted_feedback(author, association):
    """Only maintainers and authenticated review bots can spend model budget."""
    return bool(author) and (association in TRUSTED_ASSOCIATIONS or (
        (author.get('__typename') or author.get('type')) == 'Bot'
        and author['login'].removesuffix('[bot]') in BOTS))


def api(endpoint, method="GET", payload=None):
    """Use structured input; no PR-controlled text is interpolated into shell."""
    args = ["gh", "api", endpoint, "--method", method]
    if payload is not None:
        args += ["--input", "-"]
    output = subprocess.check_output(args, input=json.dumps(payload) if payload is not None else None,
                                     text=True)
    return json.loads(output) if output.strip() else None


def repo():
    """The workflow is deliberately restricted to this repository."""
    value = os.environ["GITHUB_REPOSITORY"]
    if value != "smg-project/smg-docs":
        raise ValueError("Unsupported repository")
    return value


def current_base():
    """PR base.sha can lag branch updates; read the live main ref explicitly."""
    return api(f"repos/{repo()}/git/ref/heads/main")["object"]["sha"]


def current_code():
    """Read the immutable source revision separately from the documentation base."""
    return api(f"repos/{docs.SOURCE_REPO}/git/ref/heads/main")["object"]["sha"]


def checkout_code(sha):
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError("Expected an immutable SMG source commit")
    if docs.source_git("rev-parse", "HEAD") != sha:
        docs.source_git("fetch", "--no-tags", "origin", sha)
        docs.source_git("checkout", "--detach", sha)
    return sha


def get_pr(number):
    """Keep PR identity metadata, but pin source freshness to the actual branch."""
    pr = api(f"repos/{repo()}/pulls/{number}")
    pr["base"]["sha"] = current_base()
    pr["code_sha"] = current_code()
    return pr


def eligible(pr):
    """Authenticate the original publisher, repository, branch and concern marker."""
    body = pr.get("body") or ""
    match = re.match(re.escape(docs.MARKER) + r"([0-9a-f]{40}):(" + docs.SLUG + r"):(" + docs.SLUG + r") -->", body)
    if (not match or pr["state"] != "open"
            or pr["user"]["login"] != "github-actions[bot]"
            or pr["head"]["repo"] is None
            or pr["head"]["repo"]["full_name"] != repo()
            or pr["base"]["repo"]["full_name"] != repo()
            or pr["base"]["ref"] != "main"):
        raise ValueError("Not an eligible, open, same-repository nightly docs PR")
    key = ":".join(match.groups())
    expected = "codex/nightly-docs-" + hashlib.sha256(key.encode()).hexdigest()[:16]
    if pr["head"]["ref"] != expected:
        raise ValueError("PR branch does not match its original concern")
    return match.groups()


def decode_state(comments):
    """Only our bot's exact state marker can carry retry bookkeeping."""
    matches = [c for c in comments if c["user"]["login"] == "github-actions[bot]"
               and c["body"].startswith(STATE)]
    if len(matches) > 1:
        raise ValueError("Multiple maintenance state comments require inspection")
    if not matches:
        return {}, None
    comment = matches[0]
    encoded = comment["body"][len(STATE):].split(" -->", 1)[0]
    state = json.loads(base64.b64decode(encoded, validate=True))
    if type(state.get("attempts")) is not int or not 0 <= state["attempts"] <= MAX_ATTEMPTS:
        raise ValueError("Invalid maintenance attempt counter")
    infrastructure = state.get('infrastructure_attempts', 0)
    if type(infrastructure) is not int or not 0 <= infrastructure <= MAX_INFRASTRUCTURE_ATTEMPTS:
        raise ValueError("Invalid infrastructure attempt counter")
    return state, comment["id"]


def substantive_comment(comment):
    """Bookkeeping and bot skip notices are not new review feedback."""
    author, body = comment["user"]["login"], comment["body"]
    if not trusted_feedback(comment['user'], comment.get('author_association')):
        return False
    if author == "github-actions[bot]" and body.startswith(STATE):
        return False
    if (author == "coderabbitai[bot]"
            and body.startswith("<!-- This is an auto-generated comment: summarize by coderabbit.ai -->\n"
                                "<!-- This is an auto-generated comment: skip review by coderabbit.ai -->")):
        return False
    return True


def change_requests(reviews):
    """A comment-only review does not dismiss an earlier change request."""
    latest = {}
    for review in sorted(reviews, key=lambda review: review['id']):
        if review['state'] in {'APPROVED', 'CHANGES_REQUESTED', 'DISMISSED'}:
            latest[review['user']['login']] = review['state']
    return sorted(author for author, state in latest.items() if state == 'CHANGES_REQUESTED')


def feedback(pr):
    """Read all feedback, including unresolved threads, with bounded graph pages."""
    number = pr["number"]
    comments = docs.pages(f"repos/{repo()}/issues/{number}/comments?per_page=100")
    state, state_id = decode_state(comments)
    reviews = docs.pages(f"repos/{repo()}/pulls/{number}/reviews?per_page=100")
    owner, name = repo().split("/")
    threads, cursor, unresolved, protected = [], None, [], []
    query = """query($owner:String!,$name:String!,$number:Int!,$cursor:String){
      repository(owner:$owner,name:$name){pullRequest(number:$number){
        reviewThreads(first:100,after:$cursor){pageInfo{hasNextPage endCursor}
          nodes{id isResolved isOutdated path line comments(first:100){
            pageInfo{hasNextPage} nodes{author{__typename login} authorAssociation body url}}}}
      }}}"""
    while True:
        data = api("graphql", "POST", {"query": query, "variables": {
            "owner": owner, "name": name, "number": number, "cursor": cursor}})
        connection = data["data"]["repository"]["pullRequest"]["reviewThreads"]
        for thread in connection["nodes"]:
            if thread["isResolved"]:
                continue
            unresolved.append(thread['id'])
            if thread["comments"]["pageInfo"]["hasNextPage"]:
                raise ValueError("A review thread exceeds 100 comments; human triage required")
            comments_in_thread = thread['comments']['nodes']
            accepted_comments = [c for c in comments_in_thread
                                 if trusted_feedback(c.get('author'), c.get('authorAssociation'))]
            if len(accepted_comments) != len(comments_in_thread):
                protected.append(thread['id'])
            if accepted_comments:
                threads.append({**thread, "comments": accepted_comments, "number": len(threads) + 1})
        if not connection["pageInfo"]["hasNextPage"]:
            break
        cursor = connection["pageInfo"]["endCursor"]
    failed_checks = [{"name": c["name"], "conclusion": c["conclusion"],
                      "url": c["details_url"], "output": c["output"]}
                     for c in check_runs(pr["head"]["sha"])
                     if c["name"] != CHECK and c["conclusion"] in
                     {"failure", "timed_out", "cancelled", "action_required", "startup_failure"}]
    details = {"comments": [{"id": c["id"], "author": c["user"]["login"], "body": c["body"]}
                            for c in comments if substantive_comment(c)],
               "reviews": [{"id": r["id"], "author": r["user"]["login"], "body": r["body"],
                            "state": r["state"], "commit": r["commit_id"]}
                           for r in reviews if trusted_feedback(r['user'], r.get('author_association'))
                           and (r["body"] or r["state"] == "CHANGES_REQUESTED")],
               "threads": threads, "failed_checks": failed_checks,
               "unresolved_threads": unresolved, "protected_threads": protected,
               "changes_requested": change_requests(reviews)}
    if len(json.dumps(details).encode()) > 2 * 1024 * 1024:
        raise ValueError("Feedback exceeds 2 MiB; human triage required")
    return details, state, state_id


def check_runs(head):
    """Read every check page at this immutable PR commit."""
    raw = json.loads(docs.run("gh", "api", f"repos/{repo()}/commits/{head}/check-runs?per_page=100",
                              "--paginate", "--slurp"))
    return [check for page in raw for check in page["check_runs"]]


def stable_thread(thread):
    """Compare feedback without positions GitHub recomputes after a repair push."""
    return {key: thread.get(key) for key in ('id', 'path', 'comments')}


def signature(pr, details, extra=""):
    """A cached review is invalidated by content, base, or substantive feedback."""
    substantive = {key: value for key, value in details.items()
                   if key not in {'unresolved_threads', 'protected_threads'}}
    substantive['threads'] = [stable_thread(t) for t in details.get('threads', [])]
    # Resolving even an untrusted thread can unblock draft promotion. Track IDs,
    # never feed untrusted comment bodies to the model.
    if pr.get('draft'):
        substantive['unresolved_threads'] = sorted(details.get('unresolved_threads', []))
    return hashlib.sha256(json.dumps(["reader-docs-v2", pr["head"]["sha"], pr["base"]["sha"],
                                     pr["code_sha"], pr.get("title"), pr.get("body"), pr.get("draft"), substantive, extra], sort_keys=True).encode()).hexdigest()


def decision(state, digest, force=False):
    """Bound unsuccessful rounds without resetting the budget on our own push."""
    if force:
        return "work"
    if state.get("phase") == "ready" and state.get("signature") == digest:
        return "cached"
    if (state.get("attempts", 0) >= MAX_ATTEMPTS
            or state.get('infrastructure_attempts', 0) >= MAX_INFRASTRUCTURE_ATTEMPTS):
        return "needs-human"
    return "work"


def state_body(state):
    """Expose a single readable status with machine-readable retry history."""
    state = dict(state)
    for key, limit in [('reason', 4000), ('extra_feedback', 2000)]:
        if key in state:
            state[key] = state[key][:limit]
    reject_secrets(json.dumps(state))
    encoded = base64.b64encode(json.dumps(state, ensure_ascii=False).encode()).decode()
    return (f"{STATE}{encoded} -->\n"
            f"Documentation maintenance: **{state['phase']}**. "
            f"Unsuccessful content rounds: {state['attempts']}/{MAX_ATTEMPTS}. "
            f"Operational attempts: {state.get('infrastructure_attempts', 0)}/{MAX_INFRASTRUCTURE_ATTEMPTS}.\n\n"
            f"Head: `{state['head']}`; reviewed main: `{state['base']}`.\n"
            f"Reviewed SMG source: `{state.get('code_sha', 'unavailable')}`.\n\n"
            f"<pre>{html.escape(state.get('reason', ''))}</pre>\n\n"
            f"[Workflow evidence]({state['run_url']})\n\n"
            "Human review threads and CODEOWNER approval remain under repository policy.")


def save_state(ctx, state):
    """Update one status comment; never post repeated feedback chatter."""
    body = {"body": state_body(state)}
    if ctx.get("state_id"):
        api(f"repos/{repo()}/issues/comments/{ctx['state_id']}", "PATCH", body)
    else:
        result = api(f"repos/{repo()}/issues/{ctx['number']}/comments", "POST", body)
        ctx["state_id"] = result["id"]


def output(**values):
    """Write compact workflow outputs, never multiline model-controlled values."""
    with open(os.environ["GITHUB_OUTPUT"], "a") as stream:
        for key, value in values.items():
            stream.write(f"{key}={json.dumps(value) if not isinstance(value, str) else value}\n")


def apply_mode(inputs, event):
    """Honor manual apply inputs; only scheduled sweeps publish by default."""
    if inputs is None:
        inputs = {}
    if not isinstance(inputs, dict):
        raise ValueError('Workflow inputs must be an object')
    if 'apply' in inputs:
        if type(inputs['apply']) is not bool:
            raise ValueError('apply must be a boolean')
        return inputs['apply']
    return event == 'schedule'


def select(number, force):
    """Reconcile all eligible PRs on each wake so replaced queued events are safe."""
    if not number and (force or os.getenv('EXTRA_FEEDBACK')):
        raise ValueError('feedback and force require an explicit PR number')
    if len(os.getenv('EXTRA_FEEDBACK', '')) > 2000:
        raise ValueError('Additional feedback is limited to 2000 characters')
    prs = ([get_pr(int(number))] if number else
           docs.pages(f"repos/{repo()}/pulls?state=open&per_page=100"))
    base, code = current_base(), current_code()
    for pr in prs:
        pr["base"]["sha"] = base
        pr["code_sha"] = code
    prs = list({pr["number"]: pr for pr in prs}.values())

    def candidate(pr):
        if pr['state'] != 'open':
            print(f"Skipping closed PR #{pr['number']}")
            return None
        try:
            eligible(pr)
            details, state, _ = feedback(pr)
        except ValueError as error:
            if number:
                raise
            print(f"Skipping PR #{pr['number']}: {type(error).__name__}; inspect it with an explicit dispatch")
            return None
        extra = os.getenv("EXTRA_FEEDBACK") or state.get("extra_feedback", "")
        action = decision(state, signature(pr, details, extra), force)
        if action == "needs-human" or action == "cached":
            return None
        return {"number": pr["number"]}

    with ThreadPoolExecutor(max_workers=4) as pool:
        selected = [item for item in pool.map(candidate, prs) if item is not None]
    # Work oldest first, up to 100 existing PRs per sweep.
    selected.sort(key=lambda item: item["number"])
    selected = [{"number": item["number"]} for item in selected[:100]]
    output(matrix={"include": selected}, count=str(len(selected)))


def context(pr):
    """Verify the full PR diff, and overlay data on trusted current main."""
    source, area, concern = eligible(pr)
    base, head = pr["base"]["sha"], pr["head"]["sha"]
    if any(not re.fullmatch(r"[0-9a-f]{40}", sha) for sha in (base, head)):
        raise ValueError("Expected immutable commits")
    docs.mutate_git("fetch", "--no-tags", "origin", base, head)
    # Verify the original concern against an immutable snapshot of current SMG.
    code = checkout_code(pr["code_sha"])
    try:
        docs.source_git("merge-base", "--is-ancestor", source, code)
    except subprocess.CalledProcessError as error:
        if error.returncode == 1:
            raise ValueError('The source commit is not in the pinned SMG history') from error
        raise
    source_patch = docs.source_patch(source)
    if len(source_patch.encode()) > 2 * 1024 * 1024:
        raise ValueError("Source patch exceeds 2 MiB; human evidence review required")
    fork = docs.git("merge-base", base, head)
    files = {}
    for line in docs.git("diff", "--name-status", fork, head).splitlines():
        status, path = line.split("\t", 1)
        if status not in {"A", "M"} or not docs.doc_path(path):
            raise ValueError("The entire PR must contain only added/modified authored docs")
        if docs.git("ls-tree", head, "--", path).split()[0] != "100644":
            raise ValueError("Symlinks and executable documentation are forbidden")
        files[path] = subprocess.check_output(
            ["git", "-c", "core.hooksPath=/dev/null", "show", f"{head}:{path}"], text=True)
    if not files:
        raise ValueError("No documentation changes remain")
    advanced = set(docs.git("diff", "--name-only", fork, base).splitlines())
    if advanced.intersection(files):
        raise ValueError("Main changed the same documentation; human conflict resolution required")
    details, state, state_id = feedback(pr)
    extra = os.getenv("EXTRA_FEEDBACK") or state.get("extra_feedback", "")
    item = docs.validate_item({"area": area, "concern": concern, "source_sha": source,
                              "title": "[Docs] " + concern.replace("-", " "), "question": pr["body"],
                              "evidence": "Original PR body and current source", "doc_paths": sorted(files),
                              "placement": {"examined_pages": sorted(page["path"] for page in docs.doc_inventory(base)),
                                  "canonical_pages": sorted(files.keys() & {
                                  page["path"] for page in docs.doc_inventory(base)}),
                                  "new_page_reason": "Preserve the original concern's page scope; reviewer must verify placement."}})
    return {"number": pr["number"], "head": head, "base": base, "code_sha": code, "item": item,
            "files": files, "source_patch": source_patch.splitlines(), "feedback": details, "state": state, "state_id": state_id,
            "extra_feedback": extra, "signature": signature(pr, details, extra),
            "tools_sha": os.environ["GITHUB_SHA"],
            "run_url": f"https://github.com/{repo()}/actions/runs/{os.environ['GITHUB_RUN_ID']}"}


def restore(ctx, bundle=None):
    """Import only validated markdown into a pristine trusted main checkout."""
    docs.mutate_git("checkout", "--detach", ctx["base"])
    # Every worker uses the same source snapshot selected during preparation.
    checkout_code(ctx["code_sha"])
    payload = bundle or json.dumps({"base_sha": ctx["base"], "key": ctx["item"]["key"],
                                   "files": ctx["files"]})
    changed = docs.import_bundle(ctx["item"], ctx["base"], payload)
    # Existing PRs may violate the writing policy: allow the original overlay so
    # the writer can repair it, but enforce the policy on every repaired bundle.
    if bundle is not None:
        docs.validate_reader_docs(ctx["base"])
    return changed


def prepare(number, directory, apply, force):
    """Reserve an operational attempt; charge content only after validation finishes."""
    pr = get_pr(number)
    eligible(pr)
    try:
        ctx = context(pr)
        reject_secrets(json.dumps(ctx))
        restore(ctx)
    except ValueError as error:
        if apply:
            _, state_id = decode_state(docs.pages(f"repos/{repo()}/issues/{number}/comments?per_page=100"))
            rejected = {'number': number, 'state_id': state_id, 'base': pr['base']['sha'],
                        'run_url': f"https://github.com/{repo()}/actions/runs/{os.environ['GITHUB_RUN_ID']}"}
            save_state(rejected, {'phase': 'needs-human', 'attempts': MAX_ATTEMPTS,
                                  'head': pr['head']['sha'], 'base': pr['base']['sha'],
                                  'reason': str(error), 'run_url': rejected['run_url']})
            record_check(rejected, pr['head']['sha'], False, str(error))
        raise
    action = decision(ctx["state"], ctx["signature"], force)
    directory.mkdir(parents=True, exist_ok=True)
    ctx["action"] = action
    if action == "work":
        attempts = 1 if force else ctx["state"].get("attempts", 0) + 1
        ctx["attempts"] = attempts
        ctx['infrastructure_attempts'] = (1 if force else
                                          ctx['state'].get('infrastructure_attempts', 0) + 1)
        if apply:
            save_state(ctx, {"phase": "working", "attempts": attempts - 1,
                             "infrastructure_attempts": ctx['infrastructure_attempts'], "head": ctx["head"],
                             "base": ctx["base"], "code_sha": ctx["code_sha"], "run_url": ctx["run_url"],
                             "extra_feedback": ctx["extra_feedback"],
                             "reason": "Repair/validation in progress; no merge authorization implied."})
    reject_secrets(json.dumps(ctx))
    (directory / "context.json").write_text(json.dumps(ctx, indent=2))
    output(work=str(action == "work").lower(), cached=str(action == "cached").lower(), base=ctx["base"], code=ctx["code_sha"])


def live_match(ctx):
    """Reject stale writers rather than overwrite a new commit or ignore feedback."""
    # Source is immutable for this round, like nightly discovery. Advancing the
    # external source branch invalidates the next sweep's cache, not this review.
    pr = {**get_pr(ctx['number']), 'code_sha': ctx['code_sha']}
    eligible(pr)
    details, _, _ = feedback(pr)
    if (pr["head"]["sha"] != ctx["head"] or pr["base"]["sha"] != ctx["base"]
            or signature(pr, details, ctx["extra_feedback"]) != ctx["signature"]):
        raise ValueError("PR head, main, or feedback changed; discard this stale attempt")
    return pr


def publish_repair(ctx):
    """Append a normal commit, carrying current main, with no force push."""
    live_match(ctx)
    reviewed_tree = docs.git("write-tree")
    final = {path: Path(path).read_text() if Path(path).exists() else None
             for path in ctx["item"]["doc_paths"]}
    docs.mutate_git("reset", "--hard", ctx["base"])
    docs.mutate_git("checkout", "-B", "docs-maintenance-work", ctx["head"])
    docs.git("config", "user.name", "github-actions[bot]")
    docs.git("config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com")
    docs.mutate_git("merge", "--no-ff", "--no-commit", ctx["base"])
    for path, content in final.items():
        if content is None:
            if docs.git("ls-tree", ctx["base"], "--", path):
                raise ValueError("Refusing to delete a file from main")
            docs.mutate_git("rm", "--ignore-unmatch", "--", path)
        else:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            Path(path).write_text(content)
            docs.mutate_git("add", "--", path)
    if docs.git("write-tree") != reviewed_tree:
        raise ValueError("Publication tree differs from the validated tree")
    if reviewed_tree == docs.git("rev-parse", "HEAD^{tree}"):
        return ctx["head"]
    docs.mutate_git("commit", "-s", "-m", f"docs: address feedback for #{ctx['number']}")
    # The normal push is the final compare-and-swap: a competing commit rejects it.
    live_match(ctx)
    docs.mutate_git("push", "origin", f"HEAD:refs/heads/{ctx['item']['branch']}")
    return docs.git("rev-parse", "HEAD")


def checked_threads(verdict, ctx):
    """Resolve only explicitly verified bot-only threads; never human discussions."""
    numbers = verdict.get("addressed_threads", [])
    threads = ctx["feedback"]["threads"]
    if (not isinstance(numbers, list) or any(type(n) is not int or not 1 <= n <= len(threads)
                                           for n in numbers) or len(numbers) != len(set(numbers))):
        raise ValueError("Invalid addressed-thread report")
    return [threads[n - 1]["id"] for n in numbers
            if threads[n - 1]['id'] not in ctx['feedback'].get('protected_threads', [])
            and threads[n - 1]["comments"] and all(
                c.get("author") and c["author"].get("__typename") == "Bot"
                and c["author"]["login"].removesuffix("[bot]") in BOTS
                for c in threads[n - 1]["comments"])]


def record_check(ctx, head, accepted, reason):
    """Attach the verdict to the actual PR commit, not the dispatcher commit."""
    reject_secrets(reason)
    api(f"repos/{repo()}/check-runs", "POST", {
        "name": CHECK, "head_sha": head, "status": "completed",
        "conclusion": "success" if accepted else "failure", "details_url": ctx["run_url"],
        "external_id": f"docs-maintenance:{ctx['number']}:{ctx['base']}",
        "output": {"title": "Validated documentation" if accepted else "Documentation needs repair",
                   "summary": f"Reviewed SMG source: `{ctx.get('code_sha', 'unavailable')}`.\n\n" + reason[:59000]}})


def promote_draft(ctx, current, expected_details):
    """Promote only the validated head after every review discussion is resolved."""
    if not current.get('draft'):
        return False, 'PR is already ready for review.'
    fresh, _, _ = feedback(current)
    if fresh['unresolved_threads'] or fresh.get('changes_requested'):
        return False, 'Draft retained: unresolved review threads or outstanding change requests.'
    pinned = {**current, 'code_sha': ctx['code_sha']}
    if (fresh.get('failed_checks')
            or signature(pinned, fresh, ctx['extra_feedback']) !=
            signature(pinned, expected_details, ctx['extra_feedback'])):
        return False, 'Draft retained: feedback or checks changed after validation.'
    # Recheck the head/base and PR metadata immediately before the mutation.
    latest = published_pr(ctx, current['head']['sha'])
    if signature({**latest, 'code_sha': ctx['code_sha']}, fresh, ctx['extra_feedback']) != signature(
            pinned, fresh, ctx['extra_feedback']):
        return False, 'Draft retained: PR metadata changed after validation.'
    response = api('graphql', 'POST', {
        'query': 'mutation($id:ID!){markPullRequestReadyForReview(input:{pullRequestId:$id})'
                 '{pullRequest{id isDraft headRefOid}}}',
        'variables': {'id': latest['node_id']}})
    if response.get('errors'):
        raise ValueError('GitHub rejected draft promotion')
    promoted = response['data']['markPullRequestReadyForReview']['pullRequest']
    if promoted['isDraft'] or promoted['headRefOid'] != current['head']['sha']:
        raise ValueError('Draft promotion did not confirm the validated PR head')
    return True, 'Marked ready for review after validation and resolution of all review threads.'


def finish(ctx, directory, apply):
    """Publish bounded progress and record an honest success/failure verdict."""
    raw = (directory / 'review.json').read_text() if (directory / 'review.json').exists() else os.getenv("REVIEW_JSON", "")
    reject_secrets(raw)
    verdict = docs.review_verdict(raw) if raw else {
        **{key: False for key in docs.REVIEW_GATES}, "reason": "Review did not complete."}
    threads = checked_threads(verdict, ctx)
    technical = os.getenv("BUILD_OK") == "true"
    accepted = technical and all(verdict[key] for key in docs.REVIEW_GATES)
    resolved = []
    promoted, promotion_reason = False, 'Draft promotion requires an applied, accepted review and passing build.'
    reason = verdict["reason"]
    if not technical:
        reason += "\nDocumentation type check or production build did not pass."
    head = ctx["head"]
    reject_secrets(reason)
    if apply:
        if accepted:
            head = publish_repair(ctx)
        else:
            live_match(ctx)
        current = published_pr(ctx, head)
        record_check(ctx, head, accepted, reason)
        expected_details = json.loads(json.dumps(ctx["feedback"]))
        # Checks on the old commit are evidence for the repair, not failures on
        # its successor. Any newly arriving result invalidates the saved digest.
        if head != ctx["head"]:
            expected_details["failed_checks"] = []
        if accepted and threads:
            fresh, _, _ = feedback(current)
            original_threads = {t["id"]: t for t in ctx["feedback"]["threads"]}
            fresh_threads = {t["id"]: t for t in fresh["threads"]}
            for thread in threads:
                fresh_thread = fresh_threads.get(thread)
                if (fresh_thread is None
                        or stable_thread(fresh_thread) != stable_thread(original_threads[thread])
                        or thread in fresh.get('protected_threads', [])):
                    continue
                api("graphql", "POST", {"query": "mutation($id:ID!){resolveReviewThread(input:{threadId:$id}){thread{id}}}",
                                        "variables": {"id": thread}})
                resolved.append(thread)
                if 'unresolved_threads' in expected_details:
                    expected_details['unresolved_threads'] = [
                        value for value in expected_details['unresolved_threads'] if value != thread]
                expected_details["threads"] = [t for t in expected_details["threads"] if t["id"] != thread]
            for index, thread in enumerate(expected_details["threads"], 1):
                thread["number"] = index
        current = published_pr(ctx, head)
        if accepted:
            promoted, promotion_reason = promote_draft(ctx, current, expected_details)
            if promoted:
                current = {**current, 'draft': False}
        attempts = 0 if accepted else ctx["attempts"]
        state = {"phase": "ready" if accepted else ("needs-human" if attempts >= MAX_ATTEMPTS else "needs-repair"),
                 "attempts": attempts, "infrastructure_attempts": 0,
                 "head": head, "base": ctx["base"], "reason": reason,
                 "signature": signature({**current, "code_sha": ctx["code_sha"]}, expected_details, ctx["extra_feedback"]),
                 "code_sha": ctx["code_sha"],
                 "extra_feedback": ctx["extra_feedback"], "run_url": ctx["run_url"]}
        save_state(ctx, state)
    result = {"number": ctx["number"], "applied": apply, "published": head != ctx['head'],
              "accepted": accepted, "head": head, "code_sha": ctx["code_sha"],
              "base": ctx["base"], "review_base": ctx.get("review_base", ctx["base"]),
              "reason": reason, "resolved_bot_threads": resolved,
              "marked_ready": promoted, "promotion_reason": promotion_reason}
    (directory / "result.json").write_text(json.dumps(result, indent=2))
    publication = ('dry run, no repository writes' if not apply else
                   ('repair published' if head != ctx['head'] else 'no repair published'))
    with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as summary:
        summary.write(f"PR #{ctx['number']}: {'validated' if accepted else 'needs repair'}; "
                      f"{publication}. {promotion_reason}\n\n"
                      f"<pre>{html.escape(reason)}</pre>\n")


def published_pr(ctx, head):
    """Tolerate propagation of our push, but never accept a competing head/base."""
    for attempt in range(12):
        current = get_pr(ctx['number'])
        eligible(current)
        if current['base']['sha'] != ctx['base']:
            raise ValueError('Docs main changed after publication; a fresh review is required')
        if current['head']['sha'] == head:
            return current
        if current['head']['sha'] != ctx['head'] or attempt == 11:
            raise ValueError('PR changed after publication or our push did not propagate')
        time.sleep(2)


def failure(ctx):
    """Bound incomplete worker rounds separately from rejected documentation."""
    current = get_pr(ctx['number'])
    if current["state"] != "open":
        return
    infrastructure = ctx['infrastructure_attempts']
    state = {"phase": "needs-human" if infrastructure >= MAX_INFRASTRUCTURE_ATTEMPTS else "retry-infrastructure",
             "attempts": ctx["attempts"] - 1, "infrastructure_attempts": infrastructure,
             "head": current["head"]["sha"], "base": current["base"]["sha"],
             "extra_feedback": ctx["extra_feedback"], "run_url": ctx["run_url"],
             "reason": "A worker or publication step failed before the round completed. "
                       "The content-repair budget was not charged. Inspect the linked workflow; "
                       "no success verdict was recorded."}
    save_state(ctx, state)
    record_check(ctx, current["head"]["sha"], False, state["reason"])


def main():
    """Expose narrowly scoped commands to trusted workflow steps."""
    directory = Path(os.environ.get("MAINTENANCE_DIR", os.environ.get("RUNNER_TEMP", "/tmp") + "/docs-maintenance"))
    command = sys.argv[1]
    number = int(os.getenv("PR_NUMBER") or "0")
    apply = os.getenv("APPLY") == "true"
    force = os.getenv("FORCE") == "true"
    if command == 'mode':
        output(apply=str(apply_mode(json.loads(os.getenv('INPUTS_JSON') or '{}'), os.environ['RUN_EVENT'])).lower())
        return
    if command == "select":
        select(number, force)
        return
    if command == "prepare":
        prepare(number, directory, apply, force)
        return
    ctx = json.loads((directory / "context.json").read_text())
    if command == "restore":
        restore(ctx)
    elif command == "export":
        for path in ctx['item']['doc_paths']:
            if Path(path).is_file():
                reject_secrets(Path(path).read_text())
        docs.export_bundle(ctx["item"], ctx["base"], directory / "bundle.json")
        reject_secrets((directory / 'bundle.json').read_text())
    elif command == "import":
        reject_secrets((directory / 'bundle.json').read_text())
        changed = restore(ctx, (directory / "bundle.json").read_text())
        (directory / "full-pr.patch").write_text(docs.git("diff", "--cached", ctx["base"]))
        output(changed=str(changed).lower())
    elif command == "finish":
        finish(ctx, directory, apply)
    elif command == 'review-export':
        raw = os.getenv('REVIEW_JSON', '')
        verdict = docs.review_verdict(raw) if raw else {
            **{key: False for key in docs.REVIEW_GATES}, 'reason': 'Review did not complete.', 'addressed_threads': []}
        reject_secrets(json.dumps(verdict))
        (directory / 'review.json').write_text(json.dumps(verdict))
    elif command == 'review-gate':
        raw = (directory / 'review.json').read_text()
        reject_secrets(raw)
        verdict = docs.review_verdict(raw)
        output(accepted=str(all(verdict[key] for key in docs.REVIEW_GATES)).lower())
    elif command == 'scan':
        for path in directory.rglob('*'):
            if path.is_file():
                reject_secrets(path.read_text())
    elif command == "failure":
        failure(ctx)
    else:
        raise ValueError("Unknown maintenance command")


if __name__ == "__main__":
    main()
