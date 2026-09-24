---
title: Request Streaming and Upstream Connections
---

# Request Streaming and Upstream Connections

SMG's HTTP router decides per request whether to **buffer** a request body (parse it, then re-serialize it for the worker) or **stream** it to the worker byte for byte. Streaming responses flow back chunk by chunk under backpressure. Underneath, SMG keeps pooled connections to every worker and can multiplex them over HTTP/2. This page explains those decisions and the limits that keep router memory bounded.

---

## Overview

<div class="grid" markdown>

<div class="card" markdown>

### :material-swap-horizontal: Stream or Buffer, per Request

Decided from headers and router state before any body byte is read. Large bodies that nothing needs to parse go straight to the worker.

</div>

<div class="card" markdown>

### :material-memory: Bounded Memory

Buffered bodies are capped and freed as early as the retry policy allows. Streaming responses relay through a bounded channel, so a slow client cannot pile up memory.

</div>

<div class="card" markdown>

### :material-lan-connect: HTTP/2 to Workers

Opt-in HTTP/2 upstream, negotiated per worker at registration with HTTP/1.1 fallback, so a mixed fleet can roll out in any order.

</div>

<div class="card" markdown>

### :material-connection: Healthy Connection Pools

Idle connections expire before the backend closes them, and a send that fails before any response is resent once.

</div>

</div>

---

## Why Stream Request Bodies?

Buffering a request means the router holds the whole body in memory, first as a parsed request and then as re-serialized bytes, and parses and re-encodes it on the way through. Every in-flight request pays that cost, so router memory grows with concurrency times payload size. Requests carrying inline images, audio, or video have the largest bodies, and the router has no use for their bytes: routing needs only headers or the prompt.

Streaming forwards the client's bytes to the worker as they arrive. The router holds only the chunks in transit, backpressure flows end to end, and the worker starts receiving the body before the client has finished sending it.

The trade-off: a streamed body cannot be replayed, so it gets a single attempt with no [router retries](../reliability/retries.md), and the router never reads it, so JSON validation happens at the worker.

---

## How the Router Decides

Only the HTTP router in regular (non-PD) mode can stream request bodies, and only on these routes:

`/generate`, `/v1/chat/completions`, `/v1/completions`, `/v1/messages`, `/v1/embeddings`, `/v1/classify`

Every other route buffers, and so does every other router mode: PD disaggregation, gRPC and ZMQ workers, external providers, and IGW mode (`--enable-igw`), which must read the model from the body to pick a router.

For an eligible request, the first matching rule wins:

| # | Condition | Path | `reason` label |
|---|-----------|------|----------------|
| 1 | `--routing-key-override` is enabled | Buffer | `routing_key_override` |
| 2 | The default or a per-model routing policy reads the prompt (`cache_aware`, `bucket`), and the request has no valid `x-smg-routing-tokens` header | Buffer | `policy_needs_text` |
| 3 | Workers for more than one model are registered | Buffer | `model_ambiguous` |
| 4 | A worker rewrites request bodies (DP-aware workers, `--dp-aware`) | Buffer | `worker_mutates_body` |
| 5 | WASM is enabled and a module is attached at `OnRequest` | Buffer | `wasm_request_hook` |
| 6 | The request has no valid `Content-Length` header (for example, a chunked upload) | Buffer | `no_content_length` |
| 7 | Router retries are enabled and `Content-Length` ≤ `--max-buffered-request-bytes` | Buffer | `retryable` |
| 8 | Router retries are enabled and the body is larger | Stream | `retry_forfeited` |
| 9 | Router retries are disabled | Stream, at any size | `pure_forward` |

Rules 1–6 always buffer, at any size up to `--max-payload-size`. In the first five, something must read the body: the sticky-session key comes from the body's `rid`, the policy routes on the prompt, the model name is inside the body, the request must be edited for the worker, or a plugin inspects it.

