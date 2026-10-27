---
title: KV Events Cache-Aware Routing
---

# KV Events Cache-Aware Routing

This guide wires **gRPC workers that publish KV cache events** to **SMG's `cache_aware` policy**, so the gateway routes each request to the worker whose KV cache actually holds the longest prefix of it. SGLang is the main example; vLLM and TokenSpeed workers are set up the same way.

<div class="prerequisites" markdown>

#### Before you begin

- Completed the [Getting Started](index.md) guide
- Read [Cache-Aware Routing](../concepts/routing/cache-aware.md) for the routing concepts
- A machine that can run an SGLang worker (GPU + CUDA-capable Python environment)
- `smg-grpc-servicer[sglang]` installed alongside SGLang (the extra requires SGLang 0.5.21 or newer)

</div>

---

## Why event-driven?

Without events, `cache_aware` predicts cache contents from its own routing history. With events, it routes against the blocks each engine reports it has stored and evicted.

| Mode | Index | Input | Used when |
|---|---|---|---|
| **Event-driven** | Chain index, built from engine events | Token IDs | The model's event index has data |
| Approximate token tree | `TokenTree`, built from routing decisions | Token IDs | The request has token IDs and the model has no event data |
| Approximate string tree | `Tree`, built from routing decisions | Raw text | HTTP requests without token IDs |

The choice is made **per model**, not per worker. Once any worker of a model has streamed events into the index, every token-bearing request for that model is scored against the event index, and workers that do not stream events can then receive that model's requests only through the load-based fallback. Keep events on for every worker of a model.

Event-driven routing also needs the default `--cache-index tree`: with `--cache-index hash`, the gateway never consults the event index.

---

## How the pieces fit together

```text
client
  │ OpenAI-compatible HTTP
  ▼
SMG gateway: cache_aware policy ◀── chain index (one per model)
  │                                        ▲
  │ gRPC requests                          │ KvEventMonitor
  ▼                                        │ (SubscribeKvEvents stream)
worker: smg-grpc-servicer ─────────────────┘
  ▲
  │ ZMQ PUB → SUB, on the worker host
engine scheduler (SGLang, vLLM or TokenSpeed)
```

