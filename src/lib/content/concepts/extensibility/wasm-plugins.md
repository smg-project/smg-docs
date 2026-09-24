---
title: WASM Plugins
---

# WASM Plugins

WebAssembly (WASM) plugins enable custom middleware logic in SMG's request pipeline without recompiling or restarting the gateway.

---

## Overview

<div class="grid" markdown>

<div class="card" markdown>

### :material-puzzle: Dynamic Extension

Deploy custom logic at runtime via REST API. Add or remove plugins without restarting the gateway; to update one, remove it and add the new build.

</div>

<div class="card" markdown>

### :material-shield-lock: Sandboxed Execution

Plugins run in isolated WASM environments with strict resource limits. No access to host memory, filesystem, or network.

</div>

<div class="card" markdown>

### :material-translate: Language Agnostic

Write plugins in Rust, Go, C, or any language that compiles to WebAssembly Component Model.

</div>

<div class="card" markdown>

### :material-lightning-bolt: High Performance

Compiled components are cached per runtime thread, so only a component's first run on each thread pays for compilation.

</div>

</div>

---

## How It Works

WASM plugins execute in SMG's **middleware layer**, intercepting requests before they reach workers and responses before they return to clients.

The middleware runs on the inference routes: `/v1/chat/completions`, `/v1/completions`, `/generate`, `/v1/responses` and `/v1/conversations` (with their sub-paths), `/v1/messages` and `/v1/messages/count_tokens`, `/v1/embeddings`, `/v1/rerank` and `/rerank`, `/v1/classify`, `/v1/interactions`, `/v1/tokenize`, `/v1/detokenize`, and the Realtime REST endpoints. It does not run on `/v1/audio/transcriptions`, the Realtime WebSocket and WebRTC routes, or the public, admin, and worker-management routes.

### Plugin Chain Execution

SMG runs every plugin attached at a point, in no defined order: don't rely on deployment order. Each plugin receives the request/response and returns an action:

1. **Continue**: Pass through to the next plugin (or worker)
2. **Reject(status)**: Stop processing and return an error response immediately
3. **Modify(changes)**: Apply transformations and continue

If any plugin returns `Reject`, subsequent plugins are skipped and the error response is returned to the client: on a request, the status with an empty body; on a response, the status with the response body so far. A plugin that fails or runs past its time limit is skipped, and processing continues with the next one.

---

## Attach Points

Plugins register at specific points in the request lifecycle:

| Attach Point | When | Use Cases |
|--------------|------|-----------|
| **OnRequest** | Before forwarding to worker | Authentication, rate limiting, validation, header injection |
| **OnResponse** | After receiving worker response | Response transformation, error normalization, logging |

!!! warning "Streaming responses bypass OnResponse"
    To avoid buffering entire streams into memory, SMG skips the
    **OnResponse** phase when the upstream reply is streaming — that is,
    when the response uses `Content-Type: text/event-stream`,
    `Content-Type: application/x-ndjson`, or a chunked
    `Transfer-Encoding`. The gateway logs a warning and passes the
    streaming response through untouched, so plugins attached only at
    OnResponse will not observe streaming traffic. OnRequest runs
    normally for streaming endpoints.

---

## Example Plugins

SMG includes ready-to-use example plugins demonstrating common middleware patterns:

<div class="grid" markdown>

<div class="card" markdown>

### :material-key: auth-middleware

API key authentication for `/api` and `/v1` routes.

- Validates `Authorization` or `x-api-key` header
- Returns **401 Unauthorized** on failure
- Attach point: **OnRequest**

</div>

<div class="card" markdown>

### :material-speedometer: ratelimit-middleware

Per-identifier rate limiting with configurable thresholds.

- 60 requests/minute default
- Tracks by API key, IP, or request ID
- Returns **429 Too Many Requests** when exceeded
- Attach point: **OnRequest**
- SMG runs each invocation in a fresh component instance, so the example's in-memory counters don't carry over between requests; treat it as a template

</div>

<div class="card" markdown>

### :material-file-document: logging-middleware

Request tracking and response transformation.

- Adds `x-request-id`, `x-wasm-processed`, `x-processed-at`, and `x-api-route` (on `/api` and `/v1` paths)
- Converts 500 → 503 for better client handling
- Attach points: **OnRequest** and **OnResponse**

</div>

</div>

