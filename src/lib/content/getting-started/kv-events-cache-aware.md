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
- `smg-grpc-servicer[sglang]` installed alongside SGLang (the extra requires SGLang 0.5.20 or newer)

</div>

---

## Why event-driven?

Without events, `cache_aware` predicts cache contents from its own routing history. With events, it routes against the blocks each engine reports it has stored and evicted.

| Mode | Index | Input | Used when |
|---|---|---|---|
| **Event-driven** | `PositionalIndexer`, built from engine events | Token IDs | The model's event index has data |
| Approximate token tree | `TokenTree`, built from routing decisions | Token IDs | The request has token IDs and the model has no event data |
| Approximate string tree | `Tree`, built from routing decisions | Raw text | HTTP requests without token IDs |

The choice is made **per model**, not per worker. Once any worker of a model has streamed events into the index, every token-bearing request for that model is scored against the event index, and workers that do not stream events can then receive that model's requests only through the load-based fallback. Enable events on every worker of a model.

Event-driven routing also needs the default `--cache-index tree`: with `--cache-index hash`, the gateway never consults the event index.

---

## How the pieces fit together

```text
client
  │ OpenAI-compatible HTTP
  ▼
SMG gateway: cache_aware policy ◀── PositionalIndexer (one per model)
  │                                        ▲
  │ gRPC requests                          │ KvEventMonitor
  ▼                                        │ (SubscribeKvEvents stream)
worker: smg-grpc-servicer ─────────────────┘
  ▲
  │ ZMQ PUB → SUB, on the worker host (--kv-events-config)
engine scheduler (SGLang, vLLM or TokenSpeed)
```

1. The engine's scheduler publishes block-stored, block-removed and all-blocks-cleared events on a ZMQ `PUB` socket configured by `--kv-events-config`.
2. `smg-grpc-servicer`, which serves the worker's gRPC API, subscribes to that socket locally and re-publishes the events as the server-streaming RPC `SubscribeKvEvents`.
3. SMG's `KvEventMonitor` opens one subscription per gRPC worker and feeds the events into a per-model `PositionalIndexer`; `cache_aware` scores token-bearing requests against that index. If a stream breaks, the monitor resumes after the last batch it applied (see [Recovery after gaps](#recovery-after-gaps)).

---

## Step 1 — Launch the SGLang worker

Install the SGLang extra of the servicer, then launch the SGLang server with both `--smg-grpc-mode` and `--kv-events-config`:

```bash
pip install "smg-grpc-servicer[sglang]"

python -m sglang.launch_server \
  --model-path meta-llama/Llama-3.1-8B-Instruct \
  --host 0.0.0.0 \
  --port 50051 \
  --smg-grpc-mode \
  --page-size 16 \
  --kv-events-config '{"publisher":"zmq","endpoint":"tcp://*:5557","replay_endpoint":"tcp://*:5558","topic":"kv-events"}'
```

What each flag does:

| Flag | Why |
|---|---|
| `--smg-grpc-mode` | Serves SMG's gRPC `SglangScheduler` service from `smg-grpc-servicer` instead of SGLang's HTTP server. Required for SMG to talk to this worker over gRPC. `--grpc-mode` is a deprecated alias. SGLang also opens an HTTP sidecar on `--port` + 1 (`--smg-http-sidecar-port` moves it). |
| `--page-size 16` | The KV cache page size, in tokens. Use the same value for SMG's `--block-size` (see [Block size alignment](#block-size-alignment)). |
| `--kv-events-config` | A JSON object parsed by SGLang's `KVEventsConfig.from_cli`. `"publisher":"zmq"` turns publishing on (the default `"null"` publishes nothing), and `replay_endpoint` lets the bridge replay batches SMG missed. |

### `--kv-events-config` field reference

All fields and defaults match SGLang's `KVEventsConfig` (see `python/sglang/srt/disaggregation/kv_events.py` upstream):

| Field | Default | Notes |
|---|---|---|
| `publisher` | `"null"` | Set to `"zmq"` to publish. With any other value the bridge answers `SubscribeKvEvents` with `UNIMPLEMENTED`, and SMG stops subscribing to that worker. |
| `endpoint` | `"tcp://*:5557"` | ZMQ `PUB` address. The publisher binds wildcard endpoints such as `tcp://*:PORT` (and `ipc://` or `inproc://` ones) and connects to any other address, which leaves nothing listening, so keep the wildcard form. The bridge connects to it locally, rewriting `*` and `0.0.0.0` to `127.0.0.1`. |
| `topic` | `""` | ZMQ topic. The bridge reads the same config and subscribes to this topic, so any value works. |
| `replay_endpoint` | `null` | ZMQ `ROUTER` address that serves recent batches for replay. When set, the bridge replays the batches SMG missed after a gap, instead of SMG dropping the worker's index entries. |
| `buffer_steps` | `10000` | Number of recent batches kept for replay. |
| `hwm` | `100000` | ZMQ high-water mark: once this many messages are queued for a subscriber that is not keeping up, new events are dropped. The bridge uses the same value for its receive queue. |
| `max_queue_size` | `100000` | Queue between the scheduler and SGLang's publisher thread. |

