---
title: Code Style Guide
---

# Code Style Guide

This guide describes the coding standards used in SMG. Most of them are enforced: rustfmt and clippy run on every commit (through pre-commit) and in CI, configured by `rustfmt.toml`, `clippy.toml`, and the `[workspace.lints]` tables in the workspace `Cargo.toml`. Examples labeled with a file path come from the smg source; the others are illustrative.

---

## Rust Style

### Formatting

Format with nightly rustfmt, because `rustfmt.toml` uses unstable options:

```bash
cargo +nightly fmt --all
```

`rustfmt.toml` only configures how imports and `mod` declarations are grouped and ordered. Everything else is rustfmt's default style: 4-space indentation, 100-column lines, and trailing commas in multi-line lists.

| Option | Value | Effect |
|--------|-------|--------|
| `imports_granularity` | `"Crate"` | Merges imports from the same crate into one `use` |
| `group_imports` | `"StdExternalCrate"` | Groups imports: `std`, `core`, and `alloc` first, then external crates, then `self`, `super`, and `crate` |
| `reorder_imports` | `true` | Sorts imports within each group |
| `reorder_modules` | `true` | Sorts `mod` declarations |

Don't group imports by hand; rustfmt does it. The result, from `model_gateway/src/policies/round_robin.rs`:

```rust
use std::{
    hash::{DefaultHasher, Hash, Hasher},
    sync::{
        atomic::{AtomicU64, AtomicUsize, Ordering},
        Arc,
    },
};

use dashmap::DashMap;

use super::{get_healthy_worker_indices, LoadBalancingPolicy, SelectWorkerInfo};
use crate::worker::Worker;
```

### Linting

All code must pass clippy with warnings denied:

```bash
cargo clippy --locked --all-targets --all-features -- -D warnings
```

Because of `-D warnings`, a lint set to `warn` in `[workspace.lints]` fails CI just like one set to `deny`. These are the rules that shape everyday code:

| Lint | Level | Rule |
|------|-------|------|
| `unsafe_code` | deny | No `unsafe`. The few FFI modules that need it, such as `bindings/golang/src/lib.rs`, opt out at module level |
| `clippy::unwrap_used` | deny | No `.unwrap()` in production code; tests may unwrap (`clippy.toml`) |
| `clippy::expect_used`, `clippy::panic` | warn | Return errors instead. A justified exception carries `#[expect(..., reason = "...")]` |
| `clippy::dbg_macro`, `todo`, `unimplemented`, `unreachable` | deny | No debug or placeholder macros |
| `clippy::print_stdout`, `clippy::print_stderr` | warn | Log with `tracing`, not `println!` or `eprintln!` |
| `clippy::allow_attributes` | warn | Silence a lint with `#[expect(...)]`, not `#[allow(...)]` |
| `clippy::disallowed_methods` | warn | `tokio::spawn` (confirm the gateway may shut down without waiting for the task), `std::process::exit` (skips the normal shutdown logic), and `Uuid::new_v4` (use the time-sortable `Uuid::now_v7`) |
| `clippy::absolute_paths` | warn | Import paths of four or more segments with `use` (`absolute-paths-max-segments = 3`) |
| `clippy::uninlined_format_args` | warn | Write `format!("{name}")`, not `format!("{}", name)` |
| `unused_qualifications` | warn | No redundant path prefixes |

`[workspace.lints.clippy]` enables 23 more lints, such as `unused_async`, `unnecessary_wraps`, `or_fun_call`, and `large_futures`. `clippy.toml` raises the `large_futures` threshold to 20480 bytes and exempts `http::Request` and `http::Response` from `result_large_err`.

When a lint must be silenced, say why. From `model_gateway/src/server.rs`:

```rust
#[expect(
    clippy::disallowed_methods,
    reason = "supervisor outlives the task it watches; it ends when that task ends"
)]
spawn(async move {
    // ...
});
```