Rules 7–9 apply when the only reason to buffer is to keep the request retryable. Router retries count as enabled when `--retry-max-retries` is greater than `1` and `--disable-retries` is not set. (Per-worker retry fields in a worker spec have no effect in v1.11.0; see [Per-Worker Overrides](../reliability/retries.md#per-worker-overrides).)

Once a request is headed for the streamed path, the router selects a worker from the headers alone. If no worker can be selected that way, or the fleet changed in the meantime (a body-rewriting worker or a second model registered), the request falls back to the buffered path, counted as `no_available_worker`, `worker_mutates_body`, or `model_ambiguous`.

!!! note "The default policy buffers"
    `cache_aware`, the default `--policy`, routes on the prompt, so with default settings every request buffers unless it carries a valid `x-smg-routing-tokens` hint. With the hint, `cache_aware` routes on the hinted token IDs and the body can stream. See [Sticky Sessions](../routing/sticky-sessions.md) for the routing hint headers.

!!! warning "Behavior change in v1.10.0"
    Before v1.10.0 the HTTP router buffered every request body. Since v1.10.0, eligible requests larger than `--max-buffered-request-bytes` (1 MiB by default) stream to the worker and get a single attempt; raise the limit to keep larger requests retryable. The `--stream-request-bodies-over` flag from pre-release builds was removed before v1.10.0 shipped, and SMG refuses to start if it is passed.

### What Changes for a Streamed Request

| | Buffered | Streamed |
|---|----------|----------|
| Body sent to the worker | Re-serialized from the parsed request: a model alias becomes the canonical model ID, and DP-aware workers get their rank inserted | The client's bytes, unchanged, sent with `Content-Type: application/json` and no `Content-Length` header (chunked over HTTP/1.1) |
| JSON validation | Router; an invalid request is rejected before it reaches a worker | Worker |
| Router retries | Yes, up to `--retry-max-retries` attempts | No: one attempt |
| Worker selection input | Prompt text or token IDs, body `rid`, headers | Headers only, including an `x-smg-routing-tokens` or routing-key hint |
| Size limit | `--max-payload-size` | `--max-payload-size`, counted while streaming |
| Stall protection | — | `--stream-body-stall-timeout-secs` |

A policy that hashes the prompt has nothing to hash on the streamed path: `prefix_hash` uses a routing-key or tokens hint when the request carries one, and otherwise falls back to the least-loaded worker.

Because the router never reads the body, it does not see `"stream": true`. It relays the response as a stream when the worker answers with `Content-Type: text/event-stream`, and `smg_router_requests_total` records streamed-body requests with `streaming="false"`.

### Stalled and Aborted Uploads

A streamed body moves at the client's pace, so a client that stops sending mid-upload would otherwise hold a worker connection open. `--stream-body-stall-timeout-secs` (default `300`, `0` disables) aborts the dispatch with **408** `request_body_stalled` once a single wait for the client's next bytes lasts that long.

The clock runs only while SMG is waiting on the client. While the worker applies backpressure, for example by reading the body slowly during a busy prefill, SMG is not asking the client for bytes and the clock pauses, so a slow worker never trips it. The watchdog disarms once the whole body has been forwarded, and `--request-timeout-secs` still bounds the whole exchange.

| Status | `X-SMG-Error-Code` | Cause |
|--------|--------------------|-------|
| `408` | `request_body_stalled` | The client sent nothing for `--stream-body-stall-timeout-secs` while the worker was ready for more |
| `413` | `request_body_too_large` | The streamed body grew past `--max-payload-size`; the upstream send is aborted |
| `400` | `request_body_aborted` | The client's upload failed (disconnect or reset) before the body was fully forwarded |

These aborts are caused by the client, so none of them is recorded against the worker's [circuit breaker](../reliability/circuit-breakers.md).

### Tuning the Decision

| Goal | Setting |
|------|---------|
| Keep router retries for bodies up to N bytes | `--max-buffered-request-bytes N` (default `1048576`, 1 MiB) |
| Stream every eligible request, never buffering just for retries | `--max-buffered-request-bytes 0`, or `--disable-retries` |
| Stream large requests under `cache_aware` | Send a valid `x-smg-routing-tokens` hint |
| Buffer every eligible request | Keep retries enabled and set `--max-buffered-request-bytes` to the `--max-payload-size` value |

Buffering more keeps more requests retryable, at the cost of router memory for each in-flight buffered body.

---

## Memory Bounds

**Request bodies.** A body larger than `--max-payload-size` (default `536870912`, 512 MiB) is rejected with `413`, whether it is buffered or streamed.

**Early release.** When router retries are disabled for a request's model, the HTTP routers free the parsed request and its routing inputs (prompt text, token IDs) as soon as the upstream body is serialized, before the send. A serialized body of 1 MiB or more is then handed to the connection as a one-shot stream, still with a `Content-Length` header, so it is freed when the upload finishes rather than when the worker's response headers arrive, which for a non-streaming generation is when generation ends. With retries enabled, the request stays in memory until the router has its final response, so it can be replayed. The gRPC pipeline always frees the parsed request once it has built the worker request, and keeps only that built request for retries. `smg_router_request_buffers_released_early_bytes_total` counts the bytes freed early.

**Edits without a JSON tree.** The router edits the serialized request in place (the model alias, a DP rank, PD bootstrap fields, KV-transfer parameters) instead of expanding it into a JSON tree, so large `input_ids` arrays and message content are not materialized field by field.

**Exact numbers.** Floating-point fields such as `temperature` and `top_p` are forwarded in their shortest form: `0.95` reaches the worker as `0.95`, not `0.949999988079071`. This holds on the buffered HTTP and PD paths and on the external-provider paths; streamed bodies are forwarded unchanged anyway.

**Worker responses.** A non-streaming worker response is read into memory up to `--max-payload-size`. A larger response fails with **502** `upstream_response_too_large`, which counts as a worker fault for circuit breakers and retries. Streaming responses are never buffered whole.

**Error-code labels.** Metric `error_code` labels only take codes SMG itself generates. An `X-SMG-Error-Code` header sent by a worker is dropped from the forwarded response and never becomes a label, so a misbehaving backend cannot inflate label cardinality.

---

## Response Streaming

- **Backpressure.** Streaming responses pass through a bounded channel of 32 chunks between the upstream reader and the client connection. When a client reads slowly, SMG stops reading from the worker instead of buffering the rest of the response.
- **Client disconnects.** In regular HTTP mode, when the client goes away the relay stops at once, even during a long prefill before the first token, and closes the upstream connection so the engine can abort the generation.
- **Empty chunks.** In regular HTTP mode, SMG drops zero-length chunks from the worker instead of relaying them. Over HTTP/2 each one would become an empty DATA frame, and current h2 clients close the connection with `ENHANCE_YOUR_CALM` after about 100 of them. This matters for any HTTP/2 client of SMG, including another gateway in front of it.
- **No Nagle delay.** `TCP_NODELAY` is set on accepted plain-HTTP client connections, so small SSE events go out immediately instead of waiting for earlier data to be acknowledged. Connections to workers set `TCP_NODELAY` too.
- **Load accounting.** A streaming response keeps its worker's in-flight load count until the body finishes or the client disconnects, so load-aware policies see the request for its whole duration.

---

## Upstream Connections

### Connection Pools

Workers with the same effective connection settings share one HTTP client and its connection pool, so a large uniform fleet does not need a client per worker.

`--upstream-pool-idle-timeout-secs` (default `3`) closes pooled connections that have been idle that long. Keep it **below the backend's keep-alive timeout**: vLLM and SGLang default to 5 seconds. If the backend closes an idle connection first, a request that reuses it fails before the backend sees it and has to be resent. `0` keeps idle connections forever.

A worker spec can override the connection settings in its `http_pool` block:

| Field | Default | Description |
|-------|---------|-------------|
| `pool_idle_timeout_secs` | `--upstream-pool-idle-timeout-secs` | Idle timeout for pooled connections |
| `pool_max_idle_per_host` | `500` | Idle connections kept per worker host |
| `connect_timeout_secs` | `10` | TCP connect timeout |
| `timeout_secs` | `--request-timeout-secs` | Total time for one request, including a streamed response |
| `http2` | Negotiated | Pin HTTP/2 (`true`) or HTTP/1.1 (`false`); see [Upstream HTTP/2](#upstream-http2) |

### Resending Pre-Response Failures

A send can fail before the worker produced any response: no status and no timeout, most often because the backend had already closed the pooled connection. The backend never processed the request, so SMG resends it once, immediately. The resend is independent of the retry settings, so it happens even with `--disable-retries`, and it is counted in `smg_router_upstream_send_retries_total`. It covers the HTTP router's regular and PD dispatches.

It does not apply to:

- **Timeouts.** The request may still be running on the worker, and resending would double the load exactly when the fleet is slow.
- **Streamed request bodies** and **upstream bodies of 1 MiB or more**, which are sent as one-shot streams and cannot be replayed at this layer.

### Upstream HTTP/2

By default SMG speaks HTTP/1.1 to cleartext workers, which needs one TCP connection per in-flight request. `--upstream-http2` multiplexes every request to a worker over one HTTP/2 connection, using prior knowledge (h2c) on cleartext `http://` URLs.

```bash
smg \
  --worker-urls http://worker1:8000 http://worker2:8000 \
  --upstream-http2
```

**Negotiation at registration.** With the flag on, SMG probes each new `http://` worker with HTTP/2 and HTTP/1.1 in parallel. The worker is marked HTTP/2 only if the HTTP/2 probe succeeds; otherwise it stays on HTTP/1.1. `https://` workers are not probed, since TLS negotiates the version. gRPC and ZMQ workers are unaffected. With the flag off, nothing is probed and workers that are not pinned use HTTP/1.1.

**What uses it.** Everything SMG sends to a worker goes through that worker's client: request dispatch, health checks, load polling, worker management calls, and registration probes. Calls to external providers and IGW model discovery use a separate shared client, which never forces HTTP/2.

**Connection tuning.** Under the flag, worker clients use adaptive HTTP/2 flow-control windows and send HTTP/2 PING keep-alives every 30 seconds (20-second timeout), including while idle, to detect dead peers on long-lived connections.

**Pinning a worker.** `http_pool.http2` on a worker spec skips the probe, with or without the flag: `true` forces HTTP/2 with prior knowledge, `false` forces HTTP/1.1. A worker pinned to `true` that only speaks HTTP/1.1 fails registration.

```bash
curl -X POST http://localhost:30000/workers \
  -H "Content-Type: application/json" \
  -d '{"url": "http://legacy-worker:8000", "http_pool": {"http2": false}}'
```

**Checking the result.** Each worker in `GET /workers` reports an `http2` field. The `smg_worker_http2` gauge is `1` when SMG speaks HTTP/2 to the worker and `0` otherwise. With the flag on, SMG also logs `resolved worker HTTP version` for each worker as it registers.

**Rolling out on a mixed fleet.** Because the version is chosen per worker, the flag can be turned on before, during, or after an engine upgrade: workers that answer HTTP/2 use it, and the rest stay on HTTP/1.1. The choice is made once, at registration, so a worker that gains HTTP/2 support in place keeps HTTP/1.1 until it registers again, for example when its pod is replaced or after it is removed and re-added. Turning the flag off returns every worker that is not pinned to HTTP/2 to HTTP/1.1 at the next restart.

---

## Configuration

| Flag | Default | Description |
|------|---------|-------------|
| `--max-payload-size` | `536870912` (512 MiB) | Largest accepted request body; also caps buffered worker responses |
| `--max-buffered-request-bytes` | `1048576` (1 MiB) | Largest eligible body buffered only to keep it retryable; `0` never buffers for retries |
| `--stream-body-stall-timeout-secs` | `300` | Abort a streamed body after a single client wait this long (`408`); `0` disables |
| `--request-timeout-secs` | `1800` | Total time for one upstream request, including a streamed response |
| `--upstream-pool-idle-timeout-secs` | `3` | Idle timeout for pooled upstream connections; `0` keeps them forever |
| `--upstream-http2` | off | Negotiate HTTP/2 per worker at registration |

See the [Configuration Reference](../../reference/configuration.md#request-handling-configuration) for the rest of the request-handling flags.

---

## Monitoring

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `smg_router_request_body_path_total` | Counter | `path`, `reason` | One per request on an eligible route. `path` is `buffered` or `streamed`; `reason` is the deciding rule, or for routers that always buffer, the router type (such as `pd`, `grpc`, or `openai`) or `model_selection` in IGW mode |
| `smg_router_request_buffers_released_early_bytes_total` | Counter | — | Bytes of request buffers freed at dispatch instead of at response completion |
| `smg_router_upstream_send_retries_total` | Counter | `router_type` | One-shot resends after a pre-response transport failure |
| `smg_worker_http2` | Gauge | `worker` | `1` if SMG speaks HTTP/2 to the worker, `0` otherwise |

```promql
# Share of eligible requests whose body streamed
sum(rate(smg_router_request_body_path_total{path="streamed"}[5m]))
  / sum(rate(smg_router_request_body_path_total[5m]))

# Why requests buffer or stream
sum by (path, reason) (rate(smg_router_request_body_path_total[5m]))

# Large requests running without router retries
sum(rate(smg_router_request_body_path_total{reason="retry_forfeited"}[5m]))

# Pre-response resends; a steady rate suggests the idle timeout
# is above the backend's keep-alive
sum(rate(smg_router_upstream_send_retries_total[5m]))

# Workers on HTTP/1.1
smg_worker_http2 == 0
```

---

## What's Next?

<div class="grid" markdown>

<div class="card" markdown>

### :material-refresh: Retries

What the router retries, and why streamed bodies get one attempt.

[Retries →](../reliability/retries.md)

</div>

<div class="card" markdown>

### :material-pin: Sticky Sessions

Routing hint headers that let large bodies stream under prompt-based policies.

[Sticky Sessions →](../routing/sticky-sessions.md)

</div>

<div class="card" markdown>

### :material-scale-balance: Load Balancing

Which policies read the prompt, and which route on headers or load alone.

[Load Balancing →](../routing/load-balancing.md)

</div>

<div class="card" markdown>

### :material-chart-box: Metrics Reference

Every metric SMG exports, with labels.

[Metrics Reference →](../../reference/metrics.md)

</div>

</div>
