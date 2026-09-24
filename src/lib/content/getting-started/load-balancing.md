---
title: Load Balancing
---

# Load Balancing

SMG provides ten load balancing policies to distribute requests across workers. Set the policy with `--policy` (default `cache_aware`):

```bash
smg --worker-urls http://w1:8000 http://w2:8000 --policy cache_aware
```

<div class="prerequisites" markdown>

#### Before you begin

- Completed the [Getting Started](index.md) guide
- Two or more workers running

</div>

---

## Policy Comparison

| Policy | Load Aware | Cache Affinity | Session Affinity | Best For |
|--------|:----------:|:--------------:|:----------------:|----------|
| `cache_aware` | Yes | Yes | — | **Production LLM** |
| `least_load` | Yes | — | — | Load-aware routing on gRPC workers |
| `power_of_two` | Yes | — | — | General load balancing |
| `bucket` | Yes | — | — | PD prefill leg |
| `consistent_hashing` | — | — | Yes | Session affinity |
| `prefix_hash` | Yes | Partial | — | Lightweight caching |
| `manual` | — | — | Yes | Stateful chat |
| `round_robin` | — | — | — | Even distribution |
| `random` | — | — | — | Testing |
| `passthrough` | — | — | — | Single worker |

---

## Cache-Aware (Recommended)