`#[expect]` is checked too: once the code stops triggering the lint, the unfulfilled expectation produces a warning, which fails CI, so stale suppressions get removed.

---

## Naming Conventions

### General Rules

| Item | Convention | Example |
|------|------------|---------|
| Crate packages | `kebab-case` | `smg-grpc-client` |
| Crate directories and library names | `snake_case` | `crates/grpc_client`, `smg_grpc_client` |
| Modules | `snake_case` | `service_discovery` |
| Types | `PascalCase` | `CircuitBreaker` |
| Functions | `snake_case` | `get_healthy_worker_indices` |
| Constants | `SCREAMING_SNAKE_CASE` | `MAX_TRACKED_SETS` |
| Variables | `snake_case` | `worker_count` |

### Specific Patterns

**Constructors**: use `new()` for the plain case and `with_*()` for variants that take configuration. From `model_gateway/src/policies/factory.rs`:

```rust
Arc::new(RoundRobinPolicy::new())
Arc::new(CacheAwarePolicy::with_config(config))
```

**Builders**: use a builder for types with many options, and validate in `build()`. `RouterConfig::builder()` in `model_gateway/src/config/builder.rs` returns a `RouterConfigBuilder`:

```rust
let config = RouterConfig::builder()
    .policy(PolicyConfig::Random)
    .host("127.0.0.1")
    .port(3001)
    .request_timeout_secs(60)
    .build()?; // validates; build_unchecked() skips validation
```

**Async functions**: don't add an `_async` suffix, unless the function is the async twin of a blocking function with the same name:

```rust
// crates/tokenizer/src/factory.rs: a blocking function and its async twin
pub fn create_tokenizer(model_name_or_path: &str) -> Result<Arc<dyn traits::Tokenizer>> { ... }
pub async fn create_tokenizer_async(
    model_name_or_path: &str,
) -> Result<Arc<dyn traits::Tokenizer>> { ... }

// No blocking twin: no suffix
async fn fetch_models() -> Result<Vec<Model>> { ... }

// Avoid
async fn fetch_models_async() -> Result<Vec<Model>> { ... }
```

---

## Code Organization

### Module Structure

Order a module's items the same way throughout: imports, constants, types, inherent `impl` blocks, trait implementations, and tests last. From `model_gateway/src/policies/round_robin.rs` (abridged):

```rust
//! Round-robin load balancing policy

// 1. Imports, grouped by rustfmt (see Formatting)
use dashmap::DashMap;

use super::{get_healthy_worker_indices, LoadBalancingPolicy, SelectWorkerInfo};
use crate::worker::Worker;

// 2. Constants
const MAX_TRACKED_SETS: usize = 4096;

// 3. Types
#[derive(Debug, Default)]
pub struct RoundRobinPolicy {
    // ...
}

// 4. Inherent implementations
impl RoundRobinPolicy {
    pub fn new() -> Self {
        // ...
    }
}

// 5. Trait implementations
impl LoadBalancingPolicy for RoundRobinPolicy {
    // select_worker, name, reset, as_any
}

// 6. Tests
#[cfg(test)]
mod tests {
    use super::*;
    // ...
}
```

---

## Error Handling

### Error Types

Define domain-specific errors with `thiserror`. From `model_gateway/src/config/mod.rs`:

```rust
#[derive(Debug, thiserror::Error)]
pub enum ConfigError {
    #[error("Validation failed: {reason}")]
    ValidationFailed { reason: String },

    #[error("Invalid value for field '{field}': {value} - {reason}")]
    InvalidValue {
        field: String,
        value: String,
        reason: String,
    },

    #[error("Incompatible configuration: {reason}")]
    IncompatibleConfig { reason: String },

    #[error("Missing required field: {field}")]
    MissingRequired { field: String },
}

pub type ConfigResult<T> = Result<T, ConfigError>;
```

### Error Propagation

Use the `?` operator to propagate errors:

