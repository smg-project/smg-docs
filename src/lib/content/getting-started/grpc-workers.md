---
title: gRPC Workers
---

# gRPC Workers

When workers connect via gRPC instead of HTTP, SMG becomes a full OpenAI-compatible server: it handles tokenization, chat templates, reasoning extraction, and tool calling at the gateway level. Workers run raw inference only.

For vLLM, TokenSpeed, or SGLang engines on the same host as the gateway, the [ZMQ direct backend](zmq-workers.md) is an alternative: it runs the same gateway pipeline over local IPC sockets.

<div class="prerequisites" markdown>

#### Before you begin

- Completed the [Getting Started](index.md) guide
- A gRPC-capable inference worker: vLLM, SGLang, TensorRT-LLM, TokenSpeed, or MLX (Apple Silicon)
- Access to the model weights or a HuggingFace model path (for tokenizer loading)

</div>

---

## What gRPC Mode Enables

| Capability | HTTP Mode (worker handles) | gRPC Mode (gateway handles) |
|------------|---------------------------|----------------------------|
| Chat templates | Worker | Gateway |
| Tokenization | Worker | Gateway (with optional caching) |
| Cache-aware routing | On request text, or token IDs for pre-tokenized requests | On the token IDs the gateway produced |
| Reasoning extraction | Worker | Gateway |
| Tool call parsing | Worker | Gateway, with per-model parser overrides |
| Constrained tool calls | Worker | Gateway builds the grammar, worker enforces it |
| Image and video preprocessing | Worker | Gateway; vLLM workers can take it over (`--mm-processing`, see [Multimodal Pipeline](../concepts/architecture/multimodal.md)) |
| MCP tool execution (Responses API) | Not handled by the gateway | Gateway |

In HTTP mode, SMG is a smart proxy that handles routing and failover only. In gRPC mode, SMG takes over the full request processing pipeline.

---

## Start a gRPC Worker

The vLLM, SGLang, TokenSpeed and MLX gRPC servers come from the `smg-grpc-servicer` Python package. Install the extra for your engine: `pip install "smg-grpc-servicer[vllm]"`, `[sglang]` or `[mlx]`. For TokenSpeed, install TokenSpeed first, then `smg-grpc-servicer`. SMG's engine container images already include the package. TensorRT-LLM serves gRPC from its own `serve` command.