With data parallelism, each rank publishes on `endpoint` port + `dp_rank` and serves replay on `replay_endpoint` port + `dp_rank`; rank 0 keeps the configured ports. Pick port ranges that do not overlap (`5557` and `5558` collide as soon as there are two ranks). The bridge currently subscribes to rank 0 only.

### Alternative: launch a vLLM worker

vLLM publishes KV cache events on a ZMQ socket; enable them with `--kv-events-config` and run the worker in SMG gRPC mode:

```bash
pip install "smg-grpc-servicer[vllm]"

# --grpc serves vLLM through smg-grpc-servicer instead of the OpenAI HTTP server;
# --kv-events-config turns on KV-event publishing:
vllm serve meta-llama/Llama-3.1-8B-Instruct \
  --grpc \
  --host 0.0.0.0 \
  --port 50051 \
  --kv-events-config '{"enable_kv_cache_events": true, "publisher": "zmq", "endpoint": "tcp://*:5557", "topic": "kv-events"}'
```

Event-driven routing needs the worker in SMG gRPC mode (`--grpc`; `pip install "vllm[grpc]"` pulls in the same servicer package), because KV events stream over the `SubscribeKvEvents` RPC. See [gRPC Workers](grpc-workers.md) for other launch flags.

| Field | Why |
|---|---|
| `enable_kv_cache_events: true` | vLLM's master switch. Without it no events are published, and SMG stops subscribing to the worker. |
| `publisher: "zmq"` | Selects the ZMQ publisher the bridge reads. Recent vLLM versions default to it once events are enabled. |
| `endpoint` / `topic` | As for SGLang; keep the wildcard `tcp://*:PORT` form. With data parallelism the port is `endpoint` port + `dp_rank`, and SMG consumes rank 0 only. |

The vLLM bridge does not replay missed events. SMG learns the block size from the `BlockStored` events themselves; set SMG's `--block-size` to vLLM's `--block-size` so the approximate token tree uses the same pages.

### Alternative: launch a TokenSpeed worker

TokenSpeed's scheduler publishes KV cache events on a ZMQ socket; enable them with `--kv-events-config`. The TokenSpeed gRPC server *is* the SMG gRPC entrypoint, so there is no separate `--grpc` flag:

```bash
# TokenSpeed is installed from source (engine + kernel + scheduler); see
# scripts/ci_install_tokenspeed.sh in the smg repository. Install the bridge's extra deps:
pip install "smg-grpc-servicer[tokenspeed]"

# --kv-events-config turns on KV-event publishing in the scheduler:
python -m smg_grpc_servicer.tokenspeed \
  --model meta-llama/Llama-3.1-8B-Instruct \
  --host 0.0.0.0 \
  --port 50051 \
  --kv-events-config '{"enable_kv_cache_events": true, "publisher": "zmq", "endpoint": "tcp://*:5557", "topic": "kv-events"}'
```

| Field | Why |
|---|---|
| `enable_kv_cache_events: true` | TokenSpeed master switch. Without it the scheduler records no events even if a publisher is set. |
| `publisher: "zmq"` | Selects the ZMQ publisher the bridge reads. Unset defaults to `"zmq"` when events are enabled; `"null"` (or any other value) disables bridging. |
| `endpoint` / `topic` | ZMQ `PUB` address and topic. Use a **bind-style** endpoint (`tcp://*:PORT`): TokenSpeed binds only when the endpoint contains `*` or `::` or starts with `ipc://` or `inproc://`, so a concrete address like `tcp://127.0.0.1:PORT` makes it *connect* instead, leaving nothing bound and the stream idle. With data parallelism the port is `endpoint` port + `dp_rank`, and SMG consumes rank 0 only. |

`--kv-events-config` is parsed by TokenSpeed's `KVEventsConfig.from_cli`. The TokenSpeed bridge does not replay missed events. SMG learns the block size from the `BlockStored` events; TokenSpeed's cache reuse granularity is `--prefix-granularity` (default 64 tokens; older builds call it `--block-size`, which remains an alias), so set SMG's `--block-size` to the same value.

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