```rust
// Good
fn process() -> Result<Response> {
    let data = fetch_data()?;
    let result = transform(data)?;
    Ok(result)
}

// Avoid
fn process() -> Result<Response> {
    let data = match fetch_data() {
        Ok(d) => d,
        Err(e) => return Err(e),
    };
    // ...
}
```

### Error Context

Where a module uses `anyhow`, attach context with `with_context`, so the message is only formatted on the error path. From `model_gateway/src/routers/grpc/multimodal/config.rs`:

```rust
let config: serde_json::Value = std::fs::read_to_string(&config_path)
    .with_context(|| format!("Failed to read config.json at {}", config_path.display()))
    .and_then(|s| {
        serde_json::from_str(&s).with_context(|| {
            format!("Failed to parse config.json at {}", config_path.display())
        })
    })?;
```

### Panics

Production code returns errors instead of panicking: `.unwrap()` is denied, and `.expect()` or `panic!` needs a stated reason. Where startup cannot continue, the code says so. From `model_gateway/src/observability/metrics.rs`:

```rust
#[expect(
    clippy::expect_used,
    reason = "startup initialization — metrics exporter must be installed or the process cannot serve metrics"
)]
pub fn start_prometheus(config: PrometheusConfig) -> PrometheusHandle {
```

Tests may unwrap, expect, and panic freely: `clippy.toml` sets `allow-unwrap-in-tests`, `allow-expect-in-tests`, and `allow-panic-in-tests`.

---

## Documentation

### Module Documentation

Start each public module with a `//!` comment that says what it does and, when it is not obvious, why it exists. From `model_gateway/src/health.rs` (abridged):

```rust
//! Liveness, readiness, and health endpoints: O(1) event-maintained readiness
//! state and an optional isolated probe listener. Not k8s-specific, though
//! Kubernetes is the motivating consumer.
//!
//! # Why this exists (#1694)
//!
//! `/readiness` used to scan the whole fleet on every probe ...
```

### Function Documentation

Document public items with `///`, adding sections such as `# Arguments` or `# Errors` when they help. From the `LoadBalancingPolicy` trait in `model_gateway/src/policies/mod.rs` (abridged):

```rust
/// Select a single worker from the available workers
///
/// This is used for regular routing mode where requests go to a single worker.
///
/// # Arguments
/// * `workers` - Available workers to select from
/// * `info` - Additional information for routing decisions
fn select_worker(&self, workers: &[Arc<dyn Worker>], info: &SelectWorkerInfo) -> Option<usize>;
```

### Inline Comments

Use inline comments sparingly, to explain why rather than what:

```rust
// Good: explains why, not what
// Use a longer timeout for large requests to avoid false positives
let timeout = if request.body_size() > LARGE_REQUEST_THRESHOLD {
    Duration::from_secs(60)
} else {
    Duration::from_secs(30)
};

// Bad: explains what (obvious from code)
// Set timeout to 30 seconds
let timeout = Duration::from_secs(30);
```

---

## Testing

### Test Organization

Unit tests live in a `#[cfg(test)] mod tests` block at the bottom of the file they test. Integration tests group related cases in nested modules. From `model_gateway/tests/routing/load_balancing_test.rs`:

```rust
#[cfg(test)]
mod round_robin_tests {
    use super::*;

    /// Test that round robin distributes requests evenly across workers
    #[tokio::test]
    async fn test_round_robin_distribution() {
        // ...
    }

    /// Test round robin with one worker failing
    #[tokio::test]
    async fn test_round_robin_with_failing_worker() {
        // ...
    }
}
```

### Test Naming

Name tests after the behavior they check. From `model_gateway/src/policies/round_robin.rs`:

```rust
#[test]
fn test_one_instance_keeps_each_candidate_set_fair() {
    // ...
}

#[test]
fn test_past_the_cap_a_new_set_evicts_the_least_recently_used() {
    // ...
}

// Avoid
#[test]
fn test1() {}
```