1. The engine's scheduler publishes block-stored, block-removed and all-blocks-cleared events on a ZMQ `PUB` socket. The servicer configures the publisher itself when the worker is launched without `--kv-events-config`; an explicit config replaces that default.
2. `smg-grpc-servicer`, which serves the worker's gRPC API, subscribes to the engine's publishers locally and re-publishes the events as the server-streaming RPC `SubscribeKvEvents`.
3. SMG's `KvEventMonitor` opens one subscription per gRPC worker and feeds the events into a per-model chain index; `cache_aware` scores token-bearing requests against that index. If a stream breaks, the monitor reconnects and either resumes or rebuilds the worker's entries (see [Recovery after gaps](#recovery-after-gaps)).

---

## Step 1 — Launch the SGLang worker

A worker launched with `--grpc-mode` and no `--kv-events-config` already publishes: the servicer turns SGLang's ZMQ KV-event publisher on by itself, at a port found free on the host rather than SGLang's fixed default (with data parallelism, a block of free ports, one per rank), so workers that share a host do not collide. The start log says so: `KV event publishing enabled: no --kv-events-config was given, ...`. Setting `SMG_SGLANG_SERVICER_KV_EVENTS=0` in the worker's environment leaves the publisher off.

That default supplies no `replay_endpoint`, so a missed batch costs the worker's index entries (see [Recovery after gaps](#recovery-after-gaps)). Pass `--kv-events-config` to pin the endpoint and enable gap replay. An explicit config is kept exactly as given — include `"publisher":"zmq"`, since one that leaves it out publishes nothing.

Install the SGLang extra of the servicer, then launch the SGLang server:

```bash
pip install "smg-grpc-servicer[sglang]"

python -m sglang.launch_server \
  --model-path meta-llama/Llama-3.1-8B-Instruct \
  --host 0.0.0.0 \
  --port 50051 \
  --grpc-mode \
  --page-size 16 \
  --kv-events-config '{"publisher":"zmq","endpoint":"tcp://*:5557","replay_endpoint":"tcp://*:5558","topic":"kv-events"}'
```

What each flag does:

| Flag | Why |
|---|---|
| `--grpc-mode` | Serves SMG's gRPC `SglangScheduler` service from `smg-grpc-servicer` (needs SGLang 0.5.21 or later) instead of SGLang's HTTP server. Required for SMG to talk to this worker over gRPC. |
| `--page-size 16` | The KV cache page size, in tokens. Use the same value for SMG's `--block-size` (see [Block size alignment](#block-size-alignment)). |
| `--kv-events-config` | Optional: without it the servicer publishes at a free port, with no gap replay. An explicit JSON object, parsed by SGLang's `KVEventsConfig.from_cli`, replaces that default exactly as given: `"publisher":"zmq"` keeps publishing on (leaving it out means the default `"null"`, which publishes nothing), and `replay_endpoint` lets the servicer fill gaps in the stream from SGLang's replay buffer. |

### `--kv-events-config` field reference

All fields and defaults match SGLang's `KVEventsConfig` (see `python/sglang/srt/disaggregation/kv_events.py` upstream):

| Field | Default | Notes |
|---|---|---|
| `publisher` | `"null"` | Set to `"zmq"` to publish. An explicit config is kept as given even when it turns publishing off: with any other value the servicer answers `SubscribeKvEvents` with `UNIMPLEMENTED`, and SMG stops subscribing to that worker. |
| `endpoint` | `"tcp://*:5557"` | ZMQ `PUB` address. The publisher binds wildcard endpoints such as `tcp://*:PORT` (and `ipc://` or `inproc://` ones) and connects to any other address, which leaves nothing listening, so keep the wildcard form. The servicer connects to it locally, rewriting `*` and `0.0.0.0` to `127.0.0.1`. |
| `topic` | `""` | ZMQ topic. The servicer reads the same config and subscribes to this topic, so any value works. |
| `replay_endpoint` | `null` | ZMQ `ROUTER` address that serves recent batches for replay. When set, the servicer fills gaps in each rank's events from it before forwarding later batches; without it, a missed batch costs the worker's index entries (see [Recovery after gaps](#recovery-after-gaps)). |
| `buffer_steps` | `10000` | Number of recent batches kept for replay. |
| `hwm` | `100000` | ZMQ high-water mark: once this many messages are queued for a subscriber that is not keeping up, new events are dropped. The servicer uses the same value for its receive queue. |
| `max_queue_size` | `100000` | Queue between the scheduler and SGLang's publisher thread. |

With data parallelism, each rank publishes on `endpoint` port + `dp_rank` and serves replay on `replay_endpoint` port + `dp_rank`; rank 0 keeps the configured ports. Pick port ranges that do not overlap (`5557` and `5558` collide as soon as there are two ranks). The servicer subscribes to every rank's publisher and relays them as one stream, each batch tagged with its rank, so the index sees every rank's cache.

### Alternative: launch a vLLM worker

vLLM works the same way: in SMG gRPC mode, a worker launched without `--kv-events-config` already publishes — the servicer turns vLLM's ZMQ publisher on at a port found free on the host, and `SMG_VLLM_SERVICER_KV_EVENTS=0` leaves it off. Pass `--kv-events-config` to pin the endpoint and enable gap replay:

```bash
pip install "smg-grpc-servicer[vllm]"

# --grpc serves vLLM through smg-grpc-servicer instead of the OpenAI HTTP server;
# --kv-events-config pins the event endpoint and adds gap replay:
vllm serve meta-llama/Llama-3.1-8B-Instruct \
  --grpc \
  --host 0.0.0.0 \
  --port 50051 \
  --kv-events-config '{"enable_kv_cache_events": true, "publisher": "zmq", "endpoint": "tcp://*:5557", "replay_endpoint": "tcp://*:5558", "topic": "kv-events"}'
```

Event-driven routing needs the worker in SMG gRPC mode (`--grpc`; `pip install "vllm[grpc]"` pulls in the same servicer package), because KV events stream over the `SubscribeKvEvents` RPC. See [gRPC Workers](grpc-workers.md) for other launch flags.

| Field | Why |
|---|---|
| `enable_kv_cache_events: true` | vLLM's master switch. An explicit config is kept as given, so one without it publishes nothing, and SMG stops subscribing to the worker. |
| `publisher: "zmq"` | Selects the ZMQ publisher the servicer reads. Recent vLLM versions default to it once events are enabled. |
| `endpoint` / `topic` | As for SGLang; keep the wildcard `tcp://*:PORT` form. With data parallelism the port is `endpoint` port + `dp_rank`, and the servicer subscribes to every rank. |
| `replay_endpoint` | As for SGLang: the servicer fills gaps in each rank's events from it before forwarding later batches; without it, a missed batch costs the worker's index entries (see [Recovery after gaps](#recovery-after-gaps)). Each rank serves replay on `replay_endpoint` port + `dp_rank`. |

SMG learns the block size from the `BlockStored` events themselves; set SMG's `--block-size` to vLLM's `--block-size` so the approximate token tree uses the same pages.

### Alternative: launch a TokenSpeed worker

TokenSpeed works the same way, and the TokenSpeed gRPC server *is* the SMG gRPC entrypoint, so there is no separate `--grpc` flag. A worker launched without `--kv-events-config` already publishes — the servicer turns the scheduler's ZMQ publisher on at a port found free on the host, and `SMG_TOKENSPEED_SERVICER_KV_EVENTS=0` leaves it off.

Two launches get no default. An encode worker (`--disaggregation-mode encode`) has no KV cache and is left as given. On a hybrid model (Gated DeltaNet, Mamba or KDA layers) the servicer leaves the publisher off and logs why once (`KV event publishing left off: ...`): such a model keeps its recurrent state in State groups of TokenSpeed's cache, and TokenSpeed's KV-event drain aborts the engine on such a group. An explicit `--kv-events-config` turns the publisher on anyway.

Pass `--kv-events-config` to pin the endpoint and enable gap replay:

```bash
# TokenSpeed is installed from source (engine + kernel + scheduler); see
# scripts/ci_install_tokenspeed.sh in the smg repository. Install the servicer's extra deps:
pip install "smg-grpc-servicer[tokenspeed]"

# --kv-events-config pins the event endpoint and adds gap replay:
python -m smg_grpc_servicer.tokenspeed \
  --model meta-llama/Llama-3.1-8B-Instruct \
  --host 0.0.0.0 \
  --port 50051 \
  --kv-events-config '{"enable_kv_cache_events": true, "publisher": "zmq", "endpoint": "tcp://*:5557", "replay_endpoint": "tcp://*:5558", "topic": "kv-events"}'
```

| Field | Why |
|---|---|
| `enable_kv_cache_events: true` | TokenSpeed master switch. Without it the scheduler records no events even if a publisher is set. |
| `publisher: "zmq"` | Selects the ZMQ publisher the servicer reads. Unset defaults to `"zmq"` when events are enabled; `"null"` (or any other value) disables event streaming. |
| `endpoint` / `topic` | ZMQ `PUB` address and topic. Use a **bind-style** endpoint (`tcp://*:PORT`): TokenSpeed binds only when the endpoint contains `*` or `::` or starts with `ipc://` or `inproc://`, so a concrete address like `tcp://127.0.0.1:PORT` makes it *connect* instead, leaving nothing bound and the stream idle. With attention data parallelism each rank publishes on `endpoint` port + rank, and the servicer subscribes to every rank, so the index sees every rank's cache. |
| `replay_endpoint` | As for SGLang and vLLM: the servicer fills gaps in each rank's events from it before forwarding later batches; without it, a missed batch costs the worker's index entries (see [Recovery after gaps](#recovery-after-gaps)). Each rank serves replay on `replay_endpoint` port + rank. |

`--kv-events-config` is parsed by TokenSpeed's `KVEventsConfig.from_cli`. Gap replay needs an explicit `replay_endpoint` in the config — no default supplies one.

SMG learns the block size from the `BlockStored` events; TokenSpeed's cache reuse granularity is `--prefix-granularity` (default 64 tokens; older builds call it `--block-size`, which remains an alias), so set SMG's `--block-size` to the same value.

---

## Step 2 — Launch SMG

Point SMG at the gRPC workers and select `cache_aware`:

```bash
smg launch \
  --worker-urls grpc://worker-1:50051 grpc://worker-2:50051 \
  --model-path meta-llama/Llama-3.1-8B-Instruct \
  --policy cache_aware \
  --block-size 16 \
  --host 0.0.0.0 \
  --port 30000
```

`worker-2` is a second worker launched the same way, on another host. On a single host, give each worker its own `--port` and its own event ports.

The flags that matter for event-driven routing:

| Flag | Why |
|---|---|
| `grpc://...` worker URLs | SMG subscribes to KV events only on gRPC workers. HTTP workers are skipped, and ZMQ (`ipc://`) workers log a warning and use the approximate token tree. |
| `--policy cache_aware` | The only policy that reads the event index. It is also the default policy. |
| `--block-size 16` | Page size of the approximate token tree, which routes until the event index has data, and the block size the event index assumes until it learns the engine's. Match the engine's page size. |

`--model-path` tells the gateway which tokenizer to use, as in any gRPC-worker deployment ([gRPC Workers](grpc-workers.md)). For the other routing knobs, see [Tuning](#tuning).

### Block size alignment

The event index cuts each request's token IDs into blocks and looks the blocks up by content hash. If the block size differs from the one the engine used, **no block matches**, and the model's token requests are routed by load alone.

SMG picks the block size per model, in this order:

1. **Learned from events** (highest priority): the first stored block a worker's subscription receives replaces the provisional size, and a larger block raises it; a smaller block never lowers it (hybrid engines publish, beside their full blocks, partial entries at their hash granularity, and a request chunked at that size would never match a full block).
2. **`--block-size`** (router-wide default).

The `kv_block_size` field of a worker spec registered through the [admin API](../reference/api/admin.md) (`POST /workers`) seeds the model's block size until the events name it (since v1.12.0; v1.11.0 accepted it without applying it). In practice, keep the engine's page size (SGLang `--page-size`, vLLM `--block-size`, TokenSpeed `--prefix-granularity`) and SMG's `--block-size` equal, and let SMG correct itself once events arrive. The approximate token tree always uses `--block-size`. Serve each model with one page size: with mixed sizes, the model's block size is whichever a worker reported last.

---

## Step 3 — Send a request

The API surface is unchanged:

```bash
curl http://localhost:30000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "meta-llama/Llama-3.1-8B-Instruct",
    "messages": [
      {"role": "user", "content": "Hello, who are you?"}
    ]
  }'
```

Send the same prompt twice. The first request has no cached prefix and goes to the lowest-expected-wait worker. Once that worker reports the blocks it stored, the second request is scored against them and lands on the same worker, as long as that worker is not far busier than the rest of the fleet.

---

## Verifying event delivery

The gateway logs these lines when the path is live (timestamps, targets and source locations omitted).

**1. Subscription started.** When SMG registers a gRPC worker under `cache_aware`, `KvEventMonitor` logs:

```text
INFO Starting KV event subscription worker_url=grpc://worker-1:50051 model_id=meta-llama/Llama-3.1-8B-Instruct
INFO KV event stream connected worker_url=grpc://worker-1:50051 start_seq=0
```

If you do not see the first line for a worker, the worker is not a gRPC worker, the gateway's `--policy` is not `cache_aware` (a `policy` worker label alone does not start subscriptions), or the worker's model is routed by another policy. If events are disabled on the worker — an explicit `--kv-events-config` without a ZMQ publisher, the worker's `SMG_<ENGINE>_SERVICER_KV_EVENTS=0`, or a TokenSpeed hybrid model — the servicer rejects the stream and SMG stops trying:

```text
WARN Backend does not implement SubscribeKvEvents, disabling KV event subscription for this worker worker_url=grpc://worker-1:50051
```

**2. Backend block size learned.** Once the first stored block arrives, SMG records the backend's block size (a larger block arriving later raises it; a smaller one does not lower it):

```text
INFO Learned block_size from KV event model_id=meta-llama/Llama-3.1-8B-Instruct block_size=16
```

**3. Routing decision uses the index.** With `RUST_LOG=info,smg::policies::cache_aware=debug`, each routed request logs `Event-driven routing: overlap match` with the chosen `worker` and a `branch` of `event_hit` (affinity held) or `event_spill` (the spill gate moved the request), or `Event-driven routing: no overlap, expected-wait fallback`.

While the model's event index is empty, token requests route on the approximate token tree, so cache affinity keeps working, only less accurately.

---

## Recovery after gaps

Every batch on the stream carries a sequence number. `KvEventMonitor` applies batches in order and skips duplicates. When it sees a gap in the sequence, it reconnects immediately and asks the servicer to resume after the last batch it applied; after a stream error or a clean close, it reconnects with exponential backoff from 100 ms up to 5 s. A subscription call that is not answered within 2 s — a port that accepts but does not serve yet, such as an engine still starting — is abandoned and retried.

With the default Python servicer — SGLang, vLLM and TokenSpeed alike — a gap in a rank's events is filled from that rank's `replay_endpoint` before later batches are forwarded, so SMG receives an unbroken stream and the index stays intact. The servicer waits up to five seconds for each replay message before declaring a gap unfillable. A gap it cannot fill — no `replay_endpoint`, the missed batches already left the replay buffer, the replay failed, or the engine's publisher restarted — ends the stream with `DATA_LOSS`: SMG drops that worker's index entries and starts a new subscription.

The default Python servicer keeps nothing between subscriptions: a resume cursor is answered with `OUT_OF_RANGE`, and a new subscription carries live events only. After any stream break between SMG and the servicer, SMG therefore drops the worker's index entries and rebuilds them from live events. The rebuilt index is not a snapshot of the engine's cache: affinity to that worker returns as it caches new prefixes.

Workers running a [Rust servicer](grpc-workers.md) keep more. Its relay follows the engine's publishers for the servicer's whole lifetime: a reconnecting gateway is served the batches after its cursor from history, and a new subscription is answered with either the engine's complete event history or an in-band snapshot of the engine's live blocks, which SMG applies in place of whatever it held for that worker. With several data-parallel ranks the relay refuses a resume cursor (the request carries a single cursor, and each rank keeps its own sequence numbers), so the gateway clears and resubscribes from zero and is answered with the history or snapshot instead, losing nothing. Cache affinity then survives gateway restarts and long disconnects without waiting for new stores.

SMG logs `Sequence gap detected, reconnecting for replay from seq N` on a gap, and `KV event replay cursor expired; clearing worker state and requesting a current snapshot` or `KV event subscriber fell behind; clearing worker state and requesting a current snapshot` when it drops a worker's entries. A restart of the engine's own publisher (its sequence numbers start over) also clears the worker's entries — the engine's cache is empty again — and the stream continues from the new sequence.

---

## Index size

The event index holds what the engines report: block-removed and all-blocks-cleared events shrink it as each engine evicts, and when a worker is removed, its entries go with it. There is no prune and no size flag to set.

`--kv-indexer-ttl-secs` and `--kv-indexer-max-entries` are deprecated and ignored: they bounded the positional indexer, which the chain index replaced. The Rust `smg` binary still accepts them so existing launch lines keep working, and logs a warning at start-up when either is set. Both flags will be removed in a later release; drop them from your launch lines.

---

## Tuning

| Knob | Where | Effect |
|---|---|---|
| `--block-size` | SMG | Keep equal to the engine's page size. |
| `--balance-abs-threshold` / `--balance-rel-threshold` | SMG | Spill gate: a holder whose in-flight request count exceeds both margins over the fleet mean sends the request to another worker. |
| `--overlap-decay` | SMG | De-ranks candidates with a waiting-prefill backlog. Needs workers that report waiting uncached tokens (SGLang does; vLLM and TokenSpeed do not). |
| `--selection-temperature` | SMG | Spreads picks across candidates with near-equal overlap instead of always taking the best. |
| `--balance-token-usage-threshold` / `--overload-token-usage-threshold` | SMG | Suspend cache affinity under KV pressure (off by default). |
| `replay_endpoint` / `buffer_steps` | `--kv-events-config` | Enable gap replay; raise `buffer_steps` (SGLang and vLLM only) if gaps go unfilled because the missed batches already left the buffer. |
| `hwm` | `--kv-events-config` | Raise if events are dropped under bursty load. |
| `SMG_KV_EVENT_HISTORY_BATCHES` / `SMG_KV_EVENT_HISTORY_BYTES` | Rust servicer environment | How much relayed history a Rust servicer keeps per rank for a reconnecting gateway to resume from; see [Engine Servicer Variables](../reference/configuration.md#engine-servicer-variables). |

`--cache-threshold` does not apply to event-driven routing: any worker holding at least the request's first full block is a candidate. See [Cache-Aware Routing](../concepts/routing/cache-aware.md#tuning-knobs) for the full list of knobs.

---

## Monitoring

| Metric | Type | Labels | Description |
|---|---|---|---|
| `smg_kv_event_subscription_failures_total` | counter | `worker`, `reason` | A subscription task failed: `panic`, `join_error` or `intern_failed`. After `panic` or `intern_failed`, that worker's events no longer reach the index. |
| `smg_engine_cache_hit_rate` | gauge | `worker`, `model`, `dp_rank` | Engine-reported prefix cache hit rate, to check that routing on events pays off. |

The `smg_cache_aware_policy_branch_total` and `smg_cache_aware_match_ratio` metrics count tree-mode decisions only; use the debug log above to inspect event-driven decisions.

The stream metrics of the subscriptions (`smg_kv_event_*`, `smg_kv_events_unavailable`, `smg_kv_block_size_tokens`) are listed under [KV Event Metrics](../reference/metrics.md#kv-event-metrics) in the metrics reference.

---

## Caveats

- **gRPC only.** Events stream over gRPC through `smg-grpc-servicer`. HTTP workers use the approximate string tree, ZMQ (`ipc://`) workers use the approximate token tree, and MLX workers do not implement `SubscribeKvEvents`.
- **One block size per model.** If workers serving the same model use different page sizes, the policy uses whichever block size a worker reported last. Keep page sizes homogeneous within a model.
- **Per-gateway index.** Each gateway subscribes to every worker itself. With `--enable-mesh`, gateways synchronize the approximate trees, not the event index.
- **No cache partitions.** The event index ignores the `cache_salt`, `extra_key` and LoRA fields that partition the approximate trees.
- **Unaligned stores are skipped.** The servicers drop any `BlockStored` event whose token count is not its block count times its block size (vLLM can emit these for Mamba models); those blocks never reach the index.
- **Replay needs `replay_endpoint`.** The servicers fill gaps only when `replay_endpoint` is set in `--kv-events-config`; without it, a gap costs the worker's index entries (see [Recovery after gaps](#recovery-after-gaps)).

---

## Reference

- Policy implementation: `model_gateway/src/policies/cache_aware.rs`
- Event subscription manager: `model_gateway/src/worker/kv_event_monitor.rs` (gap settling and resyncs: `model_gateway/src/worker/kv_event_recovery.rs`)
- KV event proto: `crates/grpc_client/proto/common.proto` (messages `SubscribeKvEventsRequest`, `KvEventBatch`, `KvSnapshotChunk`, `KvCacheEvent`, `KvBlocksStored`, `KvBlocksRemoved`, `KvCacheCleared`)
- Engine-neutral relay (per-rank subscriptions, gap replay, normalization): `grpc_servicer/smg_grpc_servicer/kv_relay.py`; the Rust servicer's relay with history and state snapshots: `crates/engine_servicer/src/kv_events.rs`
- SGLang servicer: `grpc_servicer/smg_grpc_servicer/sglang/servicer.py` (`SubscribeKvEvents`) + per-rank sources `grpc_servicer/smg_grpc_servicer/sglang/kv_events.py`
- vLLM servicer: `grpc_servicer/smg_grpc_servicer/vllm/servicer.py` (`SubscribeKvEvents`) + config resolver `grpc_servicer/smg_grpc_servicer/vllm/kv_events.py`
- TokenSpeed servicer: `grpc_servicer/smg_grpc_servicer/tokenspeed/servicer.py` (`SubscribeKvEvents`) + per-rank sources and config resolver `grpc_servicer/smg_grpc_servicer/tokenspeed/kv_events.py`
- SGLang upstream config: `python/sglang/srt/disaggregation/kv_events.py` (class `KVEventsConfig`)
- vLLM upstream config: `vllm/config/kv_events.py` (class `KVEventsConfig`)
- TokenSpeed upstream config: `python/tokenspeed/runtime/pd/kv_events.py` (class `KVEventsConfig`)
