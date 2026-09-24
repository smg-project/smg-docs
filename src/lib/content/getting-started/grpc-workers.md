---
title: gRPC Workers
---

# gRPC Workers

When workers connect via gRPC instead of HTTP, SMG becomes a full OpenAI-compatible server: it handles tokenization, chat templates, reasoning extraction, and tool calling at the gateway level. Workers run raw inference only.

For vLLM or TokenSpeed engines on the same host as the gateway, the [ZMQ direct backend](zmq-workers.md) is an alternative: it runs the same gateway pipeline over local IPC sockets.

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

=== "SGLang"

    ```bash
    python -m sglang.launch_server \
      --model-path meta-llama/Llama-3.1-8B-Instruct \
      --host 0.0.0.0 \
      --port 50051 \
      --smg-grpc-mode
    ```

    `--smg-grpc-mode` needs SGLang 0.5.16 or later; older releases use `--grpc-mode`, now a deprecated alias. In this mode SGLang also opens an HTTP sidecar on `--port + 1` (move it with `--smg-http-sidecar-port`).

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

=== "MLX"

    ```bash
    python -m smg_grpc_servicer.mlx.server \
      --model mlx-community/Qwen3-0.6B-4bit \
      --host 0.0.0.0 \
      --port 50051
    ```

    MLX workers are gRPC-only and run on Apple Silicon. They don't support constrained decoding: chat requests with a forced `tool_choice` or a `response_format` get a 400, as do requests with `n` greater than 1 or string `stop` sequences.

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
    The gateway needs each model's tokenizer to apply chat templates, count tokens, and parse tool calls. When a worker registers, SMG loads the tokenizer from the `tokenizer_path` or `model_path` the worker reports (vLLM, SGLang and MLX report one), and falls back to `--tokenizer-path` or `--model-path`. If that path doesn't load on the gateway host, for example because it's a directory on the worker's machine, SMG fetches the tokenizer files from the worker over gRPC. TensorRT-LLM and TokenSpeed workers report no path, so pass `--model-path` for them. `--disable-tokenizer-autoload` turns all of this off; tokenizers then come only from the [tokenizer API](../reference/api/admin.md).

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
| 500 `tokenizer_not_found` | The model's tokenizer didn't load | Check the gateway logs; pass `--model-path` or set a `tokenizer_path` label |
| Tool calls or reasoning left in `content` | No parser matched the model name | Set `--tool-call-parser` / `--reasoning-parser` or a per-model label |

---

## Next Steps

- [gRPC Pipeline Concepts](../concepts/architecture/grpc-pipeline.md) — Pipeline architecture, parser selection, and all supported parsers
- [ZMQ Workers](zmq-workers.md) — Same-host vLLM and TokenSpeed engines over the ZMQ direct backend
- [Multimodal Pipeline](../concepts/architecture/multimodal.md) — Image and video processing on the router or the worker
- [Tokenizer Caching](../concepts/performance/tokenizer-caching.md) — Two-level cache for reduced CPU overhead
- [MCP in Responses API](mcp.md) — Configure Model Context Protocol servers for `/v1/responses`
- [PD Disaggregation](pd-disaggregation.md) — Separate prefill and decode with gRPC workers
