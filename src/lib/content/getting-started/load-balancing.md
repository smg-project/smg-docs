---
title: Load Balancing
---

# Load Balancing

SMG provides multiple load balancing policies to distribute requests across workers. Set the policy with `--policy`:

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
| `bucket` | Yes | — | — | PD disaggregation |
| `power_of_two` | Yes | — | — | General load balancing |
| `consistent_hashing` | — | — | Yes | Session affinity |
| `prefix_hash` | Yes | Partial | — | Lightweight caching |
| `manual` | — | — | Yes | Stateful chat |
| `round_robin` | — | — | — | Even distribution |
| `random` | — | — | — | Testing |

---

## Cache-Aware (Recommended)

The production default. Maintains a radix tree mirroring backend KV cache state for optimal prefix routing with load balancing fallback. Maximizes KV cache hits (60-90% hit rate), reduces TTFT by 70-75%.

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
| `--cache-threshold` | `0.3` | Minimum prefix match ratio (0.0–1.0) to route to highest-match worker. At or below this threshold, routes to the least-loaded healthy worker |
| `--balance-abs-threshold` | `64` | Absolute load difference threshold — triggers load balancing when exceeded |
| `--balance-rel-threshold` | `1.5` | Relative load ratio threshold — triggers load balancing when max_load > min_load × ratio |
| `--eviction-interval` | `120` | Seconds between LRU eviction cycles for the radix trees |
| `--max-tree-size` | `67108864` | Maximum nodes per radix tree. Excess nodes are evicted during maintenance cycles |

Best for multi-turn conversations, RAG applications, and batch processing with shared templates.

---

## Power of Two Choices

Samples two random workers and routes to the one with lower load. Good load distribution with minimal overhead.

```bash
smg --policy power_of_two --worker-urls http://w1:8000 http://w2:8000
```

Best for heterogeneous workers with varying response times.

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

A lightweight alternative to full cache-aware routing. Routes based on a hash of the first N tokens using consistent hashing with bounded load balancing.

```bash
smg \
  --policy prefix_hash \
  --worker-urls http://w1:8000 http://w2:8000 \
  --prefix-token-count 256 \
  --prefix-hash-load-factor 1.25
```

| Parameter | Default | Description |
|-----------|---------|-------------|
| `--prefix-token-count` | `256` | Number of prefix tokens to hash. Longer = more precise routing, shorter = more requests grouped together |
| `--prefix-hash-load-factor` | `1.25` | Load threshold ratio — if a worker's load exceeds avg_load × factor, walk the hash ring to find a less loaded worker |

Lower memory than `cache_aware` with predictable O(log n) performance.

---

## Bucket

Routes requests based on text length with adaptive boundaries. Periodically adjusts boundaries based on observed load distribution.

```bash
smg \
  --policy bucket \
  --worker-urls http://w1:8000 http://w2:8000 http://w3:8000 \
  --balance-abs-threshold 64 \
  --balance-rel-threshold 1.5
```

| Parameter | Default | Description |
|-----------|---------|-------------|
| `--balance-abs-threshold` | `64` | Absolute load difference threshold for load balancing |
| `--balance-rel-threshold` | `1.5` | Relative load ratio threshold for balancing decisions |

Best for PD disaggregation where prefill workers handle different request sizes.

---

## Manual

Pins each routing key to a worker and keeps it there until that worker becomes unavailable or the key goes unused for `--max-idle-secs`. Keys come from the `X-SMG-Routing-Key` header, or from the request body's `rid` when `--routing-key-override` is also enabled. Requests without a key are placed by the assignment mode and not pinned.

```bash
smg \
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
| `--eviction-interval` | `120` | Seconds between eviction sweeps. Also sets the cache-aware tree eviction interval |

To pin conversations on top of another policy, such as `cache_aware`, use `--routing-key-override` (alias `--sticky-sessions`) instead. See [Sticky Sessions and Routing Keys](../concepts/routing/sticky-sessions.md).

Best for stateful chat where context is stored on workers.

---

## Round Robin

Rotates through workers sequentially. Skips unhealthy workers automatically.

```bash
smg --policy round_robin --worker-urls http://w1:8000 http://w2:8000
```

---

## Random

Each healthy worker has equal probability of selection. Zero state overhead.

```bash
smg --policy random --worker-urls http://w1:8000 http://w2:8000
```

---

## Choosing a Policy

| Requirement | Recommended Policy |
|-------------|-------------------|
| Production LLM inference | `cache_aware` |
| Session affinity (sticky sessions) | `manual` or `consistent_hashing` |
| PD disaggregation | `bucket` |
| Load balancing without cache | `power_of_two` |
| Lightweight cache locality | `prefix_hash` |
| Even distribution | `round_robin` |
| Testing/development | `random` |

---

## Next Steps

- [Load Balancing Concepts](../concepts/routing/load-balancing.md) — Detailed policy architecture, advantages/limitations, scenario guides
- [Cache-Aware Routing Concepts](../concepts/routing/cache-aware.md) — Radix tree architecture and routing algorithm deep dive
- [Tokenizer Caching](tokenizer-caching.md) — Reduce tokenization overhead with two-level caching