`worker-2` is a second worker launched the same way, on another host. On a single host, give each worker its own `--port` and its own event ports, and leave room for each SGLang worker's sidecar on `--port` + 1.

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

1. **Learned from events** (highest priority): each worker's subscription records the block size of the first stored block it receives; the most recent value wins.
2. **`--block-size`** (router-wide default).

The `kv_block_size` field of a worker spec registered through the [admin API](../reference/api/admin.md) (`POST /workers`) is accepted but not applied in v1.11.0. In practice, keep the engine's page size (SGLang `--page-size`, vLLM `--block-size`, TokenSpeed `--prefix-granularity`) and SMG's `--block-size` equal, and let SMG correct itself once events arrive. The approximate token tree always uses `--block-size`. Serve each model with one page size: with mixed sizes, the model's block size is whichever a worker reported last.

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

If you do not see the first line for a worker, the worker is not a gRPC worker, the gateway's `--policy` is not `cache_aware` (a `policy` worker label alone does not start subscriptions), or the worker's model is routed by another policy. If events are disabled on the worker, the bridge rejects the stream and SMG stops trying:

```text
WARN Backend does not implement SubscribeKvEvents, disabling KV event subscription for this worker worker_url=grpc://worker-1:50051
```

**2. Backend block size learned.** Once the first stored block arrives, SMG records the backend's block size:

```text
INFO Learned block_size from KV event model_id=meta-llama/Llama-3.1-8B-Instruct block_size=16
```

**3. Routing decision uses the index.** With `RUST_LOG=info,smg::policies::cache_aware=debug`, each routed request logs `Event-driven routing: overlap match` with the chosen `worker` and a `branch` of `event_hit` (affinity held) or `event_spill` (the spill gate moved the request), or `Event-driven routing: no overlap, expected-wait fallback`.

While the model's event index is empty, token requests route on the approximate token tree, so cache affinity keeps working, only less accurately.

---

## Recovery after gaps

Every event batch carries the publisher's sequence number. `KvEventMonitor` applies batches in order and skips duplicates. When it sees a gap in the sequence, it reconnects immediately and asks the bridge to resume after the last batch it applied; after a stream error or a clean close, it reconnects with exponential backoff from 100 ms up to 30 s.

| Bridge | On resume |
|---|---|
| SGLang with `replay_endpoint` | Replays the missed batches from SGLang's replay buffer, then continues with live events without duplicates. The index stays intact. |
| SGLang without `replay_endpoint`, or when the missed batches are no longer available | Rejects the resume with `OUT_OF_RANGE` (or `DATA_LOSS` if the replay fails partway). SMG drops that worker's index entries and resubscribes from live events. |
| vLLM, TokenSpeed | No replay: the bridge ignores the resume point and streams live events. If batches were missed, SMG sees every live batch as another gap and keeps reconnecting, repeating the `Sequence gap detected` warning, and that worker's index entries stop updating until the worker is removed. |

Resubscribing from live events rebuilds the worker's entries from new events only; it is not a snapshot of the engine's cache, so affinity to that worker returns as it caches new prefixes. SMG logs `KV event replay cursor expired; clearing worker state ...` or `KV event subscriber fell behind; clearing worker state ...` when it drops a worker's entries, and `Sequence gap detected, reconnecting for replay from seq N` on a gap. The SGLang bridge waits up to five seconds for each replay message before it gives up.

---

## Bounding the index

The event index drops a worker's entries when its engine reports evictions or the worker is removed. If an engine stops reporting evictions, entries accumulate. Two optional bounds, both off by default, are checked every 30 seconds:

| Flag | Effect |
|---|---|
| `--kv-indexer-ttl-secs` | Prune entries that were neither stored nor read by a routing query within this many seconds. Unset or `0` disables the TTL pass. |
| `--kv-indexer-max-entries` | Per-model ceiling; above it, the least recently touched entries are pruned down to 90% of the ceiling. Unset or `0` disables the ceiling. |

A pass that prunes entries logs `Pruned positional indexer` with the counts. Both flags exist only in the Rust `smg` binary: the Python launcher that `pip install smg` and the container image use does not accept them.

---

## Tuning

