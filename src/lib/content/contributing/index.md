---
title: Contributing
---

# Contributing to SMG

Thank you for your interest in contributing to Shepherd Model Gateway (SMG). The gateway's source code lives in [smg-project/smg](https://github.com/smg-project/smg). This documentation lives in a separate repository, [smg-project/smg-docs](https://github.com/smg-project/smg-docs), and is published at [lightseek.org/smg](https://lightseek.org/smg). The smg repository's [CONTRIBUTING.md](https://github.com/smg-project/smg/blob/main/CONTRIBUTING.md) is the front door; these pages hold the details.

---

## Ways to Contribute

<div class="grid cards" markdown>

-   :material-bug:{ .lg .middle } **Report Bugs**

    ---

    Found a bug? The bug report form asks for reproduction steps, expected and actual behavior, the component, routing policy, and connection mode, your configuration, and logs.

    [:octicons-arrow-right-24: Report a bug](https://github.com/smg-project/smg/issues/new?template=1-bug-report.yml)

-   :material-lightbulb:{ .lg .middle } **Suggest Features**

    ---

    Describe the problem you're solving and your proposed solution. Latency, throughput, and memory problems have their own performance issue form.

    [:octicons-arrow-right-24: Request a feature](https://github.com/smg-project/smg/issues/new?template=2-feature-request.yml)

-   :material-code-tags:{ .lg .middle } **Contribute Code**

    ---

    Set up the toolchain, build and test the workspace, and open a pull request.

    [:octicons-arrow-right-24: Development guide](development.md)

-   :material-file-document:{ .lg .middle } **Improve Docs**

    ---

    Fix typos, clarify explanations, or document a missing feature. Every page links to its source file in smg-docs.

    [:octicons-arrow-right-24: Edit on GitHub](https://github.com/smg-project/smg-docs/tree/main/src/lib/content)

</div>

---

## Quick Start

### 1. Fork and Clone

```bash
# Fork smg-project/smg on GitHub, then:
git clone https://github.com/YOUR_USERNAME/smg.git
cd smg
```

### 2. Install the Toolchain

```bash
# Rust 1.98.0 (the version CI uses) for this checkout, plus nightly rustfmt
rustup toolchain install 1.98.0
rustup override set 1.98.0
rustup toolchain install nightly --profile minimal --component rustfmt

# Git hooks: formatting, lint, DCO sign-off, and commit-message checks
pip install pre-commit
pre-commit install
pre-commit install --hook-type commit-msg
```

You also need `protoc` and a C toolchain with OpenSSL headers; see [Prerequisites](development.md#prerequisites).

### 3. Branch, Build, and Test

```bash
# Branch names must be <type>/<description> or <username>/<description>
git checkout -b feat/my-change

cargo build --locked
cargo test --locked
```

### 4. Commit and Open a Pull Request

```bash
git commit -s -m "feat(policies): describe the change"
git push origin feat/my-change
```

Open the pull request against `main` and fill in the [PR template](https://github.com/smg-project/smg/blob/main/.github/PULL_REQUEST_TEMPLATE.md).

---

## The Pre-PR Gate

Every PR must pass these checks locally before you request review. "Probably passes" is not passing: paste the output or re-run.

| # | Command | Expectation |
|---|---------|-------------|
| 1 | `cargo +nightly fmt --all` | No output (silent success) |
| 2 | `cargo clippy --locked --all-targets --all-features -- -D warnings` | Zero warnings, zero errors |
| 3 | `cargo test --locked` | `test result: ok` with 0 failures |
| 4 | `make python-dev` (only if `config/types.rs`, `protocols/`, or `bindings/` changed) | Builds successfully |
| 5 | Commit format | Conventional commit, DCO sign-off present, no AI attribution |

Check 2 uses `--all-features`, which turns on the `opencv-video` feature and needs system OpenCV. Install it once with `make opencv-deps`, or see [Linting and Formatting](development.md#linting-and-formatting) for a lint that skips it.

---

## Commits

SMG uses [Conventional Commits](https://www.conventionalcommits.org/):

```text
<type>(<scope>): <short summary>

<optional body explaining why>

Signed-off-by: Your Name <your.email@example.com>
```

- **Types**: `feat`, `fix`, `docs`, `style`, `refactor`, `perf`, `test`, `chore`, `ci`
- **Scope**: the crate or subsystem, such as `mesh`, `grpc_client`, `worker`, or `protocols`
- **One logical change per commit.** Prefer many small commits to one large one.
- **Sign off every commit** with `git commit -s`. The sign-off certifies the [Developer Certificate of Origin](https://developercertificate.org/): you wrote the code or have the right to submit it.
- **No AI attribution.** `Co-authored-by` or `Signed-off-by` lines that name Claude or `noreply@anthropic.com` are rejected by the `no-ai-co-author` hook and by the PR check.

Examples from the smg history:

```text
feat(routers): compile provider routers behind per-provider Cargo features
fix(protocols): preserve Responses tool namespaces and content-array outputs
ci: upgrade Rust toolchain to 1.98
```

Pull requests are squash-merged, and the PR title becomes the commit title on `main`, so the title must follow the same format. The `PR Title & Commit Messages` check accepts the types `feat`, `fix`, `chore`, `docs`, `refactor`, `test`, `ci`, `perf`, `lint`, `style`, `revert`, and `build`, an optional lowercase scope, and `!` for a breaking change (`feat(api)!: ...`).

### Branch Names

Name branches `<type>/<description>` (for example `feat/add-auth` or `fix/null-pointer`) or `<username>/<description>` (for example `changsu/fix-routing`), in lowercase. For branches pushed to smg-project/smg, the `Branch Naming Convention` check comments on and closes a PR whose branch does not match. Locally, the `branch-name-check` hook checks the name at push time (lowercase letters, digits, `.`, `_`, and `-`, with one `/`) once you install the pre-push hook (`pre-commit install --hook-type pre-push`).

---

## Pull Requests

- **Fill in the PR template**: Problem and Solution, Changes, and especially the **Test Plan**. "Ran `cargo test`" is not a test plan; name the scenarios the reviewer can reproduce.
- **Keep PRs small.** Aim for 400 changed lines or fewer. Above that, split the PR or coordinate with reviewers in advance.
- **One concern per PR.** A refactor or a feature, not both.
- **Link the issue** with `Closes #1234` or `Refs: #1234`.
- **Answer review comments with a commit SHA and a one-line reason**, for example `Fixed in abc1234 — capped total_chunks at 1024 before allocation`. Silence or "Fixed!" makes reviewers hunt for your change.
- **Propose significant changes first.** New architecture, breaking API changes, new workspace crates, and deprecations start as a GitHub issue or design discussion that the core maintainers decide on.
- **Keep the PR active.** A stale bot labels a non-draft PR `stale` after 14 days without activity and closes it 16 days later. Draft PRs are exempt.

### Merge Requirements

`main` accepts squash merges only. A pull request can merge once it has:

- two approving reviews, including one from a [code owner](https://github.com/smg-project/smg/blob/main/.github/CODEOWNERS) of the changed paths
- every review thread resolved
- passing required checks: `finish` (the summary job of the PR test workflow, which fails if any lint, test, or e2e job failed), `DCO`, and `PR Title & Commit Messages`

If the DCO check fails, Mergify comments with the fix (`git rebase HEAD~N --signoff`, then `git push --force-with-lease`). A PR with merge conflicts gets the `needs-rebase` label until you rebase it on `origin/main`.

---

## Using Code Agents

Agents such as Claude Code, Cursor, and Copilot are welcome. Three ground rules:

1. **You own the PR, not the agent.** Read every line before you open it.
2. **Show the gate output.** Paste the real `cargo fmt`, `clippy`, and `test` output, not "I have run the tests."
3. **No AI attribution** in commits, PR bodies, or review replies. The hook rejects it, and so will the reviewer.

---

## Reviewing

Reviews follow [REVIEW.md](https://github.com/smg-project/smg/blob/main/REVIEW.md). Prefix every inline comment with a severity marker:

| Marker | Severity | Meaning |
|--------|----------|---------|
| 🔴 | **Important** | A bug that should be fixed before merging |
| 🟡 | **Nit** | A minor issue, worth fixing but not blocking |
| 🟣 | **Pre-existing** | A bug in the codebase that this PR did not introduce |

- Cite `file:line` in every substantive comment, and finish with a short summary that counts findings per severity.
- Focus on logic errors, security problems, missing error handling (swallowed errors, silent fallbacks to defaults), incorrect defaults or config values, and broken cross-references. Skip formatting-only changes and dependency bumps with no code changes.
- Approve small, clean PRs fast. The faster the turnaround, the fewer giant PRs reviewers face later.

REVIEW.md also lists the subsystem pitfalls reviewers look for; config changes, for example, must reach the CLI arguments, `config/types.rs`, `main.rs`, the Python bindings, and the Go SDK. Automated reviewers comment on PRs as well: CodeRabbit, and, for branches pushed to smg-project/smg rather than forks, a Claude-based review that uses the same severity markers.

---

## Governance

SMG's [GOVERNANCE.md](https://github.com/smg-project/smg/blob/main/GOVERNANCE.md) follows the spirit of the Linux Foundation's [Minimum Viable Governance](https://github.com/github/MVG) framework.

| Role | Who | Responsibilities |
|------|-----|------------------|
| **Contributors** | Anyone who files issues, joins discussions, or opens pull requests | No special status required |
| **Code owners** | Contributors with sustained, high-quality work in an area, listed in [CODEOWNERS](https://github.com/smg-project/smg/blob/main/.github/CODEOWNERS) | Review and approve changes in their areas |
| **Core maintainers** | Listed in GOVERNANCE.md; the default (`*`) owners in CODEOWNERS | Direction, architecture, releases, and the health of the project |

- Day-to-day technical decisions use **lazy consensus** on pull requests and issues.
- Significant changes (new architecture, breaking API changes, new workspace crates, release planning, deprecations) are proposed as a GitHub issue or design discussion and decided by consensus among the core maintainers.
- The usual path is contributor, then code owner, then core maintainer. Each addition needs the core maintainers' agreement and lands as a pull request that updates CODEOWNERS.

---

## Documentation Changes

These docs are not in the smg repository: its old `docs/` folder was removed when the documentation moved to [smg-project/smg-docs](https://github.com/smg-project/smg-docs) (smg-project/smg#2012). The docs repository follows the same conventions as smg (`<type>/<description>` or `<username>/<description>` branch names, Conventional Commits PR titles, DCO sign-off, no AI attribution) and runs the same PR checks. See [Documentation](development.md#documentation) for the local preview workflow.

---

## Reporting Security Issues

Do not open a public issue for a security vulnerability. Report it privately instead: contact a maintainer listed in [CODEOWNERS](https://github.com/smg-project/smg/blob/main/.github/CODEOWNERS), or reach out in the `#security` channel of the [Lightseek Slack](https://slack.lightseek.org).

---

## Getting Help

- **Questions**: [GitHub Discussions](https://github.com/smg-project/smg/discussions)
- **Bugs**: [GitHub Issues](https://github.com/smg-project/smg/issues)
- **Chat**: [Slack](https://slack.lightseek.org) (`#sig-smg` for discussing, reviewing, and merging PRs) or [Discord](https://discord.lightseek.org)

---

## Code of Conduct

We are committed to providing a welcoming and inclusive environment. The [Code of Conduct](https://github.com/smg-project/smg/blob/main/CODE_OF_CONDUCT.md) applies to every interaction in the repository and in community spaces.

---

## License

By contributing, you agree that your contributions will be licensed under the [Apache License 2.0](https://github.com/smg-project/smg/blob/main/LICENSE).
