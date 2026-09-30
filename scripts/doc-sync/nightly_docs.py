# Adapted from ome-projects/ome at 776fbe3b4be9e826be3404699886543d83e8e380.
# SMG cross-repository discovery and publication changes; Apache-2.0 (LICENSE).
"""Bound and publish one-concern documentation updates (standard library only)."""

import argparse
from datetime import datetime, timezone
import hashlib
import html
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import tempfile


DOC_ROOT = "src/lib/content/"
SOURCE_REPO = "smg-project/smg"
INITIAL_SINCE = "2026-06-27T00:00:00Z"
LEGACY_MARKER = "<!-- smg-doc-sync:"
MAX_LINES = 1000
MAX_PRS = 100
SLUG = r"[a-z0-9]+(?:-[a-z0-9]+)*"
MARKER = "<!-- nightly-docs:"
REVIEW_GATES = ("single_concern", "accurate", "placement_appropriate", "related_docs_consistent")


def run(*args):
    return subprocess.check_output(args, text=True).strip()


def git(*args):
    return run("git", "-c", "core.hooksPath=/dev/null", *args)


def source_git(*args):
    return git("-C", os.environ["SOURCE_ROOT"], *args)


def source_patch(sha):
    """Decode Git patch evidence as UTF-8, replacing invalid source-file bytes."""
    raw = subprocess.check_output([
        "git", "-c", "core.hooksPath=/dev/null", "-C", os.environ["SOURCE_ROOT"],
        "show", "--first-parent", "--no-ext-diff", "--no-textconv", sha])
    return raw.decode("utf-8", errors="replace").strip()


def mutate_git(*args):
    subprocess.run(["git", "-c", "core.hooksPath=/dev/null", *args], check=True)


def pages(endpoint):
    chunks = json.loads(run("gh", "api", endpoint, "--paginate", "--slurp"))
    return [item for chunk in chunks for item in chunk]


def doc_path(path):
    p = PurePosixPath(path)
    return (isinstance(path, str) and path.startswith(DOC_ROOT) and path.endswith(".md")
            and ".." not in p.parts and str(p) == path and all(part not in (".git", ".github") for part in p.parts))


def doc_inventory(base):
    """Index existing Markdown pages from the pinned documentation revision."""
    pages = []
    for entry in git("ls-tree", "-r", "-z", base, "--", DOC_ROOT).split("\0"):
        if not entry:
            continue
        metadata, path = entry.split("\t", 1)
        if not metadata.startswith("100644 blob ") or not doc_path(path):
            continue
        content = git("show", f"{base}:{path}")
        title = re.search(r"^title:\s*(.+)$", content, re.MULTILINE)
        pages.append({"path": path, "title": title.group(1).strip("\"'") if title else path,
                      "headings": re.findall(r"^#{1,6}\s+(.+)$", content, re.MULTILINE)})
    return pages


def validate_placement(item, pages):
    """Require examined existing pages, complete planned corrections, and new-page reasons."""
    known = {page["path"] for page in pages}
    decision = item.get("placement")
    if not isinstance(decision, dict):
        raise ValueError("Missing documentation placement decision")
    for key in ("examined_pages", "canonical_pages"):
        paths = decision.get(key)
        if (not isinstance(paths, list) or any(not isinstance(p, str) for p in paths)
                or len(paths) != len(set(paths)) or not set(paths) <= known):
            raise ValueError(f"Invalid placement {key}; use existing documentation pages")
    if known and not decision["examined_pages"]:
        raise ValueError("Inspect existing documentation before selecting pages")
    canonical = set(decision["canonical_pages"])
    planned_existing = set(item["doc_paths"]) & known
    if canonical != planned_existing or not canonical <= set(decision["examined_pages"]):
        raise ValueError("Canonical pages must be examined and all included in doc_paths")
    reason = decision.get("new_page_reason")
    if not isinstance(reason, str) or len(reason) > 4000:
        raise ValueError("Invalid new-page justification")
    if set(item["doc_paths"]) - known:
        if not reason.strip():
            raise ValueError("New pages require justification against the existing documentation")
    elif not canonical:
        raise ValueError("An existing-page update must identify its canonical pages")
    elif reason.strip():
        raise ValueError("New-page justification supplied without a new page")