| Knob | Where | Effect |
|---|---|---|
| `--block-size` | SMG | Keep equal to the engine's page size. |
| `--balance-abs-threshold` / `--balance-rel-threshold` | SMG | Spill gate: a holder whose in-flight request count exceeds both margins over the fleet mean sends the request to another worker. |
| `--overlap-decay` | SMG | De-ranks candidates with a waiting-prefill backlog. Needs workers that report waiting uncached tokens (SGLang does; vLLM and TokenSpeed do not). |
| `--selection-temperature` | SMG | Spreads picks across candidates with near-equal overlap instead of always taking the best. |
| `--balance-token-usage-threshold` / `--overload-token-usage-threshold` | SMG | Suspend cache affinity under KV pressure (off by default). |
| `--kv-indexer-ttl-secs` / `--kv-indexer-max-entries` | SMG | Bound the event index (see [Bounding the index](#bounding-the-index)). |
| `replay_endpoint` / `buffer_steps` | SGLang `--kv-events-config` | Enable replay; raise `buffer_steps` if resumes fail because the missed batches already left the buffer. |
| `hwm` | `--kv-events-config` | Raise if events are dropped under bursty load. |

`--cache-threshold` does not apply to event-driven routing: any worker holding at least the request's first full block is a candidate. See [Cache-Aware Routing](../concepts/routing/cache-aware.md#tuning-knobs) for the full list of knobs.

---

## Monitoring

| Metric | Type | Labels | Description |
|---|---|---|---|
| `smg_kv_event_subscription_failures_total` | counter | `worker`, `reason` | A subscription task failed: `panic`, `join_error` or `intern_failed`. After `panic` or `intern_failed`, that worker's events no longer reach the index. |
| `smg_engine_cache_hit_rate` | gauge | `worker`, `model`, `dp_rank` | Engine-reported prefix cache hit rate, to check that routing on events pays off. |

The `smg_cache_aware_policy_branch_total` and `smg_cache_aware_match_ratio` metrics count tree-mode decisions only; use the debug log above to inspect event-driven decisions.

---

## Caveats

- **gRPC only.** Events stream over gRPC through `smg-grpc-servicer`. HTTP workers use the approximate string tree, ZMQ (`ipc://`) workers use the approximate token tree, and MLX workers do not implement `SubscribeKvEvents`.
- **DP rank 0 only.** The bridges subscribe to rank 0's publisher, so for data-parallel workers the index reflects rank 0's cache.
- **One block size per model.** If workers serving the same model use different page sizes, the policy uses whichever block size a worker reported last. Keep page sizes homogeneous within a model.
- **Per-gateway index.** Each gateway subscribes to every worker itself. With `--enable-mesh`, gateways synchronize the approximate trees, not the event index.
- **No cache partitions.** The event index ignores the `cache_salt`, `extra_key` and LoRA fields that partition the approximate trees.
- **Unaligned stores are skipped.** The vLLM and TokenSpeed bridges drop any `BlockStored` event whose token count is not its block count times its block size (vLLM can emit these for Mamba models) and log `Skipping BlockStored: ...`; those blocks never reach the index.
- **Replay needs SGLang.** Only the SGLang bridge replays missed events, and only when `replay_endpoint` is set. On vLLM and TokenSpeed workers, a sequence gap stops that worker's index updates (see [Recovery after gaps](#recovery-after-gaps)).

---

## Reference

- Policy implementation: `model_gateway/src/policies/cache_aware.rs`
- Event subscription manager: `model_gateway/src/worker/kv_event_monitor.rs`
- KV event proto: `crates/grpc_client/proto/common.proto` (messages `SubscribeKvEventsRequest`, `KvEventBatch`, `KvCacheEvent`, `KvBlocksStored`, `KvBlocksRemoved`, `KvCacheCleared`)
- SGLang bridge: `grpc_servicer/smg_grpc_servicer/sglang/servicer.py` (`SubscribeKvEvents`) and `grpc_servicer/smg_grpc_servicer/sglang/kv_events.py` (live stream and replay)
- Shared ZMQ→proto conversion: `grpc_servicer/smg_grpc_servicer/kv_events.py` (engine-neutral; used by the vLLM and TokenSpeed bridges)
- vLLM bridge: `grpc_servicer/smg_grpc_servicer/vllm/servicer.py` (`SubscribeKvEvents`) + config resolver `grpc_servicer/smg_grpc_servicer/vllm/kv_events.py`
- TokenSpeed bridge: `grpc_servicer/smg_grpc_servicer/tokenspeed/servicer.py` (`SubscribeKvEvents`) + config resolver `grpc_servicer/smg_grpc_servicer/tokenspeed/kv_events.py`
- SGLang upstream config: `python/sglang/srt/disaggregation/kv_events.py` (class `KVEventsConfig`)
- vLLM upstream config: `vllm/config/kv_events.py` (class `KVEventsConfig`)
- TokenSpeed upstream config: `python/tokenspeed/runtime/pd/kv_events.py` (class `KVEventsConfig`)