Find complete source code and build instructions in [`examples/wasm/`](https://github.com/smg-project/smg/tree/main/examples/wasm).

---

## Plugin Development

### Interface

Plugins implement the SMG middleware interface using the WebAssembly Component Model:

```rust
// OnRequest: Called before forwarding to worker
fn on_request(req: Request) -> Action {
    // Validate, modify, or reject the request
    Action::Continue
}

// OnResponse: Called after receiving worker response
fn on_response(resp: Response) -> Action {
    // Transform or log the response
    Action::Continue
}
```

### Actions

| Action | Effect | Example |
|--------|--------|---------|
| `Action::Continue` | Pass through unmodified | Logging, metrics |
| `Action::Reject(401)` | Return error immediately | Auth failure |
| `Action::Modify(changes)` | Apply transformations | Add headers, rewrite body |

### Request Context

Plugins receive rich context for decision-making:

| Field | Description |
|-------|-------------|
| `method` | HTTP method (GET, POST, etc.) |
| `path` | Request path |
| `query` | URL query string |
| `headers` | All request headers |
| `body` | Request body (if present) |
| `request_id` | Unique request identifier |
| `now_epoch_ms` | Current timestamp |

---

## Configuration

### Enabling WASM Support

```bash
smg --enable-wasm --worker-urls http://worker:8000
```

### Runtime Settings

| Setting | Value | Description |
|---------|-------|-------------|
| `max_memory_pages` | 1024 | Maximum memory (64KB per page = 64MB) |
| `max_execution_time_ms` | 1000 | Execution timeout per invocation |
| `max_stack_size` | 1 MB | Stack size per invocation |
| `module_cache_size` | 10 | Compiled components cached per runtime thread |
| `max_body_size` | 10 MB | Largest request or response body a plugin can receive |

These values are fixed in v1.11.0; no flag changes them. Plugins run on a pool of runtime threads, one per CPU core up to 4.

With an **OnRequest** plugin attached, SMG reads the whole request body before running it, and answers `400` when the body exceeds `max_body_size`. With an **OnResponse** plugin attached, it reads each non-streaming response body the same way, and answers `500` when the body is too large.

---

## Module Management

Plugins are managed via the Admin API at runtime. These routes need `--enable-wasm` (without it they answer `500`) and are protected like the other admin routes.

### Deploy Plugins

```bash
curl -X POST http://localhost:30000/wasm \
  -H "Content-Type: application/json" \
  -d '{
    "modules": [
      {
        "name": "auth-middleware",
        "file_path": "/plugins/auth.component.wasm",
        "module_type": "Middleware",
        "attach_points": [{"Middleware": "OnRequest"}]
      },
      {
        "name": "logging-middleware",
        "file_path": "/plugins/logging.component.wasm",
        "module_type": "Middleware",
        "attach_points": [
          {"Middleware": "OnRequest"},
          {"Middleware": "OnResponse"}
        ]
      }
    ]
  }'
```

SMG loads each module before it answers. The response lists every module with an `add_result`: `{"Success": "<module uuid>"}`, or `{"Error": "<message>"}` for a module that failed, in which case the status is `400`.

### List Plugins

```bash
curl http://localhost:30000/wasm
```

### Remove Plugin

```bash
curl -X DELETE http://localhost:30000/wasm/{uuid}
```

---

## Security Model

WASM plugins execute in a sandboxed environment with multiple protection layers:

| Layer | Protection |
|-------|------------|
| **Path Validation** | Only absolute paths without `.` or `..` segments, `.wasm` extension required, system directories blocked (checked again after resolving symlinks) |
| **Runtime Sandboxing** | No access to host memory, filesystem, network, or system calls |
| **Resource Limits** | Memory caps, execution timeouts, stack size limits |
| **Deduplication** | A module whose SHA-256 hash matches a loaded module is rejected |

**Blocked directories**: `/etc/`, `/proc/`, `/sys/`, `/dev/`, `/boot/`, `/root/`, `/var/log/`, `/var/run/`

---

## Performance

The first run of a component on each runtime thread compiles it, and later runs on that thread reuse the compiled component. Every invocation still instantiates the component fresh. `GET /wasm` reports execution counts (total, successful, failed) and the total, average, and maximum execution time, so you can measure your plugins' overhead.

---

## What's Next?

<div class="grid" markdown>

<div class="card" markdown>

### :material-github: Example Plugins

Complete source code with build instructions.

[View Examples →](https://github.com/smg-project/smg/tree/main/examples/wasm)

</div>

<div class="card" markdown>

### :material-tools: MCP Integration

Connect models to external tools.

[Model Context Protocol →](mcp.md)

</div>

</div>
