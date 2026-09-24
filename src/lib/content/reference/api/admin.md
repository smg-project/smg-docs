# Admin API Reference

SMG provides administrative endpoints for managing tokenizers, workers, cache, and cluster operations.

!!! tip "Related Documentation"
    For health checks, worker status, and monitoring endpoints, see [Gateway Extensions](extensions.md).

---

## Tokenizer Management

Manage tokenizers for text processing and tokenization.

!!! note "Authentication Required"
    These endpoints require admin authentication via API key or control plane credentials.

### Add Tokenizer

```
POST /v1/tokenizers
```

Adds a new tokenizer from a local path or HuggingFace model ID.

**Request Body:**
```json
{
  "name": "llama3-tokenizer",
  "source": "meta-llama/Meta-Llama-3-8B",
  "chat_template_path": "/path/to/template.jinja"
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | Yes | Unique tokenizer identifier |
| `source` | string | Yes | HuggingFace model ID or local path |
| `chat_template_path` | string | No | Path to custom Jinja2 chat template |

**Response:** `202 Accepted`
```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "pending",
  "message": "Tokenizer 'llama3-tokenizer' registration job submitted. Loading from: meta-llama/Meta-Llama-3-8B"
}
```

---

### List Tokenizers

```
GET /v1/tokenizers
```

Returns all registered tokenizers.

**Response:** `200 OK`
```json
{
  "tokenizers": [
    {
      "id": "550e8400-e29b-41d4-a716-446655440000",
      "name": "llama3-tokenizer",
      "source": "meta-llama/Meta-Llama-3-8B",
      "vocab_size": 128256
    }
  ]
}
```

---

### Get Tokenizer

```
GET /v1/tokenizers/{tokenizer_id}
```

Returns details for a specific tokenizer.

**Response:** `200 OK`
```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "name": "llama3-tokenizer",
  "source": "meta-llama/Meta-Llama-3-8B",
  "vocab_size": 128256
}
```

**Response:** `404 Not Found`
```json
{
  "error": {
    "message": "Tokenizer 'llama3-tokenizer' not found",
    "type": "tokenizer_not_found"
  }
}
```

---

### Get Tokenizer Status

```
GET /v1/tokenizers/{tokenizer_id}/status
```

Returns the loading status of a tokenizer.

**Response:** `200 OK`
```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "completed",
  "message": "Tokenizer 'llama3-tokenizer' is loaded and ready",
  "vocab_size": 128256
}
```

| Status | Description |
|--------|-------------|
| `pending` | Tokenizer loading queued |
| `processing` | Tokenizer currently loading |
| `completed` | Tokenizer ready for use |
| `failed` | Loading failed (see message) |

---

### Remove Tokenizer

```
DELETE /v1/tokenizers/{tokenizer_id}
```

Removes a tokenizer.

**Response:** `200 OK`
```json
{
  "success": true,
  "message": "Tokenizer 'llama3-tokenizer' removed successfully"
}
```

---

## Worker Management

Register, inspect, update, and remove backend workers at runtime.

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/workers` | [Register a worker](#create-worker) |
| `GET` | `/workers` | [List workers](#list-workers) |
| `GET` | `/workers/{worker_id}` | [Get one worker and its job status](#get-worker) |
| `PATCH` | `/workers/{worker_id}` | [Change priority, cost, labels, API key, or health settings](#update-worker-partial) |
| `PUT` | `/workers/{worker_id}` | [Replace the spec and re-run registration](#replace-worker-full) |
| `DELETE` | `/workers/{worker_id}` | [Drain and remove a worker](#delete-worker) |

`worker_id` is the UUID that `POST /workers` returns. Changes are asynchronous: the gateway checks the request, queues a job, and answers `202 Accepted`. Follow the job with `GET /workers/{worker_id}`.

!!! note "Authentication"
    These are control-plane routes. With [control-plane auth](../../getting-started/control-plane-auth.md) configured, they need a credential with the admin role (`401` without a valid one, `403` for other roles). Otherwise they need `Authorization: Bearer <key>` with the gateway `--api-key`. Per-tenant keys are not accepted, so a gateway with tenant keys but no `--api-key` answers `401` to every call. With no keys configured at all, the routes are open.

### Create Worker

```
POST /workers
```

Registers a worker. The body is a [worker spec](#worker-spec); only `url` is required. The URL scheme selects the transport:

| Scheme | Transport | Backends |
|--------|-----------|----------|
| `http://`, `https://` | HTTP | SGLang, vLLM, or other OpenAI-compatible HTTP servers, and external providers. Hosts ending in `openai.com`, `anthropic.com`, `x.ai`, or `googleapis.com` register as external automatically. |
| `grpc://`, `grpcs://` | gRPC | SGLang, vLLM, TensorRT-LLM, TokenSpeed, or MLX gRPC servers. See [gRPC Workers](../../getting-started/grpc-workers.md). |
| `ipc://<path>` | ZMQ | A vLLM or TokenSpeed engine core on the same host. See [ZMQ Workers](../../getting-started/zmq-workers.md). |

The scheme must be lowercase. Send the spec as JSON:

```bash
curl -X POST http://localhost:30000/workers \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer ${ADMIN_TOKEN}" \
  -d @worker.json
```

=== "HTTP worker"

    ```json
    {
      "url": "http://gpu1:8000",
      "api_key": "worker-secret-key",
      "labels": {"region": "us-east"}
    }
    ```

    The gateway probes the worker, detects the engine, and reads the served model from it.

=== "gRPC worker"

    ```json
    {
      "url": "grpc://gpu2:50051",
      "runtime_type": "sglang",
      "models": [
        {
          "id": "meta-llama/Llama-3.1-8B-Instruct",
          "aliases": ["llama-3.1-8b"],
          "tool_parser": "llama"
        }
      ]
    }
    ```

    An explicit `runtime_type` skips engine detection. The model card adds an alias and pins the tool-call parser for this model.

=== "ZMQ worker"

    ```json
    {
      "url": "ipc:///tmp/smg-zmq/engine-31000",
      "runtime_type": "vllm",
      "labels": {"model_path": "Qwen/Qwen3-8B"}
    }
    ```

    The gateway binds the sockets and the engine dials in. For this path the handshake listens on `tcp://127.0.0.1:22714`, a port derived from the `ipc://` path. An engine core does not report a model name, so a ZMQ worker needs `models`, a model label such as `model_path` (which also locates the tokenizer), or a gateway started with `--model-path`. For a group of data-parallel engines or a fixed handshake address, see the ZMQ fields under [Worker Spec](#worker-spec).

=== "Overload and HTTP/2 overrides"

    ```json
    {
      "url": "http://gpu3:8000",
      "runtime_type": "vllm",
      "overload": {
        "waiting_requests": 32,
        "token_usage": 0.85
      },
      "http_pool": {
        "http2": true,
        "connect_timeout_secs": 5
      },
      "health": {
        "drain_settle_secs": 30
      }
    }
    ```

    This worker leaves routing while 32 or more requests wait in its queue or its KV cache is at least 85% used, even if gateway-wide overload protection is off. The gateway always speaks HTTP/2 (h2c) to it instead of negotiating the version, so registration fails if the worker only speaks HTTP/1.1. When deleted while `ready`, it drains for 30 seconds.

**Response:** `202 Accepted`, with a `Location` header equal to `location`.
```json
{
  "status": "accepted",
  "worker_id": "01997a4e-3c2b-7f1d-9a8e-2b6c4d5e7f80",
  "url": "http://gpu1:8000",
  "location": "/workers/01997a4e-3c2b-7f1d-9a8e-2b6c4d5e7f80",
  "message": "Worker addition queued for background processing"
}
```

`202` means the request passed the checks listed under [Worker Errors](#worker-errors). Probing, engine detection, and the remaining validation run in the background job; [Get Worker](#get-worker) shows how to follow it.

---

### Worker Spec

The body of `POST /workers` and `PUT /workers/{worker_id}`. Only `url` is required. The nested blocks (`health`, `overload`, `http_pool`, `resilience`) are partial: a field you leave out falls back to the gateway default. Unknown fields are ignored rather than rejected, so check the spelling. A known field with the wrong type, or an unknown enum value, is rejected with `422`.

**Identity and transport**

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `url` | string | required | Worker address. The scheme selects the transport (see [Create Worker](#create-worker)). Each URL can be registered once. |
| `runtime_type` | string | `unspecified` | `sglang`, `vllm`, `trtllm`, `mlx`, `tokenspeed`, `generic` (an OpenAI-compatible HTTP server whose engine is unknown), or `external` (a third-party API, required for a provider behind a host the gateway does not recognize). `unspecified` detects the engine: over HTTP from `/v1/models`, `/version`, and `/server_info`, registering an unidentified OpenAI-compatible server as `generic`; over gRPC by trying `sglang`, `vllm`, `trtllm`, `tokenspeed`, then `mlx`. ZMQ workers default to `vllm`. Also accepted as `runtime`. |
| `worker_type` | string | `regular` | `regular`, `prefill`, `decode`, or `encode` (EPD encode worker). See [PD Disaggregation](../../concepts/routing/pd-disaggregation.md). |
| `api_key` | string | none | Key the gateway presents to the worker. Write-only: never returned. Workers added through the API do not inherit the gateway `--api-key`. For provider keys, see [External Providers](../../getting-started/external-providers.md). |

**Models and labels**

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `models` | array | `[]` | Model cards the worker serves (fields below). A self-hosted worker uses the first card. With none, it takes the model the engine reports: the `served_model_name`, `model_id`, or `model_path` label, in that order. External workers ignore this field: the gateway lists the provider's models when it has a key (`api_key`, or the provider's `*_ADMIN_KEY` environment variable), and without one the worker accepts any model. |
| `labels` | object | `{}` | String-to-string metadata, merged over the labels the gateway discovers from the engine (your values win). Some keys have built-in meaning (see below). |

Model card fields (`models[]`):

| Field | Description |
|-------|-------------|
| `id` | Model ID (required). |
| `aliases` | Other model names that route to this model. |
| `model_type` | Capabilities, any of `chat`, `completions`, `responses`, `embeddings`, `rerank`, `generate`, `vision`, `tools`, `reasoning`, `image_gen`, `audio`, `moderation`. Default: `["chat", "completions", "responses", "tools"]`. |
| `context_length` | Context window in tokens. |
| `chat_template` | Chat template path used when the gateway loads the tokenizer for a gRPC or ZMQ worker. |
| `tool_parser`, `reasoning_parser` | Parser names for this model on the gRPC path, overriding `--tool-call-parser` and `--reasoning-parser`. An unknown name fails registration. |

Cards also accept `display_name`, `hf_model_type`, `architectures`, `provider`, `tokenizer_path`, `metadata`, `id2label`, and `num_labels`.

Labels with built-in meaning:

| Label | Effect |
|-------|--------|
| `realtime` | `"true"` (exactly) lets the worker serve the [Realtime API](openai.md#realtime-api). |
| `tool_parser`, `reasoning_parser` | Same as the model card fields, when the card does not set them. |
| `served_model_name`, `model_id`, `model_path` | Model ID when `models` is empty, checked in this order. |
| `tokenizer_path`, `model_path` | Where the gateway loads the tokenizer for a gRPC or ZMQ worker, ahead of `--tokenizer-path` and `--model-path`. |
| `pairing_protocol` | Explicit PD pairing key: a prefill and a decode worker that both set it pair only when the values match. |
| `kv_connector`, `kv_role`, `kv_engine_id` | Moved into the spec fields of the same name at registration (a non-empty `kv_connector` or `kv_engine_id` field wins over the label). |

**Routing metadata**

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `priority` | integer | `50` | Stored and returned in responses. No built-in routing policy reads it in v1.11.0. |
| `cost` | number | `1.0` | Stored and returned in responses. No built-in routing policy reads it in v1.11.0. |

**Health checks (`health`)**: per-worker overrides of the gateway health-check flags. See [Health Checks](../../concepts/reliability/health-checks.md).

| Field | Gateway default | Description |
|-------|-----------------|-------------|
| `timeout_secs` | `--health-check-timeout-secs` (5) | Probe timeout, also used for the registration probes. |
| `check_interval_secs` | `--health-check-interval-secs` (60) | Seconds between probes. |
| `success_threshold` | `--health-success-threshold` (2) | Consecutive successful probes before the worker becomes `ready`. |
| `failure_threshold` | `--health-failure-threshold` (3) | Consecutive failed probes before the worker leaves rotation. |
| `disable_health_check` | `--disable-health-check` (off); `true` for external workers | Skip probing: the worker is routable as soon as it registers. Ignored for ZMQ workers. |
| `drain_settle_secs` | `--drain-settle-secs` (5) | Seconds a `ready` worker stays `draining` when it is removed. `0` removes it immediately. |

**Overload thresholds (`overload`)**: self-hosted workers only. See [Overload Protection](../../concepts/reliability/overload-protection.md).

| Field | Type | Gateway default | Description |
|-------|------|-----------------|-------------|
| `waiting_requests` | integer, at least 1 | `--worker-overload-waiting-requests` | Waiting requests, summed across DP ranks, at or above which the worker leaves routing. |
| `token_usage` | number in (0.0, 1.0] | `--worker-overload-token-usage` (0.9 under `--worker-overload-protection`) | Mean KV-cache usage across DP ranks at or above which the worker leaves routing. |

Either field turns overload protection on for this worker, even when the gateway flags leave it off; a field you leave out uses the gateway value. The worker returns to routing once a load report is below both thresholds. An out-of-range value fails registration. `PATCH` cannot change this block; use `PUT`.

**HTTP client (`http_pool`)**: applies to HTTP workers, including external providers. See [Request Streaming](../../concepts/performance/request-streaming.md).

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `http2` | boolean | `--upstream-http2` (off) | `true`: always speak HTTP/2 with prior knowledge (h2c on `http://`); registration fails if the worker only speaks HTTP/1.1. `false`: no HTTP/2 prior knowledge, so an `http://` worker stays on HTTP/1.1. Unset: under `--upstream-http2`, the gateway probes an `http://` worker with both versions and prefers HTTP/2; without the flag, an `http://` worker uses HTTP/1.1. Responses report the result as `http2`. |
| `pool_max_idle_per_host` | integer | `500` | Idle connections kept per host. |
| `pool_idle_timeout_secs` | integer | `--upstream-pool-idle-timeout-secs` (3) | How long an idle connection is kept. Keep it below the engine's keep-alive timeout; `0` keeps idle connections forever. |
| `timeout_secs` | integer | `--request-timeout-secs` (1800) | Default request timeout. |
| `connect_timeout_secs` | integer | `10` | Connection timeout. |

**Resilience (`resilience`)**: per-worker circuit-breaker settings. See [Circuit Breakers](../../concepts/reliability/circuit-breakers.md).

| Field | Type | Gateway default | Description |
|-------|------|-----------------|-------------|
| `cb_failure_threshold` | integer | `--cb-failure-threshold` (10) | Failures that open this worker's circuit. |
| `cb_success_threshold` | integer | `--cb-success-threshold` (3) | Successes in the half-open state that close it. |
| `cb_timeout_secs` | integer | `--cb-timeout-duration-secs` (60) | Seconds before an open circuit tries half-open. |
| `cb_window_secs` | integer | `--cb-window-duration-secs` (120) | Accepted but unused: the circuit breaker counts consecutive failures, not failures in a window. |
| `retryable_status_codes` | integer array | `[408, 429, 500, 502, 503, 504]` | Statuses counted as circuit-breaker failures. Replaces the default set; it does not change which responses are retried. |
| `capacity_status_codes` | integer array | `[429]` | Capacity-pushback statuses, which the circuit breaker never counts, as either failure or success. Replaces the default set. |

**ZMQ workers**: see [ZMQ Workers](../../getting-started/zmq-workers.md).

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `dp_size` | integer | none | On an `ipc://` worker, the number of data-parallel engines that dial into its one socket set. A value above 1 makes a grouped worker, and the gateway spreads requests across the group's engines. On other workers the gateway sets this field itself during data-parallel discovery. |
| `zmq_handshake_address` | string | derived | `tcp://` address the gateway binds for the engine handshake. The default is `tcp://127.0.0.1:<port>`, with the port (20000 to 29999) derived from the `ipc://` path. Set it for an engine that dials a fixed address, such as `tcp://127.0.0.1:30500`, TokenSpeed's default. |

Registration of a ZMQ worker fails when the runtime is not `vllm` or `tokenspeed`, when `worker_type` is not `regular` (disaggregated workers need gRPC), when no model ID is available, when `dp_size` is above 1 on a gateway running with `--dp-aware`, or when the handshake address is not `tcp://` or is already bound by another ZMQ worker. Setting `zmq_handshake_address` on a non-ZMQ worker also fails registration. Health checks stay on for ZMQ workers, because the probe is what reconnects a restarted engine.

**PD disaggregation and KV transfer**: see [PD Disaggregation](../../concepts/routing/pd-disaggregation.md).

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `bootstrap_port` | integer | none | KV bootstrap port of a `prefill` or `encode` worker. The gRPC and vLLM Mooncake paths use `8998` when it is unset. |
| `kv_connector` | string | engine-reported | KV connector, such as `MooncakeConnector` or `NixlConnector`. Overrides the value a vLLM gRPC engine reports. |
| `kv_engine_id` | string | engine-reported | vLLM `kv_transfer_config.engine_id`, used for Mooncake PD. For gRPC prefill and decode workers, the gateway reads it from the engine again when the worker recovers from `failed` or `not_ready` (a restarted engine gets a new ID), and the engine's value wins. |

`health`, `http_pool`, and `resilience` take effect but are not echoed back by `GET /workers`; `overload` is.

!!! warning "Accepted but not applied"
    v1.11.0 accepts these fields without an error, but a worker registered through this API does not use them:

    - `connection_mode`: the URL scheme decides the transport, and responses report the result (`http`, `grpc`, or `zmq`).
    - `provider`: only checked against the provider routers compiled into the build (see `PROVIDER_NOT_COMPILED` under [Worker Errors](#worker-errors)); it is not kept on the worker. For a provider behind a host the gateway does not recognize, set `runtime_type` to `external`.
    - `kv_role`: set a `kv_role` label instead. vLLM gRPC engines report it themselves.
    - `pairing_protocol`: set a `pairing_protocol` label instead, or `SMG_PAIRING_PROTOCOL` in the engine's environment for gRPC engines.
    - `multimodal_tensor_transport` and `multimodal_shm_min_bytes`: use the gateway-wide `--multimodal-tensor-transport` and `--multimodal-shm-min-bytes` (see [Multimodal](../../concepts/architecture/multimodal.md)).
    - `load_monitor_interval_secs`: use `--load-monitor-interval`.
    - `kv_block_size`: the engine's KV event stream reports the block size.
    - `max_connection_attempts`: unused, and responses always show the default `20`; registration keeps probing until `--worker-startup-timeout-secs`.
    - The `resilience` retry fields (`max_retries`, `initial_backoff_ms`, `max_backoff_ms`, `backoff_multiplier`, `jitter_factor`, `disable_retry`) and `disable_circuit_breaker`: use the gateway-wide `--retry-*`, `--disable-retries`, and `--disable-circuit-breaker` flags.
    - `dp_base_url`, `dp_rank`, and `dp_size` (except on ZMQ workers): the gateway sets them on data-parallel workers under `--dp-aware`.

---

### Worker Errors

Worker endpoints report errors as `{"error": "<message>", "code": "<CODE>"}`, not in the shape described under [Error Responses](#error-responses):

```json
{
  "error": "Invalid value for field 'worker_url': 10.0.0.5:8000 - URL must start with a lowercase http://, https://, grpc://, grpcs://, or ipc:// scheme",
  "code": "BAD_REQUEST"
}
```

| Status | `code` | When |
|--------|--------|------|
| `400` | `BAD_REQUEST` | `worker_id` is not a UUID. The `url` is empty, does not start with a lowercase `http://`, `https://`, `grpc://`, `grpcs://`, or `ipc://` scheme, is not a valid URL or has no host, or is an `ipc://` URL without a path. A `PUT` changes the URL or is sent to a gateway running with `--dp-aware`. |
| `400` | `PROVIDER_NOT_COMPILED` | The spec targets a provider whose router is not compiled into this build. |
| `404` | `WORKER_NOT_FOUND` | No worker has this ID. |
| `409` | `WORKER_ALREADY_EXISTS` | `POST` for a URL that is already registered. The message names the existing ID. |
| `409` | `WORKER_CREATE_IN_PROGRESS` | `POST` for a URL whose registration is still running. The message names the ID to poll. |
| `500` | `INTERNAL_SERVER_ERROR` | The job queue is unavailable or full. |

A body that does not parse never reaches these checks. The gateway answers with a plain-text body: `415` when `Content-Type: application/json` is missing, `400` for malformed JSON, and `422` (`Failed to deserialize the JSON body into the target type: ...`) for a missing `url`, a wrong type, or an unknown value such as `"worker_type": "prefil"`.

Other problems surface in the background job, after the `202`:

- `overload.waiting_requests` of `0`, or `overload.token_usage` outside (0.0, 1.0]
- the ZMQ constraints listed under [Worker Spec](#worker-spec)
- an unknown `tool_parser` or `reasoning_parser`
- a worker that never answers, or `http_pool.http2: true` for a worker that only speaks HTTP/1.1

---

### List Workers

```
GET /workers
GET /workers?model={model_id}
```

Returns every registered worker. `model` keeps only workers that serve that model ID or alias; workers without a model list match any model. The `stats` counts cover the returned workers (`encode` workers are counted in `total` only). Workers whose registration job is still running are not listed.

**Response:** `200 OK` (abridged: discovered labels and card fields vary by engine)
```json
{
  "workers": [
    {
      "id": "01997a4e-3c2b-7f1d-9a8e-2b6c4d5e7f80",
      "model_id": "meta-llama/Llama-3.1-8B-Instruct",
      "url": "grpc://gpu2:50051",
      "models": [
        {
          "id": "meta-llama/Llama-3.1-8B-Instruct",
          "aliases": ["llama-3.1-8b"],
          "model_type": ["chat", "completions", "responses", "tools"],
          "context_length": 131072,
          "tool_parser": "llama"
        }
      ],
      "worker_type": "regular",
      "connection_mode": "grpc",
      "runtime_type": "sglang",
      "labels": {
        "model_path": "meta-llama/Llama-3.1-8B-Instruct",
        "tp_size": "1"
      },
      "priority": 50,
      "cost": 1.0,
      "max_connection_attempts": 20,
      "is_healthy": true,
      "status": "ready",
      "load": 2,
      "http2": false
    }
  ],
  "total": 1,
  "stats": {
    "prefill_count": 0,
    "decode_count": 0,
    "regular_count": 1
  }
}
```

Each worker object carries its spec fields (never `api_key`) plus:

| Field | Description |
|-------|-------------|
| `id` | Worker UUID. |
| `model_id` | ID of the first model card. Absent for workers without a model list. |
| `is_healthy` | `true` when `status` is `ready`. |
| `status` | `pending` (registered, not yet proven healthy), `ready`, `not_ready` (failing probes, may recover), `failed`, or `draining` (being removed). Only `ready` workers receive traffic. |
| `load` | Requests the gateway has in flight to this worker. |
| `http2` | Whether the gateway speaks HTTP/2 to this worker. |
| `pd_pairing` | Prefill and decode workers only: the key used to pair them for KV transfer, either the explicit `pairing_protocol` or `runtime/transport/layout`. |
| `engine_load` | The engine's last polled load report (per-DP-rank queue and KV-cache figures). Absent until the load monitor has polled the worker. |

---

### Get Worker

```
GET /workers/{worker_id}
```

Returns one worker in the same shape as a `GET /workers` entry. While a job for the worker is queued or running, or for about five minutes after one fails, the response also carries `job_status`. A job that succeeds clears it.

While `POST /workers` is still registering the worker, the response is a placeholder: only `id`, `url`, `status`, and `job_status` are meaningful, and the other fields hold defaults.

**Response:** `200 OK`
```json
{
  "id": "01997a4e-3c2b-7f1d-9a8e-2b6c4d5e7f80",
  "url": "grpc://gpu2:50051",
  "worker_type": "regular",
  "connection_mode": "http",
  "runtime_type": "unspecified",
  "priority": 50,
  "cost": 1.0,
  "max_connection_attempts": 20,
  "is_healthy": false,
  "status": "pending",
  "load": 0,
  "http2": false,
  "job_status": {
    "job_type": "AddWorker",
    "worker_url": "grpc://gpu2:50051",
    "status": "processing",
    "message": null,
    "timestamp": 1790250060
  }
}
```

| `job_status` field | Description |
|--------------------|-------------|
| `job_type` | `AddWorker` (create or replace), `UpdateWorker`, or `RemoveWorker`. |
| `worker_url` | URL of the worker the job targets. |
| `status` | `pending` (queued), `processing`, or `failed`. |
| `message` | Error text when `status` is `failed`; otherwise `null`. |
| `timestamp` | Unix time, in seconds, of the last status change. |

Registration keeps probing a worker that does not answer, every `--worker-startup-check-interval` seconds (default 30), until `--worker-startup-timeout-secs` (default 1800) runs out. If a `POST /workers` job fails, the gateway releases the reserved ID: `GET /workers/{worker_id}` returns `404`, and the reason appears only in the gateway log (`Failed job: type=AddWorker ...`). A failed `PATCH`, `PUT`, or `DELETE` job leaves the worker in place with a `failed` `job_status`.

---

### Update Worker (partial)

```
PATCH /workers/{worker_id}
```

Changes a few fields in place. Omitted fields keep their current values. The worker keeps its status and connections, and nothing is probed again.

**Request Body:**
```json
{
  "priority": 75,
  "labels": {"tier": "gold"},
  "api_key": "new-api-key",
  "health": {"check_interval_secs": 15, "drain_settle_secs": 60}
}
```

| Field | Type | Description |
|-------|------|-------------|
| `priority` | integer | New priority. |
| `cost` | number | New cost. |
| `labels` | object | Merged into the current labels: listed keys are added or overwritten, and the rest are kept. `PATCH` cannot remove a label. |
| `api_key` | string | New worker API key, for key rotation. |
| `health` | object | Partial health overrides, merged into the worker's current settings: `timeout_secs`, `check_interval_secs`, `success_threshold`, `failure_threshold`, `disable_health_check`, `drain_settle_secs`. Setting `disable_health_check` to `true` makes the worker routable immediately. |

Any other field, such as `overload`, `http_pool`, `resilience`, or `models`, is ignored. Use `PUT` to change those.

In v1.11.0, a `PATCH` also rebuilds the worker's circuit breaker with built-in thresholds (5 failures, 2 successes, 30 seconds), ignoring the gateway's circuit-breaker flags and any `resilience` overrides until a `PUT` re-registers the worker.

**Response:** `202 Accepted`
```json
{
  "status": "accepted",
  "worker_id": "01997a4e-3c2b-7f1d-9a8e-2b6c4d5e7f80",
  "message": "Worker update queued for background processing"
}
```

---

### Replace Worker (full)

```
PUT /workers/{worker_id}
```

Replaces the worker's spec and re-runs the full registration workflow (probe, engine detection, metadata discovery) under the same ID. The body is a complete [worker spec](#worker-spec). Fields you omit take their defaults rather than keeping their current values, so resend any `labels`, `overload`, or other settings you want to keep.

The `url` must equal the worker's current URL; to move a worker, `DELETE` it and `POST` the new URL. `PUT` is refused with `400` when the gateway runs with `--dp-aware`, because one spec expands to one worker per data-parallel rank; use `DELETE` and `POST` there too. If the worker is deleted or replaced again before the job runs, the job fails rather than overwrite the newer state.

**Response:** `202 Accepted`, with the same body as `PATCH`.

---

### Delete Worker

```
DELETE /workers/{worker_id}
```

Removes a worker. A `ready` worker first moves to `draining`: it receives no new requests, and the gateway waits `health.drain_settle_secs` (default `--drain-settle-secs`, 5 seconds) so in-flight requests can finish before it removes the worker. A worker in any other status is removed without waiting.

**Response:** `202 Accepted`
```json
{
  "status": "accepted",
  "worker_id": "01997a4e-3c2b-7f1d-9a8e-2b6c4d5e7f80",
  "message": "Worker removal queued for background processing"
}
```

---

## Cache Management

Manage the routing cache and load information.

### Flush Cache

```
POST /flush_cache
```

Flushes the KV cache on all HTTP workers. gRPC workers are skipped. The response status is `200 OK` on full success and `206 Partial Content` when some workers fail.

**Response:** `200 OK`
```json
{
  "status": "success",
  "message": "Successfully flushed cache on all 3 HTTP workers",
  "workers_flushed": 3,
  "total_http_workers": 3,
  "total_workers": 3
}
```

On partial failure, the response additionally includes `successful` (list of worker URLs) and `failed` (list of `{worker, error}` entries), and `status` becomes `"partial_success"`.

---

### Get Loads

```
GET /get_loads
```

Returns the current load distribution across workers. The gateway fans out to every registered worker (HTTP and gRPC) and returns whatever each backend reports. The `load` field is the total number of KV-cache tokens in use across all data-parallel ranks for that worker; `-1` indicates the worker failed to respond.

**Response:** `200 OK`
```json
{
  "workers": [
    {
      "worker": "http://gpu1:8000",
      "load": 1234,
      "details": {
        "timestamp": "2024-01-15T12:00:00Z",
        "dp_rank_count": 1,
        "loads": [
          {
            "dp_rank": 0,
            "num_running_reqs": 5,
            "num_waiting_reqs": 2,
            "num_total_reqs": 7,
            "num_used_tokens": 1234,
            "max_total_num_tokens": 16384,
            "token_usage": 0.075,
            "gen_throughput": 45.2,
            "cache_hit_rate": 0.82,
            "utilization": 0.31,
            "max_running_requests": 256
          }
        ]
      }
    }
  ]
}
```

---

## Model Information

Query model and server information.

### List Models

```
GET /v1/models
```

Returns available models (proxied to workers).

**Response:** `200 OK`
```json
{
  "object": "list",
  "data": [
    {
      "id": "llama3-70b",
      "object": "model",
      "created": 1700000000,
      "owned_by": "meta"
    }
  ]
}
```

---

### Get Model Info

```
GET /get_model_info
```

Returns detailed model information (proxied to HTTP workers).

**Response:** `200 OK`
```json
{
  "model_name": "llama3-70b",
  "max_tokens": 8192,
  "vocab_size": 128256
}
```

!!! note "gRPC workers"
    This endpoint is not proxied for gRPC-connected workers. The gateway calls
    the backend's `GetModelInfo` RPC once at worker registration and surfaces
    the result as worker labels (`model_path`, `served_model_name`,
    `vocab_size`, `max_context_length`, ...) in
    [`GET /workers`](extensions.md#worker-management).

---

### Get Server Info

```
GET /get_server_info
```

Returns server information (proxied to HTTP workers).

**Response:** `200 OK`
```json
{
  "version": "0.1.0",
  "backend": "vllm",
  "gpu_count": 8
}
```

!!! note "gRPC workers"
    This endpoint is not proxied for gRPC-connected workers. The gateway calls
    the backend's `GetServerInfo` RPC once at worker registration and surfaces
    a curated subset of `server_args` (`tp_size`, `dp_size`,
    `max_total_tokens`, `version`, ...) as worker labels in
    [`GET /workers`](extensions.md#worker-management). Live scheduler state
    (running/waiting requests, KV utilization) is served by
    [`GET /get_loads`](#get-loads) instead.

---

## WASM Module Management

Manage WebAssembly plugins. Modules are registered from files accessible to the gateway process; the request body contains descriptors with paths, not binary payloads.

### Add WASM Module

```
POST /wasm
```

Registers one or more WASM modules.

**Request Body:** JSON `WasmModuleAddRequest`
```json
{
  "modules": [
    {
      "name": "custom-middleware",
      "file_path": "/etc/smg/wasm/custom-middleware.wasm",
      "module_type": "Middleware",
      "attach_points": [
        {"Middleware": "OnRequest"},
        {"Middleware": "OnResponse"}
      ]
    }
  ]
}
```

The only supported `module_type` today is `Middleware`. Valid `Middleware` attach points are `OnRequest`, `OnResponse`, and `OnError`.

**Response:** `200 OK` on full success, `400 Bad Request` if any module failed to register. The response body echoes every requested module with an `add_result` field indicating success (carrying the assigned UUID) or failure (carrying the error message).

```json
{
  "modules": [
    {
      "name": "custom-middleware",
      "file_path": "/etc/smg/wasm/custom-middleware.wasm",
      "module_type": "Middleware",
      "attach_points": [
        {"Middleware": "OnRequest"},
        {"Middleware": "OnResponse"}
      ],
      "add_result": {
        "Success": "550e8400-e29b-41d4-a716-446655440000"
      }
    }
  ]
}
```

---

### List WASM Modules

```
GET /wasm
```

Returns all registered WASM modules together with aggregate execution metrics.

**Response:** `200 OK`
```json
{
  "modules": [
    {
      "module_uuid": "550e8400-e29b-41d4-a716-446655440000",
      "module_meta": {
        "name": "custom-middleware",
        "file_path": "/etc/smg/wasm/custom-middleware.wasm",
        "sha256_hash": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        "size_bytes": 65536,
        "created_at": "2024-01-15T12:00:00.000000000Z",
        "last_accessed_at": "2024-01-15T12:05:00.000000000Z",
        "access_count": 42,
        "attach_points": [
          {"Middleware": "OnRequest"}
        ]
      }
    }
  ],
  "metrics": {
    "total_executions": 42,
    "successful_executions": 42,
    "failed_executions": 0,
    "total_execution_time_ms": 125,
    "max_execution_time_ms": 8,
    "average_execution_time_ms": 2.97
  }
}
```

---

### Remove WASM Module

```
DELETE /wasm/{module_uuid}
```

Removes a WASM module. The body is a plain text status message, not JSON.

**Response:** `200 OK`
```
Module removed successfully
```

On failure returns `400 Bad Request` with the error text as the body.

---

## Error Responses

All endpoints return errors in a consistent format:

```json
{
  "error": {
    "message": "Detailed error description",
    "type": "error_type"
  }
}
```

| HTTP Status | Error Type | Description |
|-------------|------------|-------------|
| `400` | `bad_request` | Invalid request format or parameters |
| `401` | `unauthorized` | Missing or invalid authentication |
| `403` | `forbidden` | Insufficient permissions |
| `404` | `not_found` | Resource not found |
| `409` | `conflict` | Resource already exists |
| `503` | `service_unavailable` | No healthy workers available |

---

## Authentication

Admin endpoints require authentication via one of:

1. **API Key**: Pass via `Authorization: Bearer <api-key>` header
2. **Control Plane Key**: For cluster management operations

Public endpoints (health checks, model info) do not require authentication.