The production default. Routes each request to a worker that already holds its prefix; when no worker holds enough of it, or the holder is far busier than the rest, it routes to the worker with the lowest expected wait (the [`least_load`](#least-load) score).

```bash
smg \
  --policy cache_aware \
  --worker-urls http://w1:8000 http://w2:8000 \
  --cache-threshold 0.3 \
  --balance-abs-threshold 64 \
  --balance-rel-threshold 1.5
```

| Parameter | Default | Description |
|-----------|---------|-------------|
| `--cache-threshold` | `0.3` | Minimum matched-prefix share (0.0–1.0) before a request is pinned to a worker that holds the prefix. At or below it, the request goes to the worker with the lowest expected wait |
| `--balance-abs-threshold` | `64` | Spill gate, absolute part: a matched worker is skipped when its in-flight requests exceed the mean across available workers by more than this many and also exceed `--balance-rel-threshold` × that mean |
| `--balance-rel-threshold` | `1.5` | Spill gate, relative part: a multiple of that mean (at least `1.0`); fires only together with the absolute part |
| `--eviction-interval` | `120` (Python launcher: `60`) | Seconds between cache-tree eviction cycles. The Python launcher behind `pip install smg` and the container images names the flag `--eviction-interval-secs` |
| `--max-tree-size` | `67108864` | Maximum total size of each model's prefix tree (characters for HTTP, tokens for gRPC), shared across all workers |

Best for multi-turn conversations, RAG applications, and batch processing with shared templates. See [Cache-Aware Routing](../concepts/routing/cache-aware.md) for KV-event mode, the hash index, and KV-pressure options.

---

## Power of Two Choices

Samples two random workers and routes to the one with the lower expected wait, using the same formula as `least_load` with its default tuning. Good load distribution while reading only two workers' load per request.

```bash
smg --policy power_of_two --worker-urls http://w1:8000 http://w2:8000
```

Best for large or heterogeneous fleets where cache locality doesn't matter.

---

## Least Load

Routes to the worker with the lowest expected wait: the token work queued on it divided by its generation throughput, plus a penalty that grows as its KV cache fills. Work sent since the worker's last load report is counted too, so bursts spread out between polls. It is intended for gRPC workers, where each request's token count is known.

```bash
smg launch \
  --policy least_load \
  --worker-urls grpc://worker1:50051 grpc://worker2:50052 \
  --model-path meta-llama/Llama-3.1-8B-Instruct
```

| Parameter | Default | Description |
|-----------|---------|-------------|
| `--least-load-kv-pressure-weight` | `0.15` | Weight (seconds) of the KV-pressure penalty. Raise it to avoid nearly full KV caches more aggressively; `0` turns the penalty off |
| `--least-load-default-throughput` | `2000` | Generation throughput (tokens/s) assumed for a worker that reports none. Set it to your measured per-replica rate |
| `--least-load-mean-prefill-tokens` | `1024` | Tokens assumed per request when the real count is unknown (HTTP requests without token IDs, and queues reported only as request counts) |
| `--least-load-max-waiting-requests` | `0` | Skip a worker once its waiting requests, plus requests sent to it since its last load report, reach this count; `0` disables. Set it below the engine's max batch size |

When every worker is at the waiting-queue cap, the request fails with `503` instead of deepening a backlog. See [Least Load](../concepts/routing/load-balancing.md#least-load) for the scoring model.

---

## Consistent Hashing

Header-based routing with minimal redistribution on scaling. Routes based on `X-SMG-Routing-Key` header or implicit keys (`Authorization`, `X-Forwarded-For`, `Cookie`).

```bash
smg --policy consistent_hashing --worker-urls http://w1:8000 http://w2:8000
```

### Routing Headers

| Header | Description |
|--------|-------------|
| `X-SMG-Target-Worker` | Route to a worker by 0-based index into the model's available workers; `503` if that index has no available worker |
| `X-SMG-Routing-Key` | Consistent hash routing for session affinity. Non-empty UTF-8, at most 128 bytes; add names with `--routing-key-headers` |

**Priority:** `X-SMG-Target-Worker` > body `rid` (with `--routing-key-override`) > routing-key header > implicit keys > random fallback

Best for session affinity and user-to-worker pinning.

---

## Prefix Hash

A lightweight alternative to full cache-aware routing. Hashes the start of each request (its first `--prefix-token-count` tokens, or four times as many characters when it carries no token IDs) onto a consistent hash ring, and moves a request off its ring worker only when that worker's in-flight load is clearly above average.

```bash
smg \
  --policy prefix_hash \
  --worker-urls http://w1:8000 http://w2:8000 \
  --prefix-token-count 256 \
  --prefix-hash-load-factor 1.25
```

| Parameter | Default | Description |
|-----------|---------|-------------|
| `--prefix-token-count` | `256` | Number of prefix tokens to hash, or four times as many characters for untokenized requests. Longer = more precise routing, shorter = more requests grouped together |
| `--prefix-hash-load-factor` | `1.25` | Relative overload margin: a worker is overloaded once its in-flight requests exceed the average × this factor and the absolute margin. An overloaded worker's requests go to the least-loaded worker that is not overloaded |
| `--prefix-hash-balance-abs-threshold` | `10` | Absolute overload margin: how many in-flight requests above the average a worker must also exceed to count as overloaded. `0` makes the check purely relative |
| `--cache-boundaries` | unset | Comma-separated, ascending token positions. Requests hash at the deepest boundary they reach instead of at `--prefix-token-count` |

A valid `X-SMG-Routing-Key` header replaces the prompt as the hash key. Lower memory than `cache_aware` with predictable O(log n) performance.

---

## Bucket

Routes prefill requests by length in PD mode: each prefill worker owns a range of request sizes, and the ranges adapt to recent traffic every 5 seconds. Only the prefill leg gets buckets, so set it with `--prefill-policy`: `--decode-policy bucket` is rejected at startup, the Python launcher also rejects `--policy bucket`, and anywhere else `bucket` picks a random worker.

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

| Parameter | Default | Description |
|-----------|---------|-------------|
| `--balance-abs-threshold` | `64` | Imbalance gate, absolute part: the busiest and least busy prefill workers differ by more than this many characters (tokens for tokenized requests) routed over the last 5 seconds |
| `--balance-rel-threshold` | `1.5` | Imbalance gate, relative part: busiest > least busy × this ratio. When both parts fire, the request goes to the least busy worker instead of its bucket |

Both flags are shared with `cache_aware`. Best for PD disaggregation where prefill workers handle different request sizes.

---

## Manual

Pins each routing key to a worker and keeps it there until that worker becomes unavailable or the key goes unused for `--max-idle-secs`. Keys come from the `X-SMG-Routing-Key` header, or from the request body's `rid` when `--routing-key-override` is also enabled. Requests without a key are placed by the assignment mode and not pinned.

```bash
smg launch \
  --policy manual \
  --worker-urls http://w1:8000 http://w2:8000 \
  --assignment-mode min_load \
  --max-idle-secs 14400 \
  --eviction-interval 120
```

| Parameter | Default | Description |
|-----------|---------|-------------|
| `--assignment-mode` | `random` | How a new key picks its worker: `random`, `min_load` (fewest in-flight requests), `min_group` (fewest active routing keys), or `delegate` (same as `min_load` under this policy) |
| `--max-idle-secs` | `14400` | Seconds a key can go unused before its pin is evicted (4 hours). Alias: `--sticky-key-idle-secs` |
| `--eviction-interval` | `120` (Python launcher: `60`) | Seconds between eviction sweeps. Also sets the cache-aware tree eviction interval |

To pin conversations on top of another policy, such as `cache_aware`, use `--routing-key-override` (alias `--sticky-sessions`) instead. See [Sticky Sessions and Routing Keys](../concepts/routing/sticky-sessions.md).

Best for stateful chat where context is stored on workers.

---

## Round Robin

Rotates through the available workers in order, keeping a separate rotation for each set of candidate workers (for example, each PD prefill's decode partners), so every worker in a set gets an even share.

```bash
smg --policy round_robin --worker-urls http://w1:8000 http://w2:8000
```

---

## Random

Each available worker has equal probability of selection. Zero state overhead.

```bash
smg --policy random --worker-urls http://w1:8000 http://w2:8000
```

---

## Passthrough

Sends every request to the first available worker, with no balancing and no KV-event subscription. Use it when the gateway fronts a single worker; with more workers, the others receive traffic only while the first is unavailable. `smg serve` switches to it automatically when it launches one worker.

```bash
smg launch --policy passthrough --worker-urls http://w1:8000
```

---

## Choosing a Policy

| Requirement | Recommended Policy |
|-------------|-------------------|
| Production LLM inference | `cache_aware` |
| Load-aware routing without cache affinity | `least_load` (gRPC workers) or `power_of_two` |
| Session affinity (sticky sessions) | `manual` or `consistent_hashing`, or [sticky sessions](../concepts/routing/sticky-sessions.md) on any policy |
| PD disaggregation | `cache_aware` or `bucket` for prefill, `power_of_two` for decode |
| Lightweight cache locality | `prefix_hash` |
| Even distribution | `round_robin` |
| A single worker | `passthrough` |
| Testing/development | `random` |

---

## Next Steps

- [Load Balancing Concepts](../concepts/routing/load-balancing.md) — Detailed policy architecture, advantages/limitations, scenario guides
- [Cache-Aware Routing Concepts](../concepts/routing/cache-aware.md) — Radix tree architecture and routing algorithm deep dive
- [PD Disaggregation](pd-disaggregation.md) — Separate prefill and decode policies with `--prefill-policy` and `--decode-policy`
- [Tokenizer Caching](tokenizer-caching.md) — Reduce tokenization overhead with two-level caching