def validate_item(item):
    for key in ("area", "concern"):
        if not re.fullmatch(SLUG, item[key]) or len(item[key]) > 64:
            raise ValueError(f"Invalid {key} slug")
    if not re.fullmatch(r"[0-9a-f]{40}", item["source_sha"]):
        raise ValueError("Expected a full source commit SHA")
    title = item["title"]
    if (not title.startswith("[Docs] ") or not title[7:].strip()
            or len(title) > 120 or not title.isprintable()):
        raise ValueError("Expected a concise single-line [Docs] title")
    for key in ("question", "evidence"):
        if not isinstance(item[key], str) or not item[key].strip():
            raise ValueError(f"Missing {key}")
    paths = item["doc_paths"]
    if not paths or len(set(paths)) != len(paths):
        raise ValueError("Expected one or more distinct documentation files")
    if not all(doc_path(path) for path in paths):
        raise ValueError("Only handwritten documentation Markdown is allowed")
    key = f'{item["source_sha"]}:{item["area"]}:{item["concern"]}'
    digest = hashlib.sha256(key.encode()).hexdigest()[:16]
    return {**item, "key": key, "branch": f'codex/nightly-docs-{digest}'}


def open_pr_files(repo, numbers):
    # Fetch the common case in batches instead of one API round trip per PR in
    # every publisher. Large PRs still use the fully paginated REST endpoint.
    owner, name = repo.split("/")
    result = {}
    for start in range(0, len(numbers), 25):
        batch = numbers[start:start + 25]
        fields = " ".join(f"p{number}: pullRequest(number: {number}) {{ files(first: 100) "
                          "{ nodes { path } pageInfo { hasNextPage } } }" for number in batch)
        query = "query($owner:String!,$name:String!){repository(owner:$owner,name:$name){" + fields + "}}"
        response = json.loads(run("gh", "api", "graphql", "-f", "query=" + query,
                                  "-f", "owner=" + owner, "-f", "name=" + name))
        data = response["data"]["repository"]
        for number in batch:
            files = data[f"p{number}"]["files"]
            if files["pageInfo"]["hasNextPage"]:
                result[number] = [f["filename"] for f in pages(
                    f"repos/{repo}/pulls/{number}/files?per_page=100")]
            else:
                result[number] = [f["path"] for f in files["nodes"]]
    return result


def existing_prs(repo):
    # All states are needed to remember declined proposals and merged fixes.
    prs = pages(f"repos/{repo}/pulls?state=all&per_page=100")
    files_by_pr = open_pr_files(repo, [pr["number"] for pr in prs if pr["state"] == "open"])
    result = []
    for pr in prs:
        body = pr.get("body") or ""
        if pr["state"] != "open" and not (MARKER in body or LEGACY_MARKER in body
                or pr["head"]["ref"].startswith(("docs/smg-sync-", "codex/nightly-docs-"))):
            continue
        result.append({"number": pr["number"], "title": pr["title"], "created_at": pr["created_at"],
                       "body": body, "state": pr["state"],
                       "merged": bool(pr["merged_at"]),
                       "branch": pr["head"]["ref"], "files": files_by_pr.get(pr["number"], [])})
    return result


def covered(item, prs):
    marker = f'{MARKER}{item["key"]} -->'
    for pr in prs:
        if marker in pr["body"] or pr["branch"] == item["branch"]:
            return True
        if pr["state"] == "open" and set(item["doc_paths"]) & set(pr["files"]):
            return True
    return False


def daily_remaining(prs, today=None):
    today = today or datetime.now(timezone.utc).date().isoformat()
    used = sum(pr.get("created_at", "").startswith(today) and
               (MARKER in pr["body"] or LEGACY_MARKER in pr["body"] or
                pr["branch"].startswith(("docs/smg-sync-", "codex/nightly-docs-")))
               for pr in prs)
    return max(0, MAX_PRS - used)


def prepare(repo, output):
    # A fixed initial cutoff keeps unfinished work eligible as the backlog ages.
    source_sha = source_git("rev-parse", "HEAD")
    history = source_git("log", "--first-parent", "--since=" + INITIAL_SINCE,
                        "--format=%H %cs %s", source_sha)
    sources = Path(output).parent / "nightly-docs-sources"
    sources.mkdir(exist_ok=True)
    for line in history.splitlines():
        sha = line.split()[0]
        (sources / f"{sha}.patch").write_text(source_patch(sha) + "\n", encoding="utf-8")
    prs = existing_prs(repo)
    requested = int(os.environ.get("MAX_PRS", "100"))
    if not 1 <= requested <= MAX_PRS:
        raise ValueError("max_prs must be between 1 and 100")
    context = {"base_sha": git("rev-parse", "HEAD"), "source_sha": source_sha,
               "source_repo": SOURCE_REPO, "source_root": os.environ["SOURCE_ROOT"],
               "initial_since": INITIAL_SINCE, "requested_max_prs": requested, "max_prs": min(requested, daily_remaining(prs)),
               "code_history": history.splitlines(), "source_diffs": str(sources),
               "existing_prs": prs, "doc_inventory": doc_inventory("HEAD"),
               "discovery_shard": os.environ.get("DISCOVERY_SHARD", ""),
               "dry_run": os.environ.get("DRY_RUN") == "true"}
    Path(output).write_text(json.dumps(context, indent=2) + "\n")


