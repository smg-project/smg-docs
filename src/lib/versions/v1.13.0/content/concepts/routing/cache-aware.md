---
title: Cache-Aware Routing
---

# Cache-Aware Routing

`cache_aware` is SMG's default routing policy. For each request it looks up which workers are likely to hold the request's prefix in their KV cache and sends the request to one of them, unless that worker is already much busier than the rest of the fleet. Requests with no usable cached prefix go to the worker with the lowest expected wait.

---

## Overview

<div class="grid" markdown>

<div class="card" markdown>

### :material-tree: Per-Model Prefix Index

A radix tree per model remembers which workers served which prompt prefixes. With KV events from gRPC workers, the index follows what the engines actually cached instead.

</div>

<div class="card" markdown>

### :material-scale-balance: Pressure-Aware Selection

Warm-cache workers keep receiving their prefixes until they heat up. A per-request load gate then spills requests to other workers, which become additional holders of the hot prefix.

</div>

<div class="card" markdown>

### :material-timer-sand: Expected-Wait Fallback

Cache misses, spills and ties between equally good holders go to the worker with the lowest expected wait, scored the same way as the `least_load` policy.

</div>

<div class="card" markdown>

### :material-memory: Bounded Memory

A shared per-model tree budget, immediate cleanup when workers leave, an optional TTL'd hash index, and optional pruning of the KV-event index keep gateway memory bounded.

</div>

</div>

---

## Why Cache-Aware Routing?

LLM inference workers keep a **KV cache** of the attention states they have already computed. When a new request shares a prefix with an earlier request on the same worker, the engine reuses that part of the cache instead of recomputing it, which cuts prefill work and time to first token.

<div class="grid" markdown>

<div class="card" markdown>

### :material-message-text: Multi-Turn Conversations

Same system prompt across turns. Growing context benefits from cached prefixes.

</div>

<div class="card" markdown>

### :material-file-document-multiple: RAG Applications

Common document snippets. Shared retrieval prefixes across queries.

</div>

<div class="card" markdown>

### :material-tray-full: Batch Processing

Similar prompts in sequence. Template-based generation with variable suffixes.

</div>

<div class="card" markdown>

### :material-account-group: Shared System Prompts

Multiple users with same instructions. Amortized prefill cost across sessions.

</div>

</div>

**The Challenge:** If requests with shared prefixes go to different workers, each worker must recompute the same prefix independently, wasting GPU cycles.

**Cache-Aware Solution:** Route requests to workers that already have the relevant prefix cached, and move load elsewhere only when those workers get too busy.

---

## How the Prefix Index Works

### Index Modes

`cache_aware` looks each request up in one of four indexes:

| Index | Used for | Keys on | Reflects |
|-------|----------|---------|----------|
| **KV-event index** | Requests with token IDs, once the model's KV-event index has data | Full blocks of token IDs | Blocks each engine reports it has stored or evicted |
| **Token tree** | Requests with token IDs when the model has no KV-event data | Token IDs, in whole pages of `--block-size` tokens | Where the gateway sent earlier requests |
| **String tree** | Requests that carry only text | Request text | Where the gateway sent earlier requests |
| **Hash index** | Requests with token IDs, when `--cache-index hash` replaces the other three | Token-ID heads cut at `--cache-boundaries` | Where the gateway sent each head within `--cache-ttl-secs` |

A request carries token IDs when:

- it is routed to a gRPC or ZMQ worker, because the gateway tokenizes the prompt itself;
- it is an HTTP `/generate` request with non-empty `input_ids`. The IDs win when `text` is also present, and a batch routes on its first sequence;
- it is an HTTP request in regular (non-PD) mode with a valid `x-smg-routing-tokens` header: the prompt's leading token IDs as comma-separated decimals, at most 512 IDs and 4,096 bytes. The hint replaces body-derived tokens and text for worker selection; a malformed or oversized value is ignored.

Every other HTTP request routes on its text (for chat requests, the text of the messages in order). Text and token requests for the same model build separate trees and never match each other.

