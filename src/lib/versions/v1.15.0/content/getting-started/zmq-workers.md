---
title: ZMQ Direct Workers
---

# ZMQ Direct Workers

SMG can connect straight to the engine core over ZMQ. No engine HTTP server or gRPC servicer sits in the path: the engine runs headless (scheduling and model execution only), SMG does everything else — tokenization, chat templates, parsing, stop handling, and routing — and the two exchange token IDs over ZMQ sockets: local `ipc://` sockets when they share a host, or TCP sockets for an engine on another host (see [Engines on Another Host](#engines-on-another-host)).

<div class="prerequisites" markdown>

#### Before you begin

- Completed the [Getting Started](index.md) guide
- For `ipc://` workers (the quick start): SMG and the engine on the same host (in containers, they must share a network namespace, as in one pod, and a volume for the socket directory). An engine on another host connects through a `tcp://` worker URL instead
- vLLM, TokenSpeed, or SGLang (see [Supported Engines](#supported-engines))
- A HuggingFace model ID or local model path for SMG's `--model-path`: over ZMQ the engine reports neither its model name nor a tokenizer

</div>

---

## When to Use ZMQ

| | HTTP | gRPC | ZMQ |
|---|------|------|-----|
| Worker URL | `http://host:port` | `grpc://host:port` | `ipc:///path` or `tcp://host:port` |
| Engine-side process | Engine's OpenAI-compatible server | Engine with a gRPC servicer | Headless engine core only |
| Tokenization, chat templates, parsers | Engine | Gateway | Gateway |
| Engine location | Any reachable host | Any reachable host | Same host (`ipc://`), or any host that can dial the gateway (`tcp://`) |
| Prefill-decode disaggregation | Yes | Yes | TokenSpeed only |
| KV-event stream for cache-aware routing | No | Yes | No |

Compared with gRPC, the direct path drops the servicer process, a serialization round-trip, and a context switch per message. Use [gRPC](grpc-workers.md) when you need KV-event routing, embeddings, disaggregation with vLLM or SGLang, or vLLM engine admin operations (see [Limits](#limits)).

---

## How It Works

For a worker URL `ipc://<path>`:

1. SMG binds two data-plane sockets, `ipc://<path>-in.sock` (requests) and `ipc://<path>-out.sock` (outputs), plus a handshake listener at `tcp://127.0.0.1:<port>`, where the port is derived from `<path>` (see [Handshake Address](#handshake-address)). A `tcp://` worker binds TCP sockets instead (see [Engines on Another Host](#engines-on-another-host)).
2. The headless engine dials the handshake address. SMG answers with the data-plane addresses (a HELLO, INIT, READY exchange); the engine connects to them and reports its context length and data-parallel size.
3. SMG marks the worker ready as soon as the handshake completes.
4. Requests go to the engine as token IDs. Outputs come back as token IDs, with the engine's scheduler load piggybacked on the output batches.

The engine never sees text, so SMG also does the work that the engine's API server or gRPC servicer would otherwise do:

| Task | What SMG does |
|------|---------------|
| Tokenization, chat templates, reasoning and tool-call parsing | Same as in [gRPC mode](grpc-workers.md) |
| String stops | Sends a stop string that encodes to a single token as a stop token ID, and matches longer stop strings on the decoded text and trims them. For Harmony (gpt-oss) models, stops are matched on the parsed channel text |
| EOS (vLLM) | Attaches the model's EOS token IDs to every request: from `config.json` and `generation_config.json` when `--model-path` is a local directory, otherwise from the tokenizer. TokenSpeed and SGLang stop at EOS on their own |
| Default `max_tokens` (vLLM) | Uses the context length the engine reported at handshake minus the prompt length |
| `n > 1` | Sends `n` single-sample requests and merges the results; an explicit `seed` becomes `seed + i` for sample `i`, so samples differ but stay reproducible (vLLM and TokenSpeed; the SGLang wire carries no `seed`) |
| Data-parallel ranks | Picks the least-loaded engine inside a grouped worker (see [Data-Parallel Engines](#data-parallel-engines)) |

---

## Supported Engines

| Engine | Runtime | Headless engine command | Notes |
|--------|---------|-------------------------|-------|
| vLLM | `vllm` | `vllm serve <model> --headless ...` | Assumed when a ZMQ worker declares no runtime |
| TokenSpeed | `tokenspeed` | `python -m tokenspeed.cli serve --headless ...` | Must be declared. Needs `--grammar-backend xgrammar` for structured outputs on checkpoints it doesn't default to xgrammar, and `--enable-output-logprobs` for logprobs |
| SGLang | `sglang` | `python -m smg_grpc_servicer.sglang.headless ...` | Must be declared. Needs `smg-grpc-servicer[sglang]` (SGLang 0.5.21 or newer): its plugin dials SMG from inside the scheduler, and SGLang itself is unchanged |

All of these engines use the same handshake, so SMG can't tell which one dialed in. Declare the runtime with `--backend` (startup workers) or `runtime_type` (worker API). A ZMQ worker without a runtime is treated as vLLM, and SMG logs `runtime_type unspecified for ZMQ worker; defaulting to vLLM EngineCore`.

These are the only three ZMQ runtimes: `smg serve` accepts `--connection-mode zmq` only with `--backend vllm`, `--backend tokenspeed`, or `--backend sglang`, and the gateway rejects any other runtime with `ZMQ worker ... has unsupported runtime ...: only vllm, tokenspeed and sglang are supported over the ZMQ direct backend`. TensorRT-LLM and MLX can't use ZMQ; connect those engines over [gRPC](grpc-workers.md).

SMG doesn't check engine versions at connect time, but it does check wire layout. An engine can declare the field order of its positional (array-encoded) request structs in its ready response — the SGLang launcher does — and SMG compares the fields it encodes against that order once per engine at connect, before the first request. If a field sits in a different slot than the one SMG encodes it in, the connection fails with a `wire layout mismatch` error naming the engine, the struct, and the field that differs (for example, that `SamplingParams` field 2 is `stop_token_ids` on SMG's side and `watermark` on the engine's). An engine release whose wire moved under SMG is refused at connect rather than failing every request one by one.

Only the fields SMG sends are compared, so an engine release that appends new fields after them still connects, and an engine that declares no layouts connects unchecked. SMG's decoders likewise accept fields that newer engine releases append. SMG's CI runs the ZMQ suites against vLLM 0.31.0, SGLang 0.5.21, and a pinned TokenSpeed revision.

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

=== "SGLang"

    ```bash
    smg serve \
      --backend sglang \
      --connection-mode zmq \
      --model-path meta-llama/Llama-3.1-8B-Instruct \
      --host 0.0.0.0 \
      --port 30000
    ```

!!! warning "Pass `--router-model-path` for vLLM and TokenSpeed"
    vLLM and TokenSpeed take the model as `--model`, which `smg serve` does not forward to the gateway. Over ZMQ the gateway gets the model name and tokenizer only from its own model path, so without `--router-model-path` the worker registration fails with `ZMQ worker ... has no model identity`. SGLang takes the model as `--model-path`, which `smg serve` parses itself and forwards, so the SGLang tab needs no `--router-model-path`.

What `smg serve` sets up in ZMQ mode:

| Item | Behavior |
|------|----------|
| Worker URL | `ipc://<socket dir>/engine-<worker port>` per replica. The socket directory is `$SMG_ZMQ_SOCKET_DIR`, default `/tmp/smg-zmq-<uid>` |
| Engine command | A headless engine that dials the handshake port derived from its worker URL (commands below) |
| TokenSpeed defaults | Adds `--grammar-backend xgrammar` and `--enable-output-logprobs` unless you pass your own value for either flag |
| Engine health wait | Skipped. The gateway starts at once, and `/readiness` returns 503 until a worker has completed its handshake and the tokenizer has loaded |
| Gateway runtime | `--backend` is forwarded so the gateway speaks the engine's wire protocol |
| Replicas | `smg serve`'s own `--data-parallel-size N` (before any `--`) starts `N` independent single-engine workers, each with its own socket path and GPU slice. The launch stops early if two replicas' paths derive the same handshake port (`ZMQ handshake port collision on ...`); change `--worker-base-port` |

The launcher builds these engine commands. If you pass a flag the launcher sets, your copy is dropped, except for the two TokenSpeed defaults above, where your value is used:

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

# SGLang
python -m smg_grpc_servicer.sglang.headless \
  --zmq-handshake-address tcp://127.0.0.1:<handshake port> --zmq-engine-index 0 \
  --model-path <model> --port <worker port>
```

To put several data-parallel TokenSpeed engines behind one worker, pass the engine-level `--data-parallel-size N` after a `--` separator on the `smg serve` command line: the flag passes through to the headless engine command, and the gateway awaits `N` engines on the worker's one socket set, so no `--zmq-engine-count` is needed. You can also start the engines yourself and register them as a grouped worker with `smg launch` (see [Data-Parallel Engines](#data-parallel-engines)).

The `--` passthrough works only with `--backend tokenspeed`. A vLLM or SGLang `smg serve` command exits with an `unrecognized arguments` error on anything after a `--`, and a `--data-parallel-size` (or `--dp-size`) before any `--` is always `smg serve`'s own replica count, so neither engine takes a grouped launch through `smg serve`: a grouped vLLM worker is set up with `smg launch` only, and the SGLang ZMQ wire has no data parallelism at all (use [gRPC](grpc-workers.md) for SGLang data parallelism).

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
Binding ZMQ client for worker ipc:///tmp/smg-zmq/engine-0 (handshake=tcp://127.0.0.1:22670, engines=Declared)
```

`engines=` shows how many engines the handshake waits for: `Exactly(N)` for a grouped worker with an explicit engine count, or `Declared` when the gateway takes the count from the first engine to dial (see [Data-Parallel Engines](#data-parallel-engines)).

Two paths can hash to the same port; SMG rejects the second worker at registration. Workers added through the [worker API](#register-workers-at-runtime) can set a fixed `zmq_handshake_address` instead.

### Engines on Another Host

A worker URL `tcp://<host>:<port>` serves engines that run on other machines. The scheme selects ZMQ like `ipc://` does, but everything rides TCP: SMG binds the handshake at the URL itself and its request and output sockets on ephemeral ports of the same host, and hands those addresses to the engines during the handshake. `<host>` must therefore be an address of the gateway's machine that the engines can reach — a wildcard such as `0.0.0.0` is refused, and a bare IPv6 host needs brackets (`tcp://[fd00::5]:31000`).

Start the engine with the worker URL as its dial target: `--data-parallel-address <host> --data-parallel-rpc-port <port>` for vLLM and TokenSpeed, or `--zmq-handshake-address tcp://<host>:<port>` for the SGLang headless launcher. A `tcp://` worker binds no socket files and takes no `zmq_handshake_address` (its URL already is the handshake address).

These sockets carry no authentication, so put `tcp://` workers only on a trusted network. Everything else on this page — runtimes, grouped workers, lifecycle, features and limits — applies to `ipc://` and `tcp://` workers alike.

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

    Without `--grammar-backend`, TokenSpeed turns its grammar backend on only for checkpoints it defaults to xgrammar; the rest run with `none` and reject requests that need constrained decoding, such as `tool_choice: "required"` or a `json_schema` response format — the request fails with the engine's refusal instead of coming back unenforced. `--grammar-backend xgrammar` turns enforcement on for any model. Output logprobs are off by default. When several TokenSpeed engines share a host, give each its own `--port`; TokenSpeed derives its internal control ports from it.

=== "SGLang"

    ```bash
    python -m smg_grpc_servicer.sglang.headless \
      --zmq-handshake-address tcp://127.0.0.1:22670 \
      --zmq-engine-index 0 \
      --model-path meta-llama/Llama-3.1-8B-Instruct \
      --port 31000
    ```

    The launcher comes with `pip install "smg-grpc-servicer[sglang]"` and starts SGLang's own scheduler ranks with no HTTP server, tokenizer manager, or detokenizer; the package's plugin dials SMG's handshake from inside the scheduler once the model has loaded. Every flag other than the two `--zmq-*` options is an ordinary SGLang server arg. When several SGLang engines share a host, give each its own `--port`; SGLang derives its internal control ports from it.

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

Use `--backend tokenspeed` or `--backend sglang` to match the engine. `--model-path` is required: it names the model and loads the tokenizer.

SMG binds the sockets as soon as it registers the worker, then waits up to `--zmq-startup-timeout-secs` (default 3600 seconds; with `smg serve`, `--router-zmq-startup-timeout-secs`) for each handshake message. SMG sees nothing from the engine except those messages, so this one bound must cover everything the engine does between its HELLO and its READY — loading weights, profiling the KV cache, capturing graphs. `--worker-startup-timeout-secs` bounds registration, not the handshake. If the handshake fails, the next health probe starts a new attempt. An engine that dialed in during an earlier attempt still connects: the new attempt accepts its READY.

To serve several engines, list one URL per engine. They are independent workers balanced by `--policy`.

### Register Workers at Runtime

Add a ZMQ worker to a running gateway with `POST /workers`. The `ipc://` and `tcp://` schemes select ZMQ, so `connection_mode` can be left out:

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
| `url` | `ipc://<path>` (the path is required), or `tcp://<host>:<port>` for [engines on another host](#engines-on-another-host) |
| `runtime_type` | `vllm`, `tokenspeed`, or `sglang` (`runtime` is accepted as an alias). Unset means vLLM |
| `zmq_handshake_address` | A `tcp://` address to bind for the handshake instead of the one derived from an `ipc://` path. Rejected on non-ZMQ workers, and on `tcp://` workers, whose URL already is the handshake address |
| `dp_size` | Number of engines in a [grouped worker](#data-parallel-engines); if omitted, the worker waits for the group size declared by the first engine to dial (one for an engine that declares none). Leave `dp_rank` unset |
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
- **Grouped worker:** one worker URL and one socket set, with `N` data-parallel engines behind it. The gateway sees one worker and picks the engine (rank) for each request itself. For TokenSpeed, `smg serve` sets this up when an engine-level `--data-parallel-size N` follows `--` on its command line; a vLLM group is set up only manually with `smg launch`. [Configure a Grouped Worker](#configure-a-grouped-worker) shows the manual setup for both.

Replicas work with every ZMQ engine. Grouped workers are a vLLM and TokenSpeed setup: data parallelism over the SGLang ZMQ wire isn't supported yet (use gRPC for SGLang data parallelism).

### Configure a Grouped Worker

Start the engine group with its data-parallel size. A TokenSpeed attention-DP group declares its size when it dials in, so `--zmq-engine-count` can stay unset and the gateway waits for the whole declared group. A vLLM engine declares no group size, so a vLLM group needs the explicit count:

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

- `--zmq-engine-count` applies to every ZMQ (`ipc://` or `tcp://`) URL in `--worker-urls`; an explicit value must be a positive integer, and `--zmq-engine-count 1` behaves like leaving the flag unset. For a worker added through the API, set `"dp_size"` on the worker instead; a `dp_size` of 1 likewise behaves like leaving the field out.
- Left unset, the count settles on what the first engine to dial declares, and an engine that declares no group size counts as one. A vLLM group with the count unset therefore settles at a single engine, and the rest of its ranks dial in as extras.
- The count the gateway waits for must match the number of engines that dial in. An extra engine can fail the handshake with `duplicate HELLO ... after INIT phase`; a missing one leaves the handshake waiting until it times out.
- An engine may declare a group size that differs from the count the handshake awaits, whether that count was set explicitly or settled by the first engine. The engine still connects; the gateway logs `engine declares a group of a different size than this handshake awaits` and keeps the count it was waiting for.
- The engine process needs `N` times its tensor-parallel size in GPUs.
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

Rank selection happens inside the connection. Routing policies see the group as one worker, and a plain request is never pinned to a rank. A disaggregated TokenSpeed request follows its KV rendezvous instead: a prefill group places it on the rank its rendezvous room selects, and a decode group takes it on the matching rank, so the KV transfer lands where the decode side looks it up.

!!! note "TokenSpeed attention-DP rank affinity uses gRPC"
    Rank affinity for TokenSpeed attention data parallelism keeps multi-turn requests on the rank that holds their prefix cache. It works over gRPC with `--dp-aware`: the TokenSpeed gRPC servicer advertises a `dp_size` label only when the engine and its protocol stubs support rank pinning (the capability handshake) and the width is above 1, and the gateway then registers one worker per rank and pins `data_parallel_rank` on each request. Single-rank engines register as ordinary, unpinned workers. ZMQ workers can't be dp-aware expanded, so rank affinity does not apply over ZMQ; inside a grouped ZMQ worker, only disaggregated requests land on a specific rank (placed by the KV rendezvous, see above).

---

## Worker Lifecycle

| Stage | What happens |
|-------|--------------|
| Registered | The worker starts as `pending`. SMG binds its sockets and starts the handshake right away |
| Connected | SMG promotes the worker to `ready` the moment the handshake completes, without waiting for the health-check success threshold |
| Serving | Health probes read a local liveness flag; the engine has no health RPC. `/readiness` stays at 503 with `tokenizer not yet registered` until the model's tokenizer has loaded |
| Engine lost | SMG marks the connection dead when the engine signals that it died (vLLM does so on a fatal error, TokenSpeed also on shutdown, and SGLang whenever its scheduler's event loop exits), the socket fails, three outputs in a row can't be decoded, the engine leaves a frame unaccepted on its input for 300 seconds, or in-flight requests spend a combined 300 seconds — past the prefill allowance for the prompt tokens the engine holds — without any output from the engine. In-flight requests fail. The next health probe drops the dead connection, and a later probe binds the sockets again so a restarted engine can reconnect |
| Removed | `DELETE /workers/{worker_id}` drains the worker, then removes it; a handshake still in progress is cancelled and its sockets are released |

- Health checks stay on for ZMQ workers even with `--disable-health-check` or a per-worker `disable_health_check`, because the probe is what reconnects a restarted engine. SMG logs `Ignoring disabled health checks for ZMQ worker ...`.
- With `--remove-unhealthy-workers`, a ZMQ worker whose engine stays down is removed and nothing re-adds it. The flag is off by default (left unset with [service discovery](service-discovery.md), removal covers only the workers discovery itself re-adds); leave it off for ZMQ workers so they rejoin when their engine restarts.
- An engine that stops reading its input (a paused or frozen engine core) fails requests, not the connection. A request the engine does not accept within 10 seconds fails with a retryable `503`, and a retry can select a different worker (see [Retries](../concepts/reliability/retries.md)); the requests the engine already holds keep streaming. The unaccepted frame is still delivered when the engine reads again, followed by an abort, so the engine drops the failed request instead of generating output nobody is waiting for. While the input is blocked, 256 requests or 32 MiB of request payload may wait behind it; a request that would take the waiting requests past either bound gets the `503` immediately instead of after the 10-second wait.
- The 10-second and 300-second windows are fixed, and so is the prefill allowance's rate: the prompt tokens of requests the engine has accepted but not yet produced output for extend the 300-second bound at 2,000 tokens per second — 500 extra seconds per million pending prompt tokens, and none when no prefill is pending. The TokenSpeed and SGLang wires carry token batches only, as does vLLM's with `--disable-log-stats`: such a wire says nothing for a prompt until its first token, so a batch of long prompts is silent for as long as its prefill takes. The allowance keeps that silence from counting, so a single request whose prefill outlasts 300 seconds is not failed as a dead engine. A vLLM engine with stats logging on reports every scheduler step, so it is never silent through a prefill. The rate is a bound on how slowly a healthy engine may prefill, not an estimate of how fast it does, so the verdict on a genuinely dead engine comes later in proportion to the prompt it was handed.
- Only silence past the allowance counts, and it accumulates across requests: the in-flight time since the engine's last output, beyond its pending prefill work, adds up over consecutive busy periods, and only an output of any kind — or 300 seconds with nothing in flight — resets the count. A request cancelled before its first output leaves its prefill allowance in place until it has passed: the engine may still be mid-prefill on the cancelled prompt.
- Updating a ZMQ worker's properties, such as labels or priority, keeps its live connection.

---

## Features and Limits

### Supported over ZMQ

| Feature | vLLM | TokenSpeed | SGLang |
|---------|------|------------|--------|
| Chat Completions, Completions, Responses, and Messages, including streaming | Yes | Yes | Yes |
| Tool-call and reasoning parsing | Yes | Yes | Yes |
| Structured outputs (`response_format`, constrained `tool_choice`, regex, grammar) | Yes | Yes, with `--grammar-backend xgrammar` set on the engine (without the flag, only checkpoints TokenSpeed defaults to xgrammar enforce them) | Yes |
| String stops and EOS | Yes | Yes | Yes |
| Harmony (gpt-oss) stop strings | Yes | Yes | Yes |
| Multimodal inputs | Yes, one modality per request | Yes; media whose `image_grid_thw`/`video_grid_thw` grids feed MRoPE positions are served only for the Qwen3.5 lineage (Qwen3.5, Qwen3.5-MoE, Qwen4-Exp), and only when the engine's handshake declares the checkpoint's `spatial_merge_size`, `image_token_id`, and `video_token_id`; patch-layout grids (Muse Spark) pass | Images, assembled as over gRPC; video and audio are refused |
| Output logprobs | Yes, including `top_logprobs` | Sampled-token logprobs, with `--enable-output-logprobs`; `top_logprobs` above 1 only when every connected engine declares the top-k column at its handshake, rejected otherwise | Yes, including `top_logprobs` |
| Prompt logprobs | Forwarded to the engine, but no API field requests them | Only when every connected engine declares the prompt-logprob column at its handshake, rejected otherwise; `token_ids_logprob` is rejected | Yes, requested through `/generate`'s `logprob_start_len`; `token_ids_logprob` is rejected |
| `n > 1` and sampling `seed` | Yes | Yes | `n > 1` yes; the SGLang wire carries no `seed` |
| Prefill, decode, and encode worker roles | No | Yes (see [Limits](#limits)) | No |
| Engine admin (cache flush, profiling) and RL control routes | No | Yes, when every rank's handshake declares the control reply | Cache flush and profiling yes; RL control routes no |

### Limits

| Limit | What you see |
|-------|--------------|
| Host scope and mesh | `ipc://` workers are same-host: local socket paths with a loopback handshake. `tcp://` workers serve [engines on another host](#engines-on-another-host) over unauthenticated sockets — trusted networks only. ZMQ workers of either scheme are never synced to [high-availability mesh](../concepts/architecture/high-availability.md) peers; each belongs to the gateway that registered it |
| Disaggregated roles are TokenSpeed-only | A vLLM or SGLang ZMQ worker registered as prefill, decode, or encode fails with `ZMQ worker ... cannot serve worker type ...`. A TokenSpeed ZMQ worker serves every role, with conditions checked at connect: the engine must declare the matching role, a prefill or encode engine must declare its bootstrap address in the handshake, and an encode engine must list the encode request — a connection failing any of these ends with an error naming the gap. PD and EPD routing select gRPC workers and TokenSpeed ZMQ workers |
| No KV-event stream | `cache_aware` tracks ZMQ workers with its approximate prefix tree, and SMG logs `... the ZMQ transport has no KV-event stream ...` |
| No embeddings | `501` with code `unsupported_backend`: `ZMQ backend does not support embeddings yet` |
| Engine admin operations | vLLM EngineCore carries no control calls: `POST /flush_cache` skips such workers, and profiling on one fails with `... is not supported over this engine's ZMQ wire`. SGLang schedulers take the cache flush and the profiler; TokenSpeed schedulers take both — plus the [RL control routes](rl-control-plane.md) (weight updates, pause/continue, and the engine's other POST control routes) — when every rank's handshake declared the control reply. vLLM's management RPCs (LoRA add/remove, sleep/wake, weight updates) stay unported over ZMQ |
| No tokenizer from the engine | The gateway loads it from `--model-path` or `--tokenizer-path` (or the worker's `model_path`/`tokenizer_path` labels) |
| No dp-aware rank pinning | See [Rank Selection](#rank-selection) |
| vLLM multimodal | Mixed image and video in one request: `400`, `the vLLM ZMQ backend takes one modality per request ...`. Worker-side media processing (media references) needs a gRPC vLLM worker: `400`, `multimodal_not_supported` |
| TokenSpeed logprobs | `top_logprobs` above 1 or prompt logprobs on an engine that declares no column for them, or `token_ids_logprob` on any: `400`, `... not supported over the TokenSpeed ZMQ backend` |
| TokenSpeed MRoPE models | Outside the Qwen3.5 lineage: `400`, `... MRoPE position tensors of this model are not derivable over the TokenSpeed ZMQ wire (the Qwen3.5 lineage's are); use the gRPC transport for this model`. Any multimodal request to a Qwen3.5-lineage engine whose handshake omits the layout facts: `400`, `... need its MRoPE position table, which the launcher's model facts (spatial_merge_size, image_token_id, video_token_id) did not provide ...` |
| SGLang non-image media | Video or audio in a request to an SGLang ZMQ worker: `400`, `multimodal_not_supported` (`backend sglang does not support video inputs`). Video needs SGLang [gRPC](grpc-workers.md) workers |
| SGLang `token_ids_logprob` | `501`, `selected-token logprobs require the SGLang gRPC servicer` |
| SGLang data parallelism | The wire has no DP yet (see [Data-Parallel Engines](#data-parallel-engines)); use [gRPC](grpc-workers.md) for SGLang data parallelism |

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

    1. Check that the engine dials the address SMG bound: compare the engine's `--data-parallel-rpc-port` and `--data-parallel-address` with the `handshake=` value in SMG's `Binding ZMQ client for worker ...` log line. With no engine, once `--zmq-startup-timeout-secs` expires, SMG logs `ZMQ backend handshake failed for ...: ... startup handshake timed out while waiting for HELLO after 3600s; will retry on the next health probe`.
    2. For a grouped worker, check that an explicit `--zmq-engine-count` (or `dp_size`) matches the engine's `--data-parallel-size`. With no explicit count, the gateway waits for the group size declared by the first engine to dial, so the worker stays pending until that many engines have dialed in.
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
    2. `has unsupported runtime`: use `vllm`, `tokenspeed`, or `sglang`.
    3. `would bind handshake address ..., already claimed by worker ...`: two socket paths hash to the same port. Rename one, or set `zmq_handshake_address`.
    4. `zmq_handshake_address must be a tcp:// address` or `ZMQ worker URL must be ipc://<path> or tcp://<host>:<port>`: fix the address or URL.
    5. `cannot serve worker type`: vLLM and SGLang ZMQ workers must be `regular`; TokenSpeed engines can serve prefill, decode, and encode (see [Limits](#limits)).
    6. `cannot be dp-aware expanded`: drop `--dp-aware`, or use single-engine workers.

??? question "TokenSpeed or SGLang requests fail or hang"

    **Symptoms:** The worker connects, but requests error or time out.

    **Solutions:**

    1. Look for `runtime_type unspecified for ZMQ worker; defaulting to vLLM EngineCore` in the gateway log. A TokenSpeed or SGLang worker must be declared with `--backend tokenspeed` / `--backend sglang` or an explicit `runtime_type`, or SMG speaks the vLLM wire protocol to it.
    2. If a TokenSpeed engine rejects requests that need constrained decoding (its grammar backend is off) or logprobs are missing, start it with `--grammar-backend xgrammar` and `--enable-output-logprobs`.

??? question "Readiness reports tokenizer not yet registered"

    **Symptoms:** `/readiness` returns 503 with `tokenizer not yet registered` after the worker is ready.

    **Solutions:**

    1. Wait for the tokenizer download from `--model-path` to finish, and check the gateway log for load errors.
    2. Set `--tokenizer-path` if the tokenizer lives somewhere other than the model path.

---

## Next Steps

- [gRPC Workers](grpc-workers.md) — Engines behind a gRPC servicer, vLLM and SGLang disaggregation, and KV-event routing
- [Multiple Workers](multiple-workers.md) — Mix worker types and register workers at runtime
- [Load Balancing](load-balancing.md) — Policies for spreading traffic across ZMQ replicas
- [Architecture Overview](../concepts/architecture/overview.md#zmq-path) — Where the ZMQ path sits in the gateway
- [Configuration Reference](../reference/configuration.md#worker-configuration) — `--worker-urls` and related worker flags
- [Monitoring](monitoring.md) — Gateway and engine-load metrics
