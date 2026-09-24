---
title: Multiple Workers
---

# Multiple Workers

SMG can route across many workers simultaneously — local inference servers, remote cloud APIs, or a mix of both. This guide covers how to add workers and balance traffic across them.

<div class="prerequisites" markdown>

#### Before you begin

- Completed the [Getting Started](index.md) guide

</div>

## Supported Worker Types

SMG connects to workers over HTTP, gRPC, or ZMQ, and supports both local inference servers and remote API providers:

| Worker Type | Protocol | Example URL |
|-------------|----------|-------------|
| vLLM | HTTP / gRPC / ZMQ | `http://worker:8000`, `grpc://worker:50051`, or `ipc:///tmp/smg-zmq/engine-0` |
| TensorRT-LLM | gRPC | `grpc://worker:50051` |
| TokenSpeed | gRPC / ZMQ | `grpc://worker:50051` or `ipc:///tmp/smg-zmq/engine-0` |
| SGLang | HTTP / gRPC | `http://worker:8000` or `grpc://worker:50051` |
| MLX (Apple Silicon) | gRPC | `grpc://worker:50051` |
| OpenAI (GPT) | HTTP | `https://api.openai.com` |
| Anthropic (Claude) | HTTP | `https://api.anthropic.com` |
| xAI (Grok) | HTTP | `https://api.x.ai` |
| Google (Gemini) | HTTP | `https://generativelanguage.googleapis.com` |
| Any OpenAI-compatible API | HTTP | `https://your-provider.com` |

ZMQ workers (`ipc://`) run on the same host as SMG, which connects directly to the engine core with no engine API server in between. See [ZMQ Direct Workers](zmq-workers.md).

## Static Workers via CLI

Pass multiple URLs to `--worker-urls`:

```bash
smg \
  --worker-urls http://worker1:8000 http://worker2:8000 http://worker3:8000 \
  --policy round_robin \
  --host 0.0.0.0 \
  --port 30000
```

For gRPC workers, use the `grpc://` scheme and provide `--model-path` so the gateway can load the tokenizer:

```bash
smg \
  --worker-urls grpc://worker1:50051 grpc://worker2:50052 \
  --model-path meta-llama/Llama-3.1-8B-Instruct \
  --policy round_robin
```

See [gRPC Workers](grpc-workers.md) for details on what gRPC mode enables.

## Cloud API Workers

Route to a cloud provider by setting `--backend` to its router and passing the provider's base URL, without a `/v1` suffix. Callers send their own provider key with each request, and SMG forwards it upstream (bring your own key). SMG does not read provider key variables such as `OPENAI_API_KEY`.

=== "OpenAI"

    ```bash
    smg \
      --backend openai \
      --worker-urls https://api.openai.com \
      --host 0.0.0.0 \
      --port 30000
    ```

    Serves `/v1/chat/completions`, `/v1/responses`, and the Realtime API. Callers send `Authorization: Bearer <OpenAI key>`.

=== "Anthropic"

    ```bash
    smg \
      --backend anthropic \
      --worker-urls https://api.anthropic.com \
      --host 0.0.0.0 \
      --port 30000
    ```

    Serves the Messages API (`/v1/messages`). SMG forwards the caller's `x-api-key` and `anthropic-version` headers as sent.

=== "xAI (Grok)"

    ```bash
    smg \
      --backend openai \
      --worker-urls https://api.x.ai \
      --host 0.0.0.0 \
      --port 30000
    ```

    Serves the same endpoints as OpenAI. Callers send `Authorization: Bearer <xAI key>`.

=== "Gemini"

    ```bash
    smg \
      --backend gemini \
      --worker-urls https://generativelanguage.googleapis.com \
      --host 0.0.0.0 \
      --port 30000
    ```

    Serves the Interactions API (`/v1/interactions`): non-streaming requests with `"store": false` in v1.11.0. Callers send `x-goog-api-key` (or `Authorization: Bearer <key>`). `--backend gemini` belongs to the Rust `smg` binary; the Python launcher does not accept it.

The OpenAI-compatible router (`--backend openai`) also adjusts each request for the provider that serves its model, detected from the model name (for example, xAI handling for `grok*` models).

