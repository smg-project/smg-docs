---
title: Development Guide
---

# Development Guide

This guide covers setting up a development environment, building and testing SMG, and the checks CI runs. For the contribution workflow (commits, pull requests, and review), see [Contributing](index.md).

---

## Prerequisites

| Tool | Version | Used for |
|------|---------|----------|
| Rust | 1.98.0 | Building the workspace. CI pins this version in `scripts/ci_install_rust.sh`; the repository has no `rust-toolchain.toml` |
| Rust nightly with `rustfmt` | nightly | Formatting only: `rustfmt.toml` uses unstable options |
| `protoc` | — | Compiling the gRPC (`crates/grpc_client`) and mesh (`crates/mesh`) protobuf definitions at build time |
| C toolchain, `pkg-config`, OpenSSL headers | — | Native dependencies |
| Python with `maturin` | 3.12+ | The Python bindings (`make python-dev`), Python lint, and the e2e harness |
| `pre-commit` | — | Git hooks |
| OpenCV and libclang | optional | `--all-features` builds; install with `make opencv-deps` |
| Docker | optional | Running the Postgres storage tests locally |
| Go | 1.24 (optional) | The Go bindings in `bindings/golang` |

The Python bindings themselves support Python 3.9 and later, but ruff and mypy target 3.12 and the e2e harness requires it. Building and testing smg needs no Node.js; only `make generate-java-types`, which runs the OpenAPI generator through `npx`, and the [documentation site](#documentation) do.

---

## Setting Up

### 1. Clone the Repository

```bash
git clone https://github.com/smg-project/smg.git
cd smg
```

To contribute, clone your fork instead (see [Quick Start](index.md#quick-start)).

### 2. Install Rust

```bash
# Install rustup
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh

# The toolchain CI uses, pinned for this checkout
rustup toolchain install 1.98.0
rustup override set 1.98.0

# Nightly, used only for rustfmt
rustup toolchain install nightly --profile minimal --component rustfmt
```

Newer stable toolchains can report clippy lints that 1.98.0 does not: moving CI to 1.98 needed code changes to pass `-D warnings` (smg-project/smg#2272). Use 1.98.0 when you reproduce CI's lint results.

### 3. Install System Packages

=== "Debian/Ubuntu"

    ```bash
    sudo apt-get update
    sudo apt-get install -y build-essential libssl-dev pkg-config protobuf-compiler
    ```

=== "macOS"

    ```bash
    xcode-select --install
    brew install protobuf pkg-config openssl@3
    ```

The Debian packages are the ones CI installs (`scripts/ci_install_rust.sh`). For `--all-features` builds, also run `make opencv-deps` (see [Linting and Formatting](#linting-and-formatting)).

### 4. Install the Git Hooks

```bash
pip install pre-commit
pre-commit install                          # hooks that run on every commit
pre-commit install --hook-type commit-msg   # DCO sign-off and no-AI-attribution checks
pre-commit install --hook-type pre-push     # optional: branch-name check before pushing
```

| Hook | Runs on | Checks |
|------|---------|--------|
| `rustfmt` | commit (Rust files) | `cargo +nightly fmt --all` |
| `clippy` | commit (Rust files) | `cargo clippy --workspace --all-targets --all-features -- -D warnings`, which needs OpenCV |
| `ruff`, `ruff-format` | commit (Python files) | Python lint with `--fix`, and formatting |
| `codespell` and file-hygiene hooks | commit | Spelling, trailing whitespace, final newlines, YAML and TOML syntax, merge-conflict markers, private keys, and large files; `no-commit-to-branch` blocks commits on `main` |
| `dco-check` | commit message | A `Signed-off-by` line that exactly matches your `git config` `user.name` and `user.email` |
| `no-ai-co-author` | commit message | No `Co-authored-by` or `Signed-off-by` lines naming Claude or `noreply@anthropic.com` |
| `branch-name-check` | push | `<type>/<description>` or `<username>/<description>`, in lowercase |

With the pre-push hook installed, the commit-stage hooks run again at push time. CI's `pre-commit` job runs the same hooks on all files, except `rustfmt`, `clippy`, and the branch and commit-message hooks, which CI checks in other jobs.

### 5. Cache Compilation (Optional)

The Makefile uses [sccache](https://github.com/mozilla/sccache) automatically when it is on your `PATH`. `make setup-sccache` installs it; to use it with plain `cargo` as well, export `RUSTC_WRAPPER=sccache` in your shell profile.

---

## Building

```bash
# Debug build of the whole workspace
cargo build --locked

# Only the gateway binary: target/debug/smg
cargo build --locked --bin smg

# Optimized build (`make build` runs `cargo build --release`)
cargo build --locked --release
```

The gateway is the `smg` package in `model_gateway/`. It builds two binaries from the same `src/main.rs`, `smg` and `amg`, so `cargo run` needs `--bin smg`. Both `smg launch [OPTIONS]` and plain `smg [OPTIONS]` start the gateway.

| Profile | Select with | Settings | Notes |
|---------|-------------|----------|-------|
| `dev` | default | `opt-level = 0` for workspace crates, `opt-level = 2` for dependencies | Day-to-day development |
| `release` | `--release` | `opt-level = "z"`, fat LTO, `codegen-units = 1`, symbols kept | Slow to compile; symbols stay so CPU profiles are readable (smg-project/smg#1582) |
| `ci` | `--profile ci` | `opt-level = 2`, thin LTO, 16 codegen units, stripped | What CI builds wheels with; a faster optimized build |
| `bench` | `cargo bench` | `opt-level = 3`, thin LTO | Benchmarks |

### Cargo.lock and `--locked`

`Cargo.lock` is committed (smg-project/smg#2248), so every build resolves the same dependency graph. Pass `--locked` to `cargo build`, `cargo test`, `cargo clippy`, and `cargo run`: cargo then fails instead of silently re-resolving dependencies.

```text
error: cannot update the lock file .../Cargo.lock because --locked was passed to prevent this
```

This error means a `Cargo.toml` changed without a matching `Cargo.lock` update. When you add or bump a dependency on purpose, run cargo once without `--locked` (or run `cargo update -p <crate>`) and commit the updated `Cargo.lock` in the same PR. The `make` targets call cargo without `--locked`.

### Cargo Features

Features of the `smg` package (`model_gateway/Cargo.toml`):

| Feature | Default | Effect |
|---------|---------|--------|
| `grpc-client` | yes | Marker feature that gates no code; gRPC worker support is always compiled in. The non-default `grpc-server` is also a marker |
| `jemalloc-stats` | yes | Exports jemalloc allocator gauges: `smg_allocator_allocated_bytes`, `smg_allocator_active_bytes`, `smg_allocator_resident_bytes`, and `smg_allocator_metadata_bytes` |
| `providers` | yes | Umbrella for the three provider features below |
| `provider-openai` | via `providers` | The OpenAI-compatible router, which also serves xAI and custom providers |
| `provider-anthropic` | via `providers` | The Anthropic router |
| `provider-gemini` | via `providers` | The Gemini router |
| `opencv-video` | no | Video decoding through system OpenCV for multimodal requests |
| `mm-rdma` | no | The real NIXL transport for multimodal pixel RDMA; without it the gateway compiles a no-op stub |
| `jemalloc-profiling` | no | Builds jemalloc with heap-profiling support |
| `vendored-openssl` | no | Builds OpenSSL from source instead of linking the system library; `make python-build` uses it for wheels |
| `test-util` | no | A client-injection seam for the crate's own Kubernetes discovery tests. The crate's test targets turn it on automatically; production builds never do |

The provider routers live in `crates/external_router` and are compiled per provider (smg-project/smg#2449). IGW mode registers every router the build contains. To build a gateway for self-hosted engines only:

```bash
# Self-hosted engines only: no third-party provider routers
cargo build --locked -p smg --no-default-features --features grpc-client,jemalloc-stats --bin smg

# Self-hosted engines plus Anthropic
cargo build --locked -p smg --no-default-features \
  --features grpc-client,jemalloc-stats,provider-anthropic --bin smg
```

A build without a provider's router refuses that provider. Registering a worker whose traffic needs a missing router returns `400` with the error code `PROVIDER_NOT_COMPILED`, and selecting the router with `--backend` fails at startup. For `--backend anthropic` on a self-hosted-only build:

```text
Anthropic routing is not compiled into this build; rebuild with the `provider-anthropic` Cargo feature
```

CI lints both of these builds in addition to the default one (see [Linting and Formatting](#linting-and-formatting)).

---

## Repository Layout

```text
smg/
├── model_gateway/              # The gateway: package `smg`, binaries `smg` and `amg`
│   ├── src/
│   │   ├── main.rs             # CLI parsing and startup
│   │   ├── lib.rs              # Library root
│   │   ├── server.rs           # HTTP server and route table
│   │   ├── app_context.rs      # Shared application state
│   │   ├── config/             # RouterConfig types, builder, and validation
│   │   ├── routers/            # HTTP, gRPC and ZMQ, PD, and external-provider routing
│   │   ├── policies/           # Load-balancing policies
│   │   ├── worker/             # Worker registry, health, circuit breakers, load monitoring
│   │   ├── service_discovery/  # Kubernetes worker discovery
│   │   ├── mesh/               # HA mesh adapters (crates/mesh does the gossip)
│   │   ├── mesh_discovery/     # Router-peer discovery for the mesh
│   │   ├── middleware/         # Auth, request IDs, concurrency limits, admission scheduler
│   │   ├── rate_limit/         # Per-tenant token and request rate limiting
│   │   ├── endpoints/          # Non-inference endpoints: models, tokenize, parse, conversations, responses
│   │   ├── workflow/           # Registration workflows for workers, tokenizers, MCP servers, WASM modules
│   │   ├── observability/      # Logging, metrics, and tracing
│   │   ├── wasm/               # WASM plugin support
│   │   ├── health.rs           # Liveness and readiness endpoints
│   │   ├── tenant.rs           # Tenant identity
│   │   ├── rl_adapter.rs       # Glue to the RL control plane (crates/rl)
│   │   └── version.rs          # Version information
│   ├── tests/                  # Integration tests
│   └── benches/                # Criterion benchmarks
├── crates/                     # Library crates (directory: package name)
│   ├── auth/                   # smg-auth: control-plane authentication and authorization
│   ├── data_connector/         # data-connector: conversation and response storage backends
│   ├── engine_zmq_client/      # engine-zmq-client: ZMQ transport for same-host engine connections
│   ├── external_router/        # smg-external-router: third-party provider routers
│   ├── grpc_client/            # smg-grpc-client: engine gRPC clients, .proto files, Python and Go packages
│   ├── kv_index/               # kv-index: radix trees for cache-aware routing
│   ├── mcp/                    # smg-mcp: Model Context Protocol client
│   ├── mesh/                   # smg-mesh: HA mesh gossip and state synchronization
│   ├── mm_rdma/                # smg-mm-rdma: multimodal pixel RDMA (NIXL) transport
│   ├── mock_worker/            # mock-worker: mock HTTP, gRPC, and ZMQ workers for local and scale testing
│   ├── multimodal/             # llm-multimodal: image, video, and audio preprocessing
│   ├── protocols/              # openai-protocol: OpenAI-compatible API types
│   ├── radix_tree/             # smg-radix-tree: prefix-membership radix tree
│   ├── reasoning_parser/       # reasoning-parser: reasoning output extraction
│   ├── rl/                     # smg-rl: RL control plane
│   ├── tokenizer/              # llm-tokenizer: tokenizers, caching, and chat templates
│   ├── tool_parser/            # tool-parser: tool-call parsing
│   ├── wasm/                   # smg-wasm: WebAssembly runtime for plugins
│   └── workflow/               # wfaas: workflow engine for multi-step operations
├── bindings/
│   ├── python/                 # PyPI package `smg`: `smg launch` / `smg serve` and the Rust extension
│   └── golang/                 # Go SDK over a Rust FFI library
├── clients/
│   ├── rust/                   # smg-client: Rust HTTP client
│   ├── python/                 # smg-client: Python client (types generated from OpenAPI)
│   ├── java/                   # Generated Java types
│   └── openapi-gen/            # Generates the OpenAPI spec from the protocol types
├── grpc_servicer/              # smg-grpc-servicer: Python gRPC servicers for vLLM, SGLang, TokenSpeed, MLX
├── e2e_test/                   # End-to-end tests against real engines (pytest)
├── deploy/helm/smg/            # Helm chart
├── docker/                     # Dockerfiles for the gateway and engine images
├── examples/                   # RL and WASM plugin examples
├── scripts/                    # Dev and CI scripts (e2e-test.sh, install_opencv.sh, ci_*.sh, ...)
├── Cargo.toml                  # Workspace manifest and shared lints
├── Cargo.lock                  # Committed lockfile
├── clippy.toml, rustfmt.toml   # Rust lint and format configuration
├── ruff.toml, mypy.ini         # Python lint configuration
└── Makefile                    # Development shortcuts (make help)
```

The documentation is not part of this tree: it lives in [smg-project/smg-docs](https://github.com/smg-project/smg-docs) and is published at [lightseek.org/smg](https://lightseek.org/smg).

---

## Running Locally

You don't need a GPU to exercise the gateway: `crates/mock_worker` serves canned responses on the same HTTP surface the engines expose.

```bash
# Terminal 1: two mock HTTP workers on 127.0.0.1:9000 and 127.0.0.1:9001
cargo run --locked -p mock-worker -- --http-count 2

# Terminal 2: the gateway, round-robin across them
cargo run --locked --bin smg -- launch \
  --worker-urls http://127.0.0.1:9000 http://127.0.0.1:9001 \
  --policy round_robin --log-level debug

# Terminal 3: a request
curl http://127.0.0.1:30000/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model": "mock-model", "messages": [{"role": "user", "content": "Hello"}]}'
```

The gateway listens on port `30000` and serves Prometheus metrics on port `29000` by default. The mock worker also has gRPC and ZMQ modes and a realistic engine simulator (`--engine realistic`) that exercises load- and cache-aware routing; see `crates/mock_worker/README.md`.

---

## Linting and Formatting

### Rust

```bash
# Format (nightly, because rustfmt.toml uses unstable options)
cargo +nightly fmt --all

# Check formatting without changing files, as CI does
cargo +nightly fmt --all -- --check

# Lint the way CI and the pre-commit hook do
cargo clippy --locked --all-targets --all-features -- -D warnings

# Apply clippy's machine-applicable fixes, then re-run the lint above
cargo clippy --locked --all-targets --all-features --fix --allow-dirty
```

CI also lints two reduced builds, so code that only one provider router uses cannot hide behind the default feature set:

```bash
# Self-hosted-only build (no provider routers)
cargo clippy --locked -p smg -p smg-external-router --no-default-features \
  --features smg/grpc-client,smg/jemalloc-stats --all-targets -- -D warnings

# Anthropic-only build
cargo clippy --locked -p smg -p smg-external-router --no-default-features \
  --features smg/grpc-client,smg/jemalloc-stats,smg/provider-anthropic --all-targets -- -D warnings
```

Clippy runs with `-D warnings`, so every workspace lint set to `warn` fails CI too; see [Code Style](code-style.md#linting).

!!! note "`--all-features` needs OpenCV and libclang"
    `--all-features` turns on `opencv-video`, which links system OpenCV, and `mm-rdma`, whose NIXL bindings are generated with bindgen; both need libclang at build time. `make opencv-deps` runs `scripts/install_opencv.sh`, which installs `opencv` and `pkg-config` with Homebrew on macOS (libclang comes with the Xcode Command Line Tools), or `clang`, `libclang-dev`, `cmake`, `ninja-build`, `pkg-config`, and `libopencv-dev` with apt on Debian/Ubuntu. To lint without them, drop `--all-features`:

    ```bash
    cargo clippy --locked --workspace --all-targets -- -D warnings
    ```

### Python

Python code in `e2e_test/`, `bindings/python/`, and `scripts/` is linted and formatted with [ruff](https://docs.astral.sh/ruff/); `e2e_test/` and `bindings/python/` are also type-checked with [mypy](https://mypy-lang.org/). CI's `python-lint` job runs:

```bash
ruff check e2e_test/ bindings/python/ scripts/
ruff format --check e2e_test/ bindings/python/ scripts/
mypy e2e_test/ --config-file mypy.ini
mypy bindings/python/ --config-file mypy.ini
```

`ruff check --fix` and `ruff format` (without `--check`) apply the fixes. The configuration lives in `ruff.toml` and `mypy.ini` at the repository root. The pre-commit hooks run ruff on every commit; mypy runs only in CI or by hand.

---

## DCO Sign-Off

Every commit needs a `Signed-off-by` line certifying the [Developer Certificate of Origin](https://developercertificate.org/):

```bash
git commit -s -m "feat(policies): add a weighted policy"
```

`-s` appends `Signed-off-by: Your Name <you@example.com>` from your git `user.name` and `user.email`. The `dco-check` hook rejects a commit whose sign-off does not exactly match your git config, and the required `DCO` check rejects a PR with unsigned commits. To sign off commits you already made:

```bash
git rebase HEAD~N --signoff   # N = the number of commits to sign
git push --force-with-lease
```

---

## Testing

### Rust Unit and Integration Tests

Unit tests sit next to the code in `#[cfg(test)] mod tests` blocks. Integration tests live in `model_gateway/tests/`: each top-level `.rs` file there is a separate test binary, and `api_tests.rs`, `reliability_tests.rs`, `routing_tests.rs`, and `security_tests.rs` each pull in the matching subdirectory plus the shared helpers in `tests/common/`.

```bash
# Everything (what `make test` and CI run)
cargo test --locked

# The gateway's unit tests only
cargo test --locked -p smg --lib

# One integration test binary: routing_tests.rs and tests/routing/
cargo test --locked -p smg --test routing_tests

# Tests whose names contain a string, with their output shown
cargo test --locked -p smg round_robin -- --nocapture
```

A unit test, from `model_gateway/src/policies/round_robin.rs` (abridged):

```rust
#[cfg(test)]
mod tests {
    use super::*;
    use crate::worker::{BasicWorkerBuilder, WorkerType};

    #[test]
    fn test_round_robin_selection() {
        let policy = RoundRobinPolicy::new();
        let workers: Vec<Arc<dyn Worker>> = vec![/* three BasicWorkerBuilder workers */];

        // Should select workers in order: 0, 1, 2, 0, 1, 2, ...
        let info = SelectWorkerInfo::default();
        assert_eq!(policy.select_worker(&workers, &info), Some(0));
        assert_eq!(policy.select_worker(&workers, &info), Some(1));
        assert_eq!(policy.select_worker(&workers, &info), Some(2));
        assert_eq!(policy.select_worker(&workers, &info), Some(0));
    }
}
```

An integration test drives the full router against in-process mock workers, from `model_gateway/tests/routing/load_balancing_test.rs` (abridged):

```rust
use crate::common::{AppTestContext, TestRouterConfig, TestWorkerConfig};

#[tokio::test]
async fn test_round_robin_distribution() {
    let config = TestRouterConfig::round_robin(3100);
    let ctx =
        AppTestContext::new_with_config(config, TestWorkerConfig::healthy_workers(19001, 3))
            .await;

    let app = ctx.create_app();
    // ... send 30 POST /generate requests through `app` and count the 200s ...

    assert_eq!(
        success_count, num_requests,
        "All requests should succeed with round robin"
    );

    ctx.shutdown().await;
}
```

These tests bind fixed local ports for their mock workers (this one uses 19001 to 19003; the router itself runs in-process through `app`), so run one test process at a time on a machine.

### Postgres Storage Tests

The Postgres backend tests are `#[ignore]`d because they need a live database. CI runs them against a shared instance; locally:

```bash
docker run -d -e POSTGRES_PASSWORD=test -e POSTGRES_DB=smg_test -p 5432:5432 postgres:16
DATA_CONNECTOR_TEST_POSTGRES_URL=postgres://postgres:test@localhost:5432/smg_test \
  cargo test --locked -p data-connector --test postgres_integration -- --ignored --test-threads=1
```

`--test-threads=1` matters: each test runs schema migrations against the same fresh database.

### WASM Fixtures

The storage-hook tests in `crates/wasm/tests/` skip silently unless their guest fixture is built. CI builds the fixtures first; locally:

```bash
bash crates/wasm/tests/fixtures/build_fixtures.sh   # adds the wasm32-wasip2 target if missing
cargo test --locked -p smg-wasm
```

### Python Bindings

The Python package in `bindings/python` wraps the gateway (`smg launch`, `smg serve`). Build it into a virtualenv and run its unit tests:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install maturin pytest
make python-dev          # maturin develop: a debug build of bindings/python

cd bindings/python
pytest -q tests
```

CI runs the same tests against the wheel it builds and requires 80% coverage (`--cov=smg --cov-fail-under=80`). `make python-test` is a different suite: it runs the GPU e2e tests in `e2e_test/`. The Python gRPC servicers in `grpc_servicer/` have their own tests (CI runs `pytest -q grpc_servicer/tests -rs`) and development notes in `grpc_servicer/DEVELOPMENT.md`.

### End-to-End Tests

The harness in `e2e_test/` starts real inference engines and the gateway, then drives them with the OpenAI SDK and `smg-client`. It needs GPUs, the engine under test installed in the same environment (CI uses `scripts/ci_install_<engine>.sh`), and the model weights.

```bash
# One-time setup, in the virtualenv that has the engine installed
make python-dev                       # the harness launches `python3 -m smg.launch_router`
make generate-python-types            # generate the smg_client types (uses uvx)
bash scripts/ci_install_e2e_deps.sh   # pip install e2e_test/ and clients/python/

# One suite, on vLLM, 1-GPU tests only
E2E_RUNTIME=vllm E2E_ENGINE=vllm E2E_GPU_TIER=1 ROUTER_LOCAL_MODEL_PATH=$HOME/models \
  pytest e2e_test/chat_completions -s -vv
```

`scripts/e2e-test.sh <suite>` wraps pytest with two reruns per failure for the `router`, `chat`, `responses`, `embeddings`, `realtime`, `benchmarks`, and `go` suites; without an argument it runs the router, embeddings, chat, and responses suites.

| Variable | Effect |
|----------|--------|
| `E2E_RUNTIME` | Engine for local workers: `sglang` (default), `vllm`, `trtllm`, `tokenspeed`, or `mlx` (Apple silicon) |
| `E2E_ENGINE` | Keep only the tests marked for this engine |
| `E2E_GPU_TIER` | Keep only the tests for this GPU count (`1`, `2`, `4`, ...); unmarked tests count as 1 |
| `E2E_CONNECTION_MODE` | Run the local cases over `http`, `grpc`, or `zmq`; PD and EPD cases keep their own transport |
| `ROUTER_LOCAL_MODEL_PATH` | Directory of downloaded models, laid out as `<path>/<org>/<model>`; a missing model falls back to its Hugging Face ID |
| `SHOW_ROUTER_LOGS`, `SHOW_WORKER_LOGS` | Set to `1` to stream gateway or worker logs |

The harness also has CPU-only unit tests of its own:

```bash
pip install ./e2e_test
PYTHONPATH=e2e_test pytest -q --noconftest e2e_test/infra e2e_test/fixtures
```

On pull requests, CI runs each e2e lane only when the files it covers change:

| GPUs | Lanes (engines) |
|------|-----------------|
| 1 | Chat (SGLang, vLLM, TensorRT-LLM, TokenSpeed); chat over ZMQ (vLLM, TokenSpeed); worker-side multimodal processing (vLLM); completions and embeddings (SGLang, vLLM); realtime (vLLM); router suite (SGLang, vLLM); Responses API (SGLang); Go bindings (SGLang) |
| 2 | PD disaggregation (SGLang, vLLM with NIXL or Mooncake, TokenSpeed); PD multimodal (vLLM); ZMQ data-parallel chat (vLLM, TokenSpeed) |
| 4 | Chat (SGLang, vLLM, TensorRT-LLM); router suite (SGLang); EPD multimodal (TokenSpeed); PD topologies (SGLang, vLLM, TokenSpeed) |
| Other | Third-party provider lanes on CPU runners (Anthropic Messages, OpenAI Responses and Realtime, xAI Responses); Kubernetes discovery on a kind cluster and MLX on Apple silicon, in separate workflows |

### Go Bindings

```bash
cd bindings/golang
make test   # builds the Rust FFI library (release), then runs go test ./...
```

### Benchmarks

Criterion benchmarks live in `model_gateway/benches/` and in a few crates, such as `crates/kv_index` and `crates/multimodal`. Run one by name:

```bash
cargo bench --locked --bench manual_policy_benchmark
cargo bench --locked --bench tool_parser_benchmark
```

When a PR touches benchmarked code, the matching `benchmark-*` workflow (manual policy, radix tree, request processing, tokenizer, or tool parser) runs its benchmark. The GPU `benchmarks` job (`e2e_test/benchmarks`, driven by genai-bench) is advisory: a missed latency threshold is reported as a warning and does not block merge.

---

## Adding a New Feature

### Example: Adding a Routing Policy

Routing policies implement the `LoadBalancingPolicy` trait from `model_gateway/src/policies/mod.rs`. A new policy touches these files:

| Step | File | Change |
|------|------|--------|
| 1 | `model_gateway/src/policies/<name>.rs` | Implement `LoadBalancingPolicy`, with unit tests |
| 2 | `model_gateway/src/policies/mod.rs` | `mod <name>;` and a `pub use` |
| 3 | `model_gateway/src/config/types.rs` | A `PolicyConfig` variant with its serialized name and options |
| 4 | `model_gateway/src/policies/factory.rs` | Construct it in `PolicyFactory::create_from_config` and `create_by_name` |
| 5 | `model_gateway/src/main.rs` | Add the name to the `--policy` value list (and to `--prefill-policy`/`--decode-policy` if it applies to PD) and map it in `parse_policy` |
| 6 | `bindings/python/src/lib.rs`, `bindings/python/src/smg/router.py`, `bindings/python/src/smg/router_args.py` | Mirror it in `PolicyType` and its config conversion, the `policy_from_str` map, and the CLI choices, then run `make python-dev` |
| 7 | `model_gateway/tests/routing/` | An integration test through the router |
| 8 | smg-docs | The [configuration reference](../reference/configuration.md) and [load balancing](../concepts/routing/load-balancing.md) pages |

The trait requires `select_worker`, `name`, and `as_any`. The other methods (`on_request_complete`, `needs_request_text`, `update_loads`, `needs_backend_loads`, `remove_worker`, and `reset`) have no-op defaults that stateful or load-aware policies override. An illustrative skeleton:

```rust
// model_gateway/src/policies/my_policy.rs (illustrative)
use std::sync::Arc;

use super::{get_healthy_worker_indices, LoadBalancingPolicy, SelectWorkerInfo};
use crate::worker::Worker;

#[derive(Debug, Default)]
pub struct MyPolicy;

impl LoadBalancingPolicy for MyPolicy {
    fn select_worker(
        &self,
        workers: &[Arc<dyn Worker>],
        _info: &SelectWorkerInfo,
    ) -> Option<usize> {
        // Return an index into `workers`, or None when no worker is available.
        get_healthy_worker_indices(workers).first().copied()
    }

    fn name(&self) -> &'static str {
        "my_policy"
    }

    fn as_any(&self) -> &dyn std::any::Any {
        self
    }
}
```

[REVIEW.md](https://github.com/smg-project/smg/blob/main/REVIEW.md) calls config changes the number one source of bugs: a new option has to reach the CLI arguments, `config/types.rs`, both conversion paths in `main.rs`, the Python bindings, and the Go SDK.

---

## Debugging

### Logging

```bash
# --log-level takes debug, info (default), warn, or error
cargo run --locked --bin smg -- launch --worker-urls http://127.0.0.1:9000 --log-level debug

# RUST_LOG replaces the whole filter, which allows per-module and trace levels
RUST_LOG=info,smg::policies=trace \
  cargo run --locked --bin smg -- launch --worker-urls http://127.0.0.1:9000
```

Without `RUST_LOG`, `--log-level` applies to the crates listed in `WORKSPACE_CRATES` in `model_gateway/src/observability/logging.rs`, and everything else logs at `warn`. Add a new workspace crate to that list.

### Debugger

```bash
cargo build --locked --bin smg
lldb target/debug/smg -- launch --worker-urls http://127.0.0.1:9000
```

The `dev` profile builds workspace crates with limited debug info (`debug = 1`) and dependencies without it.

### Profiling

Release builds keep their symbols (`strip = false`, smg-project/smg#1582), so CPU profiles of a release binary are readable:

```bash
cargo install flamegraph
cargo flamegraph --bin smg -- launch --worker-urls http://127.0.0.1:9000
```

For memory, the gateway uses jemalloc as its global allocator (except on MSVC and musl targets). Default builds export the `smg_allocator_*_bytes` gauges on the metrics endpoint. `--features jemalloc-profiling` compiles in jemalloc's heap profiler, which reads its options from the `_RJEM_MALLOC_CONF` environment variable because SMG's jemalloc uses prefixed symbols.

---

## Documentation

These docs live in [smg-project/smg-docs](https://github.com/smg-project/smg-docs) and are published at [lightseek.org/smg](https://lightseek.org/smg). The smg repository no longer has a `docs/` folder (smg-project/smg#2012). The site is built with SvelteKit: pages are Markdown files in `src/lib/content/<section>/`, and navigation is defined in `src/lib/config/*-nav.ts`.

```bash
git clone https://github.com/smg-project/smg-docs.git
cd smg-docs
pnpm install
pnpm dev     # local dev server
pnpm lint    # prettier + eslint
pnpm check   # type checks
pnpm build   # production build
```

The docs site needs Node 22 and pnpm 10 (`pnpm node:use` installs Node with fnm). CI runs lint, type checks, and a production build on every docs PR.

### Writing Documentation

- Put each page where readers look for it: **Getting Started** for task guides, **Concepts** for how things work, **Reference** for flags, APIs, and metrics.
- Verify every flag, default, endpoint, and metric name against the smg source (or `smg launch --help`) for the release you document.
- Include runnable examples, and run them.
- The Markdown pipeline supports mkdocs-material admonitions, collapsible blocks, content tabs, and card grids; copy the syntax from a neighboring page.

---

## Release Process

Releases are cut by the core maintainers:

1. A `chore(release): bump versions for vX.Y.Z` PR bumps the versions. `make bump-version VERSION=X.Y.Z` updates the gateway and binding versions (`model_gateway/Cargo.toml`, both binding `Cargo.toml` files, `bindings/python/pyproject.toml`, and `bindings/python/src/smg/version.py`), and `make check-versions` checks that every workspace crate changed since the last tag has a version bump.
2. Merging the PR to `main` starts the release workflows: `release-crates` publishes each crate whose version is newer than the one on crates.io, and `release-pypi`, `release-docker`, and `release-helm` run when `bindings/python/pyproject.toml` changes.

Crate versions follow [Semantic Versioning](https://semver.org/). `make check-versions` derives the bump level from conventional commits: a `!` after any type (such as `feat!` or `fix!`) or `BREAKING CHANGE` in the message for major, `feat` for minor, anything else for patch.

---

## CI at a Glance

| Workflow or job | What it checks | Run it locally |
|-----------------|----------------|----------------|
| `pre-commit` | The pre-commit hooks on all files, minus rustfmt, clippy, and the branch and commit-message hooks | `pre-commit run --all-files` |
| `python-lint` | ruff and mypy | [Python](#python) |
| `unit-tests` | Clippy (all features, self-hosted-only, and Anthropic-only builds), `cargo +nightly fmt -- --check`, `cargo test`, and the Postgres tests | [Rust tests](#rust-unit-and-integration-tests) |
| `build-wheel`, `python-unit-tests` | The Python wheel and Go FFI library; binding, servicer, and e2e-harness unit tests | [Python Bindings](#python-bindings) |
| `e2e-*` | GPU lanes against real engines, and CPU lanes against third-party providers | [End-to-End Tests](#end-to-end-tests) |
| `go-unit-tests`, `go-bindings-e2e` | The Go bindings | [Go Bindings](#go-bindings) |
| `finish` | Fails if any job above failed; required for merge | — |
| `benchmark-*`, `benchmarks` | Performance; advisory | [Benchmarks](#benchmarks) |

On pull requests, a `detect-changes` job skips work whose inputs did not change: the Rust `unit-tests` job runs only when Rust code, manifests, or lint configuration change, and each e2e lane only when its paths do. Forks can run CI on their own runners by setting repository variables such as `SMG_RUNNER_CPU` and `SMG_RUNNER_GPU_1`; see [Running CI on your own runners](https://github.com/smg-project/smg/blob/main/CONTRIBUTING.md#running-ci-on-your-own-runners).

---

## Common Issues

### `protoc` Not Found

The `smg-grpc-client` and `smg-mesh` build scripts compile `.proto` files and fail when `protoc` is missing. Install `protobuf-compiler` (apt) or `protobuf` (Homebrew), or point the `PROTOC` environment variable at the binary.

### OpenSSL Not Found on macOS

```bash
brew install openssl@3
export OPENSSL_DIR="$(brew --prefix openssl@3)"
```

Alternatively, build OpenSSL from source with the `vendored-openssl` feature: `cargo build --locked -p smg --features vendored-openssl`.

### `Failed to find installed OpenCV package`

The `--all-features` build (used by `make check`, `make pre-commit`, the `clippy` pre-commit hook, and CI's lint step) enables `opencv-video`, which links system OpenCV. Install it, or lint without `--all-features`:

```bash
# Install OpenCV (Homebrew on macOS, apt on Debian/Ubuntu)
make opencv-deps

# ...or skip the feature for a quick lint
cargo clippy --locked --workspace --all-targets -- -D warnings
```

### `cannot update the lock file ... because --locked was passed`

A `Cargo.toml` changed without a matching `Cargo.lock` update. See [Cargo.lock and `--locked`](#cargolock-and---locked).

### Tests Fail with "Address already in use"

The integration tests bind fixed local ports. Make sure no other test run (from another worktree, for example) or local service is using them, and run one `cargo test` process at a time.

### Clippy Passes Locally but Fails in CI

- CI uses Rust 1.98.0, and other toolchains can report different lints.
- CI lints with `--all-features` and also lints the self-hosted-only and Anthropic-only builds.
- `-D warnings` turns every `warn`-level workspace lint into an error.

---

## Getting Help

- **Stuck?** Open a [Discussion](https://github.com/smg-project/smg/discussions) or ask in `#sig-smg` on [Slack](https://slack.lightseek.org)
- **Found a bug?** Open an [Issue](https://github.com/smg-project/smg/issues)
- **Questions about a PR?** Ask its reviewers or the code owners of the files it changes
