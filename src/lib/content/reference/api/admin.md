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

Manage backend inference workers.

!!! tip
    For listing workers and viewing metrics, see [Gateway Extensions](extensions.md#worker-management).

### Create Worker

```
POST /workers
```

Registers a new backend worker.

**Request Body:**
```json
{
  "url": "http://gpu1:8000",
  "models": [{ "id": "llama3-70b" }],
  "api_key": "worker-secret-key"
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `url` | string | Yes | Worker base URL |
| `worker_type` | string | No | `regular`, `prefill`, or `decode` (default: `regular`) |
| `connection_mode` | string | No | `http` or `grpc` (default: `http`) |
| `runtime_type` | string | No | `sglang`, `vllm`, `trtllm`, `mlx`, `external`, or `unspecified` (default: `unspecified`, which triggers auto-detection) |
| `models` | array | No | Model cards served by this worker (empty = wildcard) |
| `api_key` | string | No | API key for worker authentication |
| `priority` | integer | No | Routing priority (higher = preferred, default: 50) |

**Response:** `202 Accepted`
```json
{
  "status": "accepted",
  "worker_id": "worker-abc123",
  "url": "http://gpu1:8000",
  "location": "/workers/worker-abc123",
  "message": "Worker addition queued for background processing"
}
```

---

### Update Worker (partial)

```
PATCH /workers/{worker_id}
```

Partially updates worker configuration. Only the fields you include are changed.

**Request Body:**
```json
{
  "priority": 75,
  "api_key": "new-api-key"
}
```

| Field | Type | Description |
|-------|------|-------------|
| `priority` | integer | New routing priority |
| `cost` | number | New cost factor |
| `labels` | object | Updated labels |
| `api_key` | string | New API key (for key rotation) |
| `health` | object | Partial health-check overrides (`timeout_secs`, `check_interval_secs`, `success_threshold`, `failure_threshold`, `disable_health_check`) |

**Response:** `202 Accepted`
```json
{
  "status": "accepted",
  "worker_id": "worker-abc123",
  "message": "Worker update queued for background processing"
}
```

---

### Replace Worker (full)

```
PUT /workers/{worker_id}
```

Re-runs the full worker registration workflow (model discovery and all). The request body must be a complete `WorkerSpec` whose `url` matches the existing worker's URL — URL changes are not supported via `PUT`; use `DELETE` + `POST` instead.

**Response:** `202 Accepted` with the same shape as `PATCH`.

---

### Delete Worker

```
DELETE /workers/{worker_id}
```

Removes a worker from the pool.

**Response:** `202 Accepted`
```json
{
  "status": "accepted",
  "worker_id": "worker-abc123",
  "message": "Worker removal queued for background processing"
}
```

---

## Cache Management

Flush the engines' KV caches, and read the engine load the gateway has polled from its workers.

### Flush Cache

```text
POST /flush_cache
```

Asks every registered worker to drop its KV prefix cache. The calls go out in parallel, and the response reports the outcome per worker. Requires admin [authentication](#authentication).

| Worker | How it is flushed |
|--------|-------------------|
| HTTP | `POST {worker_url}/flush_cache`, with the worker's API key (if it has one) as a bearer token and a 45-second timeout. Any non-2xx answer counts as a failure, so the engine must serve this route (SGLang's HTTP server does). |
| gRPC SGLang, TokenSpeed | `FlushCache` RPC. |
| gRPC vLLM | `FlushCache` RPC, new in v1.11.0. Needs `smg-grpc-servicer` 0.12.0 or later; older servicers answer `UNIMPLEMENTED`. Resets vLLM's local prefix cache without preempting running requests or clearing connector caches. The gateway makes a single attempt, so a reset that vLLM refuses while requests are in flight is reported under `failed`. |
| gRPC TRT-LLM, MLX | Not supported: reported under `failed` (`UNIMPLEMENTED`). |
| ZMQ (`ipc://`) | Skipped, because the transport has no cache-flush RPC. Counted in `total_zmq_workers_skipped`. |

The fan-out does not filter by runtime: external-provider workers registered over HTTP receive the `POST` too, and appear under `failed` if they reject it. The endpoint only calls the workers; it does not reset the gateway's own cache-aware routing state.

**Response:** `200 OK` when every worker that was called succeeds (or there was nothing to call), `206 Partial Content` when at least one fails, including when all of them fail.

```json
{
  "status": "success",
  "message": "Successfully flushed cache on all 3 workers",
  "workers_flushed": 3,
  "total_http_workers": 2,
  "total_grpc_workers": 1,
  "total_zmq_workers_skipped": 0,
  "total_workers": 3
}
```

On a partial failure, `status` is `"partial_success"` and two more fields list the outcome, `successful` (worker URLs) and `failed` (`{worker, error}` entries):

```json
{
  "status": "partial_success",
  "message": "Cache flush: 2 succeeded, 1 failed (1 ZMQ workers skipped: no cache-flush RPC)",
  "workers_flushed": 2,
  "total_http_workers": 2,
  "total_grpc_workers": 1,
  "total_zmq_workers_skipped": 1,
  "total_workers": 4,
  "successful": ["http://gpu1:8000", "grpc://gpu3:50051"],
  "failed": [
    {
      "worker": "http://gpu2:8000",
      "error": "flush_cache failed for worker http://gpu2:8000: HTTP 500 Internal Server Error"
    }
  ]
}
```

| Field | Description |
|-------|-------------|
| `status` | `success` or `partial_success` |
| `message` | Human-readable summary |
| `workers_flushed` | Workers that confirmed the flush |
| `total_http_workers`, `total_grpc_workers` | Registered workers by transport |
| `total_zmq_workers_skipped` | ZMQ workers that were not called |
| `total_workers` | All registered workers (HTTP + gRPC + ZMQ) |
| `successful`, `failed` | Present only when at least one worker failed |

---

### Get Loads

```text
GET /loads
GET /get_loads
```

Returns the engine load the gateway's load monitor last collected: one entry per worker per DP rank, plus a fleet-wide `aggregate`. The body uses the same schema engines report on `/v1/loads`, with every entry tagged by `worker` and `worker_type`.

The response is built from the monitor's cached snapshot, the same numbers the routing policies act on, so no request reaches a worker. Values can be up to one poll interval old (`--load-monitor-interval`, 10 seconds by default). Serving from the cache is also what makes the route safe to poll from another SMG gateway that registers this one as a worker.

- **Auth**: `/loads` is a public route, served without auth like `/health`. `/get_loads` is a deprecated alias that stays on the admin tier and requires admin [authentication](#authentication).
- **Filter**: `?model=<model_id>` limits the response to workers serving that model.
- **Missing workers**: workers without a current report (not `Ready` yet, no load source, or a failed last poll) are left out rather than reported as idle. Before the first poll, `loads` is empty and `aggregate` is absent.

**Response:** `200 OK`
```json
{
  "timestamp": "2026-09-24T18:20:11.482913+00:00",
  "version": "smg-1.11.0",
  "dp_rank_count": 2,
  "loads": [
    {
      "worker": "http://gpu1:8000",
      "worker_type": "regular",
      "dp_rank": 0,
      "num_running_reqs": 12,
      "num_waiting_reqs": 3,
      "num_waiting_uncached_tokens": 5120,
      "num_total_reqs": 15,
      "num_used_tokens": 48000,
      "max_total_num_tokens": 131072,
      "token_usage": 0.37,
      "gen_throughput": 1850.5,
      "cache_hit_rate": 0.62,
      "utilization": 0.41,
      "max_running_requests": 256
    },
    {
      "worker": "http://gpu2:8000",
      "worker_type": "regular",
      "dp_rank": 0,
      "num_running_reqs": 9,
      "num_waiting_reqs": 0,
      "num_waiting_uncached_tokens": 0,
      "num_total_reqs": 9,
      "num_used_tokens": 30000,
      "max_total_num_tokens": 131072,
      "token_usage": 0.23,
      "gen_throughput": 1420.0,
      "cache_hit_rate": 0.55,
      "utilization": 0.3,
      "max_running_requests": 256
    }
  ],
  "aggregate": {
    "total_running_reqs": 21,
    "total_waiting_reqs": 3,
    "total_reqs": 24,
    "avg_token_usage": 0.3,
    "avg_throughput": 1635.25,
    "avg_utilization": 0.355
  }
}
```

| Field | Description |
|-------|-------------|
| `timestamp` | When the gateway built this response (RFC 3339) |
| `version` | `smg-` followed by the gateway version |
| `dp_rank_count` | Number of entries in `loads`, across the whole fleet |
| `loads[].worker`, `loads[].worker_type` | Worker URL and type (`regular`, `prefill`, `decode`, or `encode`) |
| `loads[].dp_rank` | DP rank within that worker |
| `loads[].num_running_reqs`, `num_waiting_reqs`, `num_total_reqs` | Requests running, queued, and both |
| `loads[].num_waiting_uncached_tokens` | Queued tokens not served from the prefix cache (`0` when the engine does not report it) |
| `loads[].num_used_tokens`, `max_total_num_tokens` | KV tokens in use and KV capacity (`0` for workers read from Prometheus `/metrics`) |
| `loads[].token_usage` | KV token usage ratio, 0.0 to 1.0 |
| `loads[].gen_throughput`, `cache_hit_rate`, `utilization`, `max_running_requests` | As reported by the engine |
| `loads[].memory`, `loads[].queues` | Optional sections, present when the engine reports them: `memory` (`weight_gb`, `kv_cache_gb`, `graph_gb`, `token_capacity`) and `queues` (`waiting`, `grammar`, `paused`, `retracted`) |
| `loads[].kv_transfer_latency_ms`, `kv_transfer_speed_gb_s`, `prefill_queue_reqs`, `decode_queue_reqs`, `disagg_mode` | Optional prefill/decode disaggregation fields, present when the engine reports them |
| `aggregate` | Fleet roll-up across all entries: `total_running_reqs`, `total_waiting_reqs`, and `total_reqs` are sums; `avg_token_usage`, `avg_throughput`, and `avg_utilization` are means |

The same per-worker report appears as `engine_load` on `GET /workers` and `GET /workers/{worker_id}`. It is unrelated to their `load` field, which counts requests the gateway has in flight to the worker. [Overload Protection](../../concepts/reliability/overload-protection.md) explains where each backend's report comes from.

!!! warning "Changed in v1.11.0"
    `GET /get_loads` used to call every worker on each request and return `{"workers": [{"worker", "load", "details"}]}`. It now returns the `/loads` body above. Callers that read `.workers[].load` must switch to `.loads[]` or `.aggregate`.

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
