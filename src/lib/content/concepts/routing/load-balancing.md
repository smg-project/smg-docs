---
title: Load Balancing
---

# Load Balancing

SMG ships ten load balancing policies that decide which worker serves each request. Set one with `--policy` (default `cache_aware`); the right choice depends on your workload characteristics.

Whatever the policy, only available workers are candidates: healthy, not blocked by an open [circuit breaker](../reliability/circuit-breakers.md), and not excluded by [overload protection](../reliability/overload-protection.md).

---

## Overview

<div class="grid" markdown>

<div class="card" markdown>

### :material-cached: Cache-Aware

**Production default.** Sends each request to a worker that already holds its prefix, and to the lowest expected wait when no worker does or the holder is far busier than the rest.

</div>

<div class="card" markdown>

### :material-timer-sand: Least Load

Routes to the worker with the lowest expected wait: queued token work over throughput, plus a KV-cache pressure penalty. Intended for gRPC workers.

</div>

<div class="card" markdown>

### :material-scale-balance: Power of Two

Samples two workers and routes to the one with the lower expected wait. Near-least-load placement for two load reads per request.

</div>

<div class="card" markdown>

### :material-link-variant: Consistent Hashing

Header-based routing with minimal redistribution on scaling. Ideal for session affinity.

</div>

<div class="card" markdown>

### :material-tray-full: Bucket

Request-length buckets with adaptive boundaries, for the prefill leg of PD disaggregation.

</div>

<div class="card" markdown>

### :material-arrow-right-bold: Passthrough

Forwards every request to the single available worker without reading load or cache state. For one-worker gateways.

</div>

</div>

---

## Policy Comparison

| Policy | Load Aware | Cache Affinity | Session Affinity | Complexity | Best For |
|--------|:----------:|:--------------:|:----------------:|:----------:|----------|
| `cache_aware` | :material-check: | :material-check: | :material-close: | O(prefix) | **Production LLM** |
| `least_load` | :material-check: | :material-close: | :material-close: | O(n) | Load-aware routing on gRPC workers |
| `power_of_two` | :material-check: | :material-close: | :material-close: | O(1) | Load balancing on large fleets |
| `bucket` | :material-check: | :material-close: | :material-close: | O(n) | PD prefill leg |
| `consistent_hashing` | :material-close: | :material-close: | :material-check: | O(log n) | Session affinity |
| `prefix_hash` | :material-check: | Partial | :material-close: | O(log n) | Lightweight caching |
| `manual` | :material-close: | :material-close: | :material-check: | O(1) | Stateful chat |
| `round_robin` | :material-close: | :material-close: | :material-close: | O(n) | Even distribution |
| `random` | :material-close: | :material-close: | :material-close: | O(1) | Testing |
| `passthrough` | :material-close: | :material-close: | :material-close: | O(1) | Single worker |

Complexity is the selection cost per request over `n` available workers. [Sticky sessions](sticky-sessions.md) (`--routing-key-override`) add session affinity on top of any policy.

---

## Load Signals

Load-aware policies read one or both of two signals:

- **In-flight requests.** The gateway counts the requests it has sent to each worker that have not finished yet; a streamed response counts until its body ends. The count is kept for every request under every policy, on the HTTP, gRPC and PD paths alike, and is exported as `smg_worker_requests_active`. Each gateway replica counts only its own traffic. `prefix_hash` and the `cache_aware` spill gate compare these counts, and `least_load` and `power_of_two` fall back to them for workers without a load report.
- **Load reports.** The gateway polls each worker's load every `--load-monitor-interval` seconds (default `10`). HTTP workers are asked for `/v1/loads` whatever the engine, falling back to the engine's Prometheus `/metrics` (SGLang, vLLM) when that route does not answer; gRPC and ZMQ workers report over their engine connection (`GetLoads`). A report carries the worker's waiting requests and KV-cache usage, plus queued tokens and generation throughput where the engine exposes them. `least_load`, `power_of_two` and `cache_aware` read these reports. See [Load Monitoring](../../reference/configuration.md#load-monitoring-configuration) for the polling options.

In `least_load`, `power_of_two` and `cache_aware`, scores that tie exactly are broken uniformly at random, so equally idle workers share traffic instead of the first worker taking every tie.

---