!!! note "Request bodies"
    Because `cache_aware` needs the request text, the gateway buffers and parses HTTP request bodies before routing them. A request with a valid `x-smg-routing-tokens` hint may instead be forwarded without body parsing (see [Request Streaming](../performance/request-streaming.md)); it then routes on the hint alone and without a [cache partition](#cache-partitions).

### Approximate Trees

The string and token trees are multi-tenant radix trees. Each node lists its tenants: the workers the gateway has sent that prefix to.

```text
root
├── "You are a helpful assistant."           tenants: worker-a, worker-b
│   ├── " Answer in French. ..."             tenants: worker-a
│   └── " Write Python code for ..."         tenants: worker-b
└── "Summarize the following report: ..."    tenants: worker-c
```

A lookup walks the request down the tree and returns the length of the longest matching prefix together with up to eight tenants of the deepest matched node. Every one of them holds the matched prefix, so any of them is a valid cache-affinity target. The gateway then inserts the request for the worker it finally chose, in the same pass, so the tree learns from every dispatch.

The trees record routing decisions, not engine state. An engine may already have evicted a prefix that the tree still lists, so a tree match is a prediction. To route on what the engines actually cached, stream KV events from gRPC workers; see [KV Events Cache-Aware Routing](../../getting-started/kv-events-cache-aware.md).

The token tree counts only whole pages of `--block-size` tokens (default 16), because engines reuse KV cache in whole pages; a request shorter than one page never matches. Set `--block-size` to your engines' page size (see [Block Size](#block-size)).

### Tree Size and Eviction

- `--max-tree-size` (default 67,108,864) is a per-model budget shared by all of that model's workers: characters for the string tree, tokens for the token tree. A prefix held by several workers counts once per worker, so total tree memory grows with the number of models times `--max-tree-size`.
- Every `--eviction-interval` seconds (default 120, or 60 under the Python launcher that `pip install smg` and the container image use), each tree that is over budget evicts leaf entries, least recently used first, until it is back within budget. A tree within budget skips the walk.
- When a worker is removed, its entries are purged from every tree immediately instead of waiting for eviction.
- After each eviction cycle, the `smg_cache_tree_chars`, `smg_cache_tree_tokens` and `smg_cache_tree_tenants` gauges report each model's tree size (see [Monitoring](#monitoring)).

### Hash Index Mode

`--cache-index hash` replaces the trees with a TTL'd exact-match placement map. It suits engines that reuse cached prefix state only at fixed token positions, and only for a few minutes:

```bash
smg launch \
  --policy cache_aware \
  --cache-index hash \
  --cache-boundaries 2048,8192,32768 \
  --cache-ttl-secs 180 \
  --worker-urls grpc://worker-1:50051 grpc://worker-2:50051 \
  --model-path meta-llama/Llama-3.1-8B-Instruct
```

- A request's *applicable* boundaries are the `--cache-boundaries` positions its token IDs reach. At each one, the gateway hashes the request's head (its first N token IDs).
- Selection probes the deepest applicable boundary first and stops at the first head with a live, eligible holder: a worker that was sent the same head within `--cache-ttl-secs` (default 180). The [spill gate](#decision-flow) then applies to those holders.
- Every dispatch records the chosen worker at each applicable boundary. A head keeps at most three holders; recording a fourth evicts the stalest. A short first turn and its longer follow-up that share a boundary-aligned head therefore land on the same worker.
- Requests without token IDs, and requests shorter than the smallest boundary, are load-balanced and not recorded.
- Expired holders are dropped when read and by a sweep every `--eviction-interval` seconds, which also updates the `smg_cache_placement_entries` gauge.

`--cache-boundaries` takes positive, strictly ascending token positions, comma-separated, and SMG refuses to start with `--cache-index hash` and no boundaries. Set `--cache-ttl-secs` close to how long your engines keep cached prefixes. The same `--cache-boundaries` setting also controls the hashing depth of the `prefix_hash` policy. In hash mode the trees, the KV-event index, `--cache-threshold`, `--overlap-decay` and `--selection-temperature` are not used.

### Cache Partitions

Engines keep separate prefix caches for requests that differ in a cache salt, an extra cache key or a LoRA adapter, even when the prompts are identical. `cache_aware` mirrors this: it derives a cache namespace from the request's partition fields and keys the trees and the hash index under it, so requests in different namespaces never match each other.

The partition fields are `cache_salt`, `extra_key` and the LoRA adapter (`lora_path`, or `lora_id` on `/generate`), read from Chat Completions, Completions, Messages and `/generate` request bodies. An empty string counts as unset, and a request without any of these fields routes exactly as before.

- Partitions apply to HTTP workers only. Requests to gRPC and ZMQ workers stay unpartitioned because those paths do not forward the partition fields to the engine.
- The KV-event index is partitioned by the cache salt and the LoRA adapter alone: the engines fold those two fields into their block hashes, and the gateway hashes a partitioned request's blocks the same way, so a salted request matches only blocks stored under the same salt. `extra_key` does not partition the KV-event index.
- Each namespace keeps its own copy of a shared prefix, and all copies count against `--max-tree-size`. A unique salt on every request makes every request a new path, so size the budget for namespaces times working set.
- The namespace marker does not count toward the match ratio, and only a hash of the fields is stored: no client value reaches the trees, the hash index or mesh gossip.

---

## Routing Algorithm

### Decision Flow

Every request goes through the same steps. Cache affinity comes first; load decides only when affinity cannot, or when the preferred worker is too busy.

1. **Collect eligible workers:** healthy workers whose circuit breaker allows traffic and that [worker overload protection](../reliability/overload-protection.md) has not excluded. Their mean in-flight request count is the fleet baseline for the spill gate. A KV-event-mode decision over a pool larger than 32 workers reads the holders of the request's blocks plus a uniform sample of 16 eligible workers instead of the whole pool; the sample supplies the fleet mean and the spill and miss targets. Hash mode, and pools with a KV-pressure trigger configured, always read the whole pool.
2. **Check KV pressure** (off by default). If the hottest backend's KV-cache usage is above `--overload-token-usage-threshold`, or the hottest minus the coldest is above `--balance-token-usage-threshold`, the request skips cache affinity and goes to the lowest-expected-wait eligible worker. The tree or hash index still records the request for that worker.
3. **Find affinity candidates** in the request's index:
    - *Trees:* the match ratio is the matched length divided by the request length (characters for the string tree; whole matched pages over request tokens for the token tree). If it is **greater than** `--cache-threshold` (default 0.3), the candidates are the eligible tenants of the matched node. Otherwise the request is a cache miss.
    - *KV-event index:* every eligible worker that holds at least the request's first full block is a candidate, scored by how many consecutive leading blocks it holds. `--cache-threshold` does not gate candidates here, though it defines the thin overlaps the [warm-up slice](#worker-warm-up) treats as misses.
    - *Hash index:* the eligible live holders of the deepest head that has any.
4. **Rank candidates** (tree and KV-event modes). With `--overlap-decay` above 0, a candidate's score shrinks with its waiting-prefill backlog. The top-scoring group is kept; with `--selection-temperature` above 0, a group is sampled instead, weighted toward higher scores. With both at their default of 0, the tree keeps every matched tenant and the KV-event index keeps the workers with the most cached blocks.
5. **Apply the spill gate.** A candidate is *hot* when its in-flight request count exceeds both `--balance-rel-threshold` times the fleet mean (default 1.5) and the fleet mean plus an adaptive absolute margin derived from `--balance-abs-threshold` (see [Spill Gate](#spill-gate)). The request goes to the lowest-expected-wait candidate that is not hot. If every candidate is hot, the request spills to the lowest-expected-wait eligible worker that is not hot (or to the lowest-expected-wait eligible worker when every worker is hot).
6. **Cache miss:** the request goes to the lowest-expected-wait eligible worker.
7. **Record.** Tree and hash modes record the final worker as a holder of the request's prefix, so a spill makes the spill target an additional holder: a hot prefix replicates instead of queueing behind one worker. The gateway never records its routing decisions in the KV-event index, which changes only through engine events, worker removal and the optional [pruning](../../getting-started/kv-events-cache-aware.md#bounding-the-index).

### Worker Warm-Up

In KV-event mode, a worker with little or nothing in its index — newly added, restarted, or emptied by a resync — would otherwise idle while every prompt has a better overlap elsewhere. The gateway routes a slice of traffic to such *warming* workers so they build a cache:

- A worker is warming while its KV-event index holds less than `--worker-warmup-thin-ratio` (default 0.5) of the fleet's median block count, or nothing at all (applied once the fleet median reaches `--worker-warmup-blocks`), and also for `--worker-warmup-secs` (default 60) after it becomes routable, until its index has grown by `--worker-warmup-blocks` (default 1024).
- One cache miss in `1 / --worker-warmup-share` (default 0.25, so one in four) goes to the least-loaded warming worker. A miss here includes thin overlaps: a best overlap at or below the `--cache-threshold` share of the request, or of at most 4 blocks covering less than half of it (a shared chat-template head).
- One hit in `--worker-warmup-divert-every` (default 8) is diverted to a thin worker even though another worker holds its prefix — shallow overlaps (at most half the request) first — so an index emptied mid-traffic refills on workloads where every request has a holder. A deep hit is diverted only away from a busier holder, and a thin worker with requests in flight receives no second diversion within 2 seconds.
- Warming candidates still pass the eligibility and spill-gate checks, and warm-up picks are counted in the `warmup_slice` and `warmup_divert` branches of `smg_cache_aware_policy_branch_total`. `--worker-warmup-share 0` disables the slice and the diversion; `--worker-warmup-divert-every 0` disables the diversion alone.

### Expected-Wait Selection

Misses, spills, KV-pressure fallbacks and ties among equally good candidates are all resolved by the scorer the `least_load` policy uses: the worker's queued and in-flight token work divided by its reported throughput, plus a penalty that grows as its KV cache fills. Each dispatch is credited to the chosen worker until its next load report, so a burst of arrivals does not all land on the worker that looked idlest at the last poll. When no worker reports load, the scorer compares in-flight request counts. `cache_aware` runs the scorer with its built-in defaults; the `--least-load-*` flags configure only `--policy least_load`. See [Load Balancing](load-balancing.md) for the scoring details.

### Tuning Knobs

| Flag (alias) | Default | Effect | Input |
|--------------|---------|--------|-------|
| `--cache-threshold` (`--cache-match-threshold`) | `0.3` | Tree affinity only when the match ratio is above this (0.0-1.0) | Tree match |
| `--balance-abs-threshold` (`--spill-abs-threshold`) | `64` | Spill gate: cap of the adaptive margin of requests above the fleet mean (see [Spill Gate](#spill-gate)) | Gateway in-flight counts |
| `--balance-rel-threshold` (`--spill-rel-threshold`) | `1.5` | Spill gate: multiple of the fleet mean (must be at least 1.0) | Gateway in-flight counts |
| `--balance-token-usage-threshold` | `1.0` (off) | Skip affinity when the hottest backend's KV usage exceeds the coldest's by more than this | Backend KV usage |
| `--overload-token-usage-threshold` | `1.0` (off) | Skip affinity when any backend's KV usage exceeds this; best set high, for example `0.9` | Backend KV usage |
| `--overlap-decay` | `0.0` (off) | Divide a candidate's score by `1 + overlap_decay * x`, where `x` is its waiting-prefill backlog above the least-backlogged candidate, in blocks, per block of the request | Backend waiting-prefill tokens |
| `--selection-temperature` | `0.0` (argmax) | Softmax temperature over min-max normalized scores; spreads picks across near-equal candidates | Candidate scores |
| `--selection-accounting-ttl-ms` | `0` (off) | Charge each dispatch's predicted prefill and prefix placement to its worker until the engine's KV events report them or the booking expires; set a little above the engine's KV-event lag | Gateway dispatches, KV events |

The token-usage thresholds must be above 0 (a value of 1.0 or more disables them), and `--overlap-decay` and `--selection-temperature` must be 0 or more. In the trees every matched tenant starts with the same score, so `--selection-temperature` changes nothing there unless `--overlap-decay` separates them.

!!! warning "Per-model policies from worker labels"
    When the first worker registered for a model carries a `policy` label, that model gets its own policy instance. A `cache_aware` label inherits the flags on this page only when the gateway's `--policy` is also `cache_aware`. Otherwise the instance uses built-in defaults: a `cache_threshold` of 0.5, a spill gate of 1.1x the mean with an absolute-margin cap of 32 requests, eviction every 30 seconds, and a `max_tree_size` of 10,000.

### Backend Load Reports

The KV-pressure triggers, `--overlap-decay` and the expected-wait scorer read the load reports the gateway polls from every worker each `--load-monitor-interval` seconds (default 10). `cache_aware` always receives them: polling is on by default, and `--disable-load-monitoring` still polls for load-aware policies, which include `cache_aware`.

| Signal | Used by | Reported by |
|--------|---------|-------------|
| KV-cache usage | Token-usage thresholds, expected wait | gRPC workers (SGLang, vLLM, TokenSpeed) through `GetLoads`; ZMQ workers; HTTP SGLang and vLLM workers through `/v1/loads` or their Prometheus `/metrics` |
| Waiting uncached (prefill) tokens | `--overlap-decay`, expected wait | SGLang and vLLM gRPC workers (vLLM's figure is a servicer-side estimate of its queued token work); HTTP workers only if their `/v1/loads` response includes it. TokenSpeed gRPC workers report none. |

A worker without a signal is never penalized for it: `--overlap-decay` skips it, and the KV-pressure check compares only the workers that reported.

---

## Block Size

`--block-size` (default 16) is the match granularity for token routing:

- **Token tree:** the page size. Matches count only whole pages, and prompts shorter than one page never match.
- **KV-event index:** the block size used to cut a request into blocks, until the model's real block size is known.

For the KV-event index, SMG uses the first of these that is available for the model:

| Source | Notes |
|--------|-------|
| Block size reported in the model's KV events | The first stored block any worker reports sets the model's size; a larger reported size later raises it, a smaller one never lowers it |
| The worker spec's `kv_block_size` field | Seeds the model's block size at subscription until events report the engine's real page size; must be at least 1 |
| `--block-size` | Router-wide fallback |

Set `--block-size` to the engines' KV page size (SGLang `--page-size`, vLLM `--block-size`, TokenSpeed `--prefix-granularity`) and keep one page size per model. The token tree always uses `--block-size`; only the KV-event index learns from events and worker specs.

---

## When Cache-Aware Routing Helps

<div class="grid" markdown>

<div class="card" markdown>

#### :material-check-circle: High Impact

- Multi-turn conversations with shared system prompts
- RAG applications with common document prefixes
- Batch processing with template-based prompts
- Multiple users with identical instructions

</div>

<div class="card" markdown>

#### :material-close-circle: Lower Impact

- Completely random, unique prompts
- Single-turn diverse queries
- Very short prompts, which leave little to reuse
- Highly variable system prompts

</div>

</div>

!!! note "Results vary by workload"
    Workloads with repeated prefixes (chatbots, RAG, agents) gain the most. With diverse, unique prompts, most requests are cache misses and `cache_aware` behaves like expected-wait load balancing.

---

## Tuning Guidelines

### Cache Threshold

`--cache-threshold` sets how much of a request must match a tree prefix before its holders are preferred:

| Value | Behavior | Use Case |
|-------|----------|----------|
| **Low (0.1-0.2)** | Affinity on shorter shared prefixes; more requests go to holders | Long shared prompts with varying suffixes |
| **Default (0.3)** | Balanced approach | Most deployments |
| **High (0.5-0.8)** | Affinity only on long matches; more requests are load-balanced | Diverse workloads |

!!! tip "Recommendation"
    Start with the default. The `smg_cache_aware_match_ratio` histogram shows how requests' match ratios are distributed, and therefore how many requests a threshold change would move. The threshold does not affect the KV-event or hash index.

### Spill Gate

`--balance-abs-threshold` and `--balance-rel-threshold` decide how busy a holder may get before its requests spill. Both margins must be exceeded, so the gate ignores small fluctuations at low load (absolute margin) without missing a deep queue at high load (relative margin).

The absolute margin is adaptive, not the configured value alone: `min(--balance-abs-threshold, max(⌈2·√mean⌉, 4))` — the configured threshold capped at the in-flight count's natural swing at the pool's current mean (two standard deviations of a Poisson count), never under four requests. A margin fixed at the default 64 could never clear in a pool whose per-worker in-flight count stays below it, so small pools would never spill; sized to the swing, the gate balances them too, while the floor keeps a handful of requests on an otherwise idle fleet from counting as pressure. A configured value under 4 lowers the margin below the floor, and 0 leaves the relative margin to decide alone.

| Scenario | Recommendation |
|----------|---------------|
| **Long shared prefixes, prefill-bound** | Higher thresholds (hold affinity longer) |
| **Bursty, latency-sensitive traffic** | Lower thresholds (spill sooner; hot prefixes replicate faster) |
| **Uneven request sizes** | Also set `--balance-token-usage-threshold`, which request counts cannot replace |

### KV Pressure

Request counts miss long-context imbalance: a few long requests can fill one engine's KV cache. `--overload-token-usage-threshold` (for example `0.9`) stops cache affinity while any engine is that full, and `--balance-token-usage-threshold` (for example `0.5`) stops it while one engine is much fuller than another. The request then goes to the lowest-expected-wait worker, which already accounts for KV usage. Unlike `--worker-overload-token-usage`, which removes a worker from routing entirely, these flags only suspend affinity.

### Memory

`--max-tree-size` bounds each model's string tree (characters) and token tree (tokens). When `smg_cache_tree_chars` or `smg_cache_tree_tokens` sits at the budget, eviction is trimming the working set; raise the budget if memory allows. `--eviction-interval` sets how often eviction runs (and, in hash mode, the placement sweep).

---

## Monitoring

### Key Metrics

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `smg_cache_aware_policy_branch_total` | counter | `branch` | Tree-mode decisions: `tree_match` (affinity kept), `spill` (the match cleared the threshold but the request went to a worker outside the matched set), `expected_wait_fallback` (match ratio at or below `--cache-threshold`), `first_healthy_fallback` (no worker could be selected). KV-event-mode decisions: `event_hit` (the chosen worker holds part of the request), `event_spill` (an overlap existed but the chosen worker holds none of it), `event_miss` (no overlap), and `warmup_slice` / `warmup_divert` for [warm-up](#worker-warm-up) picks |
| `smg_cache_aware_match_ratio` | histogram | none | Best prefix match ratio of each tree-mode decision; buckets `0`, `0.1`, ... `1.0` |
| `smg_cache_tree_chars` | gauge | `model` | String-tree size in characters, summed across workers |
| `smg_cache_tree_tokens` | gauge | `model` | Token-tree size in tokens, summed across workers |
| `smg_cache_tree_tenants` | gauge | `model`, `tree` | Workers (tenants) tracked by the model's `string` or `token` tree |
| `smg_cache_placement_entries` | gauge | `model` | Hash-mode heads with a live holder |
| `smg_kv_event_subscription_failures_total` | counter | `worker`, `reason` | KV-event subscription tasks that failed: `panic`, `join_error`, `intern_failed` |
| `smg_engine_cache_hit_rate` | gauge | `worker`, `model`, `dp_rank` | Engine-reported prefix cache hit rate, from the load poll |
| `smg_engine_token_usage` | gauge | `worker`, `model`, `dp_rank` | Engine-reported KV usage, the signal the token-usage thresholds read |
| `smg_worker_requests_active` | gauge | `worker` | In-flight requests per worker, the spill gate's input |

The branch counter covers string-/token-tree and KV-event decisions; hash-mode dispatches and KV-pressure fallbacks are not counted there. The match-ratio histogram covers tree decisions only. The tree gauges update after each eviction cycle, and the placement gauge after each hash-mode sweep. See the [Metrics Reference](../../reference/metrics.md) for all gateway metrics.

### Debug Logs

With `RUST_LOG=info,smg::policies::cache_aware=debug` (which overrides `--log-level`), each tree and hash decision logs a `Cache-aware selection` line with its `index`, `branch`, `worker` and `model_id` (plus `matched_ratio` and `threshold` for trees), KV-event decisions log an `Event-driven routing` line with their `branch`, `overlap_blocks` and `request_blocks`, and warm-up picks log `Cache miss routed to a warming worker` or `Cache hit diverted to a thin worker`.

### Decision Traces

On requests served by the gRPC pipeline — gRPC and ZMQ workers — the gateway can capture each dispatch's routing evidence: per selection the policy, the index's prediction (source, branch, matched or overlapping amount), the chosen worker, and, in the log line, up to 32 candidate workers with their load, health, overload state and backend cache epochs, plus the score and spill-gate evaluations. HTTP-worker routers do not capture traces; the request header has no effect there.

| Switch | Effect |
|--------|--------|
| `x-smg-cache-trace: 1` request header | Returns that request's decision record in the `x-smg-cache-trace` response header, with no log line |
| `SMG_CACHE_TRACE=1` | Logs each dispatch's evidence, and each failed request's, as an INFO line (target `smg::cache_trace`) |
| `SMG_CACHE_TRACE_SAMPLE=N` | Logs one dispatch in every N (default 1); failure lines and the response header are never sampled |
| `SMG_CACHE_TRACE_HEADER=1` | Adds the `x-smg-cache-trace` response header to every dispatch |
| `SMG_CACHE_TRACE_MAX_BYTES=B` | An evidence line longer than B bytes drops its candidate, score and gate lists and keeps the decision record, marked `capped` |

The response header carries a compact record — request and dispatch ids, the attempt number, and each selection's policy, prediction and chosen worker — and is omitted when it would exceed 2,048 bytes or contain non-ASCII characters. With every switch unset, nothing is captured for requests that did not ask.

### Useful PromQL Queries

<div class="grid" markdown>

<div class="card" markdown>

#### Affinity Hit Share (Tree Mode)

```promql
sum(rate(smg_cache_aware_policy_branch_total{branch="tree_match"}[5m]))
/
sum(rate(smg_cache_aware_policy_branch_total{
  branch=~"tree_match|spill|expected_wait_fallback|first_healthy_fallback"}[5m]))
```

</div>

<div class="card" markdown>

#### Spill Share (Tree Mode)

```promql
sum(rate(smg_cache_aware_policy_branch_total{branch="spill"}[5m]))
/
sum(rate(smg_cache_aware_policy_branch_total{
  branch=~"tree_match|spill|expected_wait_fallback|first_healthy_fallback"}[5m]))
```

</div>

<div class="card" markdown>

#### Median Match Ratio

```promql
histogram_quantile(0.5,
  sum by (le) (rate(smg_cache_aware_match_ratio_bucket[5m])))
```

</div>

<div class="card" markdown>

#### Engine Prefix Cache Hit Rate

```promql
avg by (worker) (smg_engine_cache_hit_rate)
```

</div>

<div class="card" markdown>

#### Load Distribution

```promql
stddev(smg_worker_requests_active) /
avg(smg_worker_requests_active)
```

</div>

<div class="card" markdown>

#### Tree Size per Model

```promql
max by (model) (smg_cache_tree_tokens)
```

</div>

</div>

### Signals to Watch

| Signal | Meaning | Action |
|--------|---------|--------|
| Spill share rising | Hot prefixes are saturating their holders | Add capacity, or raise the spill thresholds if spills happen at low load |
| Mostly `expected_wait_fallback` | Few requests share prefixes above the threshold | Check the match-ratio histogram; lower `--cache-threshold`, or accept load-based routing |
| High tree match ratios but low `smg_engine_cache_hit_rate` | Tree predictions do not match engine state (evicted prefixes, mismatched page size) | Align `--block-size` with the engines; stream KV events from gRPC workers |
| Tree gauges pinned at `--max-tree-size` | Eviction is trimming the working set | Raise `--max-tree-size` if memory allows |
| `smg_kv_event_subscription_failures_total` increasing | A worker's KV-event stream stopped feeding the index | Check the gateway logs for that worker |

---

## Running Several Gateways

Each gateway keeps its own trees and hash index. Without [mesh synchronization](#mesh-state-synchronization), gateways learn affinity independently, and the same prefix can end up cached on different workers by different gateways. The KV-event index is not affected: every gateway subscribes to every worker and sees the same engine events.

If you do not enable mesh, you can keep each client on one gateway with session affinity at the external load balancer:

=== "NGINX"

    ```nginx
    upstream smg {
        hash $http_x_user_id consistent;  # Affinity by user
        server smg-1:30000;
        server smg-2:30000;
    }
    ```

=== "HAProxy"

    ```haproxy
    backend smg
        balance hdr(X-User-ID)
        hash-type consistent
        server smg-1 smg-1:30000 check
        server smg-2 smg-2:30000 check
    ```

=== "Kubernetes"

    ```yaml
    apiVersion: networking.k8s.io/v1
    kind: Ingress
    metadata:
      annotations:
        nginx.ingress.kubernetes.io/upstream-hash-by: "$http_x_user_id"
    ```

---

## Mesh State Synchronization

With mesh HA enabled (`--enable-mesh`), gateways share tree updates:

- When a tree lookup records the chosen worker, the gateway broadcasts a compact delta: the tree kind, a hash of the request's path, and the chosen worker. Deltas are batched per model and gossip round. Inserts made by the KV-pressure fallback are not broadcast.
- A peer that already knows the path adds the worker as a tenant. A peer that does not requests a repair from a random live peer, which sends that model's tree in pages.
- Only the string and token trees are synchronized. The KV-event index (each gateway subscribes to the engines itself) and the hash index (`--cache-index hash`) stay local to each gateway.
- Host-local ZMQ workers (`ipc://`) are never shared with peers, so they receive traffic only from the gateway on their own host. A prefix whose only holder is another gateway's ZMQ worker routes like a cache miss.

Synchronization is best effort and approximate: trees on different gateways can differ. Inserts are shared but evictions are not: each gateway evicts its own trees against its own `--max-tree-size`. Each delta batch is sent once; a batch that cannot be delivered right away, for example because a peer's stream is backed up, is dropped and not resent. The peer catches up only through a repair, which starts the next time a delta names a path it does not know. A repair that makes no progress for 5 seconds is retried, preferably with another peer, up to three times, then abandoned until the next unknown path. A gateway can also route a few requests before a peer's delta arrives. For mesh setup and configuration, see [High Availability](../architecture/high-availability.md).

---

## Comparison with Other Policies

| Aspect | cache_aware | prefix_hash | consistent_hashing |
|--------|-------------|-------------|-------------------|
| **Affinity key** | Longest prefix in the tree, engine-reported blocks, or boundary heads (hash index) | Hash of the first `--prefix-token-count` tokens, or of the deepest `--cache-boundaries` head | `X-SMG-Routing-Key` header |
| **Memory** | Up to `--max-tree-size` per model and tree | Hash ring, O(workers) | Hash ring, O(workers) |
| **Learns from traffic** | Yes | No | No |
| **Load balancing** | Per-request spill gate, expected-wait fallback, optional KV-pressure triggers | Load factor and absolute threshold, then least loaded | None |

---

## What's Next?

<div class="grid" markdown>

<div class="card" markdown>

### :material-scale-balance: Load Balancing

Compare all available routing policies.

[Load Balancing →](load-balancing.md)

</div>

<div class="card" markdown>

### :material-lightning-bolt: KV Events

Route on what the engines actually cached.

[KV Events Cache-Aware Routing →](../../getting-started/kv-events-cache-aware.md)

</div>

<div class="card" markdown>

### :material-chart-box: Metrics Reference

Complete list of routing metrics.

[Metrics Reference →](../../reference/metrics.md)

</div>

</div>