def plan(raw, context):
    proposed = json.loads(raw)["concerns"]
    if len(proposed) > MAX_PRS:
        raise ValueError("Plan exceeds the nightly PR limit")
    candidates = {line.split()[0] for line in context["code_history"]}
    selected, occupied, keys = [], set(), set()
    for proposal in proposed:
        # The workflow owns the repository's presentation prefix. Keep all
        # content, length, and printable-character validation below unchanged.
        if not proposal["title"].startswith("[Docs] "):
            proposal = {**proposal, "title": "[Docs] " + proposal["title"]}
        item = validate_item(proposal)
        validate_placement(item, context["doc_inventory"])
        if item["source_sha"] not in candidates:
            raise ValueError("Source commit is not in the supplied default-branch history")
        if item["key"] in keys or occupied.intersection(item["doc_paths"]):
            raise ValueError("Planned concerns duplicate or overlap each other")
        keys.add(item["key"])
        occupied.update(item["doc_paths"])
        if not covered(item, context["existing_prs"]):
            selected.append(item)
    return selected


def validate_diff(item, base):
    if git("rev-parse", "HEAD") != base:
        raise ValueError("The writer must not commit or switch branches")
    validate_placement(item, doc_inventory(base))
    # Include added files but never silently ignore edits outside the allowlist.
    changed = set(filter(None, git("diff", "--name-only", "HEAD").splitlines()))
    changed.update(filter(None, git("ls-files", "--others", "--exclude-standard").splitlines()))
    if not changed:
        return False
    if not changed <= set(item["doc_paths"]):
        raise ValueError("Changes exceed the planned documentation file allowlist")
    if not set(item["placement"]["canonical_pages"]) <= changed:
        raise ValueError("The patch leaves a planned canonical-page correction unchanged")
    for path in changed:
        p = Path(path)
        if not p.is_file() or any(parent.is_symlink() for parent in (p, *p.parents)):
            raise ValueError("Deleted files and symbolic links are not allowed")
        if p.stat().st_mode & 0o111:
            raise ValueError("Documentation must not be executable")
    mutate_git("add", "--", *sorted(changed))
    total = 0
    for line in git("diff", "--cached", "--numstat", base).splitlines():
        added, removed, _ = line.split("\t", 2)
        if not added.isdigit() or not removed.isdigit():
            raise ValueError("Binary changes are not allowed")
        total += int(added) + int(removed)
    if total >= MAX_LINES:
        raise ValueError(f"Documentation diff must be under {MAX_LINES} changed lines")
    mutate_git("diff", "--cached", "--check", base)
    return total > 0


def export_bundle(item, base, output):
    """Writer output is untrusted data; never transfer its scripts or .git."""
    validate_diff(item, base)
    changed = git("diff", "--cached", "--name-only", base).splitlines()
    payload = {"base_sha": base, "key": item["key"],
               "files": {path: Path(path).read_text() for path in changed}}
    Path(output).write_text(json.dumps(payload) + "\n")


def import_bundle(item, base, raw):
    """Revalidate writer output using the publisher's pristine default-branch code."""
    if len(raw.encode()) > 10 * 1024 * 1024:
        raise ValueError("Documentation bundle exceeds 10 MiB")
    payload = json.loads(raw)
    if payload["base_sha"] != base or payload["key"] != item["key"]:
        raise ValueError("Bundle does not match this concern and base")
    files = payload["files"]
    if not isinstance(files, dict) or not set(files) <= set(item["doc_paths"]):
        raise ValueError("Bundle contains paths outside the documentation allowlist")
    # Validate the entire payload before writing anything. Neither hooks nor
    # executable scripts/configuration from the writer are ever imported.
    for path, content in files.items():
        p = Path(path)
        if (not doc_path(path) or not isinstance(content, str) or "\x00" in content
                or any(parent.is_symlink() for parent in (p, *p.parents))):
            raise ValueError("Invalid documentation bundle entry")
    for path, content in files.items():
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    return validate_diff(item, base)


def review_verdict(raw):
    verdict = json.loads(raw)
    if (not isinstance(verdict, dict)
            or any(type(verdict.get(key)) is not bool for key in REVIEW_GATES)
            or not isinstance(verdict.get("reason"), str)
            or not verdict["reason"].strip() or len(verdict["reason"]) > 10000):
        raise ValueError("Malformed documentation review")
    return verdict


def record_review(raw):
    verdict = review_verdict(raw)
    accepted = all(verdict[key] for key in REVIEW_GATES)
    with open(os.environ["GITHUB_OUTPUT"], "a") as output:
        output.write(f"accepted={str(accepted).lower()}\n")
    if not accepted:
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as summary:
            summary.write("Documentation proposal rejected; no PR created.\n\n<pre>"
                          + html.escape(verdict["reason"]) + "</pre>\n")