### Test Assertions

Prefer `assert_eq!` with a message that states the expectation:

```rust
// Good (model_gateway/tests/routing/load_balancing_test.rs)
assert_eq!(
    success_count, num_requests,
    "All requests should succeed with round robin"
);

// Avoid
assert!(success_count == num_requests);
```

---

## Performance

[REVIEW.md](https://github.com/smg-project/smg/blob/main/REVIEW.md) asks reviewers to watch for `clone()` in gRPC streaming hot paths (per-token response processing) and for worker-registry mutations without proper locking (`DashMap` rather than a bare `HashMap`). The examples below are illustrative.

### Avoid Unnecessary Allocations

```rust
// Good: reuse buffer
let mut buffer = Vec::with_capacity(1024);
for item in items {
    buffer.clear();
    serialize_into(&mut buffer, item)?;
    send(&buffer).await?;
}

// Bad: allocate each iteration
for item in items {
    let buffer = serialize(item)?;
    send(&buffer).await?;
}
```

### Use Appropriate Data Structures

```rust
// Good: HashMap for frequent lookups
let workers: HashMap<String, Worker> = ...;

// Bad: Vec for frequent lookups
let workers: Vec<Worker> = ...;
workers.iter().find(|w| w.url == url) // O(n) each time
```

### Async Best Practices

```rust
// Good: concurrent operations
let (health1, health2) = tokio::join!(
    check_health(&worker1),
    check_health(&worker2),
);

// Bad: sequential when not needed
let health1 = check_health(&worker1).await;
let health2 = check_health(&worker2).await;
```

---

## Security

### Input Validation

Validate external input where it enters the system. Worker URLs, for example, are checked against a scheme allow-list. From `model_gateway/src/config/validation.rs` (abridged):

```rust
pub fn validate_worker_url(url: &str) -> ConfigResult<()> {
    // ...
    const ALLOWED_SCHEMES: &[&str] = &["http", "https", "grpc", "grpcs", "ipc"];
    let scheme = url.split_once("://").map_or("", |(s, _)| s);
    if !ALLOWED_SCHEMES.contains(&scheme) {
        return Err(ConfigError::InvalidValue {
            field: "worker_url".to_string(),
            value: url.to_string(),
            reason: "URL must start with a lowercase http://, https://, grpc://, grpcs://, or ipc:// scheme"
                .to_string(),
        });
    }
    // ...
}
```

### Sensitive Data

Never log secrets. A type that holds one gets a hand-written `Debug` implementation that redacts it. From `model_gateway/src/config/types.rs`:

```rust
impl std::fmt::Debug for TenantApiKeyEntry {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("TenantApiKeyEntry")
            .field("tenant_id", &self.tenant_id)
            .field("key", &"<redacted>")
            .finish()
    }
}
```

---

## Python Style

Python code (the e2e tests, the bindings, the gRPC servicers, and scripts) follows `ruff.toml` and is formatted with ruff's Black-compatible formatter:

- The target is Python 3.12 (`target-version = "py312"`). `grpc_servicer/` still supports Python 3.10, so rule `UP017` is ignored there to keep 3.11+ syntax such as `datetime.UTC` out.
- The formatter wraps lines at 100 columns. The line-length lint (`E501`) only flags lines over 120 characters, which leaves room for comments, docstrings, and help strings the formatter cannot wrap.
- The lint rules are `E`, `F`, `I` (import sorting), `W`, and `UP` (pyupgrade).
- Type checking uses mypy with `mypy.ini`.

---

## Git Commit Messages

Commits follow Conventional Commits, carry a DCO sign-off, and have no AI attribution; see [Commits](index.md#commits) for the types, scopes, and checks.

```text
feat(routers): compile provider routers behind per-provider Cargo features

One Cargo feature per provider router, plus a `providers` umbrella in the
default set, so a self-hosted build can leave the provider routers out.

Signed-off-by: Your Name <your.email@example.com>
```