A worker can also carry a stored key: pass `api_key` when you [add a worker](#add-a-worker) through the API, or set `--api-key`, which the `--worker-urls` workers take as their key. With `--api-key` set, SMG lists the provider's models with that key when it registers the worker (unless the provider's admin key variable, such as `OPENAI_ADMIN_KEY`, is set), and callers must authenticate to the gateway with it, which puts it in the `Authorization` header that SMG forwards upstream. See [External Providers](external-providers.md#api-key-handling) for how stored and caller keys combine.

## Dynamic Workers with IGW Mode

The worker API (`POST /workers`, `GET /workers`, `DELETE /workers/{worker_id}`) is available in every mode. Inference Gateway (IGW) mode (`--enable-igw`) also runs every router at once (HTTP and gRPC, regular and disaggregated, plus the provider routers) and picks one per request from the workers that serve the requested model, so one gateway can mix worker types. Start it without `--worker-urls` and add workers at runtime:

```bash
smg --enable-igw --host 0.0.0.0 --port 30000
```

### Add a worker

```bash
curl -X POST http://localhost:30000/workers \
  -H "Content-Type: application/json" \
  -d '{"url": "http://worker1:8000"}'
```

Response (`202 Accepted`, with a `Location` header):

```json
{
  "status": "accepted",
  "worker_id": "0199a3c2-7f1e-7c52-9b1e-5d7c3a2f4e10",
  "url": "http://worker1:8000",
  "location": "/workers/0199a3c2-7f1e-7c52-9b1e-5d7c3a2f4e10",
  "message": "Worker addition queued for background processing"
}
```

The `worker_id` is a UUID. SMG registers the worker in the background: `GET /workers` lists it once registration finishes, and until then `GET /workers/{worker_id}` (the `location`) shows the job's status.

### List workers

```bash
curl http://localhost:30000/workers
```

### Remove a worker

```bash
curl -X DELETE http://localhost:30000/workers/{worker_id}
```

### Worker configuration options

`POST /workers` takes a worker spec. Only `url` is required; the gateway probes the worker and discovers the rest. A spec with commonly used options:

```json
{
  "url": "http://worker:8000",
  "runtime_type": "vllm",
  "api_key": "optional-key",
  "labels": {"region": "us-east"},
  "health": {"check_interval_secs": 30},
  "overload": {"token_usage": 0.9}
}
```

| Field | Default | Description |
|-------|---------|-------------|
| `url` | (required) | Worker URL. The scheme picks the transport: `http://` or `https://`, `grpc://` or `grpcs://`, or `ipc://` for a same-host [ZMQ worker](zmq-workers.md) |
| `runtime_type` | (auto-detect) | Engine: `sglang`, `vllm`, `trtllm`, `mlx`, `tokenspeed`, `generic`, or `external`. Also accepted as `runtime` |
| `worker_type` | `regular` | `regular`, `prefill`, `decode`, or `encode` |
| `models` | `[]` | Model cards, such as `[{"id": "meta-llama/Llama-3.1-8B-Instruct"}]`. Empty: the gateway uses the model the worker reports (ZMQ workers fall back to `--model-path`) |
| `api_key` | — | API key for authenticated workers. Never returned by `GET /workers` |
| `labels` | `{}` | String metadata, such as `realtime: "true"` (see [Realtime-capable workers](#realtime-capable-workers)) |
| `health` | gateway settings | Per-worker [health check](../concepts/reliability/health-checks.md) overrides, such as `check_interval_secs` or `drain_settle_secs` |
| `overload` | gateway settings | Per-worker [overload protection](../concepts/reliability/overload-protection.md) thresholds: `waiting_requests`, `token_usage` |
| `http_pool` | gateway settings | HTTP client overrides, such as `{"http2": true}` to pin HTTP/2 |

The [Admin API reference](../reference/api/admin.md#worker-spec) lists every field, the validation rules, and how `PATCH` and `PUT` change a registered worker.

### Realtime-capable workers

To route the [Realtime API](../reference/api/openai.md#realtime-api) — the WebSocket
`/v1/realtime` endpoint, WebRTC `/v1/realtime/calls`, and the realtime REST endpoints —
through the HTTP router to a **local** worker, mark that worker with the well-known
`realtime` label. Only workers labeled `realtime: "true"` receive realtime traffic, so
SMG never proxies a realtime connection to a worker that can't serve it.

The worker must itself expose an OpenAI-compatible realtime endpoint. For example, vLLM
serving a speech model with the realtime task (such as `Qwen/Qwen3-ASR-1.7B`) exposes
`ws://<worker>/v1/realtime` for streaming transcription.

```bash
curl -X POST http://localhost:30000/workers \
  -H "Content-Type: application/json" \
  -d '{
    "url": "http://asr-worker:8000",
    "runtime": "vllm",
    "labels": {"realtime": "true"}
  }'
```

The same worker also serves batch transcription via `POST /v1/audio/transcriptions`,
which the HTTP router forwards without requiring the `realtime` label.

## Verify

```bash
# List connected workers
curl http://localhost:30000/workers

# Check health
curl http://localhost:30000/health

# Send a request
curl http://localhost:30000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "meta-llama/Llama-3.1-8B-Instruct",
    "messages": [{"role": "user", "content": "Hello!"}]
  }'
```

## Next Steps

- [Monitoring](monitoring.md) — Track request rates, latency, and worker health
- [gRPC Workers](grpc-workers.md) — Enable tokenization, chat templates, and tool parsing at the gateway
- [PD Disaggregation](pd-disaggregation.md) — Separate prefill and decode onto specialized workers
