---
title: Configuration
---

# Configuration Reference

Gateway configuration flags, defaults, accepted values, and environment variables. Sections mostly follow the headings that `smg launch --help` prints.

---

## Configuration Methods

SMG reads its configuration from:

1. **Command-line flags** (highest priority)
2. **Environment variables**, only for the flags that declare one (the Oracle `ATP_*` variables, `JWT_ISSUER`, `JWT_AUDIENCE`, `JWT_JWKS_URI`, and `CONTROL_PLANE_API_KEYS`) and for the multimodal flags with `SMG_*` fallbacks, plus the environment-only settings in the [Environment Variable Reference](#environment-variable-reference)
3. **Built-in defaults** (lowest priority)

One exception: `RUST_LOG`, when set to a valid filter, replaces the log filter that `--log-level` would build. See [Logging Configuration](#logging-configuration).

The `smg` command that `pip install smg` provides parses flags with its own Python parser, which differs in a few places. See [Python Launcher Differences](#python-launcher-differences).

---

## Running the Gateway

### Commands and Binary Names

| Invocation | Notes |
|------------|-------|
| `smg launch [OPTIONS]` | Starts the gateway. `smg start` is a visible alias of `launch`. |
| `smg [OPTIONS]` | Same flags without a subcommand (Rust binary only; the pip `smg` needs `launch` or `serve`). Top-level flags and a subcommand cannot be combined: `smg --port 8080 launch` is rejected. |
| `amg [OPTIONS]` | A second binary built from the same source (`model_gateway/Cargo.toml` declares both `smg` and `amg`). Identical flags. |
| `shepherd-model-gateway` | The top-level `--help` lists it as the full command name, but Cargo builds no binary with this name. |
| `smg --version`, `smg -V`, `smg --version-verbose` | Print version information and exit. Checked before any other argument is parsed. |

### Flag Syntax

- **Space-separated lists after one flag:** `--worker-urls`, `--selector`, `--prefill-selector`, `--decode-selector`, `--encode-selector`, `--router-selector`, `--routing-key-headers`, `--request-id-headers`, `--storage-context-headers`, `--cors-allowed-origins`, `--prometheus-duration-buckets`, `--mesh-peer-urls`.
- **One value per flag (repeat the flag for more):** `--prefill`, `--decode`, `--encode`, `--model-alias`, `--tenant-api-key`, `--jwt-role-mapping`, `--control-plane-api-keys`. For example, `--jwt-role-mapping A=admin B=user` fails to parse; write `--jwt-role-mapping A=admin --jwt-role-mapping B=user`.
- **Comma-separated:** `--cache-boundaries 2048,8192`.
- **Parsed outside clap:** `--prefill` and `--encode` take an optional trailing bootstrap port (`--prefill URL [PORT]`), which clap cannot express, so the binary parses them itself before clap runs. They work on every entrypoint but do not appear in `smg launch --help`, and they need the space form: `--prefill=URL` is rejected.
- **Hidden aliases:** `--model` for `--model-path` and `--runtime` for `--backend` parse but are not listed in `--help`. Visible aliases are noted next to each flag below.

---

## Worker Configuration

Where the gateway listens and which workers it registers at startup.

| Option | Default | Description |
|--------|---------|-------------|
| `--host` | `0.0.0.0` | IP address the main HTTP server binds. Every listener flag (`--host`, `--prometheus-host`, the probe listener, `--mesh-host`) takes an IP literal, an IPv6 one with or without brackets: `::` or `[::]` for all IPv6 interfaces, `::1` or `[::1]` for IPv6 localhost. A hostname is a configuration error. |
| `--port` | `30000` | Port for the main API server. Must be greater than 0. |
| `--health-check-port` | unset | Dedicated port that also serves `/liveness`, `/readiness`, and `/health` from a middleware-free router on its own single-worker runtime and OS thread, so a saturated gateway cannot starve probes. The same routes stay available on `--port`. Unset turns the dedicated listener off; `0` is rejected. |
| `--worker-urls` | none | Space-separated worker URLs registered at startup. Schemes: `http://`, `https://`, `grpc://`, `grpcs://`, and `ipc://` (same-host ZMQ, see [ZMQ Workers](../getting-started/zmq-workers.md)). IPv6 hosts use brackets. |
| `--job-queue-capacity` | `1000` | Maximum pending control-plane jobs (worker add/remove, tokenizer, MCP, WASM). Size it to the fleet so a service-discovery reconcile pass can enqueue every worker without blocking. Range `1` to `1000000`. |
| `--job-queue-concurrency` | `200` | Maximum control-plane jobs dispatched concurrently. Range `1` to `100000`. |
| `--zmq-engine-count` | unset | Data-parallel engines per startup ZMQ worker: each `ipc://` worker becomes one grouped worker whose handshake waits for this many engines on one socket set. Must be at least `1`. |
| `--upstream-http2` | `false` | Speak HTTP/2 to workers by prior knowledge (h2c on cleartext) for request dispatch and health probes alike, multiplexing every request to a worker over one connection. Negotiated per worker at registration: a worker that does not answer HTTP/2 stays on HTTP/1.1, so mixed fleets can roll out in any order. `http_pool.http2` on a worker spec pins the version instead. |

**Examples**:
```bash
--worker-urls http://worker1:8000 http://worker2:8000
--worker-urls http://[::1]:8000 http://192.168.1.1:8000  # IPv6 and IPv4
--worker-urls grpc://worker1:50051  # gRPC mode
--worker-urls ipc:///tmp/smg-zmq/engine-0  # same-host ZMQ
```

---

## Routing Policy Configuration

Routing flags configure the default policy (`--policy`). In PD mode, `--prefill-policy` and `--decode-policy` build their policies from the same flag values. For how each policy behaves, see [Load Balancing](../concepts/routing/load-balancing.md).

### Load Balancing Policy

| Option | `--policy` |
|--------|------------|
| Default | `cache_aware` |
| Values | `random`, `round_robin`, `passthrough`, `cache_aware`, `power_of_two`, `least_load`, `prefix_hash`, `consistent_hashing`, `manual`, `bucket` |

| Policy | How it picks a worker |
|--------|-----------------------|
| `random` | A random healthy worker. |
| `round_robin` | Cycles through healthy workers. |
| `passthrough` | Always the first healthy worker. It reads no load reports (so it never forces load polling under `--disable-load-monitoring`) and starts no KV-event subscription, and is meant for single-backend gateways; with several workers registered it logs one warning and still sends everything to the first. |
| `cache_aware` | The worker holding the longest matching prompt prefix. Below `--cache-threshold`, or when the spill gate fires, it falls back to least-load expected-wait selection. See [Cache-Aware Routing](../concepts/routing/cache-aware.md). |
| `power_of_two` | Samples two healthy workers and takes the one with the lower least-load expected wait. Needs at least two workers unless service discovery, IGW mode or `--dp-aware` (each URL registers one worker per DP rank) is on. |
| `least_load` | The worker with the lowest expected wait: queued plus in-flight token work divided by throughput, plus a KV-pressure term. See [Least Load Policy Options](#least-load-policy-options). |
| `prefix_hash` | Hashes the request's leading tokens onto a consistent-hash ring and walks away from overloaded workers. |
| `consistent_hashing` | Consistent-hash ring keyed on the request's routing key or, without one, on its `X-Forwarded-For` or `Cookie` header. `X-SMG-Target-Worker` picks a worker by index; a request with none of these goes to a random worker. |
| `manual` | A sticky map from routing key to worker with idle eviction. First-seen keys are placed by `--assignment-mode`. |
| `bucket` | Maps request length (tokens, or characters when untokenized) to per-worker length buckets whose boundaries adapt every 5 seconds; picks the least-loaded worker when the `--balance-*` imbalance check fires. |

| Option | Default | Description |
|--------|---------|-------------|
| `--power-of-two-load-metric` | `expected_wait` | What `power_of_two` compares between its two samples: `expected_wait`, `requests` or `least_requests`. |

**Recommendation**: Use `cache_aware` for LLM workloads to maximize KV cache hit rates.

### Cache-Aware Policy Options

| Option | Default | Description |
|--------|---------|-------------|
| `--cache-threshold` | `0.3` | Minimum matched-prefix share (0.0 to 1.0) before a request pins to a worker already holding that prefix; below it the request is load-balanced. Alias: `--cache-match-threshold`. |
| `--balance-abs-threshold` | `64` | Spill gate, absolute part: a matched worker is skipped for the least-loaded one when its load exceeds the healthy-fleet mean by this many requests and by `--balance-rel-threshold`. `bucket` reuses it for its own imbalance check. Alias: `--spill-abs-threshold`. |
| `--balance-rel-threshold` | `1.5` | Spill gate, relative part (a multiple of the healthy-fleet mean); fires only together with the absolute part. Must be at least `1.0`. `bucket` reuses it too. Alias: `--spill-rel-threshold`. |
| `--balance-prefix-margin-tokens` | `0` | Spill gate, price of a held prefix: a matched worker's absolute spill margin is raised to the request's held-prefix tokens divided by this value (rounded up), never above `--balance-abs-threshold`, so a deep held prefix (a long conversation's follow-up) keeps its holder through a skew that would move a shallow one. `0` leaves held prefixes unpriced. Suits prefill pools serving long multi-turn sessions, for example `1024`. Decode picks are never priced. |
| `--balance-uncached-credit` | `false` | In the expected-wait estimate later picks read, a `cache_aware` hit on a worker holding the request's prefix is charged the prompt it still has to prefill (the token count less the held tokens, never under the mean prefill nor over the whole prompt) instead of the whole prompt, so a hit on a long cached conversation does not steer the next miss away from that worker. Misses, spills and decode picks keep the whole prompt. |
| `--balance-token-usage-threshold` | `1.0` | Abandon cache affinity for shortest-queue when the KV-usage spread (hottest minus coldest backend, 0.0 to 1.0) exceeds this. Catches long-context KV imbalance that request counts miss. The backend must report `token_usage`. `1.0` or more disables it; must be greater than 0. |
| `--overload-token-usage-threshold` | `1.0` | Safety valve for a saturated engine: when the hottest backend's KV utilization exceeds this, de-rank it regardless of spread. It stays routable; see [Worker Overload Protection](#worker-overload-protection) for the hard cutoff. Best set high (for example `0.9`). `1.0` or more disables it; must be greater than 0. |
| `--overlap-decay` | `0.0` | Anti-hotspot decay for event-driven selection: each candidate's overlap score is divided by `1 + overlap_decay × backlog`, where backlog is the worker's waiting-prefill blocks beyond the least-backlogged candidate's, per request block. Needs backend load reporting. `0.0` disables it. |
| `--selection-temperature` | `0.0` | Softmax temperature over min-max normalized scores in event-driven selection, spreading picks across near-equal candidates. `0.0` is exact argmax. |
| `--selection-policy` | `cache-aware-default` | The worker-selection policy `cache_aware` runs over the per-worker inputs the router gathers (prefix overlap, in-flight requests, backend load reports). `cache-aware-default`, the affinity-group decision, is the one policy. |
| `--selection-accounting-ttl-ms` | `0` | Lifetime in milliseconds of `cache_aware`'s optimistic dispatch bookings: the predicted prefill and the prefix placement are charged to the chosen worker until the engine reports them or the booking expires. `0` disables the bookings; set it a little above the engine's KV-event lag. |
| `--worker-warmup-secs` | `60` | Warm-up slice for cache-aware routing: for this many seconds after a worker becomes routable, until its index has grown by `--worker-warmup-blocks` blocks, one cache miss in `1/--worker-warmup-share` is routed to it so it builds a cache instead of idling. `0` disables the slice. |
| `--worker-warmup-share` | `0.25` | Share of cache misses offered to warming workers; a finite value from `0.0` to `1.0`. |
| `--worker-warmup-blocks` | `1024` | A worker whose index has grown by this many blocks since it became thin is warm. |
| `--worker-warmup-thin-ratio` | `0.5` | A worker whose index holds less than this share of the fleet's median (or nothing) is thin and receives the warm-up slice until it has grown by `--worker-warmup-blocks`, whatever emptied it (a resync after a publisher restart, a resubscription, an engine that came back empty). A finite value from `0.0` to `1.0`; `0` keeps the age rule alone. |
| `--worker-warmup-divert-every` | `8` | One cache hit in this many is diverted to a thin worker although another worker holds its prefix (shallow overlaps first, one in flight per thin worker), so an index emptied by a resync refills on a workload where every request has a holder. `0` disables the diversion. |
| `--eviction-interval` | `120` | Seconds between cache-tree eviction cycles (placement-map TTL sweeps with `--cache-index hash`). Also the sweep interval of the manual-policy and sticky-session maps. Must be greater than 0 for `cache_aware`. |
| `--max-tree-size` | `67108864` | Size budget for each model's approximation tree, shared across all of that model's workers: characters for HTTP workers, tokens for gRPC workers. Eviction keeps every tree at or under it. |
| `--block-size` | `16` | Match granularity for token routing: the token-tree page size, and the KV block size assumed in event-driven selection. Must be greater than 0. |
| `--cache-index` | `tree` | Index under-layer: `tree` (radix prefix trees) or `hash` (a TTL'd exact-match placement map keyed on request heads at `--cache-boundaries`; token-bearing requests only, untokenized requests stay load-balanced). `hash` requires `--cache-boundaries`. |
| `--cache-boundaries` | none | Comma-separated, strictly ascending token positions (each greater than 0) at which serving engines retain reusable prefix state, for example `2048,8192`. `cache_aware` with `--cache-index hash`, and `prefix_hash`, hash request heads at the deepest boundary the request reaches. |
| `--cache-ttl-secs` | `180` | Seconds a hash-index placement stays routable. Set it close to the engines' cache retention. Must be at least `1`. |
| `--kv-indexer-ttl-secs` | unset | Deprecated and ignored: the TTL prune of the positional indexer, which the chain index replaced. The chain index holds what the engines report and shrinks with their removals, so it has no prune. The flag is still accepted so existing launch lines keep working; a value above `0` logs a warning at start-up under cache-aware routing. Goes in a later release; drop it from launch lines. |
| `--kv-indexer-max-entries` | unset | Deprecated and ignored: the capacity ceiling of the positional indexer's prune, gone with it; accepted and warned about like `--kv-indexer-ttl-secs`. Goes in a later release. |

The event-driven options apply when workers publish KV cache events; see [KV Events Cache-Aware Routing](../getting-started/kv-events-cache-aware.md). `--kv-index` is deprecated: the chain index is the only KV index behind cache-aware routing, so the flag takes `chain` only and does nothing (`positional` names the index that was removed and is refused); it goes in a later release.

### Least Load Policy Options

These apply wherever `least_load` is the policy, including as `--prefill-policy` or `--decode-policy`. The policy routes to the worker with the lowest expected wait, `(queued_tokens + inflight_tokens) / throughput + kv_pressure_weight × k / (1 − k)`, where `k` is the worker's KV-cache usage and `throughput` is the worker's live output rate, or, for a prefill worker, the configured input rate (a prefill wait does not depend on how fast the worker generates).

| Option | Default | Description |
|--------|---------|-------------|
| `--least-load-kv-pressure-weight` | `0.15` | Weight (in seconds) of the KV-pressure term. Must be finite and at least 0. |
| `--least-load-default-throughput` | `2000` | Input throughput (tokens/s) assumed for prefill workers, and the fallback output throughput for other workers when no candidate reports a live rate. Set it to the per-replica input rate for prefill, the output rate otherwise; the CLI shares one value across the roles (separate prefill and decode values need a programmatically built router configuration). Must be greater than 0. |
| `--least-load-mean-prefill-tokens` | `1024` | Prefill length assumed for the in-flight estimate when a request's token count is unknown at routing time. Must be greater than 0. |
| `--least-load-max-waiting-requests` | `0` | Per-worker waiting-queue cap: skip workers whose reported waiting requests, plus dispatches since their last poll, have reached this count. When every candidate is at the cap, the policy selects no worker rather than deepening an engine backlog. Set it below the engine's max batch size. `0` disables the cap. |

### Prefix Hash Policy Options

| Option | Default | Description |
|--------|---------|-------------|
| `--prefix-token-count` | `256` | Number of leading tokens to hash, or four times as many characters when the request is untokenized. With `--cache-boundaries` set, the deepest boundary the request reaches is used instead. Must be greater than 0. |
| `--prefix-hash-load-factor` | `1.25` | A worker counts as overloaded when its load exceeds this multiple of the average load (and the absolute threshold below). The policy then tries shallower boundaries and finally the least-loaded worker. Must be at least `1.0`. |
| `--prefix-hash-balance-abs-threshold` | `10` | Absolute load difference over the average that a worker must also exceed before `prefix_hash` treats it as overloaded. |

### Manual Policy and Sticky Sessions

`--policy manual` pins each routing key to a worker. `--routing-key-override` applies the same sticky map on top of any other policy. `manual` and `consistent_hashing` already key on the routing key themselves, so the override adds no second map to them; they read the same key in the same order: the first valid routing-key header, then `X-SMG-Routing-Key` when no configured header carries a valid value (a step the override map does not take: it reads the configured names only), then, under the override, the body `rid`. `manual` and the override map share the options below and sweep their map every `--eviction-interval` seconds; `consistent_hashing` keeps no map (a key's place on its ring is recomputed when a worker joins or leaves), so the eviction options do not apply to it. See [Sticky Sessions](../concepts/routing/sticky-sessions.md).

| Option | Default | Description |
|--------|---------|-------------|
| `--routing-key-override` | `false` | Sticky sessions on any policy: a conversation's requests prefer the same worker while it is routable; failover, retries, the in-flight respill, eviction and `X-SMG-Target-Worker` can place a request elsewhere (see the guide's guarantees). The key is the first valid routing-key header (`--routing-key-headers`; non-empty UTF-8, at most 128 bytes) with its per-turn and per-retry suffixes stripped, or, when no header carries one, the request body's `rid` stripped the same way (`conv_t2_r1` becomes `conv`) when the result is at most 128 bytes. Keeps automatic body forwarding buffered so the body `rid` can be read. Alias: `--sticky-sessions`. |
| `--routing-key-headers` | `x-smg-routing-key` | Ordered header names checked for the routing key; the first header present with a valid value wins, and a valid header outranks the body `rid`. Names are lowercased, and invalid names fail at parse time. Passing the flag replaces the default for the override map, so list `x-smg-routing-key` again to keep it there; `manual` and `consistent_hashing` still try `X-SMG-Routing-Key` after the configured names. With the override on, header keys get the same suffix stripping as `rid` keys. |
| `--max-idle-secs` | `14400` (4 hours) | Seconds an unused routing key stays pinned before it is evicted from the manual-policy or sticky-session map. Alias: `--sticky-key-idle-secs`. |
| `--assignment-mode` | `random` for `--policy manual`, `delegate` for the sticky-session map | How a first-seen key picks its worker: `random`, `min_load` (fewest requests), `min_group` (fewest keys), or `delegate` (route through the underlying policy, then pin). With `--policy manual` there is no underlying policy, so `delegate` falls back to `min_load`. |

### Data Parallelism and Multi-Model Options

| Option | Default | Description |
|--------|---------|-------------|
| `--dp-aware` | `false` | Discover each worker's data-parallel size at registration and register one routable worker per DP rank. |
| `--dp-minimum-tokens-scheduler` | `false` | In the HTTP PD router, for an engine registered as one worker, pick each prefill and decode DP rank by fewest tracked tokens instead of the worker's registered rank. Under `--dp-aware` the engine's ranks are registered as workers and the routing policy's pick among them is the rank choice, so this flag chooses nothing there: the pair starts with a warning in the log and runs as `--dp-aware` alone does. |
| `--enable-igw` | `false` | Inference Gateway (IGW) mode: build every router family and choose one per request from the requested model's workers (HTTP or gRPC, regular or disaggregated, external provider), so one gateway serves many models. `--service-discovery` turns it on automatically. |

---

## Worker Overload Protection

Absolute per-worker ceilings, evaluated once per load report rather than per request, on by default. A worker at or above a ceiling leaves routing, while another candidate is under the ceilings, until its load report drops below it. When every candidate worker is overloaded, the request goes to the least-loaded of them; with `--worker-overload-shed` it is shed immediately with `503` and error code `worker_overload_protection_shed` instead of queueing. The shed carries `Retry-After` set to `--load-monitor-interval` (at least 1 second), and the gateway does not retry it. See [Overload Protection](../concepts/reliability/overload-protection.md).

| Option | Default | Description |
|--------|---------|-------------|
| `--worker-overload-protection` | `true` | Protection is on by default; the flag is still accepted so that command lines written for earlier releases parse unchanged. |
| `--disable-worker-overload-protection` | `false` | Switch protection off: no worker is left out of routing for its waiting queue or KV usage. Per-worker `overload` blocks still apply. |
| `--worker-overload-waiting-requests` | `8` | Queued (waiting) requests, summed across DP ranks, at or above which a worker is overloaded. Must be at least `1`, because `0` would veto every worker. |
| `--worker-overload-token-usage` | `0.8` | Mean KV-cache token usage across DP ranks, in `(0.0, 1.0]`, at or above which a worker is overloaded. The backend must report `token_usage`. `0.0` is rejected because it would veto every worker. |
| `--worker-overload-shed` | `false` | Refuse a request with `503 worker_overload_protection_shed` (`Retry-After` the load poll interval) when every worker it could use is overloaded, instead of routing it to the least-loaded one; also refuses it the same way when the worker chosen for it crossed a threshold between selection and dispatch. |

An `overload` block on a worker spec overrides these values per signal, and keeps protection on for that worker even under `--disable-worker-overload-protection`. Protection needs load reports, so the load monitor keeps polling workers that have it on (see [Load Monitoring Configuration](#load-monitoring-configuration)).

How the overload settings differ from the other load thresholds:

| Setting | Scope | At the threshold |
|---------|-------|------------------|
| `--worker-overload-token-usage`, `--worker-overload-waiting-requests` | Every policy | The worker leaves routing while another candidate is under the thresholds. If every candidate is overloaded, the request goes to the least-loaded one, or gets an immediate `503` shed under `--worker-overload-shed`. |
| `--overload-token-usage-threshold` | `cache_aware` only | The hottest backend is de-ranked within cache-aware selection but stays routable. |
| `--least-load-max-waiting-requests` | `least_load` only | The worker is skipped while at the cap. With every worker at the cap, the policy selects no worker. |

---

## RL Control Plane Configuration

| Option | Default | Description |
|--------|---------|-------------|
| `--enable-rl` | `false` | Mount the RL control plane under `/v1/rl`: worker discovery, engine-route passthrough, and fan-out. When off, no RL route exists. The routes use the same authentication as the admin routes. |
| `--rl-control-timeout-secs` | `600` | Total timeout for one proxied engine control call (weight refits can take minutes). Must be at least `1` when RL is enabled. |
| `--rl-fanout-concurrency` | `32` | Maximum concurrent engine calls in one fan-out. Must be at least `1` when RL is enabled. |

See [RL Control Plane](../getting-started/rl-control-plane.md).

---

## PD Disaggregation Configuration

Prefill-decode (PD) disaggregation runs prefill and decode on separate workers. Encode-prefill-decode (EPD) mode adds encode workers that run the vision tower. See [PD Disaggregation](../concepts/routing/pd-disaggregation.md).

### Enable PD or EPD Mode

| Option | Default | Description |
|--------|---------|-------------|
| `--pd-disaggregation` | `false` | Enable PD mode. |
| `--epd-disaggregation` | `false` | Enable EPD mode (gRPC + TokenSpeed only). Encode workers run the vision tower and ship embeddings to prefill over Mooncake; prefill and decode reuse the PD path. |

### Prefill Servers

| Option | `--prefill` |
|--------|-------------|
| Format | `URL [BOOTSTRAP_PORT]` |
| Multiple | Yes (repeat the flag) |

The token after the URL is read as the bootstrap port only if it is a port number or `none`; leaving it out, like `none`, registers the leg without one. A leg registered without a port is not left without one when its partners rendezvous on a bootstrap server: for a SGLang or TokenSpeed prefill leg, a vLLM prefill leg on the Mooncake connector, and an encode leg, the request path hands the partners the default port `8998`, and the gateway probes that port once after registration, as it probes a registered one (a vLLM prefill leg on NIXL, MoRI-IO or a KV store is handed no port). Used in PD and EPD modes.

**Examples**:
```bash
--prefill http://prefill1:30001 9001 \
--prefill http://prefill2:30002 9002 \
--prefill http://prefill3:30003 none  # No bootstrap port
```

### Decode Servers

| Option | `--decode` |
|--------|------------|
| Format | URL |
| Multiple | Yes (repeat the flag, one URL each) |

**Example**:
```bash
--decode http://decode1:30003 \
--decode http://decode2:30004
```

### Encode Servers

| Option | `--encode` |
|--------|------------|
| Format | `URL [BOOTSTRAP_PORT]`, same rules as `--prefill` |
| Multiple | Yes (repeat the flag) |

EPD mode only.

### PD-Specific Policies

| Option | Default | Description |
|--------|---------|-------------|
| `--prefill-policy` | the `--policy` value | Policy for prefill workers: `random`, `round_robin`, `cache_aware`, `power_of_two`, `least_load`, `prefix_hash`, `consistent_hashing`, `manual`, or `bucket`. |
| `--decode-policy` | the `--policy` value | Policy for decode workers; same values. An explicit `--decode-policy bucket` is rejected at startup, and in EPD or IGW mode a `bucket` decode policy inherited from `--policy` is rejected too. |
| `--encode-policy` | `consistent_hashing` | Policy for encode workers in EPD mode: `random`, `round_robin`, or `consistent_hashing`. |
| `--pd-pairing-mode` | `lenient` | How strictly placement pairs a prefill with a decode on their KV transfer protocol. `off` pairs on nothing; `lenient` refuses only a known difference in runtime, transport, or KV layout (unknown components and engine versions pair with anything); `strict` also refuses unknown components and version differences. `--help` lists it under Routing Policy. |

### Prefill Admission

These flags cap how many Prefill requests the gateway keeps in flight on each prefill worker, counting only this gateway process's own dispatches. Requests over the cap wait in one FIFO queue per gateway process, not per worker.

| Option | Default | Description |
|--------|---------|-------------|
| `--prefill-max-inflight-requests-per-worker` | `-1` (off) | Maximum in-flight Prefill requests per prefill worker. Any value of 0 or less turns the gate off, and setting either queue flag then fails startup validation. A positive value requires PD or EPD mode and is rejected together with `--priority-scheduler-enabled`. |
| `--prefill-queue-size` | unset (`100` with the gate on) | Requests that may wait for a Prefill slot. A request that finds the queue full, or no queue (`0`), gets `429` (`pd_prefill_queue_full`). |
| `--prefill-queue-timeout-secs` | unset (`60` with the gate on) | Maximum time a request waits for a Prefill slot before it gets `429` (`pd_prefill_queue_timeout`). Must be greater than 0 when the queue is on; the check applies the defaults, so an explicit `0` passes startup validation only next to `--prefill-queue-size 0`. |

### Worker Startup Configuration

These govern every worker registration, not only PD workers, although `--help` lists them under PD Disaggregation.

| Option | Default | Description |
|--------|---------|-------------|
| `--worker-startup-timeout-secs` | `1800` (30 min) | How long registration keeps probing a starting worker before giving up. Must be greater than 0. |
| `--worker-startup-delay` | `0` | Grace period in seconds before the first startup probe, leaving a loading engine alone. |
| `--worker-startup-check-interval` | `30` | Seconds between startup probes. Must be greater than 0. |

### Readiness Gates

In PD and EPD mode `/readiness` answers `200` only while every role has enough healthy workers. Each role needs `max(minimum workers, ceil(expected workers × minimum percent / 100))` healthy workers; without any of these flags that is one healthy prefill worker and one healthy decode worker (and one encode worker in EPD mode, which has no flags of its own). Setting any one of the six turns the gates on for both roles, each role keeping `1` where it has no flag. The readiness body then carries a `roles` object with the healthy, registered, expected, minimum and required counts per role.

| Option | Default | Description |
|--------|---------|-------------|
| `--prefill-min-ready-workers` | `1` | Absolute minimum number of healthy prefill workers required. Must be greater than 0 and not above `--prefill-expected-workers` when that is set. |
| `--prefill-expected-workers` | unset | Desired number of prefill workers, the denominator of the percentage gate. Has no effect on its own; must be greater than 0. |
| `--prefill-min-ready-percent` | unset | Minimum healthy percentage (`1` to `100`) of `--prefill-expected-workers`, which it requires. |
| `--decode-min-ready-workers` | `1` | Absolute minimum number of healthy decode workers required. Must be greater than 0 and not above `--decode-expected-workers` when that is set. |
| `--decode-expected-workers` | unset | Desired number of decode workers, the denominator of the percentage gate. Has no effect on its own; must be greater than 0. |
| `--decode-min-ready-percent` | unset | Minimum healthy percentage (`1` to `100`) of `--decode-expected-workers`, which it requires. |

---

## Load Monitoring Configuration

The load monitor polls every worker group for load reports. The reports feed the load-aware policies (`cache_aware`, `least_load`, `power_of_two`), worker overload protection, and the `smg_engine_*` gauges. `--help` also lists the PD admission wait under this heading; that gate uses the running window each decode engine advertises at registration rather than the load reports.

| Option | Default | Description |
|--------|---------|-------------|
| `--load-monitor-interval` | `10` | Seconds between load polls of each worker group. This is the global poll interval for all of the consumers above, even though `--help` still describes it as "for PowerOfTwo routing". The overload shed advertises it as `Retry-After`. Must be greater than 0. The worker spec's `load_monitor_interval_secs` field overrides it per group: a group polls at the smallest value its workers set, floored to one second. |
| `--disable-load-monitoring` | `false` | Poll a worker group only when something needs the data: a load-aware policy, `--dp-minimum-tokens-scheduler`, `--engine-metrics`, or overload protection on one of its workers. By default every group is polled from registration onward. |
| `--engine-metrics` | `false` | Force load polling for the `smg_engine_*` Prometheus gauges even when nothing else needs it. Every successful poll is exported anyway, so this matters only together with `--disable-load-monitoring`. |
| `--pd-admission-wait-secs` | `30` | Seconds a prefill/decode dispatch waits for a free slot in the decode engine's running window (`--max-num-seqs` / `--max-running-requests`) before shedding with `503` `worker_overload_protection_shed`. Keep it well under the engine's bootstrap deadline (120 seconds on TokenSpeed) so a request that waits still dispatches with the deadline ahead of it. `0` sheds immediately. Engines that report no running window are never gated. |

---

## Multimodal Configuration

These settings apply where the gateway fetches and preprocesses media itself: the gRPC pipeline that serves gRPC and ZMQ workers. Most of the flags fall back to an `SMG_*` environment variable when unset, so a value resolves as flag, then environment variable, then built-in default. See [Multimodal](../concepts/architecture/multimodal.md).

### Tensor Transport

Controls how the router ships preprocessed multimodal tensors (image/video encoder inputs and model-specific tensors) to gRPC workers. This does not affect accuracy: the inline and shared-memory paths produce byte-identical tensors. The TokenSpeed and vLLM gRPC paths can use shared memory; only TokenSpeed workers pull pixels over RDMA. ZMQ workers always receive tensors inline.

Resolution precedence (highest first): CLI flag, `SMG_MM_*` environment variable, built-in default.

| Option | Default | Description |
|--------|---------|-------------|
| `--multimodal-tensor-transport` | `inline` | Transport for large tensors: `inline` (gRPC bytes); `shm` (use `/dev/shm` whenever the router can write it; the operator asserts co-location); `auto` (use `/dev/shm` only when the worker is verified to share it: the router compares the worker's advertised `/dev/shm` namespace token from `GetServerInfo` with its own and falls back to inline on a mismatch); or `rdma` (pull pixels over the NIXL RDMA lane, see [RDMA Pixel Lane](#rdma-pixel-lane)). `--help` lists only the first three values, but `rdma` is accepted too. Env: `SMG_MM_TENSOR_TRANSPORT` (legacy name `SMG_TOKENSPEED_MM_TENSOR_TRANSPORT`). |
| `--multimodal-shm-min-bytes` | `65536` | Minimum tensor size in bytes before the SHM path is used; smaller tensors stay inline. Env: `SMG_MM_SHM_MIN_BYTES` (legacy name `SMG_TOKENSPEED_MM_SHM_MIN_BYTES`). |

The worker spec has matching `multimodal_tensor_transport` and `multimodal_shm_min_bytes` fields, consulted for the worker that runs the vision encoder. The legacy `SMG_TOKENSPEED_MM_*` names still work but log a migration warning.

On the worker side, the vLLM and TokenSpeed gRPC servicers read `TOKENSPEED_UNLINK_MM_SHM_AFTER_READ` (default on: unlink each `/dev/shm` segment after the worker reads it; `0`, `false`, or `no` keeps the segments), and the TokenSpeed servicer also reads `TOKENSPEED_LOG_MM_TIMING` (worker-side timing logs).

### Media Processing and Limits

| Option | Default | Description |
|--------|---------|-------------|
| `--multimodal-max-inflight-bytes` | unset (unbounded) | Most bytes of preprocessed media the gateway holds in flight for engines at once. A request that fits waits briefly for room, then gets `429`; a request larger than the whole budget gets `413` immediately. A waiting request still holds its media, so size memory for about twice this value. `0` is refused rather than read as unset. No environment fallback. |
| `--mm-per-request-image-limit` | unset (each model spec's limit) | Per-request image-count limit applied to every model, replacing each model spec's built-in limit (for example, to match the engine's `--limit-mm-per-prompt`). Must be at least `1`. Takes precedence over `SMG_IMAGE_MAX_COUNT`. |
| `--mm-processing` | `auto` | Where media for vLLM gRPC workers is fetched and preprocessed: `auto` (the worker when both the model spec and the worker allow it, otherwise the gateway), `router` (always the gateway), or `worker` (always the engine; models without worker-side expansion are rejected). Case-insensitive. Env: `SMG_MM_PROCESSING`; an unreadable value there stops startup. |
| `--mm-pixel-cache-mb` | `0` (off) | Budget in MiB for the cache of router-side preprocessed media. Env: `SMG_MM_PIXEL_CACHE_MB`; a non-numeric value logs a warning and leaves the cache off. |
| `--log-mm-timing` | `false` | Log per-request multimodal timing at `INFO`. Env: `SMG_LOG_MM_TIMING` (`1`, `true`, `yes`, or `on`). |
| `--mm-allowed-media-domains` | unset (any host the address policy passes) | Hosts the gateway fetches client-supplied media URLs from when it processes media itself, by name, comma-separated; a redirect to any other host is refused. |
| `--mm-allow-private-media-hosts` | `false` | Allow client-supplied media URLs that name or resolve to a loopback, link-local or private address, for a deployment whose media origin is internal; by default such URLs are refused before anything is connected. Env: `SMG_MM_ALLOW_PRIVATE_MEDIA_HOSTS`. |
| `--mm-fetch-timeout-ms` | `10000` | Image and audio download timeout in milliseconds; must be positive. Env: `SMG_MM_FETCH_TIMEOUT_MS`. |
| `--mm-video-fetch-timeout-ms` | `30000` | Video download timeout in milliseconds; must be positive. Env: `SMG_MM_VIDEO_FETCH_TIMEOUT_MS`. |
| `--mm-turbojpeg` | `prefer` | What the gateway's own media pipeline does when libjpeg-turbo (`libturbojpeg.so.0`) cannot be loaded: `prefer` decodes JPEGs with the pure-Rust decoder, whose pixels differ from the reference decoder's by a few levels, says so once at start-up and sets the gauge `smg_mm_turbojpeg_available` to `0`; `require` stops start-up with an error instead, for a deployment whose pixels must match the engine's. Env: `SMG_MM_TURBOJPEG`. |

### RDMA Pixel Lane

The RDMA lane lets TokenSpeed workers, including EPD encode workers, pull cached pixels from the gateway over NIXL instead of receiving them inline; vLLM workers cannot pull RDMA payloads yet. It needs a gateway built with the `mm-rdma` Cargo feature and NIXL; in other builds every transfer falls back to inline. The lane is on when `--multimodal-tensor-transport rdma` (or the legacy `--mm-pixel-rdma`) is set and a listener IP is configured.

| Option | Default | Description |
|--------|---------|-------------|
| `--mm-pixel-rdma` | `false` | Legacy switch for the lane; `--multimodal-tensor-transport rdma` is the first-class one. Env: `SMG_MM_PIXEL_RDMA` (`1` or `true`). |
| `--rdma-listen-ip` | unset | Listener IP for the lane's metadata exchange. Without one, the lane stays on the inline path. Env: `SMG_RDMA_LISTEN_IP`. |
| `--rdma-slot-ttl-s` | derived: worker hold + 30 s (210 s with default worker settings) | Seconds a leased pixel slot lives without a free notification. It must exceed the worker's maximum hold (`SMG_RDMA_LANDING_WAIT_S` + `SMG_RDMA_READ_TIMEOUT_S`); a smaller value is ignored with a warning. Env: `SMG_RDMA_SLOT_TTL_S`. |

Environment-only RDMA settings, read by the gateway. The TokenSpeed worker reads the two timeouts under the same names with the same defaults, so set them identically on both sides.

| Variable | Default | Description |
|----------|---------|-------------|
| `SMG_RDMA_LISTEN_PORT` | `18515` | Port of the metadata exchange listener. |
| `SMG_RDMA_POOL_SLOTS` | `64` | Slots in the pre-registered pixel arena. |
| `SMG_RDMA_SLOT_BYTES` | `33554432` (32 MiB) | Bytes per slot; one slot must hold one image's framed pixel buffer. The arena (slots × bytes) is capped at 8 GiB by reducing the slot count. |
| `SMG_RDMA_LANDING_WAIT_S` | `120` | How long the worker waits for a free landing slot. Part of the derived slot TTL. |
| `SMG_RDMA_READ_TIMEOUT_S` | `60` | The worker's RDMA read timeout. Part of the derived slot TTL. |

### Deprecated Environment Fallbacks

`--mm-processing`, `--mm-pixel-cache-mb`, `--mm-pixel-rdma`, `--rdma-listen-ip`, `--rdma-slot-ttl-s`, and `--log-mm-timing` replace settings that used to be environment-only. Their `SMG_*` fallbacks still work, but a value taken from the environment logs a deprecation warning stating that env support ends in the next minor release. Move these settings to the flags.

### Environment-Only Media Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `SMG_IMAGE_MAX_INPUT_BYTES` | `268435456` (256 MiB) | Byte cap for one image input. |
| `SMG_VIDEO_MAX_INPUT_BYTES` | `268435456` (256 MiB) | Byte cap for one video input. |
| `SMG_AUDIO_MAX_INPUT_BYTES` | `268435456` (256 MiB) | Byte cap for one audio input. |
| `SMG_VIDEO_MAX_DECODED_BYTES` | `1073741824` (1 GiB) | Cap on a decoded video's RGB payload. |
| `SMG_VIDEO_MAX_DECODE_PIXELS` | `1000000000000` (10^12) | Cap on one video decode's work, in pixels: the source frames the decoder must run through to produce the sampled frames, each counted as the clip's width times height. A decode past the cap is refused before any decoder starts, with an error that states the clip's frames, dimensions and pixels against the cap; a shorter or smaller clip, or a smaller frame budget, fits it. A positive integer; `0` or a non-number reads as the default. |
| `SMG_AUDIO_MAX_DECODED_BYTES` | `268435456` (256 MiB) | Cap on decoded audio. |
| `SMG_VIDEO_PROCESS_TIMEOUT_SECS` | `30` | Timeout in seconds for each external `ffmpeg`/`ffprobe` process that decodes video. |
| `SMG_AUDIO_PROCESS_TIMEOUT_SECS` | `30` | The same timeout for audio decoding. |
| `SMG_VIDEO_DECODE_BACKEND` | `auto` | Force a video decoder: `ffmpeg`, or `opencv` (needs a build with the `opencv-video` feature). |
| `SMG_AUDIO_DECODE_BACKEND` | `auto` | Force an audio decoder: `symphonia` or `ffmpeg`. `auto` tries Symphonia, then ffmpeg. |
| `SMG_IMAGE_MAX_COUNT`, `SMG_VIDEO_MAX_COUNT`, `SMG_AUDIO_MAX_COUNT` | unset | Deployment-wide per-request media-count limit that replaces each model spec's limit; it never enables a modality the spec does not declare. `--mm-per-request-image-limit` wins for images. |
| `SMG_VLLM_ENCODER_INPUT_DTYPE` | the worker's `multimodal_encoder_dtype` label, else `float32` | Wire dtype of encoder inputs sent to vLLM workers. |
| `SMG_TOKENSPEED_ENCODER_INPUT_DTYPE` | the worker's `multimodal_encoder_dtype` label, else `bfloat16` | Wire dtype of encoder inputs sent to TokenSpeed workers. `SMG_TOKENSPEED_IMAGE_ENCODER_INPUT_DTYPE`, `SMG_TOKENSPEED_VIDEO_ENCODER_INPUT_DTYPE`, and `SMG_TOKENSPEED_AUDIO_ENCODER_INPUT_DTYPE` override it per modality. |
| `SMG_MM_THREADS` | the smaller of the machine's parallelism and `8` in the gateway; no pool in a worker's own media pipeline | Threads of the dedicated pool that one image's preprocessing stages may spread over, read once when the pool is first built (the threads are named `smg-mm-<n>`). A positive integer; `0` or a non-number reads as unset. In a worker's own media pipeline each request's preprocessing stays on its own thread unless this is set. An operation that finds the pool busy runs on its caller's thread, so this is not a cap on preprocessing CPU under load. |
| `SMG_MM_ONE_PASS_WIRE` | on | Whether the images of a request whose eligible workers all read one encoder-input dtype are preprocessed straight into that dtype, in one pass. `0`, `false`, `no` or `off` (any case) sends every request through the two-pass write instead: the neutral patch layout first, then the write in the worker's dtype at assembly. The bytes are the same either way. With `--mm-pixel-cache-mb` set, the one pass caches the wire bytes, twice the size of the neutral patches at the half-width dtypes and four times at `float32`, against the same budget, so the cache holds fewer images. Read once at start-up; a value that is neither an on nor an off spelling logs a warning and leaves the setting on. |

---

## Service Discovery (Kubernetes)

Watches Kubernetes pods and registers the matching ones as workers; Kubernetes is one of four providers, the others (a file manifest, Consul, Slurm) are in [File, Consul and Slurm Providers](#file-consul-and-slurm-providers) below. Enabling service discovery, with any provider, automatically enables IGW mode. Discovery is not available with `--backend openai`, `anthropic`, or `gemini`. See [Service Discovery](../concepts/architecture/service-discovery.md).

| Option | Default | Description |
|--------|---------|-------------|
| `--service-discovery` | `false` | Enable Kubernetes service discovery. |
| `--selector` | none | Label selector for worker pods, as space-separated `key=value` pairs. Required in regular mode. |
| `--service-discovery-namespace` | all namespaces | Namespace to watch. Unset watches every namespace, which needs cluster-wide permissions. |
| `--service-discovery-port` | `80` | Worker port for discovered pods without a `smg.ai/worker-ports` annotation (pods running several servers list their ports there). |
| `--prefill-selector` | none | Label selector for prefill pods in PD mode. PD mode needs a prefill or a decode selector. |
| `--decode-selector` | none | Label selector for decode pods in PD mode. |
| `--encode-selector` | none | Label selector for encode pods in EPD mode. EPD mode needs all three of the encode, prefill, and decode selectors. |
| `--kv-connector-annotation` | `smg.ai/kv-connector` | Pod annotation that holds the vLLM KV connector name. |
| `--kv-engine-id-annotation` | `smg.ai/kv-engine-id` | Pod annotation that holds per-worker KV engine IDs. |
| `--router-selector` | none | Label selector for peer gateway pods in HA mesh mode (format: `key=value`). Takes effect only together with `--service-discovery` and `--enable-mesh`. Each peer's mesh port comes from its `sglang.ai/mesh-port` annotation, or this gateway's `--mesh-port` when the annotation is missing or invalid (an invalid value, including `0`, logs a warning). |
| `--model-id-from` | unset | Override each discovered worker's model ID from pod metadata: `namespace`, `label:<key>`, or `annotation:<key>`. |
| `--model-alias` | none | Extra client-facing model name, `<alias>=<canonical>`, one per flag. See [Model Aliases](#model-aliases). |

Prefill bootstrap ports come from the fixed `sglang.ai/bootstrap-port` pod annotation.

**Example**:
```bash
--selector app=sglang-worker tier=inference
```

### File, Consul and Slurm Providers

`--discovery-provider kubernetes|file|slurm|consul` names the provider; give it or `--service-discovery` (the Kubernetes shorthand), not both. The selectors, namespace, port and annotation options above belong to the Kubernetes provider; `--model-id-from` and `--model-alias` apply to every provider.

| Option | Default | Description |
|--------|---------|-------------|
| `--discovery-provider` | unset | `kubernetes`, `file`, `slurm` or `consul`. |
| `--discovery-check-interval-secs` | `30` | Seconds between periodic discovery passes. Kubernetes, file and Consul discovery apply a change as it happens and use the pass to retry and to catch what went unreported; Slurm discovery polls at this interval. |

**File.** The workers listed in a JSON manifest on disk, reread in full whenever it changes and on every pass. A read that fails leaves the last good list in place, so a half-written file never removes a worker; writers should write a temporary file in the same directory and rename it over the manifest. The manifest is strict: an unknown field, worker type or version rejects the whole file. Editing a record in place updates its worker in place; a new `id` at a reused address replaces the worker there.

| Option | Default | Description |
|--------|---------|-------------|
| `--discovery-file` | none | Path of the manifest, for `--discovery-provider file`. It may be created after start-up. |

```json
{
  "version": 1,
  "workers": [
    { "id": "decode-a", "url": "10.0.0.23:8000", "worker_type": "decode" },
    { "id": "prefill-a", "url": "10.0.0.22:8000", "worker_type": "prefill",
      "bootstrap_port": 8998, "kv_connector": "MooncakeConnector" }
  ]
}
```

**Consul.** The instances of one Consul service whose health checks all pass, read through a blocking query on Consul's health API so a change applies as Consul reports it; a query that fails is retried with a doubling delay and leaves the last good list in place. A worker listens at its service's address (or its node's) on the service's port; the instance's `Service.Meta` configures it: `smg_worker_type` (`regular`, `encode`, `prefill` or `decode`; required in disaggregated routing), `smg_bootstrap_port`, `smg_model_id`, and `smg_kv_connector`, `smg_kv_role`, `smg_kv_engine_id`. Any other `smg_` key is refused; keys without the prefix are ignored. An instance that cannot be a worker is left out alone.

| Option | Default | Description |
|--------|---------|-------------|
| `--consul-service` | none | Consul service whose passing instances are the workers (required with `--discovery-provider consul`). |
| `--consul-address` | `CONSUL_HTTP_ADDR`, else `127.0.0.1:8500` | Consul's HTTP API address; HTTPS for one with no scheme when `CONSUL_HTTP_SSL` is true. The ACL token and TLS settings come from Consul's own environment variables (`CONSUL_HTTP_TOKEN` or `CONSUL_HTTP_TOKEN_FILE`, `CONSUL_CACERT`, `CONSUL_CLIENT_CERT`, `CONSUL_CLIENT_KEY`). |
| `--consul-datacenter` | the agent's own | Consul datacenter to read. |

**Slurm.** A worker on every node of each running job whose name maps to a worker type, found by polling `squeue` and `scontrol` (Slurm 23.11 or newer) at the check interval, scoped to one user and optionally to an account and a partition. Every node of a selected job runs one server on the configured port, so worker jobs should run `--exclusive` or on nodes of their own. A poll whose commands fail or do not parse is rejected whole and the last good one stays; a job that cannot be placed is left out alone and counted on `smg_discovery_skipped_total`. Discovery stays within the local cluster.

| Option | Default | Description |
|--------|---------|-------------|
| `--slurm-job-name` | none | Job name whose nodes are regular workers, for `--discovery-provider slurm`; repeatable. |
| `--slurm-prefill-job-name`, `--slurm-decode-job-name`, `--slurm-encode-job-name` | none | Job names whose nodes are prefill, decode or encode workers; each repeatable. |
| `--slurm-port` | none | Port of the inference server on every node of a discovered job (required with `--discovery-provider slurm`). |
| `--slurm-bootstrap-port` | none | Bootstrap port of the Slurm-discovered encode and prefill workers. |
| `--slurm-user` | the user the gateway runs as | Discover this user's jobs, by name or uid. |
| `--slurm-account` | none | Discover only jobs charged to this account. |
| `--slurm-partition` | none | Discover only jobs in this partition. |

### Model Aliases

`--model-alias` accepts an extra client-facing model name for a served model (format `<alias>=<canonical>`, repeatable). It applies to every locally registered worker whose model ID equals the canonical side, including workers registered by Kubernetes service discovery. Matching is case-sensitive. Aliased requests are sent to the canonical model; backend responses are passed through unchanged. `/v1/models` lists canonical IDs only. An alias must differ from its canonical ID, and mapping one alias to two different canonical models fails at startup.

Aliases do not participate in Kubernetes Pod selection or model discovery. A discovered worker's canonical model ID continues to come from the backend `served_model_name` (or from `--model-id-from`); aliases only add names that resolve to that canonical ID after registration.

Example:

```bash
smg launch \
  --service-discovery ... \
  --model-alias GLM-5.2-Coding=GLM-5.2 \
  --model-alias glm-5.2=GLM-5.2
```

---

## Logging Configuration

| Option | Default | Description |
|--------|---------|-------------|
| `--log-level` | `info` | `debug`, `info`, `warn`, or `error`. Applies to every SMG crate; external dependencies log at `warn`. |
| `--log-dir` | unset (console only) | Also write logs to daily-rotated files named `smg.YYYY-MM-DD` in this directory, which is created if missing. |
| `--log-json` | `false` | Output logs as JSON (structured) instead of human-readable text, on the console and in log files. |
| `--access-log-fields` | none | Optional fields of the access-log record, comma-separated: `client_addr`, `forwarded_for`, `user_agent`, `tenant`, `key_id`, `request_bytes`, `response_bytes`, `worker`. Without the flag the record keeps the fields it has always had. |

`RUST_LOG`, when set to a valid filter, replaces the filter built from `--log-level`:

```bash
RUST_LOG=smg=debug,hyper=warn smg launch ...
```

See [Configure Logging](../getting-started/logging.md).

---

## Prometheus Metrics Configuration

| Option | Default | Description |
|--------|---------|-------------|
| `--prometheus-port` | `29000` | Port of the metrics server, which serves `/metrics`. Must be at least `1` (`0` is rejected at startup), and each listener (`--port`, `--prometheus-port`, `--health-check-port`, `--admin-port`, `--mesh-port`) needs its own port. |
| `--prometheus-host` | the unspecified address of `--host`'s family: `0.0.0.0`, or `::` for an IPv6 `--host` | IP address the metrics server binds, IPv6 with or without brackets. A value that is not an IP address is a configuration error. |
| `--prometheus-duration-buckets` | see below | Space-separated histogram bucket boundaries, in seconds, for metrics whose names end in `duration_seconds`, `ttft_seconds`, or `tpot_seconds`. |

Default buckets: `0.001 0.005 0.01 0.025 0.05 0.1 0.25 0.5 1 2.5 5 10 15 30 45 60 90 120 180 240 300 480 900 1200 1800 2700 3600 5400 7200`.

**Example**:
```bash
--prometheus-duration-buckets 0.001 0.005 0.01 0.025 0.05 0.1 0.25 0.5 1.0 2.5 5.0 10.0
```

See the [Metrics Reference](metrics.md).

---

## Request Handling Configuration

| Option | Default | Description |
|--------|---------|-------------|
| `--request-timeout-secs` | `1800` (30 min) | Maximum time for request processing. Must be greater than 0. |
| `--sse-keepalive-secs` | `15` | Send a keepalive after this many seconds of silence on an open SSE response. `0` disables it. See [Streaming Keepalives](#streaming-keepalives). |
| `--upstream-pool-idle-timeout-secs` | `3` | Idle timeout for pooled upstream connections. Keep it below the backend server's keep-alive timeout (vLLM and SGLang default to 5 seconds), or reused connections the server already closed fail non-idempotent sends. `0` keeps idle connections forever. |
| `--shutdown-grace-period-secs` | `180` (3 min) | Time to wait for in-flight requests during shutdown. See [Graceful Shutdown](../concepts/reliability/graceful-shutdown.md). |
| `--max-payload-size` | `536870912` (512 MiB) | Maximum request body size in bytes. Must be greater than 0. |
| `--max-buffered-request-bytes` | `1048576` (1 MiB) | Most bytes the gateway holds for a request it buffers only to keep it retryable. The gateway decides per request: a request it must parse (a text-routing policy without a routing hint, a body-mutating worker, WASM request hooks, a missing `Content-Length`, PD or batch backends) always buffers, bounded by `--max-payload-size`. Any other request buffers up to this many bytes when retries are enabled; otherwise, or when larger, it streams to the worker verbatim, forfeiting gateway-level retries and leaving JSON validation to the worker. `0` never buffers for retries. See [Request Streaming](../concepts/performance/request-streaming.md). |
| `--stream-body-stall-timeout-secs` | `300` | Abort a streamed request body with `408` (`request_body_stalled`) once the gateway has waited this long on the client. The clock pauses while the worker applies backpressure, so a slow worker read never trips it. Applies only to streamed request bodies. `0` disables it. |
| `--header-read-timeout-secs` | `30` | Close a connection that has not delivered a complete request head within this many seconds of being accepted, and a keep-alive connection whose next request head stalls for this long after its first byte. A server-wide bound applied per connection, not per request. `0` disables it. |
| `--idle-connection-timeout-secs` | `90` | Close a keep-alive connection that has had no request in flight for this many seconds; an open streaming response counts as in flight. A server-wide bound applied per connection, not per request. Keep it above the idle timeout of any proxy or load balancer in front of the gateway, so that side closes an idle connection first; otherwise a request can be sent on a connection the gateway is closing and fail with a reset. `0` keeps idle connections open until the client closes them. |
| `--request-id-headers` | `x-request-id`, `x-correlation-id`, `x-trace-id`, `request-id` | Space-separated headers checked for an incoming request ID. Passing the flag replaces the defaults. |
| `--storage-context-headers` | none | Space-separated `header=context_key` entries that map request headers into the storage-hook request context. See [Storage Context Headers](#storage-context-headers). |
| `--trust-tenant-header` | `false` | Take the tenant identity from an upstream-set header (see `--tenant-header-name`). The tenant identity keys tenant rate limits and the priority scheduler. An identity from an API key still wins; with neither, the tenant is the client IP. |
| `--tenant-header-name` | `x-smg-tenant-id` | Header read when `--trust-tenant-header` is on. Must be a valid header name. |
| `--max-background-responses` | `1024` | Most Responses API requests with `background: true` in flight at once; one beyond answers `429` (`background_responses_limit_reached`). `0` switches background mode off: such requests then run in the foreground. See [Responses API](api/responses.md). |
| `--cors-allowed-origins` | none (no cross-origin allowance) | Space-separated exact origins a browser may call the gateway from, or `*` alone for any origin, method and header. Without the flag no CORS headers are sent: a browser's preflight gets no `access-control-allow-*` headers and the browser refuses the cross-origin call; other clients are unaffected. Earlier releases allowed any origin when the flag was absent; set `*` to keep that. |

**Examples**:
```bash
--cors-allowed-origins http://localhost:3000 https://example.com
--cors-allowed-origins '*'   # any origin (the earlier default)
--request-id-headers x-request-id x-trace-id x-correlation-id
```

### Streaming Keepalives

SMG keeps an open streaming response active while the engine is silent, such as
during a long prefill. After **15 seconds** without response data, SMG sends an
SSE keepalive. The interval starts when the response headers are sent and
restarts when data flows. Keepalives are inserted only between complete SSE frames.

| Endpoint | Keepalive format |
|----------|------------------|
| `/v1/chat/completions`, `/v1/completions`, `/v1/responses` | SSE comment: `: ping`, followed by a blank line |
| `/v1/messages` | `event: ping` with `data: {"type":"ping"}`, followed by a blank line |

Choose an interval shorter than the idle timeout of the proxy or load balancer
in front of SMG. For example, to send a keepalive after 10 seconds of silence:

```bash
smg launch \
  --worker-urls http://localhost:8000 \
  --sse-keepalive-secs 10
```

Set `--sse-keepalive-secs 0` to disable gateway keepalives. The setting applies to
open `text/event-stream` responses without a fixed content length; JSON responses
and completed, fixed-length responses are unchanged.

### Storage Context Headers

**Example**:

```bash
--storage-context-headers x-tenant-id=tenant_id x-user-id=user_id
```

This lets storage hooks read values such as `tenant_id` and `user_id` from the
request context without hard-coding specific headers in the gateway. Each context
key can be mapped from only one header.

Only map headers that are injected or sanitized by a trusted upstream. Client-supplied
headers can otherwise spoof storage hook request context values.

---

## Rate Limiting Configuration

Gateway-wide admission control for the inference routes. For behavior and sizing guidance, see [Rate Limiting](../concepts/reliability/rate-limiting.md).

| Option | Default | Description |
|--------|---------|-------------|
| `--max-concurrent-requests` | `-1`: 1024 per available core, at least 4096 | Maximum standing concurrent requests. Each admission permit is held for the full response, including streaming bodies. `-1` (the default) derives the cap from the host; `0` disables the limit. |
| `--queue-size` | `100` | Requests that may wait for a permit when the limit is reached. A request that finds the queue full, or no queue (`0`), gets `429` (`admission_queue_full`). |
| `--queue-timeout-secs` | `60` | Maximum time a request waits in the queue before it gets `503` (`admission_queue_timeout`). Must be greater than 0 when `--queue-size` is greater than 0. |
| `--rate-limit-tokens-per-second` | unset | Refill rate of the admission token bucket, whose capacity is `--max-concurrent-requests`. It has no effect while that limit is off. Unset or `0` means no refill: `--max-concurrent-requests` bounds standing concurrency alone. A positive value also refills the bucket over time, on top of the permits that finished requests return, so more than `--max-concurrent-requests` requests can be in flight. With `--priority-scheduler-enabled`, a positive value instead caps admissions at this many requests per second, in bursts of up to `--max-concurrent-requests`; excess requests get `429` (`scheduler_queue_full`). Must be 0 or more. |

---

## Priority Scheduler Configuration

Priority-aware admission, disabled by default. The YAML format, request header, and response codes are in the [Priority Scheduler Reference](priority-scheduler.md).

| Option | Default | Description |
|--------|---------|-------------|
| `--priority-scheduler-enabled` | `false` | Use the priority-aware admission scheduler instead of the concurrency-limit middleware. If the scheduler cannot start, the gateway logs an error and keeps the legacy path. |
| `--priority-scheduler-default-max-class` | `default` | Maximum priority class for tenants not listed in the YAML: `system`, `interactive`, `default`, or `bulk`. Case-insensitive, surrounding spaces ignored. With the scheduler enabled, an unknown value is a configuration error at start-up (the error names the four classes); the flag is not read otherwise. |
| `--priority-scheduler-config` | unset | Path to the optional priority-scheduler YAML (per-class tuning and per-tenant policy). Unset uses the built-in defaults. |
| `--priority-scheduler-tenant-metric-top-n` | `32` | Intended cap on per-tenant scheduler metric label cardinality (top N plus `other`). Stored but not yet applied. |

---

## Tenant Rate Limit Configuration

Per-tenant LLM token and request rate limits, disabled by default. The YAML format is in the [Tenant Rate Limiting Reference](tenant-rate-limiting.md); see also [Tenant Rate Limiting](../concepts/reliability/tenant-rate-limiting.md).

| Option | Default | Description |
|--------|---------|-------------|
| `--tenant-rate-limit-enabled` | `false` | Enable per-tenant rate limiting. When unset, no rate limiter is constructed. |
| `--tenant-rate-limit-config` | unset | Path to the tenant-rate-limit YAML. Required when `--tenant-rate-limit-enabled` is set; startup fails without it. |

---

## Retry Configuration

Gateway-level retries of failed worker requests. See [Retries](../concepts/reliability/retries.md).

| Option | Default | Description |
|--------|---------|-------------|
| `--retry-max-retries` | `5` | Maximum attempts per request, counting the first one; `1` means no retry. Must be at least `1`. |
| `--retry-initial-backoff-ms` | `50` | Delay before the first retry. Must be greater than 0. |
| `--retry-max-backoff-ms` | `30000` | Upper bound for any retry delay. Must be at least `--retry-initial-backoff-ms`. |
| `--retry-backoff-multiplier` | `1.5` | Exponential growth factor per retry. Must be at least `1.0`. |
| `--retry-jitter-factor` | `0.2` | Random jitter (0.0 to 1.0) applied to each delay, in either direction. |
| `--disable-retries` | `false` | Disable retries (sets the attempt limit to 1). |

**Backoff Formula**:
```
delay = min(initial_backoff * multiplier^attempt, max_backoff)
delay = delay * (1 + random(-jitter_factor, +jitter_factor))
```

`attempt` is 0 before the first retry.

---

## Circuit Breaker Configuration

Per-worker circuit breakers. See [Circuit Breakers](../concepts/reliability/circuit-breakers.md).

| Option | Default | Description |
|--------|---------|-------------|
| `--cb-failure-threshold` | `10` | Consecutive failures that open a worker's circuit; a success resets the count. Must be at least `1`. |
| `--cb-success-threshold` | `3` | Consecutive successes in the half-open state that close the circuit. Must be at least `1`. |
| `--cb-timeout-duration-secs` | `60` | Seconds a circuit stays open. The move to half-open happens lazily, on the first check after this time has passed. Must be greater than 0. |
| `--cb-window-duration-secs` | `120` | Currently unused: it is validated (must be greater than 0), but the breaker counts consecutive failures and never reads this window. |
| `--disable-circuit-breaker` | `false` | Disable the circuit breaker by raising the failure threshold to its maximum, so the circuit never opens. Outcomes are still recorded. |

**Circuit Breaker States**:
- **Closed**: Normal operation, counting consecutive failures
- **Open**: All requests to the worker fail fast
- **Half-Open**: Requests pass through unthrottled; `--cb-success-threshold` consecutive successes close the circuit, and any failure reopens it

---

## Health Check Configuration

Active health probes of each worker. See [Health Checks](../concepts/reliability/health-checks.md).

| Option | Default | Description |
|--------|---------|-------------|
| `--health-failure-threshold` | `3` | Consecutive probe failures before a worker is taken out of rotation. |
| `--health-success-threshold` | `2` | Consecutive probe successes before a worker returns to rotation. |
| `--health-check-timeout-secs` | `5` | Timeout for a single probe. |
| `--health-check-interval-secs` | `60` | Seconds between probes of each worker. |
| `--health-check-endpoint` | `/health` | HTTP path probed on each worker. |
| `--disable-health-check` | `false` | Disable all worker health probing. |
| `--remove-unhealthy-workers` | `true` with `--service-discovery`, otherwise `false` | Recover failed workers by removal: a worker that stays unhealthy long enough to reach `Failed` (about 12 minutes at the default thresholds) is removed from the registry, so service discovery re-registers and re-probes it once its engine returns. Without this, a `Failed` worker stays registered, out of rotation, and probed, and rejoins in place when it answers again. Takes an optional boolean: the bare flag means `true`, and `--remove-unhealthy-workers=false` keeps it off under discovery. The default follows `--service-discovery` because a static fleet has nothing to re-add a removed worker. Alias: `--worker-auto-recovery`. |
| `--drain-settle-secs` | `5` | Seconds a Ready worker stays in `Draining` before it is removed from the registry. Applies to every removal (Kubernetes deletion, `--remove-unhealthy-workers`, the manual API). A worker spec can override it with `health.drain_settle_secs`. `0` removes immediately without draining. |

---

## Tokenizer Configuration

| Option | Default | Description |
|--------|---------|-------------|
| `--model-path` | unset | Hugging Face model ID or local path for loading the tokenizer. Hidden alias: `--model`. |
| `--tokenizer-path` | unset | Explicit tokenizer path; overrides the tokenizer from `--model-path`. |
| `--chat-template` | unset | Path to a chat template file. |
| `--disable-tokenizer-autoload` | `false` | Disable automatic tokenizer loading at startup and during worker registration. Useful when tokenizers are loaded on demand through the API. |
| `--tokenizer-cache-enable-l0` | `false` | Enable the L0 (whole-string exact match) tokenizer cache. |
| `--tokenizer-cache-l0-max-entries` | `10000` | Maximum entries in the L0 cache. Must be greater than 0 when L0 is on. |
| `--tokenizer-cache-l0-max-memory` | `268435456` (256 MiB) | Maximum memory for the L0 cache, in bytes (texts, ids and per-entry overhead). |
| `--tokenizer-cache-enable-l1` | `false` | Enable the L1 (prefix matching) tokenizer cache. |
| `--tokenizer-cache-l1-max-memory` | `52428800` (50 MiB) | Maximum memory for the L1 cache, in bytes. Must be greater than 0 when L1 is on. |

Downloads from the Hugging Face Hub use `HF_TOKEN` when it is set. See [Tokenizer Caching](../getting-started/tokenizer-caching.md).

---

## Parser Configuration

Both flags take a registered parser name; an unknown name fails startup. For each request, a parser named in the model's card wins, then these flags, then auto-detection from the model ID. See [Tokenization and Parsing APIs](../getting-started/tokenization-and-parsing.md).

| Option | Default | Values |
|--------|---------|--------|
| `--reasoning-parser` | unset (auto-detect) | `base`, `passthrough`, `deepseek_r1`, `deepseek_v31`, `deepseek_v4`, `deepseek_v41`, `qwen3`, `qwen3_thinking`, `kimi`, `kimi_k25`, `kimi_thinking`, `kimi_k3`, `glm45`, `step3`, `minimax`, `minimax_m3`, `cohere_cmd`, `nano_v3`, `inkling` |
| `--tool-call-parser` | unset (auto-detect) | `passthrough`, `json`, `mistral`, `qwen`, `qwen_xml`, `qwen_coder`, `nemotron`, `pythonic`, `llama`, `deepseek`, `deepseek31`, `deepseek32`, `deepseek_v4`, `deepseek_v41`, `glm45_moe`, `glm47_moe`, `step3`, `sarashina`, `kimik2`, `kimi_k3`, `inkling`, `minimax_m2`, `minimax_m3`, `cohere` |

| `--parser` | `legacy` | `legacy`, `symphony` |

Names use underscores: `--reasoning-parser deepseek-r1` fails; use `deepseek_r1`.

`--parser` chooses which parser reads a chat completion's reasoning and tool calls: the per-model parsers (`legacy`) or Symphony. With `symphony`, a model's worker labels decide, else Symphony's registry for a model it knows, else the two flags above: a Symphony table's name reads with that table, and so does a legacy parser's name whose format a table reads (`qwen` for `qwen3`, `qwen_xml` for `qwen3_tagged`: the table's aliases, so existing flags keep a working parser); any other legacy parser's name keeps the legacy parsers (`passthrough` among them), and no name leaves the legacy parsers to detect. A request's `X-SMG-Parser` header overrides the flag for that request.

---

## MCP Configuration

### MCP Config Path

| Option | `--mcp-config-path` |
|--------|---------------------|
| Environment | - |
| Default | unset |
| Description | Path to the MCP (Model Context Protocol) server configuration file (YAML), loaded at startup. `--help` lists it under Parsers. |

Only the file's `servers` list is applied. A server entry's own `proxy:` block sets that server's outbound proxy; otherwise MCP connections use the standard `HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`, and `NO_PROXY` variables (upper- or lowercase). v1.11.0 ignores the top-level `proxy:` block and never reads `MCP_HTTP_PROXY`, `MCP_HTTPS_PROXY`, or `MCP_NO_PROXY`. See [Model Context Protocol (MCP)](../concepts/extensibility/mcp.md).

---

## Backend Configuration

| Option | Default | Description |
|--------|---------|-------------|
| `--backend` | unset (auto-detected) | `sglang`, `vllm`, `trtllm`, `tokenspeed`, `openai`, `anthropic`, or `gemini`. `openai`, `anthropic`, and `gemini` switch to the external-provider router, where `--worker-urls` are provider endpoints (see [External Providers](../getting-started/external-providers.md)). For `ipc://` (ZMQ) workers, `vllm` and `tokenspeed` declare the engine's wire protocol, which cannot be auto-detected. For HTTP and gRPC workers the runtime is detected automatically, so the engine values change nothing but the startup banner. Hidden alias: `--runtime`. |
| `--history-backend` | `memory` | Storage for conversations and Responses API history: `memory`, `none`, `oracle`, `postgres`, or `redis`. See [Storage Configuration](#storage-configuration) and [Chat History](../concepts/data/chat-history.md). |
| `--enable-wasm` | `false` | Enable WebAssembly support: the WASM request middleware and module management through the `/wasm` admin routes. See [WASM Plugins](../concepts/extensibility/wasm-plugins.md). |
| `--storage-hook-wasm-path` | unset | Path to a WASM component implementing storage hooks. When set, every storage backend is wrapped with hook-based interceptors. Independent of `--enable-wasm`. |
| `--zmq-startup-timeout-secs` | `3600` | Seconds any one stage of a ZMQ engine's start-up handshake (its HELLO, its READY, its input registration) may stay silent before the router gives the handshake up and binds again. The router did not launch the engine and sees nothing from it beside the wire, so this alone must cover the engine's whole init between its HELLO and its READY (weights, KV-cache profiling, graph capture): err on the generous side. `--worker-startup-timeout-secs` bounds registration, not this. |
| `--schema-config` | unset | Path to a YAML schema config file for storage table/column remapping. It also carries the schema `owner`, `version`, and `auto_migrate` settings (see [Schema Migrations](#schema-migrations)). |

---

## Storage Configuration

Settings for the `--history-backend` databases. See [Data Connections](../getting-started/data-connections.md).

### Oracle Database

The connection uses `--oracle-dsn` when it is set; otherwise `--oracle-wallet-path` and `--oracle-tns-alias` are both required.

| Option | Environment | Default | Description |
|--------|-------------|---------|-------------|
| `--oracle-wallet-path` | `ATP_WALLET_PATH` | unset | Path to the Oracle ATP wallet directory. |
| `--oracle-tns-alias` | `ATP_TNS_ALIAS` | unset | Oracle TNS alias from `tnsnames.ora`. |
| `--oracle-dsn` | `ATP_DSN` | unset | Oracle connection descriptor/DSN. Takes precedence over the wallet and alias. |
| `--oracle-user` | `ATP_USER` | unset | Database username. Required unless external auth is on. |
| `--oracle-password` | `ATP_PASSWORD` | unset | Database password. Required unless external auth is on. |
| `--oracle-external-auth` | `ATP_EXTERNAL_AUTH` | `false` | Use Oracle external authentication. Username and password must then be unset. |
| `--oracle-pool-min` | `ATP_POOL_MIN` | `1` | Minimum connection pool size. Must be at least `1`. |
| `--oracle-pool-max` | `ATP_POOL_MAX` | `16` | Maximum connection pool size. Must be at least the minimum. |
| `--oracle-pool-timeout-secs` | `ATP_POOL_TIMEOUT_SECS` | `30` | Pool timeout in seconds. Must be greater than 0. |

### PostgreSQL Database

| Option | Default | Description |
|--------|---------|-------------|
| `--postgres-db-url` | unset | Connection URL (`postgres://` or `postgresql://`, with a host and a database name). Required with `--history-backend postgres`. Env: `SMG_POSTGRES_DB_URL`. |
| `--postgres-pool-max-size` | `16` | Maximum connection pool size. Must be greater than 0. |
| `--postgres-db-url-file` | unset | File holding the connection URL alone, such as a mounted secret; surrounding whitespace is dropped. Read when the history backend is `postgres`. Env: `SMG_POSTGRES_DB_URL_FILE`. |
| `--postgres-op-timeout-secs` | `5` | Bound in seconds on each call to the PostgreSQL store (pool wait, connect and the statement); a call past it is answered `503 storage_unavailable`. |

### Redis Database

| Option | Default | Description |
|--------|---------|-------------|
| `--redis-url` | unset | Connection URL (`redis://`, with a host). Required with `--history-backend redis`. Env: `SMG_REDIS_URL`. A `rediss://` URL passes validation, but the gateway is built without Redis TLS support, so startup fails. |
| `--redis-pool-max-size` | `16` | Maximum connection pool size. Must be greater than 0. |
| `--redis-retention-days` | `30` | Data retention in days. `-1` (any negative value) keeps data persistently. |
| `--redis-url-file` | unset | File holding the connection URL alone, such as a mounted secret; surrounding whitespace is dropped. Read when the history backend is `redis`. Env: `SMG_REDIS_URL_FILE`. |
| `--redis-op-timeout-secs` | `5` | Bound in seconds on each call to the Redis store (pool wait, connect and the command; it replaces the client library's 500 ms and 1 s defaults); a call past it is answered `503 storage_unavailable`. |

When its flag is unset, each URL is read from its environment variable (`SMG_POSTGRES_DB_URL`, `SMG_REDIS_URL`), or from a file named by the `*-url-file` flag or its variable (`SMG_POSTGRES_DB_URL_FILE`, `SMG_REDIS_URL_FILE`); a URL together with a URL file is refused at startup rather than ranked. See [Store URLs from the environment or a file](../getting-started/data-connections.md#store-urls-from-the-environment-or-a-file).

`POSTGRES_DB_URL`, `POSTGRES_POOL_MAX`, `POSTGRES_OP_TIMEOUT_SECS`, `REDIS_URL`, `REDIS_POOL_MAX`, `REDIS_RETENTION_DAYS`, and `REDIS_OP_TIMEOUT_SECS` are read only by the [Python launcher](#python-launcher-differences).

### Memory Backend

The in-memory history backend (the default when no database is configured) keeps its records under three bounds, each checked on every write.

| Option | Default | Description |
|--------|---------|-------------|
| `--history-memory-max-responses` | `10000` | Most stored responses kept; the oldest go first (a stored response takes about 12 KB). |
| `--history-memory-max-items` | `100000` | Most conversation items kept; the oldest go first and leave every conversation that listed them (an item takes about 1.5 KB). |
| `--history-memory-retention-secs` | `86400` | How long a record is kept, in seconds; `0` keeps a record until a count bound drops it. A conversation is kept that long after its last use (created, read or updated); a background response still running is not dropped by a count bound. |

### Schema Migrations

The Oracle and PostgreSQL backends track schema versions and apply pending migrations at startup only when `auto_migrate` is true. Set it in the `--schema-config` YAML; when the YAML leaves it out, or there is no YAML, `DB_AUTO_MIGRATE=true` (or `1`) turns it on. With `auto_migrate` off and migrations pending, startup fails and prints the SQL to apply by hand.

---

## TLS/mTLS Security Configuration

### Server TLS

For HTTPS on the gateway:

| Option | Description |
|--------|-------------|
| `--tls-cert-path` | Path to the server certificate (PEM format). Must be set together with `--tls-key-path`. |
| `--tls-key-path` | Path to the server private key (PEM format). |

### Client mTLS

The Rust CLI has no flags for a client certificate or CA bundle toward workers. The Python launcher provides `--client-cert-path` and `--client-key-path` (set together) and `--ca-cert-paths`; see [Python Launcher Differences](#python-launcher-differences) and [Configure TLS](../getting-started/tls.md). These flags act on the gateway's HTTPS connections (HTTP workers, external providers); the gRPC channel to workers is plaintext and ignores them, and a `grpcs://` worker URL fails to connect. See [Gateway to gRPC Workers](../getting-started/tls.md#gateway-to-grpc-workers).

---

## OpenTelemetry Configuration

| Option | Default | Description |
|--------|---------|-------------|
| `--enable-trace` | `false` | Export OpenTelemetry traces. |
| `--otlp-traces-endpoint` | `localhost:4317` | OTLP collector endpoint as `host:port` (port 1 to 65535). Validated only when tracing is on. |

**Example**:
```bash
smg launch --enable-trace --otlp-traces-endpoint jaeger:4317
```

---

## Control Plane Authentication

Authentication for the admin routes: worker management, cache and tokenizer operations, WASM modules, and `/v1/rl` when enabled. When neither API keys nor JWT are configured here, those routes fall back to the shared `--api-key` (see [Data Plane Authentication](#data-plane-authentication)). See [Authentication](../concepts/security/authentication.md) and [Control Plane Auth](../getting-started/control-plane-auth.md).

| Option | Environment | Default | Description |
|--------|-------------|---------|-------------|
| `--control-plane-api-keys` | `CONTROL_PLANE_API_KEYS` | none | An API key as `id:name:role:key`, where role is `admin` or `user`. One key per flag; repeat the flag for more. A malformed entry is skipped with a warning. |
| `--jwt-issuer` | `JWT_ISSUER` | unset | OIDC issuer URL. JWT auth needs both the issuer and the audience; with only one, it stays off and a warning is printed. |
| `--jwt-audience` | `JWT_AUDIENCE` | unset | Expected audience claim. |
| `--jwt-jwks-uri` | `JWT_JWKS_URI` | discovered from the issuer | Explicit JWKS URI. |
| `--jwt-role-claim` | - | `roles` | JWT claim that carries the role. |
| `--jwt-role-mapping` | - | none | Maps an IDP role to a gateway role, as `idp_role=gateway_role` with gateway role `admin` or `user`. One mapping per flag. |
| `--disable-audit-logging` | - | `false` | Disable audit logging of control plane operations (on by default when control plane auth is configured). |

`--help` also lists `--api-key` under this heading; it is the shared data plane key described in the next section.

**API key example**:
```bash
--control-plane-api-keys 'key1:Admin:admin:secret123' \
--control-plane-api-keys 'key2:ReadOnly:user:secret456'
```

**JWT role mapping example**:
```bash
--jwt-role-mapping 'Gateway.Admin=admin' --jwt-role-mapping 'Gateway.User=user'
```

---

## Data Plane Authentication

| Option | Default | Description |
|--------|---------|-------------|
| `--api-key` | unset | Shared gateway API key. When set, the inference routes require `Authorization: Bearer <key>` (or a `--tenant-api-key` key) and return `401` otherwise; public routes such as `/health` and `/v1/models` stay open. The admin routes accept it when no control plane authentication is configured. It is also attached to every startup and discovered worker as that worker's API key, and the gateway sends it to the worker (as `Authorization: Bearer <key>` for local workers) when the client request carries no `Authorization` header of its own. |
| `--api-key-file` | unset | File holding the shared API key alone, such as a mounted secret; surrounding whitespace is dropped. Env: `SMG_API_KEY_FILE`. |
| `--tenant-api-keys-file` | unset | File holding the per-tenant keys, one `tenant_id:key` per line (blank lines and lines starting with `#` are skipped), such as a mounted secret. Env: `SMG_TENANT_API_KEYS_FILE`. |
| `--tenant-api-key` | none | Per-tenant key as `tenant_id:key`, one per flag. Layers on top of `--api-key`; each key resolves to its own tenant identity (`auth:<tenant_id>`), which tenant rate limits and the priority scheduler use. Tenant keys never unlock the admin routes: with tenant keys but no `--api-key` and no control plane authentication, the admin routes return `401`. An entry without `:` fails startup, as do empty values and a key value used twice (including one equal to `--api-key`). |

**Example**:
```bash
smg launch \
  --worker-urls http://worker:8000 \
  --api-key shared-secret \
  --tenant-api-key team-red:red-secret \
  --tenant-api-key team-blue:blue-secret
```

---

## Mesh Server Configuration

High-availability mesh networking for multi-router coordination. See [High Availability](../concepts/architecture/high-availability.md).

| Option | Default | Description |
|--------|---------|-------------|
| `--enable-mesh` | `false` | Enable the mesh server for HA multi-router coordination. |
| `--mesh-server-name` | `Mesh_` plus 4 random characters | Name of this mesh node. Must be non-empty and must not contain `:`. |
| `--mesh-host` | `0.0.0.0` | IP address the mesh listener binds, IPv6 with or without brackets. |
| `--mesh-advertise-host` | the `--mesh-host` value | Routable address advertised to other mesh peers. Required when `--mesh-host` is an unspecified bind address such as `0.0.0.0`. |
| `--mesh-port` | `39527` | Port for the mesh server. `0` is rejected because peers dial the advertised port. |
| `--mesh-peer-urls` | none | Peer address to join at startup, as `IP:port`. Only the first entry is used as the initial peer, and it must be an IP address and port: a hostname fails startup. Later entries are ignored without being checked. |
| `--mesh-tls-ca-cert` | unset | The CA certificate (PEM) the mesh peers' certificates chain to. With `--mesh-tls-cert` and `--mesh-tls-key` the mesh listener serves TLS and requires peer certificates, and every dial presents this node's. |
| `--mesh-tls-cert` | unset | This mesh node's certificate (PEM), with the node's IP as a SAN. |
| `--mesh-tls-key` | unset | This mesh node's private key (PKCS#8 PEM). |

Router pods can also discover each other through `--router-selector` (see [Service Discovery (Kubernetes)](#service-discovery-kubernetes)).

**Example**:
```bash
smg launch \
  --enable-mesh \
  --mesh-server-name router-1 \
  --mesh-advertise-host 192.168.1.10 \
  --mesh-port 39527 \
  --mesh-peer-urls 192.168.1.11:39527
```

---

## WebRTC Configuration

Used by the WebRTC realtime route (`POST /v1/realtime/calls`).

| Option | Default | Description |
|--------|---------|-------------|
| `--webrtc-bind-addr` | `0.0.0.0` (auto-detect via routing table) | Bind address for WebRTC UDP sockets (the client-facing ICE candidate IP). Set to `127.0.0.1` for local development on the same machine. |
| `--webrtc-stun-server` | `stun.l.google.com:19302` | STUN server (`host:port`) used to gather the upstream socket's server-reflexive candidate. Set to your own STUN server for enterprise deployments that restrict outbound traffic to external STUN servers. `none` disables STUN. |

---

## Runtime Configuration

Controls the tokio async runtime that backs request handling.

By default the runtime is **container-aware**. Tokio sizes its worker pool to
`std::thread::available_parallelism()`, which on Rust 1.95+ already reads the
cgroup CPU quota — so under a Kubernetes `limits.cpu` the worker count matches
the pod's quota, not the host's core count. No extra configuration is needed for
the default to be right under a CPU limit.

Do **not** set an inflated `TOKIO_WORKER_THREADS` (for example a fixed `32`).
That overrides the container-aware default and oversubscribes worker threads
against the cores the scheduler actually grants, causing scheduler thrash,
tail-latency spikes, and `/health` starvation. Leaving it unset is the correct
production configuration.

### Listeners

| Option | Default | Description |
|--------|---------|-------------|
| `--admin-port` | unset (the control plane stays on the data listener) | Port of the admin listener. When set, the control-plane routes (`/flush_cache`, `/start_profile`, `/stop_profile`, `/heap_profile`, `/get_loads`, `/parse/*`, `/wasm`, `/v1/tokenizers`, the worker-registry routes `/workers` and the RL routes `/v1/rl`) are served there, with `/health`, `/liveness` and `/readiness`, and the data listener (`--port`) answers `404` for them, so the control plane can sit on a private address behind its own network policy. Control-plane authentication applies on it as on the data listener. The public `/loads` stays on the data listener. Each listener needs its own port. |
| `--admin-host` | the `--host` value | Bind host of the admin listener (needs `--admin-port`). |
| `--max-connections` | `0` (no bound) | Most client connections the data listener holds open at once, counted from accept to close (idle keep-alive connections, connections inside a TLS handshake and upgraded connections included). A connection beyond the bound is closed at once, before any TLS handshake and without a response; the client should retry with backoff; each is counted in `smg_http_connections_rejected_total`. Readiness is unaffected. Probes on the data port are subject to the bound like any connection: point them at `--health-check-port` or `--admin-port`, which it does not cover. |
| `--jemalloc-prof-dir` | unset (the route answers `404`) | Directory where `POST /heap_profile` (an admin route) writes a jemalloc heap profile of this gateway. Needs a build with the `jemalloc-profiling` feature, started with `_RJEM_MALLOC_CONF=prof:true,prof_active:true`. |

### Worker Threads

Explicit async runtime worker-thread count. Leave unset to use tokio's
container-aware default above; set it only to pin an explicit count (overriding
the cgroup-quota-derived default and any `TOKIO_WORKER_THREADS` value).

| Option | `--runtime-worker-threads` |
|--------|----------------------------|
| Environment | - |
| Default | tokio default (`available_parallelism()`, cgroup-quota-aware) |

### Fewer Worker Threads at Low Per-Instance Load

The default worker count is right for an instance that is kept busy. An
instance that serves a low request rate on its own (a few hundred requests per
second or less, as in a fleet of many small gateway instances behind a load
balancer) spends a visible share of its CPU time on the kernel side of the
runtime rather than on its requests: with more workers than there is work, each
request is handed between threads (the HTTP connection task, the request, the
HTTP/2 client connection to the engine), and every hand-off to a parked worker
is a wake-up and two context switches.

**What the measurement showed.** On a CPU-only bed (one gateway in front of
four mock engines, 64-token prompts, the same offered load on both runs), a
gateway pinned to 2 worker threads instead of 8 handled a chat request with 5.1
context switches instead of 6.9 (-26%), 30-60% fewer kernel instructions (the
request's user-space instructions moved -2%, within the measurement's noise: the
saving is the kernel side's), and 13-21% fewer CPU cycles in total. The kernel
share is also the part of a request's cost that varies most with the host's
load, so the saving is a steadier per-request cost as well as a lower one.

**What it costs.** Fewer workers is less throughput headroom per instance: a
burst above what the pinned workers drain queues behind the connection tasks
instead of spreading over idle cores, so an instance sized this way is scaled
out (more instances) rather than up when its rate grows. The saving is for
ordinary prompts: inputs over 512 bytes are tokenized on the blocking pool, off
the workers (one permit per CPU the process can see, independent of the worker
count), and that hand-off (about 8 system calls and 2 context switches per
request) remains with fewer workers. Keep the default for an instance that must
absorb the whole load on its own, or whose traffic is bursty.

**The setting.** `--runtime-worker-threads <n>` pins the count; left unset (the
default), the runtime sizes itself to `available_parallelism()` as described
above. The validator refuses `0` and counts above `4096`; a count above 4 x the
CPUs available to the process starts with a warning. The measurement used `2`.
After the change, watch `smg_tokio_worker_busy_ratio` and
`smg_tokio_global_queue_depth` (see
[Tokio Runtime Metrics](metrics.md#tokio-runtime-metrics)): busy ratios near
`1.0` across the workers together with a growing global queue depth mean the
instance is saturated and needs the headroom back, or another instance beside
it; a high busy ratio on its own is ordinary full utilization.

**Example** (one instance of a scaled-out fleet at a low per-instance rate):
```bash
smg launch \
  --worker-urls http://engine-1:8000 \
  --runtime-worker-threads 2
```

---

## Python Launcher Differences

`pip install smg` installs a Python `smg` command, not the Rust binary. Its `smg launch`, and `python -m smg.launch_router`, parse flags with `bindings/python/src/smg/router_args.py` and then start the gateway through the Python bindings. The gateway image's entrypoint is `python3 -m smg.launch_router` and the engine images' entrypoint is this Python `smg`, so `docker run` arguments go through this parser too. Most flags match the Rust CLI. The differences:

- **Entry points:** the Python `smg` needs a subcommand (`launch` or `serve`); there is no `start` alias and no subcommand-less form. Under `smg serve`, most router flags take a `--router-` prefix (for example `--router-policy`), and `--router-disable-arg-fallback` stops them from falling back to same-named backend flags.
- **Python-only flags:** `--control-plane-audit-enabled` (audit logging is off unless set, while the Rust CLI enables it and offers `--disable-audit-logging`), and the `--no-remove-unhealthy-workers` / `--no-worker-auto-recovery` forms in place of `=false`. The mTLS flags toward workers (`--client-cert-path`, `--client-key-path`, `--ca-cert-paths`) and `--bucket-adjust-interval-secs` (default `5`) are in both.
- **Flags with a second spelling:** the Python parser accepts the Rust names and, beside them, `--eviction-interval-secs`, `--oracle-connect-descriptor`, `--oracle-username`, `--postgres-pool-max` and `--redis-pool-max`. `--eviction-interval` defaults to `60` in Python and `120` in the Rust CLI. When both a TNS alias and a DSN are given, Python uses the alias, while the Rust CLI uses the DSN.
- **Different values:** `--policy` and `--decode-policy` do not accept `bucket` (`--prefill-policy` does). `--backend` accepts only `sglang`, `vllm`, `tokenspeed`, `openai`, and `anthropic`, and defaults to `sglang`, which behaves like leaving the Rust flag unset. `--reasoning-parser` and `--tool-call-parser` are checked against the registered names at parse time.
- **Environment variables:** both read `SMG_POSTGRES_DB_URL` and `SMG_REDIS_URL` for the store URLs, and the file fallbacks `SMG_POSTGRES_DB_URL_FILE` and `SMG_REDIS_URL_FILE`; Python also accepts the older `POSTGRES_DB_URL` and `REDIS_URL` names when the `SMG_` names are unset. Only Python reads `POSTGRES_POOL_MAX`, `POSTGRES_OP_TIMEOUT_SECS`, `REDIS_POOL_MAX`, `REDIS_RETENTION_DAYS`, and `REDIS_OP_TIMEOUT_SECS` as defaults for its storage flags; the Rust CLI takes those settings from flags alone. Both read the Oracle `ATP_*` variables. `JWT_ISSUER`, `JWT_AUDIENCE`, `JWT_JWKS_URI`, and `CONTROL_PLANE_API_KEYS` are Rust-only.
- **Multiple values:** `--control-plane-api-keys` and `--jwt-role-mapping` accept several values after one flag in Python; the Rust CLI takes one value per flag.
- **Not available in Python:** `--disable-audit-logging`, `--kv-indexer-ttl-secs`, `--kv-indexer-max-entries`, and the hidden `--runtime` alias of `--backend`. The drain, engine-metrics, JWT role claim, PD pairing, priority-scheduler, runtime-thread, tenant-key, tenant-header, tenant-rate-limit and WebRTC flags, once Python gaps, are in both.

---

## Configuration Examples

### Minimal Configuration

```bash
smg launch --worker-urls http://localhost:8000
```

### High-Throughput Configuration

```bash
smg launch \
  --worker-urls http://w1:8000 http://w2:8000 http://w3:8000 http://w4:8000 \
  --policy cache_aware \
  --max-concurrent-requests 200 \
  --queue-size 400 \
  --queue-timeout-secs 60 \
  --retry-max-retries 3
```

### Low-Latency Configuration

```bash
smg launch \
  --worker-urls http://w1:8000 http://w2:8000 \
  --policy power_of_two \
  --max-concurrent-requests 50 \
  --queue-size 25 \
  --queue-timeout-secs 5 \
  --health-check-interval-secs 5 \
  --request-timeout-secs 30
```

### Least-Load Routing with Overload Protection

```bash
smg launch \
  --worker-urls http://w1:8000 http://w2:8000 http://w3:8000 \
  --policy least_load \
  --least-load-max-waiting-requests 32 \
  --worker-overload-waiting-requests 64 \
  --worker-overload-shed \
  --load-monitor-interval 5
```

`least_load` skips a worker once its reported queue, plus requests dispatched since its last load report, reaches 32. Overload protection removes a worker from routing at 80% KV-cache usage (the default) or at 64 queued requests. With `--worker-overload-shed`, when all three workers are over a ceiling, requests get an immediate `503` with `Retry-After: 5`; without it they would go to the least-loaded of the three.

### Sticky Sessions on Cache-Aware Routing

```bash
smg launch \
  --worker-urls http://w1:8000 http://w2:8000 \
  --policy cache_aware \
  --sticky-sessions \
  --sticky-key-idle-secs 3600
```

### PD Disaggregated Mode

```bash
smg launch \
  --pd-disaggregation \
  --prefill http://prefill1:30001 9001 \
  --prefill http://prefill2:30002 9002 \
  --decode http://decode1:30003 \
  --decode http://decode2:30004 \
  --prefill-policy cache_aware \
  --decode-policy round_robin
```

### Kubernetes Service Discovery

```bash
smg launch \
  --service-discovery \
  --selector app=sglang-worker \
  --service-discovery-namespace inference \
  --service-discovery-port 8000 \
  --policy cache_aware
```

### High-Availability Mesh

```bash
# Router 1
smg launch \
  --enable-mesh \
  --mesh-server-name router-1 \
  --mesh-advertise-host 192.168.1.10 \
  --mesh-port 39527 \
  --mesh-peer-urls 192.168.1.11:39527 \
  --worker-urls http://worker1:8000

# Router 2
smg launch \
  --enable-mesh \
  --mesh-server-name router-2 \
  --mesh-advertise-host 192.168.1.11 \
  --mesh-port 39527 \
  --mesh-peer-urls 192.168.1.10:39527 \
  --worker-urls http://worker2:8000
```

### Secure Production Configuration

```bash
smg launch \
  --service-discovery \
  --selector app=sglang-worker \
  --service-discovery-namespace inference \
  --policy cache_aware \
  --max-concurrent-requests 100 \
  --tls-cert-path /etc/certs/server.crt \
  --tls-key-path /etc/certs/server.key \
  --jwt-issuer https://login.microsoftonline.com/tenant/v2.0 \
  --jwt-audience api://smg-gateway \
  --jwt-role-mapping 'Gateway.Admin=admin' \
  --jwt-role-mapping 'Gateway.User=user' \
  --enable-trace \
  --otlp-traces-endpoint jaeger:4317 \
  --health-check-port 8081 \
  --host 0.0.0.0 \
  --port 443
```

### With Tokenizer and Parsers

```bash
smg launch \
  --worker-urls grpc://localhost:50051 \
  --model-path Qwen/Qwen3-8B \
  --tokenizer-cache-enable-l0 \
  --tokenizer-cache-l0-max-entries 50000 \
  --reasoning-parser qwen3 \
  --tool-call-parser qwen
```

### With Database Backend

```bash
# PostgreSQL
smg launch \
  --worker-urls http://localhost:8000 \
  --history-backend postgres \
  --postgres-db-url "postgres://user:pass@localhost:5432/smg" \
  --postgres-pool-max-size 32

# Redis
smg launch \
  --worker-urls http://localhost:8000 \
  --history-backend redis \
  --redis-url "redis://localhost:6379" \
  --redis-pool-max-size 32 \
  --redis-retention-days 7
```

---

## Environment Variable Reference

### Flag Variables

These variables back a flag; the flag wins when both are set. A store URL variable together with a URL file source is refused at startup rather than ranked; see [Storage Configuration](#storage-configuration).

| Environment Variable | CLI Option | Description |
|---------------------|------------|-------------|
| `ATP_WALLET_PATH` | `--oracle-wallet-path` | Oracle wallet path |
| `ATP_TNS_ALIAS` | `--oracle-tns-alias` | Oracle TNS alias |
| `ATP_DSN` | `--oracle-dsn` | Oracle DSN |
| `ATP_USER` | `--oracle-user` | Oracle username |
| `ATP_PASSWORD` | `--oracle-password` | Oracle password |
| `ATP_EXTERNAL_AUTH` | `--oracle-external-auth` | Enable Oracle external authentication |
| `ATP_POOL_MIN` | `--oracle-pool-min` | Oracle min pool size |
| `ATP_POOL_MAX` | `--oracle-pool-max` | Oracle max pool size |
| `ATP_POOL_TIMEOUT_SECS` | `--oracle-pool-timeout-secs` | Oracle pool timeout |
| `SMG_POSTGRES_DB_URL` | `--postgres-db-url` | PostgreSQL connection URL |
| `SMG_POSTGRES_DB_URL_FILE` | `--postgres-db-url-file` | File holding the PostgreSQL connection URL |
| `SMG_REDIS_URL` | `--redis-url` | Redis connection URL |
| `SMG_REDIS_URL_FILE` | `--redis-url-file` | File holding the Redis connection URL |
| `JWT_ISSUER` | `--jwt-issuer` | JWT issuer URL |
| `JWT_AUDIENCE` | `--jwt-audience` | JWT audience |
| `JWT_JWKS_URI` | `--jwt-jwks-uri` | JWKS URI |
| `CONTROL_PLANE_API_KEYS` | `--control-plane-api-keys` | Control plane API key (`id:name:role:key`) |

### Gateway Settings

| Environment Variable | Description |
|---------------------|-------------|
| `RUST_LOG` | Log filter in `tracing` `EnvFilter` syntax. When set to a valid filter, it replaces the filter built from `--log-level`. |
| `TOKIO_WORKER_THREADS` | Read by tokio when `--runtime-worker-threads` is unset. Leave it unset; see [Runtime Configuration](#runtime-configuration). |
| `HF_TOKEN` | Hugging Face token for tokenizer and model config downloads. |
| `HF_HOME`, `HF_ENDPOINT` | Hugging Face cache directory and Hub endpoint, read by the `hf-hub` client that downloads tokenizers and model configs. |
| `DB_AUTO_MIGRATE` | `true` or `1` turns on automatic schema migrations for the Oracle and PostgreSQL backends when the schema config does not set `auto_migrate`. See [Schema Migrations](#schema-migrations). |
| `HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`, `NO_PROXY` | Standard proxy variables (upper- or lowercase), used by outbound MCP connections whose server entry sets no `proxy:`. `MCP_HTTP_PROXY`, `MCP_HTTPS_PROXY`, and `MCP_NO_PROXY` are not read in v1.11.0. See [MCP Configuration](#mcp-configuration). |
| `OPENAI_ADMIN_KEY`, `XAI_ADMIN_KEY`, `ANTHROPIC_ADMIN_KEY`, `GEMINI_ADMIN_KEY` | Key used to list an external provider's models when a provider worker is registered. Takes precedence over `--api-key`. |

### Routing Trace Variables

Opt-in evidence of the gateway's routing decisions for requests served through the gRPC pipeline (gRPC and ZMQ workers). Each variable is read once, at the first request; no flag sets them. With both switches off, one request can still ask for its own record by sending the request header `x-smg-cache-trace: 1`.

| Environment Variable | Default | Description |
|---------------------|---------|-------------|
| `SMG_CACHE_TRACE` | unset (off) | `1` logs every dispatch's routing evidence as an `INFO` line under the target `smg::cache_trace` with the message `Cache routing dispatch` and the record as JSON in its `evidence` field: the candidates with their load and health, the policy, the chosen worker, the engine request ids and, under cache-aware routing, the gates, scores and overlap prediction. A request that fails logs a `Cache routing failure` line with the selections captured so far and the status. Any other value leaves the log line off. |
| `SMG_CACHE_TRACE_HEADER` | unset (off) | `1` returns each dispatch's decision record in the `x-smg-cache-trace` response header: the request and dispatch ids, the attempt, the engine ids and, per selection, the policy, origin, worker and prediction; with or without the log line. A record longer than 2048 bytes or not plain ASCII is omitted and an `INFO` line says so. Any other value leaves the header off. |
| `SMG_CACHE_TRACE_SAMPLE` | `1` (every dispatch) | With `SMG_CACHE_TRACE=1`, log one dispatch in every N, counted per process. The response header and the failure line are not sampled. `0` or a non-number reads as `1`. |
| `SMG_CACHE_TRACE_MAX_BYTES` | unset (no cap) | A log line longer than this many bytes drops its candidate, score and gate lists (each selection keeps the count of what it dropped) and is marked `capped`; the decision record stays. The header is not capped by it. A non-number reads as no cap. |

### Multimodal Variables

Described in [Multimodal Configuration](#multimodal-configuration).

| Environment Variable | Purpose |
|---------------------|---------|
| `SMG_MM_TENSOR_TRANSPORT`, `SMG_MM_SHM_MIN_BYTES` | Fallbacks for `--multimodal-tensor-transport` and `--multimodal-shm-min-bytes` (legacy names `SMG_TOKENSPEED_MM_TENSOR_TRANSPORT`, `SMG_TOKENSPEED_MM_SHM_MIN_BYTES`) |
| `SMG_MM_PROCESSING`, `SMG_MM_PIXEL_CACHE_MB`, `SMG_MM_PIXEL_RDMA`, `SMG_RDMA_LISTEN_IP`, `SMG_RDMA_SLOT_TTL_S`, `SMG_LOG_MM_TIMING` | Deprecated fallbacks for `--mm-processing`, `--mm-pixel-cache-mb`, `--mm-pixel-rdma`, `--rdma-listen-ip`, `--rdma-slot-ttl-s`, and `--log-mm-timing` |
| `SMG_RDMA_LISTEN_PORT`, `SMG_RDMA_POOL_SLOTS`, `SMG_RDMA_SLOT_BYTES`, `SMG_RDMA_LANDING_WAIT_S`, `SMG_RDMA_READ_TIMEOUT_S` | RDMA pixel lane port, arena size, and timeouts |
| `SMG_IMAGE_MAX_INPUT_BYTES`, `SMG_VIDEO_MAX_INPUT_BYTES`, `SMG_AUDIO_MAX_INPUT_BYTES`, `SMG_VIDEO_MAX_DECODED_BYTES`, `SMG_AUDIO_MAX_DECODED_BYTES` | Media size caps |
| `SMG_VIDEO_PROCESS_TIMEOUT_SECS`, `SMG_AUDIO_PROCESS_TIMEOUT_SECS`, `SMG_VIDEO_DECODE_BACKEND`, `SMG_AUDIO_DECODE_BACKEND` | Media decoding |
| `SMG_IMAGE_MAX_COUNT`, `SMG_VIDEO_MAX_COUNT`, `SMG_AUDIO_MAX_COUNT` | Per-request media counts |
| `SMG_MM_ALLOW_PRIVATE_MEDIA_HOSTS`, `SMG_MM_FETCH_TIMEOUT_MS`, `SMG_MM_VIDEO_FETCH_TIMEOUT_MS`, `SMG_MM_TURBOJPEG` | Fallbacks for `--mm-allow-private-media-hosts`, `--mm-fetch-timeout-ms`, `--mm-video-fetch-timeout-ms`, `--mm-turbojpeg` |
| `SMG_VLLM_ENCODER_INPUT_DTYPE`, `SMG_TOKENSPEED_ENCODER_INPUT_DTYPE`, `SMG_TOKENSPEED_IMAGE_ENCODER_INPUT_DTYPE`, `SMG_TOKENSPEED_VIDEO_ENCODER_INPUT_DTYPE`, `SMG_TOKENSPEED_AUDIO_ENCODER_INPUT_DTYPE` | Encoder input wire dtype |
| `SMG_MM_THREADS`, `SMG_MM_ONE_PASS_WIRE`, `SMG_VIDEO_MAX_DECODE_PIXELS` | Preprocessing pool size, the one-pass wire write switch, and the cap on one video decode's work |

### Engine Servicer Variables

Read from the worker's environment by the Rust engine servicers (see [gRPC Workers](../getting-started/grpc-workers.md)), not by the gateway; none has a flag. Of these, only `SMG_KV_EVENT_HASH_CHECK` is read by the Python servicer's relay as well, under the same spellings.

| Environment Variable | Default | Description |
|---------------------|---------|-------------|
| `SMG_SERVICER_WORKER_THREADS` | `1` | Worker threads of the servicer's async runtime, read when the servicer starts. One thread carries tens of thousands of tokens per second at the same inter-token latency as several, in less CPU per token: with more threads every per-token send contends for the lock of the one HTTP/2 connection the gateway holds. A positive integer; anything else reads as the default. |
| `SMG_KV_EVENT_RELAY_START` | subscribe at start | When the servicer's KV-event relay subscribes to the engine's event publishers. By default it subscribes as soon as the servicer serves, probing the publisher's port every second until the engine binds it, so the relay's history covers the engine from its first batch and a reconnecting gateway is served from it. `lazy` defers the subscription to the first `SubscribeKvEvents` call, for a memory-constrained host; nothing is recorded before then. See [KV Events Cache-Aware Routing](../getting-started/kv-events-cache-aware.md#recovery-after-gaps). |
| `SMG_KV_EVENT_HISTORY_BATCHES` | `10000` | Relayed batches the relay keeps per publisher rank: the window a reconnecting gateway resumes from. A resume cursor older than the window is refused, and the gateway then resubscribes from zero and receives a snapshot of the engine's live blocks instead. A non-number logs a warning and keeps the default. |
| `SMG_KV_EVENT_HISTORY_BYTES` | `268435456` (256 MiB) | Byte budget of that history over the encoded batches, per publisher rank; the oldest batches leave when either cap is exceeded. A non-number logs a warning and keeps the default. |
| `SMG_KV_EVENT_HASH_CHECK` | unset (off) | `sglang` or `vllm-sha256-cbor` (any case) makes the relay recompute the engine's own block hash for every stored block it can verify and count the mismatches in its counters, which it logs with its summary every 500 relayed batches and when it closes. Nothing is dropped for it. An unknown value logs a warning and leaves the check off. |

### Python Launcher Variables

Read only by the Python launcher (see [Python Launcher Differences](#python-launcher-differences)); the Rust `smg` binary ignores them.

| Environment Variable | Python Option |
|---------------------|---------------|
| `POSTGRES_DB_URL` | `--postgres-db-url` |
| `POSTGRES_POOL_MAX` | `--postgres-pool-max` |
| `POSTGRES_OP_TIMEOUT_SECS` | `--postgres-op-timeout-secs` |
| `REDIS_URL` | `--redis-url` |
| `REDIS_POOL_MAX` | `--redis-pool-max` |
| `REDIS_RETENTION_DAYS` | `--redis-retention-days` |
| `REDIS_OP_TIMEOUT_SECS` | `--redis-op-timeout-secs` |