## Cache-Aware

The **recommended policy** for production LLM inference, and the default. It keeps a per-model prefix tree of the requests it has routed to each worker (for gRPC workers that publish KV-cache events, the engines' reported cache contents instead) and sends each request to a worker that already holds its prefix. When no worker holds enough of it (`--cache-threshold` in tree mode), or every holder is far busier than the fleet mean (beyond both `--balance-abs-threshold` and `--balance-rel-threshold`), it picks by the same expected-wait score as [`least_load`](#least-load).

```bash
smg --policy cache_aware --worker-urls http://w1:8000 http://w2:8000
```

<div class="grid" markdown>

<div class="card" markdown>

#### :material-check-circle: Advantages

- Reuses KV cache across requests that share a prefix
- Falls back to the lowest expected wait on a cache miss
- Spills requests off a holder that is far busier than the fleet

</div>

<div class="card" markdown>

#### :material-close-circle: Limitations

- Memory for one prefix tree per model (bounded by `--max-tree-size`)
- O(prefix) matching per request
- Needs the request's text or token IDs to route

</div>

</div>

**Use when:** Production workloads with repeated prefixes—multi-turn conversations, RAG applications, batch processing with templates.

[**Learn more about Cache-Aware Routing →**](cache-aware.md)

---

## Bucket

Routes prefill requests by size in PD disaggregation. Each prefill worker owns a range of request lengths, counted in characters of prompt text or in tokens when the request carries token IDs; the last worker's range is open-ended. Every 5 seconds the policy re-checks the ranges. On the first check that sees traffic, and afterwards whenever the load per worker has changed by more than a factor of two since the last adjustment, it recomputes them from recent request sizes so each range carries a similar share of the traffic.

```bash
smg launch \
  --pd-disaggregation \
  --prefill http://prefill1:8000 9001 \
  --prefill http://prefill2:8000 9002 \
  --decode http://decode1:8000 \
  --decode http://decode2:8000 \
  --prefill-policy bucket \
  --decode-policy power_of_two
```

<div class="grid" markdown>

<div class="card" markdown>

#### :material-check-circle: Advantages

- Request-length awareness
- Adaptive boundary adjustment
- Falls back to the least-busy worker when imbalanced

</div>

<div class="card" markdown>

#### :material-close-circle: Limitations

- PD prefill leg only
- No cache locality
- Balances characters routed recently, not engine-reported load

</div>

</div>

Before it uses a request's bucket, the policy checks balance. The load it compares is the characters (or tokens) routed to each prefill worker over the last 5 seconds: when the busiest and least busy workers differ by more than `--balance-abs-threshold` and the busiest exceeds `--balance-rel-threshold` times the least busy, the request goes to the least busy worker instead. Both flags are shared with `cache_aware`, but here they are measured in characters or tokens, not requests.

!!! warning "Prefill leg only"
    Only a PD prefill policy is given bucket boundaries, and `--decode-policy bucket` is rejected at startup. The Python launcher also rejects `--policy bucket`. With the Rust binary, as `--policy` outside PD mode, or on a decode leg that inherits it from `--policy`, `bucket` has no buckets: it picks a random available worker and logs a warning on every request. In PD mode, set it with `--prefill-policy bucket` and give the decode leg its own `--decode-policy`.

**Use when:** PD disaggregation where prefill workers should specialize by prompt length, for example with a bimodal request length distribution.

---

## Power of Two Choices

Samples two distinct available workers at random and routes to the one with the lower expected wait, using the same formula as [`least_load`](#least-load): queued token work over throughput plus the KV-pressure penalty, with credit for requests sent since the last load report. If neither sampled worker has a load report, it compares their in-flight requests. It approaches least-load placement while reading only two workers' load per request.

```bash
smg --policy power_of_two --worker-urls http://w1:8000 http://w2:8000
```

<div class="grid" markdown>

<div class="card" markdown>

#### :material-check-circle: Advantages

- Load-aware at two load reads per request
- Counts queue depth, KV pressure and in-flight work
- No full-fleet scan per request

</div>

<div class="card" markdown>

#### :material-close-circle: Limitations

- No cache locality
- May miss the single least-loaded worker
- Always uses `least_load`'s default tuning; the `--least-load-*` flags do not apply

</div>

</div>

**Use when:** Large or heterogeneous fleets where cache locality doesn't matter and each pick should read only two workers' load.

---

## Least Load

Scores every available worker by its **expected wait**, meaning how long the work already queued on it will take to drain, plus a penalty that rises steeply as its KV cache fills. It routes to the lowest score. Work sent since a worker's last load report counts too, so a burst that arrives between two polls spreads across workers instead of landing on the one that looked idlest.

```bash
smg launch \
  --policy least_load \
  --worker-urls grpc://worker1:50051 grpc://worker2:50052 \
  --model-path meta-llama/Llama-3.1-8B-Instruct
```

<div class="grid" markdown>

<div class="card" markdown>

#### :material-check-circle: Advantages

- Weighs requests by token work, not request count
- Steers away from workers near KV-cache exhaustion
- In-flight credit prevents herding between load polls

</div>

<div class="card" markdown>

#### :material-close-circle: Limitations

- No cache locality
- Scores every available worker on each request
- Estimates HTTP requests without token IDs at a mean size, so it is intended for gRPC workers

</div>

</div>

### Expected-Wait Score

```text
score = (queued_tokens + in_flight_tokens) / throughput
        + kv_pressure_weight * k / (1 - k)
```

| Term | Meaning |
|------|---------|
| `queued_tokens` | Uncached tokens waiting in the worker's queue, from its load report (summed across DP ranks). An engine that reports a queue depth but no token count, such as one scraped from Prometheus `/metrics`, is estimated at waiting requests × `--least-load-mean-prefill-tokens` |
| `in_flight_tokens` | Work this gateway has sent the worker since its last load report: each request's token count when its token IDs are known (gRPC workers, pre-tokenized requests), otherwise `--least-load-mean-prefill-tokens`. Every new report resets it |
| `throughput` | The worker's reported generation throughput in tokens/s, or `--least-load-default-throughput` when it reports none. Dividing by it turns token work into seconds, so faster workers take proportionally more |
| `k` | KV-cache usage from 0 to 1 (mean across DP ranks). `k / (1 - k)` stays small until the cache is nearly full, then climbs steeply |

Both terms are in seconds, so they add directly. Ties are broken uniformly at random.

Missing signals degrade gracefully:

- A worker that has not reported a load yet, such as one that just joined, scores as the best-scoring reporting worker plus its own in-flight requests, so it neither attracts every request nor gets starved.
- When no worker reports at all (a cold start, or engines that never report), the policy picks the worker with the fewest in-flight requests.

### Waiting-Queue Cap

`--least-load-max-waiting-requests N` skips a worker once its reported waiting requests, plus the requests this gateway has sent it since that report, reach `N`. Workers without a load report stay eligible. When every worker is at the cap, `least_load` selects nothing and the request fails with `503` (`no_available_workers`) instead of deepening a backlog; router retries, when enabled, try again after backoff. Set `N` below the engine's max batch size. The default `0` turns the cap off.

For a per-worker ceiling that applies under every policy, see [Overload Protection](../reliability/overload-protection.md).

### Tuning Flags

| Flag | Default | Description |
|------|---------|-------------|
| `--least-load-kv-pressure-weight` | `0.15` | Weight of the KV-pressure term, in seconds. Raise it to steer harder away from nearly full KV caches; `0` removes the term |
| `--least-load-default-throughput` | `2000` | Tokens/s assumed for a worker that reports no generation throughput. Set it to your measured per-replica generation rate; it co-tunes with the KV-pressure weight |
| `--least-load-mean-prefill-tokens` | `1024` | Tokens assumed per request when the real count is unknown |
| `--least-load-max-waiting-requests` | `0` | Per-worker waiting-queue cap; `0` disables it |

These flags tune only `least_load`; `power_of_two` and `cache_aware` use the same score with its default tuning. Loads are polled every `--load-monitor-interval` seconds (default `10`), and the in-flight term covers the gap between polls. See [Least Load Policy Options](../../reference/configuration.md#least-load-policy-options).

**Use when:** gRPC workers serve prompts of mixed sizes, cache locality doesn't matter, and you want each request on the worker with the shortest expected wait.

---

## Consistent Hashing

Routes each request by its routing key on a consistent hash ring with 150 virtual nodes per worker. Minimizes redistribution when workers scale—only ~1/N keys move when adding/removing workers. Each model has its own ring; requests that name no model, such as `/generate`, use a ring that spans every worker.

```bash
smg --policy consistent_hashing --worker-urls http://w1:8000 http://w2:8000
```

<div class="grid" markdown>

<div class="card" markdown>

#### :material-check-circle: Advantages

- Minimal redistribution on scaling
- Automatic failover to next healthy worker
- O(log n) lookup time

</div>

<div class="card" markdown>

#### :material-close-circle: Limitations

- No load awareness
- No cache locality
- Keyless requests go to a random worker

</div>

</div>

### Routing Headers

| Header | Description |
|--------|-------------|
| `X-SMG-Target-Worker` | Route to a worker by 0-based index into the model's currently available workers, so indices shift when a worker becomes unavailable. An index past the end of that list, or a value that is not a number, fails the request with `503` instead of falling back |
| `X-SMG-Routing-Key` | Hash this key onto the ring for session affinity. Names listed in `--routing-key-headers` are read first. Values must be non-empty UTF-8 of at most 128 bytes |

**Priority order:** `X-SMG-Target-Worker` → body `rid` (with `--routing-key-override`) → routing-key header → implicit keys (`Authorization`, `X-Forwarded-For`, `Cookie`) → random fallback

See [Sticky Sessions and Routing Keys](sticky-sessions.md) for how routing keys are derived and validated.

**Use when:** Session affinity needed, user-to-worker pinning, or consistent routing for stateful applications.

---

## Prefix Hash

A lightweight alternative to full cache-aware routing. Hashes the head of each request onto a consistent hash ring, so requests that start the same way land on the same worker, and moves a request off that worker only when it is clearly overloaded.

```bash
smg --policy prefix_hash --prefix-token-count 256 --worker-urls http://w1:8000 http://w2:8000
```

<div class="grid" markdown>

<div class="card" markdown>

#### :material-check-circle: Advantages

- Predictable O(log n) performance
- Lower memory than cache_aware
- Groups requests that share a head

</div>

<div class="card" markdown>

#### :material-close-circle: Limitations

- Prefix grouping, not exact matching
- Less precise than cache_aware
- Load check sees only this gateway's in-flight requests

</div>

</div>

### Hash Key

Each request is hashed on the first of these it carries:

1. A valid routing-key header (`X-SMG-Routing-Key` by default), hashed as is
2. Token IDs (gRPC workers, pre-tokenized requests, or an `x-smg-routing-tokens` header): the first `--prefix-token-count` tokens
3. Prompt text: the first 4 × `--prefix-token-count` characters

A request with none of these goes to the least-loaded worker. On HTTP workers the prompt is available only when the gateway parses the request body. A request it [streams to the worker unparsed](../performance/request-streaming.md), which can happen when router retries are disabled or the body exceeds `--max-buffered-request-bytes`, is hashed on one of the headers above or sent to the least-loaded worker.

With `--cache-boundaries` set (ascending token positions, for example `2048,8192`), the key is the request's head up to the deepest boundary it reaches, counting 4 characters per token for text. A short turn and its longer follow-up that share a boundary-aligned head then hash together. Requests shorter than the smallest boundary keep the `--prefix-token-count` key.

### Load Check

A ring worker counts as overloaded when its in-flight requests exceed both `--prefix-hash-load-factor` times the fleet average and the average plus `--prefix-hash-balance-abs-threshold` (default `10`). The absolute margin keeps the check from firing on noise when each gateway replica sees only part of the traffic; `0` makes the check purely relative. For an overloaded target the policy first tries each shallower boundary (with `--cache-boundaries`), then falls back to the least-loaded worker that is not overloaded, or to the ring target anyway if every worker is overloaded. The fallback is counted in the `load_balance_walk` branch of `smg_prefix_hash_policy_branch_total`.

### Comparison with Cache-Aware

| Aspect | prefix_hash | cache_aware |
|--------|-------------|-------------|
| Lookup | O(log n) | O(prefix_len) |
| Memory | O(workers × virtual_nodes) | O(total_tokens) |
| Precision | Same head (fixed length or boundary) | Longest matching prefix |

**Use when:** Need some cache locality with predictable performance and lower memory footprint.

---

## Manual

Pins each routing key to a worker in an explicit key-to-worker map. Keys come from the `X-SMG-Routing-Key` header, or from the request body's `rid` when `--routing-key-override` is also enabled. Unlike consistent hashing, adding workers never moves an existing key; a key moves only when its worker becomes unavailable or the key goes unused for `--max-idle-secs`.

```bash
smg launch --policy manual --assignment-mode min_load --worker-urls http://w1:8000 http://w2:8000
```

<div class="grid" markdown>

<div class="card" markdown>

#### :material-check-circle: Advantages

- Strong session stickiness
- Failover to a second worker, and back once the original recovers
- Keys idle longer than `--max-idle-secs` are evicted

</div>

<div class="card" markdown>

#### :material-close-circle: Limitations

- No load balancing for keys already pinned
- Requests without a key are spread by the assignment mode and never pinned
- Pins live in each gateway replica's memory

</div>

</div>

### Assignment Modes

| Mode | Description |
|------|-------------|
| `random` | Randomly select from available workers (default) |
| `min_load` | Select the worker with the fewest in-flight requests |
| `min_group` | Select the worker with the fewest active routing keys |
| `delegate` | Same as `min_load` under this policy |

To pin conversations while another policy such as `cache_aware` decides where each one starts, use `--routing-key-override` instead. See [Sticky Sessions and Routing Keys](sticky-sessions.md).

**Use when:** Stateful chat sessions where context is stored on workers, or when session continuity is critical.

---

## Round Robin

Rotates through the available workers in order. The rotation is kept per candidate set, the exact list of workers a selection chooses from, so when one policy instance serves several sets (such as the decode partners of each PD prefill), each set is covered evenly. A worker that becomes unavailable changes the set, which then starts its own rotation.

```bash
smg --policy round_robin --worker-urls http://w1:8000 http://w2:8000
```

<div class="grid" markdown>

<div class="card" markdown>

#### :material-check-circle: Advantages

- Even distribution within each candidate set
- Predictable routing pattern
- Small state (one counter per candidate set, at most 4,096 sets)

</div>

<div class="card" markdown>

#### :material-close-circle: Limitations

- No load awareness
- No cache locality
- Ignores request characteristics

</div>

</div>

**Use when:** All workers have equal capacity and you want predictable, even distribution.

---

## Random

The simplest policy—each available worker has equal probability of selection. Zero state overhead.

```bash
smg --policy random --worker-urls http://w1:8000 http://w2:8000
```

<div class="grid" markdown>

<div class="card" markdown>

#### :material-check-circle: Advantages

- Zero state overhead
- O(1) selection time
- Completely stateless

</div>

<div class="card" markdown>

#### :material-close-circle: Limitations

- No load awareness
- No cache locality
- Can create hot spots

</div>

</div>

**Use when:** Testing environments or completely homogeneous workloads where simplicity is preferred.

---

## Passthrough

Forwards every request to the first available worker. It does no balancing and never reads worker load or cache state, and because only `cache_aware` subscribes to KV-cache events, it adds no event subscription either. It is built for a gateway in front of a single worker, where a balancing policy has nothing to choose between.

```bash
smg launch --policy passthrough --worker-urls http://w1:8000
```

<div class="grid" markdown>

<div class="card" markdown>

#### :material-check-circle: Advantages

- No scoring or per-request state
- No load or cache state to maintain
- No KV-event subscription on gRPC workers

</div>

<div class="card" markdown>

#### :material-close-circle: Limitations

- With several workers, all traffic goes to the first available one
- Other workers take traffic only while the first is unavailable
- No load awareness or cache locality

</div>

</div>

If more than one worker is registered, the gateway logs a warning once and keeps sending everything to the first available worker. `smg serve` switches to `passthrough` on its own when it launches a single worker (`--data-parallel-size 1`, the default), overriding `--router-policy`. By default the gateway still polls worker loads, for engine metrics and overload protection; with `--disable-load-monitoring` it polls only when something else, such as `--engine-metrics` or overload protection, needs the data.

**Use when:** The gateway fronts exactly one worker.

---

## Choosing a Policy

### Decision Guide

| Requirement | Recommended Policy |
|-------------|-------------------|
| Production LLM inference | `cache_aware` |
| Load-aware routing without cache affinity | `least_load` (gRPC workers) or `power_of_two` |
| Session affinity (sticky sessions) | `manual` or `consistent_hashing`, or [sticky sessions](sticky-sessions.md) on any policy |
| PD disaggregation | `cache_aware` or `bucket` for prefill, `power_of_two` for decode |
| Lightweight cache locality | `prefix_hash` |
| Even distribution | `round_robin` |
| A single worker | `passthrough` |
| Testing/development | `random` |

### Scenario Guide

<div class="grid" markdown>

<div class="card" markdown>

#### :material-message-text: Conversational AI

**Recommended:** `cache_aware`

Maximizes KV cache reuse for multi-turn conversations with shared system prompts.

</div>

<div class="card" markdown>

#### :material-file-search: RAG Applications

**Recommended:** `cache_aware`

Exploits common document prefixes for faster Time to First Token.

</div>

<div class="card" markdown>

#### :material-account-group: Multi-Tenant Platform

**Recommended:** `consistent_hashing` or `manual`

User-to-worker affinity for tenant isolation or stateful sessions.

</div>

<div class="card" markdown>

#### :material-server-network: PD Disaggregation

**Recommended:** `cache_aware` or `bucket` (prefill) + `power_of_two` (decode)

Prefix affinity or length buckets for prefill, load-based selection for decode workers.

</div>

<div class="card" markdown>

#### :material-chart-bar: Mixed Prompt Sizes

**Recommended:** `least_load`

Scores token work rather than request counts, so long prompts don't pile onto one worker, and steers away from nearly full KV caches.

</div>

<div class="card" markdown>

#### :material-server: Single Worker

**Recommended:** `passthrough`

Nothing to balance, so skip the selection and cache bookkeeping entirely.

</div>

</div>

---

## Policy Scope

### Per-Model Policies

`--policy` applies to every model unless a worker names another policy. A worker registered with a `policy` label, for example `"labels": {"policy": "least_load"}` in its [worker spec](../../getting-started/multiple-workers.md), sets the policy for its model. The first worker registered for a model decides, and the model keeps that policy until its last worker is removed.

A label that names the same policy as `--policy` gets a per-model instance built from your flags. A label naming any other policy gets that policy's built-in defaults, which can differ from the CLI defaults; its flags are not applied. In PD and EPD mode, the legs always use the leg policies below, whatever the labels say.

### PD and EPD Legs

In PD mode, `--prefill-policy` and `--decode-policy` set each leg's policy and default to `--policy`. Both accept `least_load`. `--decode-policy bucket` is rejected at startup, and the Rust binary also rejects `passthrough` on either leg. Outside IGW mode, a leg given `power_of_two` through `--prefill-policy` or `--decode-policy` needs at least two workers on that leg, or startup fails. In EPD mode, `--encode-policy` accepts `random`, `round_robin` or `consistent_hashing` and defaults to `consistent_hashing`. See [PD Disaggregation](pd-disaggregation.md).

---

## Observability

- `smg_worker_selection_total` counts every policy selection by `worker_type`, `connection_mode`, `model` and `policy`.
- `smg_cache_aware_policy_branch_total`, `smg_prefix_hash_policy_branch_total`, `smg_consistent_hashing_policy_branch_total` and `smg_manual_policy_branch_total` count which branch each decision took (label `branch`), for example a `prefix_hash` request moved off its ring worker (`load_balance_walk`). The cache-aware counter covers tree-mode decisions only.
- `smg_worker_requests_active` is the per-worker in-flight count that load-aware policies compare.
- At `--log-level debug`, `cache_aware`, `prefix_hash`, `least_load`, `power_of_two` and sticky-session selections log each routing decision with the chosen worker. See [Logging](../../getting-started/logging.md).

See the [Metrics Reference](../../reference/metrics.md) for the full list.

---

## What's Next?

<div class="grid" markdown>

<div class="card" markdown>

### :material-cached: Cache-Aware Routing

Deep dive into the radix tree architecture and routing algorithm.

[Cache-Aware Routing →](cache-aware.md)

</div>

<div class="card" markdown>

### :material-shield: Circuit Breakers

How SMG handles worker failures gracefully.

[Circuit Breakers →](../reliability/circuit-breakers.md)

</div>

</div>