=== "vLLM"

    ```bash
    python -m vllm.entrypoints.grpc_server \
      --model meta-llama/Llama-3.1-8B-Instruct \
      --host 0.0.0.0 \
      --port 50051
    ```

    This starts the default Python servicer. Setting `SMG_VLLM_SERVICER_IMPL=rust` in the worker's environment serves the same contract from Rust instead; see [Rust vLLM Servicer](#rust-vllm-servicer).

    After the engine loads, the worker holds its gRPC health at `NOT_SERVING` while a planned warm-up runs over the engine — about a minute by default; `SMG_VLLM_SERVICER_WARMUP` tunes or disables it (see [Planned Warm-Up Before Serving](#planned-warm-up-before-serving)).

=== "SGLang"

    ```bash
    python -m sglang.launch_server \
      --model-path meta-llama/Llama-3.1-8B-Instruct \
      --host 0.0.0.0 \
      --port 50051 \
      --grpc-mode
    ```

    `--grpc-mode` serves the worker through `smg-grpc-servicer`, which needs SGLang 0.5.21 or later. In this mode SGLang also serves an HTTP sidecar (profiling, plus `/metrics` with `--enable-metrics`).

    This starts the default Python servicer. Adding `--servicer-impl rust` — or setting `SMG_SGLANG_SERVICER_IMPL=rust` in the worker's environment — serves the same contract from Rust instead; see [Rust SGLang Servicer](#rust-sglang-servicer).

    After the engine loads, the worker holds its gRPC health at `NOT_SERVING` while a planned warm-up runs over the engine — about a minute by default; `SMG_SGLANG_SERVICER_WARMUP` tunes or disables it (see [Planned Warm-Up Before Serving](#planned-warm-up-before-serving)).

=== "TensorRT-LLM"

    ```bash
    # Enable XGrammar guided decoding for forced tool calls and response_format
    echo "guided_decoding_backend: xgrammar" > trtllm-config.yaml

    python -m tensorrt_llm.commands.serve serve \
      meta-llama/Llama-3.1-8B-Instruct \
      --grpc \
      --host 0.0.0.0 \
      --port 50051 \
      --backend pytorch \
      --extra_llm_api_options trtllm-config.yaml
    ```

=== "TokenSpeed"

    ```bash
    python -m smg_grpc_servicer.tokenspeed \
      --model meta-llama/Llama-3.1-8B-Instruct \
      --host 0.0.0.0 \
      --port 50051 \
      --grammar-backend xgrammar
    ```

    TokenSpeed's grammar backend defaults to none, so without `--grammar-backend xgrammar` it doesn't enforce forced tool calls or `response_format`. Add `--enable-output-logprobs` if clients request `logprobs`; TokenSpeed leaves output logprobs off by default.

    This starts the default Python servicer. Setting `SMG_TOKENSPEED_SERVICER_IMPL=rust` in the worker's environment serves the same contract from Rust instead; see [Rust TokenSpeed Servicer](#rust-tokenspeed-servicer).

    After the engine loads, the worker holds its gRPC health at `NOT_SERVING` while a planned warm-up runs over the engine. A one-process engine takes about a minute, as on the other engines; an engine of more than one process takes ten minutes or more, since the pass then also warms every prompt length up to the chunk size. `SMG_TOKENSPEED_SERVICER_WARMUP` tunes or disables it (see [Planned Warm-Up Before Serving](#planned-warm-up-before-serving)).

=== "MLX"

    ```bash
    python -m smg_grpc_servicer.mlx.server \
      --model mlx-community/Qwen3-0.6B-4bit \
      --host 0.0.0.0 \
      --port 50051
    ```

    MLX workers are gRPC-only and run on Apple Silicon. They don't support constrained decoding: chat requests with a forced `tool_choice` or a `response_format` get a 400, as do requests with `n` greater than 1 or string `stop` sequences.

### Planned Warm-Up Before Serving

vLLM, SGLang and TokenSpeed gRPC workers run a planned warm-up over the engine before they report `SERVING` on the standard gRPC health service (`grpc.health.v1`) — the signal Kubernetes gRPC startup and readiness probes read. Both the Python and Rust servicer implementations run the pass; MLX and TensorRT-LLM workers have no such pass, and a model that generates nothing (a pooling or embedding model) takes none.

TokenSpeed workers skip the pass on a disaggregation role. An encoder-only SGLang worker takes none either: the engine runs that role on its own encoder server, never on a servicer. A language-only SGLang worker runs the pass as on a single engine — its text requests carry no media, so the encoders see nothing of them — and on the Rust path the plan also ends with one image through the encoders (the media leg below). On an SGLang prefill or decode leg the Python servicer skips the pass, while the Rust path runs it as on a single engine: each request carries a stand-in transfer to nobody, so the leg warms up without its peer.

A worker whose health stays `NOT_SERVING` for a while after the engine loaded is warming up, not wedged. The pass logs `Planned warm-up starts (...): readiness is held until it ran`, one INFO line per shape with its time (TokenSpeed's chunk remainders log at DEBUG, with an INFO progress line every 500), and `Planned warm-up over: N of M steps covered ...` at the end.

Engines select and compile kernels lazily: the first scheduler step at a token count the engine has not seen pays a kernel search or a compile of up to several seconds, and every request in flight during that step waits with it. The warm-up visits those token counts before the worker reports `SERVING`; without it, the first traffic ramp pays them — on every worker of a fleet at once when the workers start together. The pass covers, in order:

- a **decode staircase**: as many concurrent requests as the running window holds, each asking for one more output token than the last, so every running-batch size runs once;
- a **prefill ladder**: one request per 128-token prompt-length class up to the engine's admission step (its chunk size or prefill token budget), plus the engine's graph-capture sizes and lengths one above the kernel tuners' bucket grid;
- a **second pass** over the bucket grid, whose logged timings show what the first pass paid;
- on a language-only SGLang worker on the Rust path, the **media leg**: one request carrying one image through the encoders — the engine's own minimal warm-up image, sent as a data URL inside the prompt the model's chat template renders around it — because no text request reaches the scheduler's media path, whose first use costs a fresh worker seconds once. A chat template that renders no image placeholder leaves the leg out (a logged line says why), and the leg runs at the `fast` level too;
- on a TokenSpeed engine of more than one process, the **chunk remainders**: every remaining prompt length up to the chunk size, because on this engine a prompt length's first use during serving can deadlock the forward pass under a collective still in flight. A one-process engine has no peer to wait on and plans the length classes alone, like the other engines. The remainders run last, in an order whose every prefix spreads evenly over the range, so a budget cut leaves even coverage rather than a missing tail.

Every request carries its own token IDs, so prefix caching never hides a prefill, and the pass flushes the engine's prefix cache at the end. A servicer that does not serve `FlushCache` (the Rust TokenSpeed servicer) refuses that flush, and the end line then says the warm-up's blocks stay cached.

The gateway's [health checks](../concepts/reliability/health-checks.md) read a different signal: each engine's own `HealthCheck` RPC, not the standard health service. While the pass runs, a Rust servicer mirrors the hold there — not healthy, with the message `Warming up`, distinct from `Draining` — and admits only the warm-up's own requests, refusing anything else with gRPC `UNAVAILABLE` ("servicer is warming up"), so the gateway neither finishes registering a warming worker nor routes traffic to it.

A Python servicer answers that RPC healthy throughout (vLLM's reports engine liveness; SGLang's and TokenSpeed's one-token probe is served like any other request) and refuses nothing, so it registers and passes the gateway's health checks while it warms: a request routed to it in that window is served, sharing the engine with the warm-up. On both implementations the hold is released when the pass ends, whatever it returned — health flips to `SERVING`, and a failed warm-up request is a logged line, never a worker kept out of rotation.

`SMG_VLLM_SERVICER_WARMUP`, `SMG_SGLANG_SERVICER_WARMUP` and `SMG_TOKENSPEED_SERVICER_WARMUP` tune the pass per engine:

| Value | Effect |
|-------|--------|
| `full` | The default: every 128-token length class — about a minute at a 16,384-token step. On a multi-process TokenSpeed engine, also the chunk remainders — about 12 minutes for the whole pass, measured at a 4,096-token chunk size on a four-GPU node |
| `fast` | The decode staircase, the graph-capture sizes and the tuners' bucket grid of prefill lengths, skipping the 128-token classes: seconds. On a multi-process TokenSpeed engine this also skips the chunk remainders and leaves their first-use hazard open, which the start log warns about |
| `off` | No planned warm-up: the worker reports `SERVING` once the engine is up and answering |
| A number (`120`) | The pass's time budget in seconds, at the `full` level |
| `<level>:<seconds>` (`fast:30`) | Level and budget together |

The budget defaults to 300 seconds — 900 for TokenSpeed, whose plan can hold the chunk remainders. When it runs out, the pass ends between shapes (the end line names the shape it stopped before), and each request's timeout is clamped to what is left of the budget (never below one second), so a shape that hangs cannot hold readiness past it. A multi-process TokenSpeed worker with no chunk size set plans about 23 minutes of remainders at its default 8,192-token step, and the budget then cuts the pass about halfway through them (evenly, thanks to the spread order): set a chunk size or a larger budget to cover every length.

A value the knob does not take fails a Rust-path worker at startup with an error naming the variable; a Python servicer logs a warning and serves without the warm-up.

Under data parallelism, a vLLM worker — and, on the Rust path, an SGLang worker — runs the staircase once per rank and every prefill shape on every rank at once, each request pinned to its rank, so the log's step count is per rank. The Python SGLang servicer sends no rank pin (the engine's own load balancing places each request), and a TokenSpeed worker routes each warm-up request to the least-loaded rank on both paths; either way each prefill shape warms one rank, the other ranks pay its first-time cost on their first request of that shape, and a logged warning says so.

### Rust vLLM Servicer

vLLM gRPC workers can serve the same gRPC contract from a Rust implementation instead of the default Python one. The switch lives in the `smg-grpc-servicer` package and needs no vLLM change: vLLM's gRPC launcher imports the package's servicer classes before it defines `serve_grpc`, and that import installs the switch over `serve_grpc`.

The selection requires SMG v1.12.0 or newer wheels and engine images.

Select the implementation with `--servicer-impl` on `vllm serve --grpc` — a flag the package adds to vLLM's parser as a `vllm.general_plugins` plugin — or with the `SMG_VLLM_SERVICER_IMPL` environment variable:

```bash
vllm serve meta-llama/Llama-3.1-8B-Instruct \
  --grpc \
  --host 0.0.0.0 \
  --port 50051 \
  --servicer-impl rust

# The environment variable selects it for python -m vllm.entrypoints.grpc_server too:
SMG_VLLM_SERVICER_IMPL=rust python -m vllm.entrypoints.grpc_server \
  --model meta-llama/Llama-3.1-8B-Instruct \
  --host 0.0.0.0 \
  --port 50051
```

When both are set, the flag wins: the parsed `--servicer-impl` is read ahead of the environment, and the decision is written back to `SMG_VLLM_SERVICER_IMPL` so the worker's own guard agrees with it. A `VLLM_PLUGINS` setting that leaves out the `smg-servicer` plugin only removes the flag; the environment variable works regardless.

In Rust mode, the vLLM engine runs headless in a spawned child process and the servicer talks to it over a same-host ZMQ connection; Python keeps only the process lifecycle. That ZMQ hop is inside the worker — the gateway still connects over `grpc://`, so this is not the [ZMQ direct backend](zmq-workers.md). The gateway cannot tell the two implementations apart.

With `smg serve`, select it with `--servicer-impl rust`. The flag defaults to `python`, is valid only with `--backend vllm --connection-mode grpc` (any other backend or connection mode is rejected at startup), and is authoritative: it sets `SMG_VLLM_SERVICER_IMPL=rust` in each worker's environment, and with `--servicer-impl python` it removes an `SMG_VLLM_SERVICER_IMPL` inherited from your shell.

```bash
smg serve \
  --backend vllm \
  --connection-mode grpc \
  --servicer-impl rust \
  --model meta-llama/Llama-3.1-8B-Instruct \
  --data-parallel-size 2
```

For the selection to take effect, the worker's environment needs:

- the `smg` wheel, which carries the Rust servicer binding (`smg.servicer.VllmGrpcServer`)
- `smg-grpc-servicer[vllm]`, which carries the switch and the Rust request path
- a vLLM whose gRPC launcher imports the package's servicer classes at module level — the stock shape, so an unmodified vLLM qualifies — or carries the check (`smg_grpc_servicer.vllm.resolve_servicer_impl`) in its own `serve_grpc`

Misconfiguration fails loudly instead of silently serving Python: `smg serve --servicer-impl rust` refuses to start when `smg-grpc-servicer` is not importable or the installed vLLM's launcher would not be switched, and when `SMG_VLLM_SERVICER_IMPL=rust` reaches a worker where the switch never ran (a launcher file executed directly as `__main__`, or one that didn't import the package's servicer before defining `serve_grpc`), the Python servicer itself refuses to start.

### Rust SGLang Servicer

SGLang gRPC workers have the same choice: a Rust implementation serving the same gRPC contract as the default Python servicer.

Like the vLLM switch, the selection requires SMG v1.12.0 or newer wheels and engine images.

Select the implementation with `--servicer-impl` on `python -m sglang.launch_server` — a flag the `smg-grpc-servicer` package adds to SGLang's parser as an `sglang.srt.plugins` plugin — or with the `SMG_SGLANG_SERVICER_IMPL` environment variable:

```bash
python -m sglang.launch_server \
  --model-path meta-llama/Llama-3.1-8B-Instruct \
  --host 0.0.0.0 \
  --port 50051 \
  --grpc-mode \
  --servicer-impl rust

# The environment variable selects it on the same entrypoint:
SMG_SGLANG_SERVICER_IMPL=rust python -m sglang.launch_server \
  --model-path meta-llama/Llama-3.1-8B-Instruct \
  --host 0.0.0.0 \
  --port 50051 \
  --grpc-mode
```

When both are set, the flag wins: `--servicer-impl python` overrides an exported `rust`. The decision a flag makes is written back to `SMG_SGLANG_SERVICER_IMPL`, so the processes the worker spawns inherit the same choice. The selection defaults to `python`, and a value other than `python` or `rust` fails startup. At startup the worker logs which implementation it chose and whether the flag, the environment or the default decided it.

`python -m sglang.launch_server --help` lists the flag. SGLang's `sglang serve` wrapper prints its help before plugins load, so the flag is missing from that listing but still accepted. A `SGLANG_PLUGINS` setting that leaves out the `smg-servicer` plugin only removes the flag; the environment variable works regardless.

`smg serve`'s `--servicer-impl` stays scoped to vLLM: it rejects `rust` for every other backend. Exporting the environment variable before `smg serve --backend sglang --connection-mode grpc` reaches its workers too, since worker environments inherit the shell's, and no `smg serve` flag clears an inherited value the way `--servicer-impl python` does for vLLM.

In Rust mode, the entrypoint hands the process to the Rust servicer (`smg.servicer.SglangGrpcServer`, which needs the `smg` wheel installed alongside `smg-grpc-servicer[sglang]`) before any Python gRPC machinery exists: the Rust server speaks the gRPC contract on the worker's host and port, the scheduler runs headless in a spawned child over a same-host msgpack ZMQ connection, and Python keeps only the process lifecycle. As with vLLM, that ZMQ hop is inside the worker — the gateway still connects over `grpc://`, so this is not the [ZMQ direct backend](zmq-workers.md), and the gateway cannot tell the two implementations apart.

What the scheduler's msgpack wire does not carry, the Rust path reports rather than emulates:

- Three launches refuse to start on the Rust path: an encoder-only worker (SGLang runs that role on its own encoder server, not on a servicer), a language-only worker on the `zmq_to_tokenizer` encoder transfer backend (the frontend itself receives and processes the embeddings there, which neither servicer runs), and a prefill leg of several plain data-parallel replicas — `--dp-size N` without attention DP — where every replica reports attention-DP rank 0 to the engine's KV sender, so the decode side cannot bootstrap against the requests the other replicas ran; run attention DP (`--tp-size N --attn-dp-size N`) or one rank per leg. Any other prefill or decode leg, and a language-only worker on the backends whose scheduler ranks receive the embeddings, is served as on the Python servicer.
- Multimodal inputs are refused per request on every worker but a language-only one, whose encode dispatch hands a request's image references to the encoders; hidden states are refused per request, and LoRA loading answers UNIMPLEMENTED (the Python servicer does not serve it either).
- SGLang's HTTP sidecar (profiling, `/metrics`) is not started.

`Embed`, `FlushCache` and profiling answer as on the Python servicer, and `SubscribeKvEvents` relays the ZMQ publisher SGLang was told to run with `--kv-events-config` (UNIMPLEMENTED when events are off).

### Rust TokenSpeed Servicer

TokenSpeed gRPC workers have the same choice: a Rust implementation serving the same gRPC contract as the default Python servicer.

Like the vLLM and SGLang switches, the selection requires SMG v1.12.0 or newer wheels and engine images.

One environment variable selects it on the unchanged entrypoint — every flag is still TokenSpeed's `ServerArgs`:

```bash
SMG_TOKENSPEED_SERVICER_IMPL=rust python -m smg_grpc_servicer.tokenspeed \
  --model meta-llama/Llama-3.1-8B-Instruct \
  --host 0.0.0.0 \
  --port 50051
```

`SMG_TOKENSPEED_SERVICER_IMPL` defaults to `python`, and a value other than `python` or `rust` fails startup. This launcher is also the process `ts serve` spawns for its gRPC worker. `smg serve` offers no path here: its `--servicer-impl` stays scoped to vLLM, and it drives TokenSpeed workers over `--connection-mode zmq` only — start the worker directly and connect it with `smg launch`.

In Rust mode, the entrypoint hands the process to the Rust servicer (`smg.servicer.TokenSpeedGrpcServer`, which needs the `smg` wheel installed alongside `smg-grpc-servicer`) before any TokenSpeed engine or Python gRPC machinery exists: the Rust server speaks the gRPC contract on the worker's host and port, the scheduler(s) run headless in a spawned child — TokenSpeed's own headless launch, what `ts serve --headless` runs — over a same-host msgpack ZMQ connection, and Python keeps only the process lifecycle. As with vLLM and SGLang, that ZMQ hop is inside the worker: the gateway still connects over `grpc://`, so this is not the [ZMQ direct backend](zmq-workers.md), and the gateway cannot tell the two implementations apart.

What the scheduler's msgpack wire does not carry, the Rust path reports rather than emulates:

- `FlushCache` and profiling answer UNIMPLEMENTED.
- Ranked `top_logprobs` and prompt logprobs are refused per request.
- PD and EPD disaggregation stay with the Python servicer.
- The [RL control plane](rl-control-plane.md) extras stay with the Python servicer: the `rl.*` capability advertisement, the live `weight_version` on generate responses, and `is_paused`.

Credentials are redacted from the `server_args` the worker reports exactly as on the Python servicer. `SubscribeKvEvents` relays the ZMQ KV-event publisher TokenSpeed was told to run with `--kv-events-config` (UNIMPLEMENTED when events are off).

Tuning environment variables: `SMG_TOKENSPEED_SERVICER_HANDSHAKE_PORT` (default: a free port) pins the port the headless scheduler dials, `SMG_TOKENSPEED_SERVICER_DRAIN_SECS` (default `5`) is how long in-flight streams get to finish after a shutdown signal, and `SMG_TOKENSPEED_SERVICER_STARTUP_TIMEOUT_SECS` (default `1800`) bounds the wait for the scheduler's handshake. Model load and kernel JIT compilation count against that bound, so raise it when a first start on a cold cache needs longer; a scheduler process that died is noticed on the supervisor's poll rather than waiting out the bound.

After the handshake, the [planned warm-up](#planned-warm-up-before-serving) runs before the worker reports `SERVING`; `SMG_TOKENSPEED_SERVICER_WARMUP` tunes or disables it.

---

## Connect SMG

Point SMG at the gRPC worker with a `grpc://` URL. SMG detects the engine on its own by probing for SGLang, vLLM, TensorRT-LLM, TokenSpeed and MLX in turn:

```bash
smg launch \
  --worker-urls grpc://localhost:50051 \
  --model-path meta-llama/Llama-3.1-8B-Instruct \
  --host 0.0.0.0 \
  --port 30000
```

!!! note "How the gateway finds the tokenizer"
    The gateway needs each model's tokenizer to apply chat templates, count tokens, and parse tool calls. When a worker registers, SMG loads the tokenizer from the `tokenizer_path` or `model_path` the worker reports (vLLM, SGLang, TokenSpeed and MLX report one), and falls back to `--tokenizer-path` or `--model-path`. If that path doesn't load on the gateway host, for example because it's a directory on the worker's machine, SMG fetches the tokenizer files from the worker over gRPC. TensorRT-LLM workers report no path, so pass `--model-path` or `--tokenizer-path` for them. `--disable-tokenizer-autoload` turns all of this off; tokenizers then come only from the [tokenizer API](../reference/api/admin.md).

!!! warning "The gateway-to-worker gRPC hop is plaintext"
    The gateway dials gRPC workers without TLS: a `grpcs://` URL fails to connect, and the Python launcher's client-certificate flags apply to HTTP workers only. To encrypt and authenticate this hop, run the gateway and the workers in a service mesh. See [Gateway to gRPC Workers](tls.md#gateway-to-grpc-workers).

The API is still OpenAI-compatible, so clients send the same requests as with HTTP workers:

```bash
curl http://localhost:30000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "meta-llama/Llama-3.1-8B-Instruct",
    "messages": [
      {"role": "user", "content": "Hello!"}
    ]
  }'
```

---

## Multiple gRPC Workers

```bash
smg launch \
  --worker-urls grpc://worker1:50051 grpc://worker2:50052 \
  --model-path meta-llama/Llama-3.1-8B-Instruct \
  --policy round_robin
```

---

## Reasoning Extraction

For thinking models (DeepSeek-R1, Qwen3, and others), SMG moves chain-of-thought content into a separate field:

```bash
smg launch \
  --worker-urls grpc://worker:50051 \
  --model-path deepseek-ai/DeepSeek-R1 \
  --reasoning-parser deepseek_r1
```

SMG picks the parser from the model name by default; `--reasoning-parser` overrides that for every model. Separation is on by default, so a plain request is enough:

```bash
curl http://localhost:30000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "deepseek-ai/DeepSeek-R1",
    "messages": [{"role": "user", "content": "What is 25 * 37?"}]
  }'
```

The response includes both fields:

```json
{
  "choices": [{
    "message": {
      "role": "assistant",
      "content": "925",
      "reasoning_content": "Let me calculate 25 * 37 step by step..."
    }
  }]
}
```

Send `"separate_reasoning": false` to keep the reasoning in `content`.

### Supported Reasoning Parsers

| Parser | Models |
|--------|--------|
| `deepseek_r1` | DeepSeek-R1 |
| `deepseek_v31` | DeepSeek-V3.1 |
| `deepseek_v4` | DeepSeek-V4 |
| `deepseek_v41` | DeepSeek-V4.1 |
| `qwen3` | Qwen3 and later hybrid-thinking Qwen models |
| `qwen3_thinking` | Qwen3 `-Thinking` checkpoints |
| `glm45` | GLM-4.5, GLM-4.7, GLM-5.x |
| `kimi` | Kimi-K2 (Instruct) |
| `kimi_thinking` | Kimi-K2-Thinking |
| `kimi_k25` | Kimi-K2.5 |
| `kimi_k3` | Kimi-K3 |
| `minimax` | MiniMax-M2 |
| `minimax_m3` | MiniMax-M3 |
| `nano_v3` | Nemotron Nano, Nemotron Super, Nemotron-3 family |
| `cohere_cmd` | Command-R, Command-A |
| `step3` | Step-3 |
| `inkling` | Inkling |

`base` and `passthrough` are also registered, but only for explicit use. The [gRPC Pipeline](../concepts/architecture/grpc-pipeline.md#reasoning-parsers) page lists the name patterns each parser is auto-detected from.

---

## Tool Calling

In gRPC mode, SMG parses function calls from model output:

```bash
smg launch \
  --worker-urls grpc://worker:50051 \
  --model-path meta-llama/Llama-3.2-3B-Instruct \
  --tool-call-parser llama
```

To have the gateway execute MCP tools on `/v1/responses`, add `--mcp-config-path`; see [MCP in Responses API](mcp.md).

### Supported Tool Call Parsers

| Parser | Models |
|--------|--------|
| `json` | GPT, Claude, Gemini, Gemma, and other Llama and GLM models that emit plain JSON |
| `llama` | Llama 3.2 |
| `pythonic` | Llama 4 |
| `mistral` | Mistral, Mixtral |
| `qwen` | Qwen2.5, Qwen3 |
| `qwen_xml` | Qwen3-Coder, Qwen3.5, Qwen3.6, Qwen3.8, Nemotron-3 family (aliases `qwen_coder`, `nemotron`) |
| `deepseek` | DeepSeek-V3 |
| `deepseek31` | DeepSeek-V3.1, DeepSeek-V3.2-Exp |
| `deepseek32` | DeepSeek-V3.2 |
| `deepseek_v4` | DeepSeek-V4 |
| `deepseek_v41` | DeepSeek-V4.1 |
| `glm45_moe` | GLM-4.5, GLM-4.6 |
| `glm47_moe` | GLM-4.7, GLM-5.x |
| `kimik2` | Kimi-K2 |
| `kimi_k3` | Kimi-K3 |
| `minimax_m2` | MiniMax-M2 |
| `minimax_m3` | MiniMax-M3 |
| `cohere` | Command-R, Command-A |
| `step3` | Step-3 |
| `sarashina` | Sarashina |
| `inkling` | Inkling |

`passthrough` (no parsing) is also registered. Formats and name patterns are in the [gRPC Pipeline](../concepts/architecture/grpc-pipeline.md#tool-call-parsers) reference.

### Per-Model Parsers

`--tool-call-parser` and `--reasoning-parser` apply to every model. When one gateway serves several model families, set parsers per model through the worker API instead. Labels on the worker registration override the flags for that worker's model:

```bash
curl -X POST http://localhost:30000/workers \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer ${ADMIN_TOKEN}" \
  -d '{
    "url": "grpc://glm-worker:50051",
    "labels": {"tool_parser": "glm47_moe", "reasoning_parser": "glm45"}
  }'
```

An unknown parser name fails the registration. See [Parser Selection](../concepts/architecture/grpc-pipeline.md#parser-selection) for the full precedence rules.

---

## gpt-oss (Harmony) Vocab

Serving gpt-oss models over gRPC uses the Harmony encoding, whose vocab
(`o200k_base.tiktoken`) is fetched when a gpt-oss worker registers — from a
local directory if `TIKTOKEN_ENCODINGS_BASE` is set, otherwise downloaded and
cached (`TIKTOKEN_RS_CACHE_DIR`, default `$TMPDIR/tiktoken-rs-cache`). If the
vocab can't be loaded, that worker's registration fails and retries; other
models are unaffected.

For air-gapped deployments, pre-seed the file and point SMG at it:

```bash
export TIKTOKEN_ENCODINGS_BASE=/opt/tiktoken   # contains o200k_base.tiktoken
```

---

## HTTP vs gRPC: When to Use Which

| Use Case | Recommended Mode |
|----------|-----------------|
| Workers already run OpenAI-compatible HTTP servers | HTTP |
| You need gateway-level tool parsing, reasoning extraction, or Responses MCP | gRPC |
| You serve several model families and want parsers chosen per model | gRPC |
| You want cache-aware routing on token IDs | gRPC |
| Simplest possible setup | HTTP |

---

## Troubleshooting

| Symptom | Cause | Solution |
|---------|-------|----------|
| 404 `model_not_found` | No registered worker serves the requested model name | Check `GET /v1/models` and send a served model name or alias |
| 503 `no_available_workers` | Every worker for the model is unhealthy or has an open circuit breaker | Check worker health and logs |
| A `grpcs://` worker never registers; the log repeats `grpc health check failed ... gRPC connection failed: transport error` | The gateway's gRPC channel has no TLS | Use `grpc://`, and a service mesh when the hop must be encrypted ([Gateway to gRPC Workers](tls.md#gateway-to-grpc-workers)) |
| 503 `tokenizer_not_ready` | The model's tokenizer is still loading (the gateway loads it after the worker registers) | Retry; the response carries `Retry-After: 1`, and `/readiness` reports the same state |
| 503 `tokenizer_load_failed` | The model's tokenizer won't load on its own — no `tokenizer_path` or `model_path` to load from, the load failed, or the tokenizer was removed; the message names the reason, and the gateway log has the details | Pass `--model-path` or set a `tokenizer_path` label and re-register the worker, or register a tokenizer with `POST /v1/tokenizers`; retrying the request changes nothing until a tokenizer registers |
| 500 `tokenizer_not_found` | The model has no tokenizer and `--disable-tokenizer-autoload` is set, so the gateway won't load one itself | Register a tokenizer with `POST /v1/tokenizers` |
| Tool calls or reasoning left in `content` | No parser matched the model name | Set `--tool-call-parser` / `--reasoning-parser` or a per-model label |

---

## Next Steps

- [gRPC Pipeline Concepts](../concepts/architecture/grpc-pipeline.md) — Pipeline architecture, parser selection, and all supported parsers
- [ZMQ Workers](zmq-workers.md) — Same-host vLLM, TokenSpeed, and SGLang engines over the ZMQ direct backend
- [Multimodal Pipeline](../concepts/architecture/multimodal.md) — Image and video processing on the router or the worker
- [Tokenizer Caching](../concepts/performance/tokenizer-caching.md) — Two-level cache for reduced CPU overhead
- [MCP in Responses API](mcp.md) — Configure Model Context Protocol servers for `/v1/responses`
- [PD Disaggregation](pd-disaggregation.md) — Separate prefill and decode with gRPC workers
