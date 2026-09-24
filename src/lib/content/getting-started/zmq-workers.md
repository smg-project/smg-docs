---
title: ZMQ Direct Workers
---

# ZMQ Direct Workers

When SMG and the inference engine share a host, SMG can connect straight to the engine core over ZMQ. No engine HTTP server or gRPC servicer sits in the path: the engine runs headless (scheduling and model execution only), SMG does everything else — tokenization, chat templates, parsing, stop handling, and routing — and the two exchange token IDs over local `ipc://` sockets. Direct ZMQ connections were added in v1.10.0.

<div class="prerequisites" markdown>

#### Before you begin

- Completed the [Getting Started](index.md) guide
- SMG and the engine on the same host (in containers, they must share a network namespace, as in one pod, and a volume for the socket directory)
- vLLM or TokenSpeed (see [Supported Engines](#supported-engines))
- A HuggingFace model ID or local model path for SMG's `--model-path`: over ZMQ the engine reports neither its model name nor a tokenizer

</div>

---

## When to Use ZMQ

| | HTTP | gRPC | ZMQ |
|---|------|------|-----|
| Worker URL | `http://host:port` | `grpc://host:port` | `ipc:///path` |
| Engine-side process | Engine's OpenAI-compatible server | Engine with a gRPC servicer | Headless engine core only |
| Tokenization, chat templates, parsers | Engine | Gateway | Gateway |
| Engine location | Any reachable host | Any reachable host | Same host as SMG |
| Prefill-decode disaggregation | Yes | Yes | No |
| KV-event stream for cache-aware routing | No | Yes | No |

Compared with gRPC, the direct path drops the servicer process, a serialization round-trip, and a context switch per message. Use it when the gateway and engine run on one machine. Use [gRPC](grpc-workers.md) when the engine runs on another host, or when you need disaggregation, KV-event routing, embeddings, or engine admin operations (see [Limits](#limits)).

---

## How It Works

For a worker URL `ipc://<path>`:

1. SMG binds two data-plane sockets, `ipc://<path>-in.sock` (requests) and `ipc://<path>-out.sock` (outputs), plus a handshake listener at `tcp://127.0.0.1:<port>`, where the port is derived from `<path>` (see [Handshake Address](#handshake-address)).
2. The headless engine dials the handshake address. SMG answers with the data-plane addresses (a HELLO, INIT, READY exchange); the engine connects to them and reports its context length and data-parallel size.
3. SMG marks the worker ready as soon as the handshake completes.
4. Requests go to the engine as token IDs. Outputs come back as token IDs, with the engine's scheduler load piggybacked on the output batches.

The engine never sees text, so SMG also does the work that the engine's API server or gRPC servicer would otherwise do:

| Task | What SMG does |
|------|---------------|
| Tokenization, chat templates, reasoning and tool-call parsing | Same as in [gRPC mode](grpc-workers.md) |
| String stops | Sends a stop string that encodes to a single token as a stop token ID, and matches longer stop strings on the decoded text and trims them. For Harmony (gpt-oss) models, stops are matched on the parsed channel text |
| EOS (vLLM) | Attaches the model's EOS token IDs to every request: from `config.json` and `generation_config.json` when `--model-path` is a local directory, otherwise from the tokenizer. TokenSpeed stops at EOS on its own |
| Default `max_tokens` (vLLM) | Uses the context length the engine reported at handshake minus the prompt length |
| `n > 1` | Sends `n` single-sample requests and merges the results; an explicit `seed` becomes `seed + i` for sample `i`, so samples differ but stay reproducible |
| Data-parallel ranks | Picks the least-loaded engine inside a grouped worker (see [Data-Parallel Engines](#data-parallel-engines)) |

---

## Supported Engines

| Engine | Runtime | Headless engine command | Notes |
|--------|---------|-------------------------|-------|
| vLLM | `vllm` | `vllm serve <model> --headless ...` | Assumed when a ZMQ worker declares no runtime |
| TokenSpeed | `tokenspeed` | `python -m tokenspeed.cli serve --headless ...` | Must be declared. Needs `--grammar-backend` for structured outputs and `--enable-output-logprobs` for logprobs |

Both engines use the same handshake, so SMG can't tell which one dialed in. Declare the runtime with `--backend` (startup workers) or `runtime_type` (worker API). A ZMQ worker without a runtime is treated as vLLM, and SMG logs `runtime_type unspecified for ZMQ worker; defaulting to vLLM EngineCore`.

SGLang, TensorRT-LLM, and MLX can't use ZMQ in v1.11.0. `smg serve` accepts `--connection-mode zmq` only with `--backend vllm` or `--backend tokenspeed`, and the gateway rejects any other runtime with `ZMQ worker ... has unsupported runtime ...: only vllm and tokenspeed are supported over the ZMQ direct backend`. Connect those engines over [gRPC](grpc-workers.md).

SMG doesn't check engine versions at connect time, and its decoders accept fields that newer engine releases append. SMG's CI runs the ZMQ suites against vLLM 0.27.1 and a pinned TokenSpeed revision.

---

## Quick Start with `smg serve`

`smg serve --connection-mode zmq` starts headless engines and the gateway together:

=== "vLLM"

    ```bash
    smg serve \
      --backend vllm \
      --connection-mode zmq \
      --model meta-llama/Llama-3.1-8B-Instruct \
      --router-model-path meta-llama/Llama-3.1-8B-Instruct \
      --host 0.0.0.0 \
      --port 30000
    ```

=== "TokenSpeed"

    ```bash
    smg serve \
      --backend tokenspeed \
      --connection-mode zmq \
      --model meta-llama/Llama-3.1-8B-Instruct \
      --router-model-path meta-llama/Llama-3.1-8B-Instruct \
      --host 0.0.0.0 \
      --port 30000
    ```

!!! warning "Pass `--router-model-path`"
    vLLM and TokenSpeed take the model as `--model`, which `smg serve` does not forward to the gateway. Over ZMQ the gateway gets the model name and tokenizer only from its own model path, so without `--router-model-path` the worker registration fails with `ZMQ worker ... has no model identity`.

What `smg serve` sets up in ZMQ mode:

| Item | Behavior |
|------|----------|
| Worker URL | `ipc://<socket dir>/engine-<worker port>` per replica. The socket directory is `$SMG_ZMQ_SOCKET_DIR`, default `/tmp/smg-zmq-<uid>` |
| Engine command | A headless engine that dials the handshake port derived from its worker URL (commands below) |
| TokenSpeed defaults | Adds `--grammar-backend xgrammar` and `--enable-output-logprobs` unless you pass your own value for either flag |
| Engine health wait | Skipped. The gateway starts at once, and `/readiness` returns 503 until a worker has completed its handshake and the tokenizer has loaded |
| Gateway runtime | `--backend` is forwarded so the gateway speaks the engine's wire protocol |
| Replicas | `--data-parallel-size N` starts `N` independent single-engine workers, each with its own socket path and GPU slice. The launch stops early if two replicas' paths derive the same handshake port (`ZMQ handshake port collision on ...`); change `--worker-base-port` |

The launcher builds these engine commands; if you pass one of the flags it sets, your copy is dropped:

```text
# vLLM
python -m vllm.entrypoints.cli.main serve <model> --headless \
  --data-parallel-size 1 --data-parallel-size-local 1 \
  --data-parallel-address 127.0.0.1 --data-parallel-rpc-port <handshake port>

# TokenSpeed
python -m tokenspeed.cli serve --headless --model <model> \
  --port <worker port> --dist-init-addr 127.0.0.1:<store port> \
  --data-parallel-address 127.0.0.1 --data-parallel-rpc-port <handshake port> \
  --zmq-engine-index 0 --grammar-backend xgrammar --enable-output-logprobs
```

To put several data-parallel engines behind one worker, start the engines yourself and use `smg launch --zmq-engine-count` (see [Data-Parallel Engines](#data-parallel-engines)).

---

## Manual Setup with `smg launch`

### Socket Path

Pick a path for each worker, for example `ipc:///tmp/smg-zmq/engine-0`. SMG creates the parent directory with mode `0700` if it doesn't exist. An existing directory must be a real directory (not a symlink) owned by the user SMG runs as, or SMG refuses to bind, so don't place sockets directly in a shared directory such as `/tmp`. The engine must be able to reach the directory: run it as the same user, or create the directory yourself with the access the engine needs.

Before binding, SMG deletes any socket files already at `<path>-in.sock` and `<path>-out.sock`, treating them as leftovers from an earlier gateway run. Give each gateway process its own socket paths: a second gateway configured with the same worker URL would delete the first one's live sockets.

### Handshake Address

SMG binds the handshake on `127.0.0.1` at a port in 20000–29999, computed from the path after `ipc://` as `20000 + FNV-1a-64(path) mod 10000`. Compute it before starting the engine:

```bash
python3 - <<'EOF'
path = "/tmp/smg-zmq/engine-0"  # the worker URL without "ipc://"
h = 0xCBF29CE484222325
for b in path.encode():
    h = ((h ^ b) * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF
print(20000 + h % 10000)
EOF
```

For this path it prints `22670`. SMG also logs the address when it binds:

```text
Binding ZMQ client for worker ipc:///tmp/smg-zmq/engine-0 (handshake=tcp://127.0.0.1:22670, engines=1)
```

Two paths can hash to the same port; SMG rejects the second worker at registration. Workers added through the [worker API](#register-workers-at-runtime) can set a fixed `zmq_handshake_address` instead.

### Start the Engine

=== "vLLM"

    ```bash
    vllm serve meta-llama/Llama-3.1-8B-Instruct \
      --headless \
      --data-parallel-size 1 \
      --data-parallel-size-local 1 \
      --data-parallel-address 127.0.0.1 \
      --data-parallel-rpc-port 22670
    ```

=== "TokenSpeed"

    ```bash
    python -m tokenspeed.cli serve \
      --headless \
      --model meta-llama/Llama-3.1-8B-Instruct \
      --data-parallel-address 127.0.0.1 \
      --data-parallel-rpc-port 22670 \
      --zmq-engine-index 0 \
      --grammar-backend xgrammar \
      --enable-output-logprobs
    ```

    TokenSpeed's grammar backend defaults to none, and then the engine ignores structured-output constraints: a `tool_choice: "required"` or `json_schema` request comes back as free text. Output logprobs are off by default. When several TokenSpeed engines share a host, give each its own `--port`; TokenSpeed derives its internal control ports from it.

Add your usual engine flags, such as `--tensor-parallel-size`.

### Start the Gateway

```bash
smg launch \
  --worker-urls ipc:///tmp/smg-zmq/engine-0 \
  --backend vllm \
  --model-path meta-llama/Llama-3.1-8B-Instruct \
  --host 0.0.0.0 \
  --port 30000
```

Use `--backend tokenspeed` for a TokenSpeed engine. `--model-path` is required: it names the model and loads the tokenizer. SMG binds the sockets as soon as it registers the worker, then waits up to 600 seconds for the handshake; the engine loads the model and profiles its KV cache inside that window. If the handshake fails, the next health probe starts a new attempt.

To serve several engines, list one `ipc://` URL per engine. They are independent workers balanced by `--policy`.

### Register Workers at Runtime

Add a ZMQ worker to a running gateway with `POST /workers`. The `ipc://` scheme selects ZMQ, so `connection_mode` can be left out:

```bash
curl -X POST http://localhost:30000/workers \
  -H "Content-Type: application/json" \
  -d '{
    "url": "ipc:///tmp/smg-zmq/engine-1",
    "runtime_type": "tokenspeed"
  }'
```

ZMQ-related worker fields:

| Field | Description |
|-------|-------------|
| `url` | `ipc://<path>` (the path is required) |
| `runtime_type` | `vllm` or `tokenspeed` (`runtime` is accepted as an alias). Unset means vLLM |
| `zmq_handshake_address` | A `tcp://` address to bind for the handshake instead of the derived one. Rejected on non-ZMQ workers |
| `dp_size` | Number of engines in a [grouped worker](#data-parallel-engines); leave `dp_rank` unset |
| `labels.model_path` | Model ID and tokenizer source, for gateways started without `--model-path` |

`zmq_handshake_address` pairs a worker with an engine that dials a fixed address. A TokenSpeed engine started without `--data-parallel-rpc-port` dials `tcp://127.0.0.1:30500`, so this worker pairs with it:

```bash
curl -X POST http://localhost:30000/workers \
  -H "Content-Type: application/json" \
  -d '{
    "url": "ipc:///tmp/smg-zmq/tokenspeed-0",
    "runtime_type": "tokenspeed",
    "zmq_handshake_address": "tcp://127.0.0.1:30500"
  }'
```

Registration runs in the background (the API answers `202 Accepted`), so a rejected worker shows up only in the gateway log; see [Troubleshooting](#troubleshooting). For the full request schema, see [Worker Management](../reference/api/admin.md#worker-management).

---

## Data-Parallel Engines

There are two ways to run data parallelism over ZMQ:

- **Replicas:** several single-engine workers, each with its own socket path. The gateway's routing policy spreads requests across them. `smg serve --data-parallel-size N` sets this up.
- **Grouped worker:** one worker URL and one socket set, with `N` data-parallel engines behind it. The gateway sees one worker and picks the engine (rank) for each request itself.

### Configure a Grouped Worker

Start the engine group with its data-parallel size, then tell the gateway how many engines to wait for:

=== "vLLM"

    ```bash
    vllm serve meta-llama/Llama-3.1-8B-Instruct \
      --headless \
      --data-parallel-size 2 \
      --data-parallel-size-local 2 \
      --data-parallel-address 127.0.0.1 \
      --data-parallel-rpc-port 22670

    smg launch \
      --worker-urls ipc:///tmp/smg-zmq/engine-0 \
      --backend vllm \
      --model-path meta-llama/Llama-3.1-8B-Instruct \
      --zmq-engine-count 2
    ```

=== "TokenSpeed"

    ```bash
    python -m tokenspeed.cli serve \
      --headless \
      --model meta-llama/Llama-3.1-8B-Instruct \
      --data-parallel-size 2 \
      --dist-init-addr 127.0.0.1:31233 \
      --data-parallel-address 127.0.0.1 \
      --data-parallel-rpc-port 22670 \
      --zmq-engine-index 0 \
      --grammar-backend xgrammar \
      --enable-output-logprobs

    smg launch \
      --worker-urls ipc:///tmp/smg-zmq/engine-0 \
      --backend tokenspeed \
      --model-path meta-llama/Llama-3.1-8B-Instruct \
      --zmq-engine-count 2
    ```

    Each TokenSpeed rank dials in with its own identity (`--zmq-engine-index` plus its rank). TokenSpeed requires an explicit `--dist-init-addr` when `--data-parallel-size` is above 1; pick a free port outside the 20000–29999 handshake range.

- `--zmq-engine-count` applies to every `ipc://` URL in `--worker-urls` and must be a positive integer. For a worker added through the API, set `"dp_size"` on the worker instead.
- The engine process needs `N` times its tensor-parallel size in GPUs.
- The gateway must wait for exactly as many engines as dial in. An extra engine fails the handshake with `duplicate HELLO ... after INIT phase`; a missing one leaves the handshake waiting until it times out.
- Grouped workers can't be combined with `--dp-aware`, which tries to expand the group into one worker per rank and fails with `cannot be dp-aware expanded`. Single-engine ZMQ workers register normally under `--dp-aware`.

### Rank Selection

Output batches carry the producing rank's scheduler load: running requests, waiting requests, and KV-cache usage (vLLM's scheduler stats, or TokenSpeed's load snapshot). For each request, SMG scores every rank in the group and sends the request to the lowest score:

```text
score = max(requests SMG has in flight on the rank, running + waiting)
        + waiting × 6 × max(0, kv_cache_usage − 0.5)
```

- The in-flight floor keeps a stale report from making a busy rank look idle, so a burst of requests between reports spreads across ranks.
- Engines report load only while they produce output. When a rank's last in-flight request finishes, SMG resets that rank's running and waiting counts to zero and keeps its last reported KV usage.
- Ties rotate across ranks. A TokenSpeed build that sends no load snapshot is scored on SMG's in-flight counts alone.
- vLLM MoE models run their data-parallel ranks in lockstep and pause the whole group when idle. SMG sends the wake-up that vLLM's data-parallel coordinator process would otherwise send, so no coordinator runs. Ranks of dense models run independently.

Rank selection happens inside the connection. Routing policies see the group as one worker, and requests are never pinned to a rank.

!!! note "TokenSpeed attention-DP rank affinity uses gRPC"
    v1.10.1 added rank affinity for TokenSpeed attention data parallelism, which keeps multi-turn requests on the rank that holds their prefix cache. It works over gRPC with `--dp-aware`: the TokenSpeed gRPC servicer advertises a `dp_size` label only when the engine and its protocol stubs support rank pinning (the capability handshake) and the width is above 1, and the gateway then registers one worker per rank and pins `data_parallel_rank` on each request. Single-rank engines register as ordinary, unpinned workers. None of this applies over ZMQ: TokenSpeed ZMQ requests carry no rank field, and ZMQ workers can't be dp-aware expanded.

---

## Worker Lifecycle

| Stage | What happens |
|-------|--------------|
| Registered | The worker starts as `pending`. SMG binds its sockets and starts the handshake right away |
| Connected | SMG promotes the worker to `ready` the moment the handshake completes, without waiting for the health-check success threshold |
| Serving | Health probes read a local liveness flag; the engine has no health RPC. `/readiness` stays at 503 with `tokenizer not yet registered` until the model's tokenizer has loaded |
| Engine lost | SMG marks the connection dead when the engine reports its own shutdown, the socket fails, a request send blocks for 10 seconds, three outputs in a row can't be decoded, or no output arrives for 300 seconds while requests are in flight. In-flight requests fail. The next health probe drops the dead connection, and a later probe binds the sockets again so a restarted engine can reconnect |
| Removed | `DELETE /workers/{worker_id}` drains the worker, then removes it; a handshake still in progress is cancelled and its sockets are released |

- Health checks stay on for ZMQ workers even with `--disable-health-check` or a per-worker `disable_health_check`, because the probe is what reconnects a restarted engine. SMG logs `Ignoring disabled health checks for ZMQ worker ...`.
- With `--remove-unhealthy-workers`, a ZMQ worker whose engine stays down is removed and nothing re-adds it. The flag is off by default unless service discovery is enabled; leave it off for ZMQ workers so they rejoin when their engine restarts.
- The 300-second silence limit is fixed. A single request whose prefill takes longer than that on an otherwise idle engine is failed as if the engine had died.
- Updating a ZMQ worker's properties, such as labels or priority, keeps its live connection.

---

## Features and Limits

### Supported over ZMQ

| Feature | vLLM | TokenSpeed |
|---------|------|------------|
| Chat Completions, Completions, Responses, and Messages, including streaming | Yes | Yes |
| Tool-call and reasoning parsing | Yes | Yes |
| Structured outputs (`response_format`, constrained `tool_choice`, regex, grammar) | Yes | Yes, with `--grammar-backend` set on the engine |
| String stops and EOS | Yes | Yes |
| Harmony (gpt-oss) stop strings | Yes | Yes |
| Multimodal inputs | Yes, one modality per request | Yes, except models whose processor emits `image_grid_thw` or `video_grid_thw` (MRoPE) |
| Output logprobs | Yes, including `top_logprobs` | Sampled-token logprobs, with `--enable-output-logprobs`; `top_logprobs` above 1 is rejected |
| Prompt logprobs | Forwarded to the engine, but no API field requests them in v1.11.0 | Rejected |
| `n > 1` and sampling `seed` | Yes | Yes |

### Limits

| Limit | What you see |
|-------|--------------|
| Same host only | Sockets are local `ipc://` paths with a loopback handshake. ZMQ workers are never synced to [high-availability mesh](../concepts/architecture/high-availability.md) peers; each belongs to the gateway that registered it |
| No prefill, decode, or encode workers | Registration fails with `ZMQ worker ... cannot serve worker type ...`. PD and EPD routing select only gRPC workers |
| No KV-event stream | `cache_aware` tracks ZMQ workers with its approximate prefix tree, and SMG logs `... the ZMQ transport has no KV-event stream ...` |
| No embeddings | `501` with code `unsupported_backend`: `ZMQ backend does not support embeddings yet` |
| No engine admin operations | `POST /flush_cache` skips ZMQ workers, and profiling fails with `start_profile is not supported over ZMQ`. LoRA loading, weight updates, and sleep/wake aren't available over ZMQ |
| No tokenizer from the engine | The gateway loads it from `--model-path` or `--tokenizer-path` (or the worker's `model_path`/`tokenizer_path` labels) |
| No rank pinning | See [Rank Selection](#rank-selection) |
| vLLM multimodal | Mixed image and video in one request: `400`, `the vLLM ZMQ backend takes one modality per request ...`. Worker-side media processing (media references) needs a gRPC vLLM worker: `400`, `multimodal_not_supported` |
| TokenSpeed logprobs | `top_logprobs` above 1, or `token_ids_logprob`: `400`, `... not supported over the TokenSpeed ZMQ backend` |
| TokenSpeed MRoPE models | `400`: `MRoPE position tensors are not derivable over the TokenSpeed ZMQ wire yet; use the gRPC transport for this model` |

---

## Verify

```bash
# Ready once the handshake has completed and the tokenizer has loaded
curl http://localhost:30000/readiness

# Transport, runtime, and status of each worker
curl -s http://localhost:30000/workers | jq '.workers[] | {url, connection_mode, runtime_type, status}'
```

Expected worker entry:

```json
{
  "url": "ipc:///tmp/smg-zmq/engine-0",
  "connection_mode": "zmq",
  "runtime_type": "vllm",
  "status": "ready"
}
```

Send a request:

```bash
curl http://localhost:30000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "meta-llama/Llama-3.1-8B-Instruct",
    "messages": [{"role": "user", "content": "Hello!"}]
  }'
```

Gateway metrics label ZMQ workers with `connection_mode="zmq"`:

```bash
curl -s http://localhost:29000/metrics | grep 'connection_mode="zmq"'
```

---

## Troubleshooting

??? question "Worker stays pending"

    **Symptoms:** `/readiness` returns 503 with `insufficient healthy workers`, and `/workers` shows `"status": "pending"`.

    **Solutions:**

    1. Check that the engine dials the address SMG bound: compare the engine's `--data-parallel-rpc-port` and `--data-parallel-address` with the `handshake=` value in SMG's `Binding ZMQ client for worker ...` log line. With no engine, SMG logs `ZMQ backend handshake failed for ...: ... startup handshake timed out while waiting for HELLO after 600s; will retry on the next health probe`.
    2. For a grouped worker, check that `--zmq-engine-count` (or `dp_size`) matches the engine's `--data-parallel-size`.
    3. A worker added through the API to a gateway started with `--disable-health-check` and no ZMQ workers at startup is never promoted. Restart the gateway without that flag.

??? question "Socket bind errors or stale socket files"

    **Symptoms:** The worker stays pending, and the gateway log shows `ZMQ backend handshake failed for ...` followed by a socket or directory error.

    **Solutions:**

    SMG deletes leftover socket files at `<path>-in.sock` and `<path>-out.sock` before binding, so a crashed gateway doesn't block a restart. Other causes:

    1. `ipc socket path ... exists but is not a socket; refusing to unlink`: a regular file sits at the socket path. Move it away.
    2. `ipc socket dir ... is owned by uid ...` or `ipc socket dir ... exists but is not a directory`: use a directory owned by SMG's user, not `/tmp` itself or a symlink.
    3. Another process holds the handshake port. Choose a different socket path, or register the worker with `zmq_handshake_address`.
    4. The socket path is too long. Unix socket paths have a small length limit (108 bytes on Linux), so keep `<path>-out.sock` short.

??? question "Registration rejected"

    **Symptoms:** The worker never appears in `/workers`, and the gateway log shows `Failed job: type=AddWorker, worker=ipc://...` with the reason.

    **Solutions:**

    1. `has no model identity`: start SMG with `--model-path` (with `smg serve`, `--router-model-path`), or set a `model_path` label on the worker.
    2. `has unsupported runtime`: use `vllm` or `tokenspeed`.
    3. `would bind handshake address ..., already claimed by worker ...`: two socket paths hash to the same port. Rename one, or set `zmq_handshake_address`.
    4. `zmq_handshake_address must be a tcp:// address` or `ZMQ worker URL must be ipc://<path>`: fix the address or URL.
    5. `cannot serve worker type`: ZMQ workers must be `regular`.
    6. `cannot be dp-aware expanded`: drop `--dp-aware`, or use single-engine workers.

??? question "TokenSpeed requests fail or hang"

    **Symptoms:** The worker connects, but requests error or time out.

    **Solutions:**

    1. Look for `runtime_type unspecified for ZMQ worker; defaulting to vLLM EngineCore` in the gateway log. A TokenSpeed worker must be declared with `--backend tokenspeed` or `"runtime_type": "tokenspeed"`, or SMG speaks the vLLM wire protocol to it.
    2. If structured outputs come back unconstrained or logprobs are missing, start the engine with `--grammar-backend xgrammar` and `--enable-output-logprobs`.

??? question "Readiness reports tokenizer not yet registered"

    **Symptoms:** `/readiness` returns 503 with `tokenizer not yet registered` after the worker is ready.

    **Solutions:**

    1. Wait for the tokenizer download from `--model-path` to finish, and check the gateway log for load errors.
    2. Set `--tokenizer-path` if the tokenizer lives somewhere other than the model path.

---

## Next Steps

- [gRPC Workers](grpc-workers.md) — Engines on other hosts, disaggregation, and KV-event routing
- [Multiple Workers](multiple-workers.md) — Mix worker types and register workers at runtime
- [Load Balancing](load-balancing.md) — Policies for spreading traffic across ZMQ replicas
- [Architecture Overview](../concepts/architecture/overview.md#zmq-path) — Where the ZMQ path sits in the gateway
- [Configuration Reference](../reference/configuration.md#worker-configuration) — `--worker-urls` and related worker flags
- [Monitoring](monitoring.md) — Gateway and engine-load metrics