def review_passes(raw):
    verdict = review_verdict(raw)
    if not all(verdict[key] for key in REVIEW_GATES):
        raise ValueError("Documentation review rejected the change: " + str(verdict.get("reason")))


def publish(item, repo, base, base_branch):
    # Refresh against live PRs immediately before publishing to cover human PRs
    # opened while the model/build ran and retries after partial publication.
    prs = existing_prs(repo)
    if daily_remaining(prs) == 0:
        print("UTC daily PR limit reached; concern remains eligible next night.")
        return
    if covered(item, prs):
        print("Concern already covered or files reserved by another PR; skipping.")
        return
    if not validate_diff(item, base):
        print("No documentation gap to publish.")
        return
    # Never overwrite an existing branch, even after a prior push/PR API failure.
    # In that case reuse it only if its exact tree and parent match this run.
    branch = item["branch"]
    tree = git("write-tree")
    remote = git("ls-remote", "--heads", "origin", f"refs/heads/{branch}")
    if remote:
        mutate_git("fetch", "origin", f"refs/heads/{branch}")
        if git("rev-parse", "FETCH_HEAD^{tree}") != tree or git("rev-parse", "FETCH_HEAD^") != base:
            raise ValueError(f"Existing branch {branch} differs; inspect it before retrying")
    if os.environ.get("DRY_RUN") == "true":
        print("Dry run: validated concern and remote branch; no commit, push, or PR created.")
        return
    if not remote:
        mutate_git("switch", "-c", branch)
        # The publisher, rather than the model, owns commit metadata and DCO.
        git("config", "user.name", "XinyueZhang369")
        git("config", "user.email", "zoeyzhang369@gmail.com")
        message = f'docs: update {item["concern"]}'[:72]
        mutate_git("commit", "-s", "-m", message)
        mutate_git("push", "origin", f"HEAD:refs/heads/{branch}")
    body = f'''{MARKER}{item["key"]} -->
## What this PR does

{item["question"]}

## Why we need it

Source change: https://github.com/{SOURCE_REPO}/commit/{item["source_sha"]}

{item["evidence"]}

Scope: **{item["area"]} / {item["concern"]}**. Other concerns are deferred.

## How to test

- Passed the documentation path and size guard (under {MAX_LINES} added plus deleted lines; no file-count limit).
- Passed an independent accuracy and single-concern review.
- Passed `git diff --check` and `pnpm check` and the production `pnpm build`.

## Checklist

- [ ] Tests added/updated (if applicable)
- [x] Docs updated (if applicable)
- [x] Commit signed off by XinyueZhang369 <zoeyzhang369@gmail.com>
'''
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md") as f:
        f.write(body)
        f.flush()
        url = run("gh", "pr", "create", "--repo", repo, "--base", base_branch,
                  "--head", branch, "--title", "docs: " + item["title"][7:], "--body-file", f.name, "--draft")
    print(url)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as summary:
            summary.write(f'- {item["title"]}: {url}\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["context", "plan", "evidence", "check", "export", "import", "review", "publish"])
    args = parser.parse_args()
    repo = os.environ["GITHUB_REPOSITORY"]
    if args.command == "context":
        prepare(repo, os.environ["NIGHTLY_CONTEXT"])
    elif args.command == "plan":
        context = json.loads(Path(os.environ["NIGHTLY_CONTEXT"]).read_text())
        items = plan(os.environ["PLAN_JSON"], context)
        with open(os.environ["GITHUB_OUTPUT"], "a") as out:
            out.write("matrix=" + json.dumps({"include": items}) + "\n")
            out.write(f"count={len(items)}\n")
    elif args.command == "review":
        record_review(os.environ["REVIEW_JSON"])
    else:
        item = validate_item(json.loads(os.environ["ITEM_JSON"]))
        base = os.environ["BASE_SHA"]
        if args.command == "evidence":
            path = Path(os.environ["NIGHTLY_ITEM"])
            path.write_text(json.dumps(item) + "\n")
            path.with_name("nightly-docs-source.patch").write_text(
                source_patch(item["source_sha"]) + "\n", encoding="utf-8")
        elif args.command in ("check", "import"):
            if args.command == "import":
                changed = import_bundle(item, base, Path(os.environ["BUNDLE_PATH"]).read_text())
            else:
                changed = validate_diff(item, base)
            with open(os.environ["GITHUB_OUTPUT"], "a") as out:
                out.write(f"changed={str(changed).lower()}\n")
        elif args.command == "export":
            export_bundle(item, base, os.environ["BUNDLE_PATH"])
        else:
            review_passes(os.environ["REVIEW_JSON"])
            publish(item, repo, base, os.environ["BASE_BRANCH"])


if __name__ == "__main__":
    main()
