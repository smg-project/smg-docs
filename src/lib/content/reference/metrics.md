---
title: Metrics Reference
---

# Metrics Reference

Complete reference for the Prometheus metrics SMG exports. Metrics are grouped by layer, from the gateway's own runtime through HTTP, routing, workers, discovery, and MCP, followed by groups that belong to specific features: routing policies, the priority scheduler, the RL control plane, and the HA mesh. The worker-side servicer's own families (`smg_servicer_*`) are on the [Servicer Metrics Reference](servicer-metrics.md).

A series appears on `/metrics` only after the first event that records it, and several groups exist only when the feature that produces them is enabled. Each entry says when it is emitted.

Since v1.13.0 the gateway publishes the overload-shed, retry, and circuit-breaker transition families at zero before their first event — the overload and retry families at gateway start-up, each worker's breaker transitions when the worker is registered — so `absent()` alerts stay quiet on a healthy process and `increase()` catches the first event after a restart. In v1.12.0 and earlier these families appear only on their first event. Their entries below list the exact series.

---

## Metrics Endpoint

Metrics are served at `GET /metrics` on a dedicated listener, `--prometheus-host`:`--prometheus-port` (default `0.0.0.0:29000`):

```bash
curl http://localhost:29000/metrics
```

Configure via CLI:

```bash
smg launch --prometheus-port 29000 --prometheus-host 0.0.0.0
```

- The port must be greater than `0`. Although `--help` describes `--prometheus-port 0` as binding an OS-assigned port, configuration validation rejects it at startup (`Port must be > 0`).
- The listener binds at startup and logs `Metrics server listening on <address> (/metrics)`. If the port is already taken, startup fails with `failed to bind metrics server on <address>` instead of continuing without metrics.
- `--prometheus-duration-buckets` changes the buckets of the latency histograms (see [Histogram Buckets](#histogram-buckets)).

### Engine Metrics Passthrough

`GET /engine_metrics` returns the engines' own Prometheus metrics in a single exposition. It is served on the main API listener (`--host`:`--port`, default port `30000`), not on the metrics port, and like `/health` it requires no API key.

```bash
curl http://localhost:30000/engine_metrics
```

- SMG fetches `<worker URL>/metrics` from every registered worker, once per base URL (data-parallel ranks that share a URL are scraped once), and adds a `worker_addr` label holding the worker's base URL to every sample.
- Metric names, HELP text, and label values are passed through unchanged, colons included (`vllm:num_requests_running`, `sglang:...`), so queries written against an engine's own `/metrics` work against the gateway. Before v1.11.0 the colons were rewritten to underscores (smg-project/smg#2636).
- Workers whose scrape fails or returns a non-success status are left out. If no worker is registered, or every scrape fails, the endpoint returns `500` with a plain-text reason (`No available workers` or `All backend requests failed`).

---

## Label Cardinality

Labels whose values a client can influence are bounded, so unexpected input cannot create an unbounded number of series:

| Label | Bound |
|-------|-------|
| `path` (HTTP metrics) | The matched route template, for example `/v1/responses/{response_id}`. Requests that match no route get a bare `404` from a fallback outside the metrics layer and are not recorded. |
| `method` (HTTP metrics) | `GET`, `POST`, `PUT`, `DELETE`, `PATCH`, `HEAD`, `OPTIONS`; any other method is `OTHER`. |
| `model` | The first 1,024 distinct values keep their own series; every later new value is reported as `other` (smg-project/smg#2093). |
| `tool_name` (MCP metrics) | Same rule as `model`: 1,024 distinct values, then `other`. |
| `op` (RL control plane) | A fixed list of engine control operations; any other path is `other`. |

The `model` and `tool_name` caps never evict a value once it is admitted, so the set of series stops growing at the cap instead of churning. Worker URLs (`worker`), status codes, and gateway error codes come from the gateway itself and are not capped. The priority scheduler's `tenant` label is not capped either; see the [Priority Scheduler Reference](priority-scheduler.md#metrics).

### Removed workers

The exporter cannot delete a series, so when a worker is removed SMG overwrites its per-worker gauges instead: `smg_worker_health` and `smg_worker_cb_state` become `-1`, `smg_worker_requests_active` and the circuit-breaker streak gauges become `0`, and the worker's `smg_engine_*` gauges become `-1`, as they also do while a worker is out of Ready. `smg_worker_http2` keeps its last value. Filter on the value (for example `== 1` or `>= 0`) when you aggregate these gauges.

---

## Layer 0: Runtime Metrics

Health of the gateway process itself: the Tokio async runtime that serves requests, and the memory allocator.

### Tokio Runtime Metrics

A background task started with the metrics server observes the runtime that serves requests. An event-loop canary sleeps for 10 ms in a loop and records how late it wakes up; a sampler reads the runtime's counters every second.

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `smg_tokio_event_loop_delay_seconds` | Histogram | None | How late the 10 ms canary sleep woke up |
| `smg_tokio_event_loop_stalls_total` | Counter | None | Canary wake-ups more than 5 ms late |
| `smg_tokio_global_queue_depth` | Gauge | None | Tasks waiting in the runtime's global queue |
| `smg_tokio_alive_tasks` | Gauge | None | Tasks spawned and not yet completed |
| `smg_tokio_workers` | Gauge | None | Runtime worker threads (see `--runtime-worker-threads`, a Rust-binary flag the pip `smg launch` does not accept) |
| `smg_tokio_worker_busy_ratio` | Gauge | `worker` | Fraction of the last sampling interval each worker thread was busy (0.0-1.0) |
| `smg_tokio_worker_parks_total` | Counter | `worker` | Times each worker thread parked (went idle) |

`worker` is the runtime worker index (`0`, `1`, ...). The delay histogram has its own buckets: `0, 0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0` seconds.

A stall means every task on the runtime, including accept loops, health checks, and streaming responses, was delayed by at least the measured drift, so a rising stall rate separates "gateway starved" from "backend slow". Busy ratios near 1.0 together with a growing global queue mean the runtime is saturated; a near-zero park rate while busy means sustained overload rather than bursts.

```promql
# Event-loop stalls per second
rate(smg_tokio_event_loop_stalls_total[5m])

# P99 event-loop wake delay
histogram_quantile(0.99, sum by (le) (rate(smg_tokio_event_loop_delay_seconds_bucket[5m])))

# Mean worker-thread utilization
avg(smg_tokio_worker_busy_ratio)
```

### Allocator Metrics

SMG uses jemalloc as its global allocator and exports jemalloc's own accounting, refreshed every 60 seconds by a dedicated thread (smg-project/smg#2125). The `smg` binary and the Python package both export these gauges: they use jemalloc and build with the `jemalloc-stats` feature, which is on by default. Builds for musl or MSVC targets use the system allocator and do not export them.

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `smg_allocator_allocated_bytes` | Gauge | None | Bytes in live Rust allocations |
| `smg_allocator_active_bytes` | Gauge | None | Bytes in active pages |
| `smg_allocator_resident_bytes` | Gauge | None | Upper bound on resident bytes of the jemalloc heap |
| `smg_allocator_metadata_bytes` | Gauge | None | jemalloc metadata bytes |

Resident bytes that grow while allocated bytes stay flat point at memory the allocator retains; allocated bytes that grow with them point at data the gateway itself holds. The gauges cover only Rust allocations made through SMG's jemalloc instance: memory allocated by the Python interpreter or by bundled C libraries is not included.

```promql
# Memory the allocator holds beyond live allocations
smg_allocator_resident_bytes - smg_allocator_allocated_bytes
```

#### `smg_allocator_prof_dumps_total`

Heap profile dumps asked of `POST /heap_profile`, by outcome (`ok`, `error`). Exported by the same builds as the gauges above.

| Type | Labels |
|------|--------|
| Counter | `outcome` |

---

### Log Writer Metrics

#### `smg_log_dropped_lines`

Log lines the lossy writer queue dropped since the process started, by sink (`stdout`, `file`). Published together with the warning that reports the lines dropped since the last report.

| Type | Labels |
|------|--------|
| Gauge | `sink` |

---

## Layer 1: HTTP Metrics

Metrics for requests arriving at the gateway's main listener. Probes served on the dedicated `--health-check-port` listener bypass this layer.

### `smg_http_requests_total`

HTTP requests received, counted when the request arrives.

| Type | Labels |
|------|--------|
| Counter | `method`, `path` |

```promql
# Request rate by endpoint
sum by (path) (rate(smg_http_requests_total[5m]))

# Total request rate
sum(rate(smg_http_requests_total[5m]))
```

---

### `smg_http_request_duration_seconds`

Time from receiving the request until the response head is returned. For a streaming response this does not include the time spent streaming the body.

| Type | Labels |
|------|--------|
| Histogram | `method`, `path` |

```promql
# P99 latency by endpoint
histogram_quantile(0.99, sum by (path, le) (rate(smg_http_request_duration_seconds_bucket[5m])))

# Average latency
sum(rate(smg_http_request_duration_seconds_sum[5m])) / sum(rate(smg_http_request_duration_seconds_count[5m]))
```

---

### `smg_http_responses_total`

HTTP responses by path, status code, and gateway error code.

A request is counted once, when its outcome is known: the response's status once the body has gone out in full (at its last frame, or once its bytes add up to the length the response declared), or `499` — nginx's "client closed request" — when the client disconnected first, before the response was produced or while its body was still streaming. The rare request the server itself dropped unanswered, such as on a handler panic or a forced shutdown, is counted as a `499` too.

| Type | Labels |
|------|--------|
| Counter | `path`, `status_code`, `error_code` |

`error_code` is the code carried by error responses the gateway generates (for example `admission_queue_full` or `worker_overload_protection_shed`); it is empty for other responses. The `499` samples carry `client_closed_request`. A response whose body failed after its head went out is counted with the head's status and `stream_error`, unless the head already carried a gateway error code, which is kept.

```promql
# Error rate (5xx responses)
sum(rate(smg_http_responses_total{status_code=~"5.."}[5m])) / sum(rate(smg_http_responses_total[5m]))

# Success rate
sum(rate(smg_http_responses_total{status_code="200"}[5m])) / sum(rate(smg_http_responses_total[5m]))

# Success rate for /v1/responses
sum(rate(smg_http_responses_total{path="/v1/responses",status_code=~"2.."}[5m]))
/
sum(rate(smg_http_responses_total{path="/v1/responses"}[5m]))

# Gateway-generated errors by code
sum by (error_code) (rate(smg_http_responses_total{error_code!=""}[5m]))

# Clients disconnecting before their response completed
sum by (path) (rate(smg_http_responses_total{error_code="client_closed_request"}[5m]))
```

---

### `smg_http_connections_active`

Requests currently in flight on the main listener. A streaming response stays in flight until its body finishes or the client disconnects (smg-project/smg#2130). Despite the name, it counts requests, not TCP connections.

| Type | Labels |
|------|--------|
| Gauge | None |

---

### `smg_http_connection_timeouts_total`

HTTP connections closed for carrying no request, by the timeout that closed them: `header_read` (no request head arrived in time) or `idle` (a kept-alive connection sent nothing more).

| Type | Labels |
|------|--------|
| Counter | `timeout` |

---

### `smg_http_connections_rejected_total`

HTTP connections closed at accept for arriving beyond the listener's connection limit: nothing was read from or written to them. Published at zero at start-up.

| Type | Labels |
|------|--------|
| Counter | None |

---

### `smg_http_inflight_request_age_count`

In-flight requests per age bucket, sampled every 20 seconds, for spotting stuck requests and for heatmaps. Unlike Prometheus histogram buckets, these buckets are not cumulative: each series counts requests with `gt` < age <= `le`.

| Type | Labels |
|------|--------|
| Gauge | `gt`, `le` |

Age bounds (seconds): 30, 60, 180, 300, 600, 1200, 3600, 7200, 14400, 28800, 86400. The first bucket has `gt="0"`; the last has `gt="86400"` and `le="+Inf"`.

```promql
# Requests in flight for more than 10 minutes
sum(smg_http_inflight_request_age_count{gt=~"600|1200|3600|7200|14400|28800|86400"})
```

---

### `smg_http_rate_limit_total`

Admission decisions of the concurrency limiter.

| Type | Labels |
|------|--------|
| Counter | `result` |

Values: `allowed`, `rejected`

Recorded unless `--max-concurrent-requests` is `0`. With the priority scheduler enabled, only rejections by the `--rate-limit-tokens-per-second` limiter are counted here; the scheduler's own outcomes are in `smg_scheduler_admit_total`.

```promql
# Rejection ratio
sum(rate(smg_http_rate_limit_total{result="rejected"}[5m])) / sum(rate(smg_http_rate_limit_total[5m]))
```

---

### Admission Queue Metrics

Unless `--max-concurrent-requests` is `0` or the priority scheduler is on, every request to an inference route holds an admission token for its full lifetime, streaming body included. A request that finds no free token waits in a FIFO queue of `--queue-size` slots (default `100`) for up to `--queue-timeout-secs` (default `60`). These metrics expose that queue.

#### `smg_admission_inflight`

Requests currently holding an admission token. Compare it with `--max-concurrent-requests` to see how close the gateway is to its concurrency cap.

| Type | Labels |
|------|--------|
| Gauge | None |

#### `smg_admission_queue_depth`

Requests currently waiting in the admission queue.

| Type | Labels |
|------|--------|
| Gauge | None |

#### `smg_admission_queue_rejected_total`

Requests rejected at admission.

| Type | Labels |
|------|--------|
| Counter | `reason` |

| `reason` | Response | Cause |
|----------|----------|-------|
| `full` | `429 admission_queue_full` | Every queue slot was taken |
| `timeout` | `503 admission_queue_timeout` | The request waited `--queue-timeout-secs` without getting a token |
| `multimodal_too_large` | `413 multimodal_payload_too_large` | The request's preprocessed media alone exceeds `--multimodal-max-inflight-bytes` |
| `multimodal_inflight` | `429 multimodal_inflight_budget` | The gateway's in-flight media budget stayed full |

The two `multimodal_*` reasons come from the `--multimodal-max-inflight-bytes` budget and are recorded whether or not the concurrency limiter is on. With queuing disabled (`--queue-size 0`), a request that finds no token gets `429 admission_queue_full` but is counted only in `smg_http_rate_limit_total`.

```promql
# Admission rejections per second, by reason
sum by (reason) (rate(smg_admission_queue_rejected_total[5m]))

# Requests waiting for a token
smg_admission_queue_depth
```

---

## Layer 2: Router Metrics

Metrics for request routing and processing.

### `smg_router_requests_total`

Requests processed by the router. The gRPC router records every retry attempt as another request, and each failed attempt in `smg_router_request_errors_total`.

| Type | Labels |
|------|--------|
| Counter | `router_type`, `backend_type`, `connection_mode`, `model`, `endpoint`, `streaming` |

- `router_type`: `http`, `grpc`, `openai`
- `backend_type`: `regular`, `pd` (prefill-decode and encode-prefill-decode), `external`. The streaming metrics below also use `harmony`, for Harmony-mode (gpt-oss) streaming chat on the gRPC router.
- `connection_mode`: `http`, `grpc`, `websocket`, `webrtc`. Requests to ZMQ workers run through the gRPC pipeline and are counted with `router_type="grpc"` and `connection_mode="grpc"`.
- `endpoint`: `chat`, `generate`, `completions`, `responses`, `messages`, `embeddings`, `classify`, `rerank`, `audio_transcriptions`, `realtime`, `realtime_sessions`, `realtime_client_secrets`, `realtime_transcription`, or `other` for routes without a dedicated label
- `streaming`: `true`, `false`

```promql
# Request rate by model
sum by (model) (rate(smg_router_requests_total[5m]))

# Streaming vs non-streaming
sum by (streaming) (rate(smg_router_requests_total[5m]))
```

---

### `smg_router_request_duration_seconds`

Total router request duration.

| Type | Labels |
|------|--------|
| Histogram | `router_type`, `backend_type`, `connection_mode`, `model`, `endpoint` |

---

### `smg_router_request_errors_total`

Router errors by type.

| Type | Labels |
|------|--------|
| Counter | `router_type`, `backend_type`, `connection_mode`, `model`, `endpoint`, `error_type` |

Error types: `no_workers`, `timeout`, `backend_error`, `validation_error`, `internal_error`. The Anthropic Messages provider also reports `parse_error` and `streaming_error`.

```promql
# Error rate by type
sum by (error_type) (rate(smg_router_request_errors_total[5m]))
```

---

### `smg_router_ttft_seconds`

Time to first token, recorded for streaming responses on the gRPC router. For `backend_type="pd"` the clock starts when the decode request is sent; see [`smg_pd_ttft_seconds`](#smg_pd_ttft_seconds) for the end-to-end value.

| Type | Labels |
|------|--------|
| Histogram | `router_type`, `backend_type`, `model`, `endpoint` |

```promql
# P50 TTFT by model
histogram_quantile(0.5, sum by (model, le) (rate(smg_router_ttft_seconds_bucket[5m])))
```

---

### `smg_router_tpot_seconds`

Time per output token (the mean inter-token latency of a request), recorded for streaming responses on the gRPC router that produce more than one token.

| Type | Labels |
|------|--------|
| Histogram | `router_type`, `backend_type`, `model`, `endpoint` |

```promql
# Average TPOT
sum(rate(smg_router_tpot_seconds_sum[5m])) / sum(rate(smg_router_tpot_seconds_count[5m]))
```

---

### `smg_router_time_to_first_token_seconds`

Time to first token on edges that resolve it: the same observation as `smg_router_ttft_seconds`, which keeps the request-duration buckets (and so the `le` values existing recording rules were written against). gRPC pipeline only. The buckets are its own, six per decade (1, 1.5, 2, 3, 5, 7.5) from 10 ms to 10 s, then 20 s, 30 s, 60 s and 120 s; a later first token lands in `+Inf`. `--prometheus-duration-buckets` does not apply to it.

| Type | Labels |
|------|--------|
| Histogram | `router_type`, `backend_type`, `model`, `endpoint` |

```promql
# P99 time to first token by model, on the fine edges
histogram_quantile(0.99, sum by (model, le) (rate(smg_router_time_to_first_token_seconds_bucket[5m])))
```

---

### `smg_router_request_time_per_output_token_seconds`

Per-request mean time per output token on edges that resolve it: the same observation as `smg_router_tpot_seconds`, which keeps the request-duration buckets. A per-request mean, as the servicer's `request_time_per_output_token` family, not the per-interval gap. gRPC pipeline only. The buckets are its own, six per decade from 1 ms to 1 s, then 2.5 s and 10 s for stalled decodes. `--prometheus-duration-buckets` does not apply to it.

| Type | Labels |
|------|--------|
| Histogram | `router_type`, `backend_type`, `model`, `endpoint` |

---

### `smg_router_tokens_total`

Token counts by type. Recorded for streaming responses on the gRPC router, and for non-streaming responses from the Anthropic Messages provider.

| Type | Labels |
|------|--------|
| Counter | `router_type`, `backend_type`, `model`, `endpoint`, `token_type` |

Token types: `input`, `output`

```promql
# Tokens per second
sum by (token_type) (rate(smg_router_tokens_total[5m]))

# Output/input ratio
sum(rate(smg_router_tokens_total{token_type="output"}[5m])) / sum(rate(smg_router_tokens_total{token_type="input"}[5m]))
```

---

### `smg_router_generation_duration_seconds`

Total generation time of a streaming response on the gRPC router.

| Type | Labels |
|------|--------|
| Histogram | `router_type`, `backend_type`, `model`, `endpoint` |

---

### `smg_router_upstream_responses_total`

Responses from upstream workers, recorded by the HTTP router (`router_type="http"`).

| Type | Labels |
|------|--------|
| Counter | `router_type`, `status_code`, `error_code` |

---

### `smg_router_upstream_send_retries_total`

Requests the HTTP router sent a second time because the first send failed before any response arrived, for example on a pooled connection the worker had already closed (smg-project/smg#2162). There is at most one such resend per send, it covers both regular and PD HTTP dispatch, and it does not apply to timeouts or to request bodies that cannot be replayed.

| Type | Labels |
|------|--------|
| Counter | `router_type` |

---

### `smg_router_request_body_path_total`

Per-request decision whether the router buffers the request body or streams it to the worker unread (smg-project/smg#2286). Recorded for `/generate`, `/v1/chat/completions`, `/v1/completions`, `/v1/messages`, `/v1/embeddings`, and `/v1/classify`. See [Request Body Streaming](../concepts/performance/request-streaming.md).

| Type | Labels |
|------|--------|
| Counter | `path`, `reason` |

- `path`: `buffered`, `streamed`
- `reason` for `buffered`: `routing_key_override`, `policy_needs_text`, `model_ambiguous`, `worker_mutates_body`, `wasm_request_hook`, `no_content_length`, `no_available_worker`, `model_selection`, `retryable` (kept replayable for router retries, within `--max-buffered-request-bytes`), or the router family name (for example `pd` or `grpc`) when the active router always parses bodies
- `reason` for `streamed`: `retry_forfeited` (larger than `--max-buffered-request-bytes`, so router retries are given up), `pure_forward` (router retries are off)

```promql
# Body-path decisions by reason
sum by (path, reason) (rate(smg_router_request_body_path_total[5m]))
```

---

### `smg_router_request_buffers_released_early_bytes_total`

Serialized size of request buffers the router freed as soon as the request was dispatched, instead of holding them until the response completed (smg-project/smg#2232). The HTTP routers release at dispatch when router retries are disabled; the gRPC router counts the built request's wire size when it drops the parsed request at dispatch.

| Type | Labels |
|------|--------|
| Counter | None |

---

### `smg_router_local_stop_aborts_total`

Streamed requests the gateway aborted at the engine because its own stop (a stop string or stop token id matched in the gateway, not by the engine) had ended every choice for the client while the engine was still generating. gRPC pipeline only.

| Type | Labels |
|------|--------|
| Counter | `router_type`, `backend_type`, `model`, `endpoint` |

---

### `smg_router_local_stop_tail_tokens_total`

Output tokens the engine streamed for a choice after the gateway's own stop had ended that choice, read before the request was aborted: the tail generated for nobody. What the abort cuts on the engine side is never read, so never counted. gRPC pipeline only.

| Type | Labels |
|------|--------|
| Counter | `router_type`, `backend_type`, `model`, `endpoint` |

---

### PD Disaggregation Metrics

Signals that only the gateway can measure, because it is the one component that sees both the prefill and the decode leg of a disaggregated request. Durations are recorded once per request, not per retry attempt. See [PD Disaggregation](../concepts/routing/pd-disaggregation.md).

The three histograms share the labels `backend_type` (always `pd`), `model`, and `runtime`, the worker's runtime type (for example `sglang`, `vllm`, or `tokenspeed`).

#### `smg_pd_ttft_seconds`

End-to-end time to first token of a disaggregated request, from the start of the prefill leg to the first decode token. It complements `smg_router_ttft_seconds{backend_type="pd"}`, which starts at the decode dispatch; for sequential PD the two differ by the prefill and KV-transfer time.

| Type | Labels |
|------|--------|
| Histogram | `backend_type`, `model`, `runtime` |

#### `smg_pd_prefill_duration_seconds`

Duration of the prefill leg. Recorded by the HTTP PD router and for sequential PD on the gRPC router.

| Type | Labels |
|------|--------|
| Histogram | `backend_type`, `model`, `runtime` |

#### `smg_pd_kv_transfer_duration_seconds`

KV-transfer window, from the prefill leg draining to the decode request being sent. Recorded for sequential (vLLM) PD on the gRPC router.

| Type | Labels |
|------|--------|
| Histogram | `backend_type`, `model`, `runtime` |

#### `smg_pd_kv_connector_mode_total`

KV connector mode chosen for vLLM prefill-decode dispatches.

| Type | Labels |
|------|--------|
| Counter | `mode` |

Modes: `mooncake`, `nixl`, `passthrough`

#### `smg_pd_bootstrap_failures_total`

HTTP PD requests whose body could not take the bootstrap fields (the body is not a JSON object).

| Type | Labels |
|------|--------|
| Counter | None |

#### `smg_pd_kv_transfer_failures_total`

vLLM NIXL dispatches where the prefill leg returned no `kv_transfer_params`, so the decode worker recomputes the prompt itself.

| Type | Labels |
|------|--------|
| Counter | None |

#### `smg_pd_admission_waits_total`

gRPC prefill-decode dispatches that waited for room in the decode engine's running window (its `--max-num-seqs` / `--max-running-requests`) before being sent (smg-project/smg#2466).

| Type | Labels |
|------|--------|
| Counter | None |

#### `smg_pd_admission_sheds_total`

gRPC prefill-decode dispatches shed with `503 worker_overload_protection_shed`, because no decode room freed within `--pd-admission-wait-secs` (default `30`) or because the request needs more rooms than the window holds. Each shed also increments `smg_worker_overload_shed_total{stage="pd_admission"}`. Decode engines that report no running window are never gated.

| Type | Labels |
|------|--------|
| Counter | None |

#### `smg_pd_admission_window`

The running window the PD admission gate bounds a decode worker by: the engine's running-request window the gate claims rooms against. `0` when the engine reports none and the gate abstains for that worker.

| Type | Labels |
|------|--------|
| Gauge | `worker` |

#### `smg_pd_decode_ttft_seconds`

The PD decode phase: from the decode dispatch to the first decode token, the engines' KV transfer and the decode scheduling included (the whole TTFT under parallel dispatch).

| Type | Labels |
|------|--------|
| Histogram | `backend_type`, `model`, `runtime` |

#### `smg_pd_prefill_admission_inflight`

Prefill requests this gateway has admitted to a prefill worker and not yet released, under the [Prefill Admission](configuration.md#prefill-admission) gate.

| Type | Labels |
|------|--------|
| Gauge | `worker` |

#### `smg_pd_prefill_admission_queued`

Requests waiting in the Prefill admission queue.

| Type | Labels |
|------|--------|
| Gauge | None |

#### `smg_pd_prefill_admission_wait_seconds`

Time a request waited in the Prefill admission queue. No buckets are configured for it, so it is exported as a Prometheus summary (`quantile` series plus `_sum` and `_count`).

| Type | Labels |
|------|--------|
| Summary | None |

#### `smg_pd_prefill_admission_rejections_total`

Requests the Prefill admission gate refused, by reason.

| Type | Labels |
|------|--------|
| Counter | `reason` |

Reasons: `queue_full` (the queue held `--prefill-queue-size` waiters already, `429 pd_prefill_queue_full`), `queue_timeout` (the request waited `--prefill-queue-timeout-secs` without a slot, `429 pd_prefill_queue_timeout`), `unavailable` (no prefill worker could take the request).

#### `smg_pd_rendezvous_failures_total`

PD rendezvous failures, by `leg` (the worker type of the side whose rendezvous failed) and `phase`: `probe` (a bootstrap port that refused the registration probe), `request` (the same probe, run when a decode side's engine failed a request without a reason; counted on the prefill side whose port it is), and the failures the engines report on the gRPC path, `bootstrap` (the sides never met) and `transfer` (the KV handoff after they met failed). Its own family: the injection failure counters above keep their unlabelled series.

| Type | Labels |
|------|--------|
| Counter | `leg`, `phase` |

```promql
# P95 end-to-end TTFT for PD requests, by model
histogram_quantile(0.95, sum by (model, le) (rate(smg_pd_ttft_seconds_bucket[5m])))

# PD dispatches that waited for, or were shed by, decode admission
rate(smg_pd_admission_waits_total[5m])
rate(smg_pd_admission_sheds_total[5m])
```

---

### Multimodal Metrics

Recorded by the gRPC pipeline for requests that carry images, audio, or video. See [Multimodal](../concepts/architecture/multimodal.md).

#### `smg_mm_tensors_total`

Preprocessed media tensors sent to engines, by transport.

| Type | Labels |
|------|--------|
| Counter | `runtime`, `path` |

- `runtime`: `vllm`, `tokenspeed`
- `path`: `inline` (bytes in the request), `shm` (shared memory, per `--multimodal-tensor-transport`), `remote` (RDMA pixel lane)

#### `smg_mm_tensor_bytes_total`

Bytes of the tensors counted by `smg_mm_tensors_total`.

| Type | Labels |
|------|--------|
| Counter | `runtime`, `path` |

#### `smg_mm_shm_write_failures_total`

Shared-memory tensor writes that failed and fell back to inline transport.

| Type | Labels |
|------|--------|
| Counter | `runtime` |

#### `smg_mm_processing_total`

Where a multimodal request's media was fetched and preprocessed (the `--mm-processing` decision for vLLM gRPC workers), and why.

| Type | Labels |
|------|--------|
| Counter | `model`, `mode`, `reason` |

- `mode`: `router`, `worker`
- `reason`: `config` (set explicitly to `router` or `worker`); under `auto`: `auto_uniform` (every worker of the model accepts media references), `auto_mixed`, `auto_none` (no registered worker), `plan_not_forwardable`, `model_not_opted_in`

#### `smg_mm_processing_seconds`

Seconds a multimodal request spent in a processing stage, by model: `config_lookup`, `media_fetch_decode`, `preprocess`, `token_expand`, and `total` for the whole pipeline. The histogram has buckets of its own, from 0.5 ms to 30 s.

| Type | Labels |
|------|--------|
| Histogram | `model`, `stage` |

```promql
# P99 of the fetch-and-decode stage, by model
histogram_quantile(0.99, sum by (model, le) (rate(smg_mm_processing_seconds_bucket{stage="media_fetch_decode"}[5m])))
```

#### `smg_mm_media_per_request`

Media items a generation request carried, by model and modality, counted once per request before any refusal or limit (a refused request counts; a token count does not). Buckets from 1 to 64 items.

| Type | Labels |
|------|--------|
| Histogram | `model`, `modality` |

Modalities: `image`, `image_embeds`, `audio`, `video`.

#### `smg_mm_media_items_total`

Media items generation requests carried, by model, modality and source, counted before any refusal or limit.

| Type | Labels |
|------|--------|
| Counter | `model`, `modality`, `source` |

Sources, as the fetch reads them: `url` (fetched over the network), `data_url` (a `data:` URL in the request), `inline` (bytes in the request), `file` (a file of the host).

#### `smg_mm_media_fetch_seconds`

Seconds one media fetch and decode took, by model, modality, source and outcome. The histogram has buckets of its own, from 0.5 ms to 30 s.

| Type | Labels |
|------|--------|
| Histogram | `model`, `modality`, `source`, `outcome` |

Outcomes: `ok` (fetched and decoded), `too_large` (the payload, or what it decodes to, is over a size limit), `client_fault` (a URL that cannot be fetched, bytes that do not decode, a host the policy refuses), `host_fault` (a fetch that timed out, a host that failed, a decoder this process lacks).

#### `smg_mm_media_fetch_bytes_total`

Bytes the media fetches brought in, by model, modality and source.

| Type | Labels |
|------|--------|
| Counter | `model`, `modality`, `source` |

#### `smg_mm_pixel_cache_hits_total`

Lookups in the router-side pixel cache (`--mm-pixel-cache-mb`: one cache per process, shared by every model, keyed by image and model configuration) that found the image's preprocessed encoder input, by model. The hit and miss series of a model both exist from its first lookup, so a hit rate reads from the start.

| Type | Labels |
|------|--------|
| Counter | `model` |

```promql
# Pixel cache hit rate, by model
sum by (model) (rate(smg_mm_pixel_cache_hits_total[5m])) / sum by (model) (rate(smg_mm_pixel_cache_hits_total[5m]) + rate(smg_mm_pixel_cache_misses_total[5m]))
```

#### `smg_mm_pixel_cache_misses_total`

Pixel cache lookups that had to preprocess the image, by model.

| Type | Labels |
|------|--------|
| Counter | `model` |

#### `smg_mm_pixel_cache_evictions_total`

Entries the pixel cache dropped to stay within its byte budget. This counter and the two gauges below are published at zero when the cache is built, so they are on the first scrape.

| Type | Labels |
|------|--------|
| Counter | None |

#### `smg_mm_pixel_cache_bytes`

Bytes of preprocessed encoder inputs the pixel cache holds.

| Type | Labels |
|------|--------|
| Gauge | None |

#### `smg_mm_pixel_cache_entries`

Images the pixel cache holds.

| Type | Labels |
|------|--------|
| Gauge | None |

#### `smg_mm_turbojpeg_available`

`1` when JPEGs decode through libjpeg-turbo, `0` through the pure-Rust fallback.

| Type | Labels |
|------|--------|
| Gauge | None |

#### `smg_mm_webp_available`

`1` when WebP files decode through libwebp, `0` through the pure-Rust fallback.

| Type | Labels |
|------|--------|
| Gauge | None |

---

### Tokenizer Cache Metrics

Process-wide activity of the tokenizer [L0/L1 caches](../concepts/performance/tokenizer-caching.md), summed across all tokenizers (smg-project/smg#2603). The totals are read on every scrape, so both layers always appear; a disabled layer stays at zero. Labels carry no model IDs or prompt text.

#### `smg_tokenizer_cache_lookups_total`

Cache lookups by layer and result, one outcome per lookup. An L0 hit skips L1, and L1 counts inputs without usable special-token boundaries as misses.

| Type | Labels |
|------|--------|
| Counter | `layer`, `result` |

- `layer`: `l0` (exact match), `l1` (prefix match)
- `result`: `hit`, `miss`

#### `smg_tokenizer_cache_evictions_total`

Entries removed to make room. Clearing a cache, dropping a tokenizer, or replacing an entry does not count.

| Type | Labels |
|------|--------|
| Counter | `layer` |

#### `smg_tokenizer_cache_reused_bytes_total`

UTF-8 input bytes served by cache hits: whole inputs for L0, matched prefixes for L1. This measures reuse volume, not cache memory or CPU time saved.

| Type | Labels |
|------|--------|
| Counter | `layer` |

```promql
# Hit ratio per layer (each layer against its own lookups)
sum by (layer) (rate(smg_tokenizer_cache_lookups_total{result="hit"}[5m]))
/
sum by (layer) (rate(smg_tokenizer_cache_lookups_total[5m]))

# Evictions per second, a sign the cache is too small
sum by (layer) (rate(smg_tokenizer_cache_evictions_total[5m]))
```

---

### Tokenizer Encode Metrics

Process-lifetime totals of the tokenizer's encode and decode paths, read on every scrape like the cache totals.

#### `smg_tokenizer_encode_path_loads_total`

Hugging Face tokenizers loaded, by the encode path chosen at load: `v1` (the opt-in tokenizers v1 pipeline), `native` (the direct byte-level encoder) or `tokenizers` (the reference implementation). A tokenizer loaded twice counts twice.

| Type | Labels |
|------|--------|
| Counter | `path` |

#### `smg_tokenizer_encode_fallbacks_total`

Inputs the direct byte-level encoder gave up to the reference implementation after the load's choice, by reason: `not_nfc` (text not in NFC under an NFC normalizer) or `piece_declined` (the model declined a piece).

| Type | Labels |
|------|--------|
| Counter | `reason` |

#### `smg_detokenize_prefix_mismatch_total`

Incremental decode steps and end-of-input finishes whose output no longer started with the text already decoded and went on with the remainder (several per stream are possible; unary responses count too), by kind: `byte_run` (an invalid byte collapsed a ByteFallback run whose characters were already decoded) or `decoder` (another decoder step rewrote the decoded prefix).

| Type | Labels |
|------|--------|
| Counter | `kind` |

---

### Responses Metrics

The Responses API's streams and stores.

#### `smg_responses_stream_failures_total`

Responses streams that ended with a `response.failed` terminal event, by model and reason (`stream_error`, `server_error`, `other`).

| Type | Labels |
|------|--------|
| Counter | `model`, `reason` |

#### `smg_responses_not_stored_total`

Responses with `store: true` whose record or conversation linking was not written, by mode (`foreground`: the request was answered with the failure; `background`: the record keeps what it had) and step (`record`: nothing is stored; `linking`: the record is stored, its items are not in the conversation).

| Type | Labels |
|------|--------|
| Counter | `mode`, `step` |

#### `smg_history_memory_records`

Records the in-memory history backend holds, by kind (`responses`, `conversations`, `items`), sampled every 10 seconds. Memory backend only: a store that cannot count its records without a query (Postgres, Redis, Oracle) sets nothing.

| Type | Labels |
|------|--------|
| Gauge | `kind` |

---

## Layer 3: Worker Metrics

Metrics for worker pool management and resilience.

### `smg_worker_pool_size`

Workers registered, per type, connection mode, and model.

| Type | Labels |
|------|--------|
| Gauge | `worker_type`, `connection_mode`, `model` |

- `worker_type`: `regular`, `prefill`, `decode`, `encode`
- `connection_mode`: `http`, `grpc`, `zmq`

---

### `smg_worker_requests_active`

Requests the gateway currently has in flight to each worker.

| Type | Labels |
|------|--------|
| Gauge | `worker` |

```promql
# Load distribution across workers
smg_worker_requests_active / ignoring(worker) group_left sum without (worker) (smg_worker_requests_active)
```

---

### `smg_worker_requests_total`

Requests dispatched to a worker, by worker URL and model, counted at each send: a retried request counts once per attempt.

| Type | Labels |
|------|--------|
| Counter | `worker`, `model` |

---

### `smg_worker_health`

Worker health status.

| Type | Labels | Values |
|------|--------|--------|
| Gauge | `worker` | `1` = Ready, `0` = any other status (pending, not ready, failed, or draining), `-1` = removed |

```promql
# Count healthy workers
count(smg_worker_health == 1)

# Alert on unhealthy workers
smg_worker_health == 0
```

---

### `smg_worker_state`

Routing state of a worker: `1` on the series of its current state and reason, `0` on a series it left. `state` is `available` or `excluded`; `reason` is the first fact of the routing decision that excludes the worker: `pending`, `not_ready`, `failed`, `draining` (its status), `overloaded` (the load monitor's veto), `unreachable` or `wedged` (the liveness veto), `circuit_open` (its breaker), or `none` while it is available.

| Type | Labels |
|------|--------|
| Gauge | `worker`, `state`, `reason` |

```promql
# Excluded workers and why
smg_worker_state{state="excluded"} == 1
```

---

### `smg_worker_http2`

Whether the gateway speaks HTTP/2 with prior knowledge to each worker: `1` when it was negotiated at registration under `--upstream-http2` or pinned with `http_pool.http2` on the worker spec, otherwise `0` (HTTP/1.1). Set when the worker is registered.

| Type | Labels |
|------|--------|
| Gauge | `worker` |

---

### `smg_worker_health_checks_total`

Health check results.

| Type | Labels |
|------|--------|
| Counter | `worker_type`, `result` |

Results: `success`, `failure`

---

### `smg_worker_selection_total`

Worker selection events by load balancer.

| Type | Labels |
|------|--------|
| Counter | `worker_type`, `connection_mode`, `model`, `policy` |

`policy` is the routing policy name, for example `cache_aware` or `round_robin`.

---

### `smg_worker_errors_total`

Worker-level errors by type, recorded by the HTTP routers when a request to a worker ends in a server error (a 5xx status from the worker or a failed send), and by gRPC prefill-decode dispatch when a leg fails. Regular-mode (non-PD) requests on the gRPC pipeline, which also serves ZMQ workers, are not counted.

| Type | Labels |
|------|--------|
| Counter | `worker_type`, `connection_mode`, `error_type` |

Error types: `timeout` (status `504`) and `backend_error` (other server errors, and every failure on the gRPC PD paths).

---

### KV Event Metrics

The per-worker subscriptions to gRPC workers' KV cache events, which feed the chain index that event-driven cache-aware routing reads, and the batches they carry. See [KV Events Cache-Aware Routing](../getting-started/kv-events-cache-aware.md). `worker` is the worker URL. When a worker's subscription starts, its gap, missed-batch, resync, failure, parent-less, sub-block and degraded-rank series are published at zero, so a series that is absent means the worker has no subscription, not that nothing happened.

#### `smg_kv_event_subscriptions_total`

KV event streams connected, by worker. A reconnect counts again, so a worker whose stream keeps dropping shows as a rising counter where the log has one line per attempt.

| Type | Labels |
|------|--------|
| Counter | `worker` |

```promql
# Workers that reconnected their KV event stream in the last hour: connected more than
# once in the gateway's life, and again inside the range (the counter counts the first
# connection too, and a gateway restart resets it)
increase(smg_kv_event_subscriptions_total[1h]) > 0 and smg_kv_event_subscriptions_total > 1
```

#### `smg_kv_event_subscription_failures_total`

Failures of the per-worker tasks that subscribe to gRPC workers' KV cache events for event-driven cache-aware routing. See [Cache-Aware Routing with KV Events](../getting-started/kv-events-cache-aware.md).

| Type | Labels |
|------|--------|
| Counter | `worker`, `reason` |

Reasons: `panic`, `join_error`, `intern_failed`

#### `smg_kv_event_stream_errors_total`

KV event streams that failed after they had connected, by worker and the gRPC status of the error in lower case (`out_of_range`, `unavailable`, `data_loss`, `unimplemented`, ...). A stream that dies on its first message at every reconnect shows here, where the connection counter alone reads as a healthy subscription.

| Type | Labels |
|------|--------|
| Counter | `worker`, `error` |

#### `smg_kv_events_unavailable`

`1` while a worker's backend serves no KV events (its `SubscribeKvEvents` answered `UNIMPLEMENTED`: the engine's publisher is off), `0` once the worker leaves. Cache-aware routing is blind to such a worker's cache.

| Type | Labels |
|------|--------|
| Gauge | `worker` |

```promql
# Workers whose cache the router cannot see
smg_kv_events_unavailable == 1
```

#### `smg_kv_event_batches_total`

KV event batches received, by worker and what the subscriber did with each.

| Type | Labels |
|------|--------|
| Counter | `worker`, `disposition` |

Dispositions: `applied` (a live batch applied to the index), `snapshot` (a chunk of a relay's state snapshot, applied in place of the worker's earlier state), `stale` (a sequence number the rank's cursor had already passed, skipped), `load_only` (a batch carrying the engine's load record and no cache events: the load is read, nothing is indexed).

#### `smg_kv_event_gaps_total`

Sequence gaps in a worker's KV event stream, by outcome.

| Type | Labels |
|------|--------|
| Counter | `worker`, `outcome` |

Outcomes: `replay_requested` (the first sight of a gap: the subscriber reconnects asking the publisher to replay the missing batches), `unrecovered_kept` (the replay did not bring them and the stream went on from the live events with the worker's index kept; the rank counts as degraded), `unrecovered_cleared` (the same for a gap too large to keep the index: the worker's index state was cleared first).

#### `smg_kv_event_missed_batches_total`

KV event batches the publisher skipped and could not replay, by worker: a gap's size, counted once, when its replay is known not to bring the batches: the `unrecovered_kept` and `unrecovered_cleared` outcomes of `smg_kv_event_gaps_total`, or a pending replay dropped when the worker's stream is reset or cleared (the `publisher_restart`, `data_loss`, `out_of_range` and `gap_cleared` reasons of `smg_kv_event_resyncs_total`); the replay request itself adds nothing.

| Type | Labels |
|------|--------|
| Counter | `worker` |

#### `smg_kv_event_resyncs_total`

Times a worker's index state was dropped to be rebuilt from the stream, by reason.

| Type | Labels |
|------|--------|
| Counter | `worker`, `reason` |

Reasons: `publisher_restart` (a rank's sequence numbers started over), `out_of_range` and `data_loss` (the stream ended with that status: the publisher declared its history gone), `gap_cleared` (an unreplayed gap too large to keep the index), `snapshot` (a relay's state snapshot replaced the worker's state).

#### `smg_kv_event_degraded_ranks`

Data-parallel ranks of the worker whose index may be stale: a gap that was not replayed, or a relay snapshot that started after the engine's earliest blocks. Back to `0` when the state is resynced.

| Type | Labels |
|------|--------|
| Gauge | `worker` |

#### `smg_kv_event_lag_seconds`

Age of a KV event batch when it is applied: the router's clock minus the publisher's timestamp, by worker. A batch is normally a few milliseconds old; seconds or more mean a relay replaying history or a router falling behind. The histogram has buckets of its own, eighteen edges from 0.1 ms to 60 s, so its percentiles aggregate across workers.

| Type | Labels |
|------|--------|
| Histogram | `worker` |

```promql
# P99 age of the KV events a worker's batches carry
histogram_quantile(0.99, sum by (worker, le) (rate(smg_kv_event_lag_seconds_bucket[5m])))
```

#### `smg_kv_event_apply_seconds`

Time to apply one KV event batch to the index, by worker. The buckets double from half a microsecond to a quarter of a second.

| Type | Labels |
|------|--------|
| Histogram | `worker` |

#### `smg_kv_event_blocks_total`

Blocks named by the applied KV events, by worker and op, counted before the monitor's tier and group filters: the rate at which the index is offered blocks.

| Type | Labels |
|------|--------|
| Counter | `worker`, `op` |

Ops: `stored` and `removed` (live events), `snapshot` (the live set a relay's state snapshot listed).

```promql
# Blocks stored per second, by worker
sum by (worker) (rate(smg_kv_event_blocks_total{op="stored"}[5m]))
```

#### `smg_kv_event_parentless_stores_total`

Stores whose parent block the index did not hold for the worker (the parent evicted, dropped or cleared), placed as a new chain from the root instead, by worker.

| Type | Labels |
|------|--------|
| Counter | `worker` |

#### `smg_kv_event_parentless_blocks_total`

Blocks the parent-less stores carried, by worker.

| Type | Labels |
|------|--------|
| Counter | `worker` |

#### `smg_kv_event_sub_block_stores_total`

Stores of blocks narrower than the worker's block of record (a hybrid engine's prefix entries at its hash granularity), left out of the index, by worker.

| Type | Labels |
|------|--------|
| Counter | `worker` |

#### `smg_kv_event_sub_block_blocks_total`

Blocks the sub-block stores carried, by worker.

| Type | Labels |
|------|--------|
| Counter | `worker` |

#### `smg_kv_block_size_tokens`

Tokens per KV block of a model as the gateway knows it: seeded from a worker's registration, replaced by the size of the first block its KV event stream stores, `0` once the model's last subscription ended and no size is known.

| Type | Labels |
|------|--------|
| Gauge | `model` |

---

### Overload Protection Metrics

A worker whose load report crosses an overload threshold is excluded from routing until the signal recovers; when every candidate is excluded, the request is shed immediately (smg-project/smg#2220). Protection is on when `--worker-overload-protection`, `--worker-overload-token-usage`, or `--worker-overload-waiting-requests` is set, or when a worker spec has an `overload` block. See [Overload Protection](../concepts/reliability/overload-protection.md).

#### `smg_workers_overloaded`

Workers currently flagged overloaded and excluded from routing, per model. The flag is evaluated on each load poll, and the gauge changes only when a worker's flag flips.

| Type | Labels |
|------|--------|
| Gauge | `model` |

#### `smg_worker_overload_shed_total`

Requests shed with `503 worker_overload_protection_shed`. The response carries `Retry-After` set to the load-monitor interval, and the gateway does not retry it internally.

| Type | Labels |
|------|--------|
| Counter | `stage` |

| `stage` | Cause |
|---------|-------|
| `selection` | Every worker the request could use was flagged overloaded |
| `dispatch` | The selected worker became overloaded between selection and dispatch |
| `pd_admission` | A gRPC prefill-decode dispatch found no room on the decode engine (see `smg_pd_admission_sheds_total`); this stage does not depend on overload protection being enabled |

Since v1.13.0 the gateway publishes all three `stage` series at zero when the metrics listener starts, whether or not overload protection is enabled; the same start-up pass zeroes the selection-stage fallback counters `smg_worker_overload_fallback_total` and `smg_worker_liveness_fallback_total`. `smg_workers_overloaded` is not pre-published: the gauge still first appears when a load poll flips a worker's overload flag.

```promql
# Overload sheds per second, by stage
sum by (stage) (rate(smg_worker_overload_shed_total[5m]))

# Models with vetoed workers
smg_workers_overloaded > 0
```

---

### Liveness Metrics

The liveness veto excludes a worker from routing on what the transport shows rather than on a load report: `unreachable` when its transport fails and nothing has been heard from it for the stall threshold, `wedged` when it answers polls but holds requests that make no progress while its queue grows. A veto that changes reason clears the old reason's series and sets the new one. See [When a Worker Dies or Loses Its Link](../concepts/reliability/overload-protection.md#when-a-worker-dies-or-loses-its-link).

#### `smg_worker_stalled`

`1` while routing skips the worker under the liveness veto, by worker and reason; `0` once the veto lifts.

| Type | Labels |
|------|--------|
| Gauge | `worker`, `reason` |

#### `smg_worker_stall_transitions_total`

Times the liveness veto was set on a worker, by worker and reason.

| Type | Labels |
|------|--------|
| Counter | `worker`, `reason` |

```promql
# Workers vetoed by liveness right now, with the reason
smg_worker_stalled == 1
```

---

### Circuit Breaker Metrics

#### `smg_worker_cb_state`

Circuit breaker state per worker.

| Type | Labels | Values |
|------|--------|--------|
| Gauge | `worker` | `0` = closed, `1` = open, `2` = half-open, `-1` = worker removed |

```promql
# Workers with open circuits
count(smg_worker_cb_state == 1)
```

#### `smg_worker_cb_transitions_total`

Circuit breaker state transitions.

| Type | Labels |
|------|--------|
| Counter | `worker`, `from`, `to` |

States: `closed`, `open`, `half_open`

Since v1.13.0 the gateway publishes the five transitions a breaker can make (`closed` → `open`, `open` → `half_open`, `open` → `closed`, `half_open` → `closed`, `half_open` → `open`) at zero for each worker when it is registered, so `increase()` sees a worker's first trip. `smg_worker_cb_outcomes_total` is not pre-published and still appears per worker with its first recorded outcome.

#### `smg_worker_cb_outcomes_total`

Request outcomes tracked by the circuit breaker.

| Type | Labels |
|------|--------|
| Counter | `worker`, `outcome` |

Outcomes: `success`, `failure`

#### `smg_worker_cb_consecutive_failures`

Consecutive failures per worker.

| Type | Labels |
|------|--------|
| Gauge | `worker` |

#### `smg_worker_cb_consecutive_successes`

Consecutive successes per worker.

| Type | Labels |
|------|--------|
| Gauge | `worker` |

#### `smg_worker_cb_health_readmissions_total`

Times a worker's open circuit breaker went half-open because the worker's health returned (its promotion to Ready) rather than at the breaker's timeout, by worker. Published at zero before the worker's first return, so the series exists from the start.

| Type | Labels |
|------|--------|
| Counter | `worker` |

---

### Retry Metrics

Since v1.13.0 the gateway publishes these three families at start-up: `smg_worker_retries_total` and `smg_worker_retries_exhausted_total` at zero for every combination of the `regular`, `prefill`, and `decode` worker types and the ten `endpoint` values, and the `attempt="1"` series of `smg_worker_retry_backoff_seconds` as an empty distribution (a `_count` of 0; later attempts still appear on their first backoff). The external-provider series (`worker_type="external"`) are not pre-published: each appears on its first respective event — `smg_worker_retries_total` on the first retry against a provider, `smg_worker_retries_exhausted_total` on the first exhaustion.

#### `smg_worker_retries_total`

Retry attempts.

| Type | Labels |
|------|--------|
| Counter | `worker_type`, `endpoint` |

`worker_type` is `regular`, `prefill`, or `decode` for self-hosted workers, or `external` for requests proxied to an [external provider](../getting-started/external-providers.md); a PD retry counts once for the `prefill` and once for the `decode` worker type. `endpoint` is the retried route reduced to `chat`, `generate`, `completions`, `rerank`, `responses`, `decisions`, `systemone`, `messages`, `audio_transcriptions`, or `other` — a narrower set than `smg_router_requests_total` uses.

#### `smg_worker_retries_exhausted_total`

Requests that exhausted all retries.

| Type | Labels |
|------|--------|
| Counter | `worker_type`, `endpoint` |

#### `smg_worker_retry_backoff_seconds`

Backoff before each retry, by retry number: `attempt="1"` is the wait before the first retry, which is the second attempt. The histogram has buckets of its own, twelve edges from 10 ms to 60 s, which span the exponential schedule from the default initial backoff (50 ms) to the default maximum (30 s).

| Type | Labels |
|------|--------|
| Histogram | `attempt` |

```promql
# P99 backoff before the second retry (the third attempt)
histogram_quantile(0.99, sum by (le) (rate(smg_worker_retry_backoff_seconds_bucket{attempt="2"}[5m])))
```

---

### Engine Load Metrics

SMG re-exports every worker load report it receives as `smg_engine_*` gauges (smg-project/smg#2226). By default the load monitor polls every Ready worker every `--load-monitor-interval` seconds (default `10`), so these gauges exist for any worker that reports load. With `--disable-load-monitoring`, a worker group is polled only when a load-aware routing policy, worker overload protection, or `--engine-metrics` needs the data; `--engine-metrics`, a Rust-binary flag the pip `smg launch` does not accept, forces polling for these gauges alone (see [Load Monitoring Configuration](configuration.md#load-monitoring-configuration)). gRPC and ZMQ workers answer the `GetLoads` load query; HTTP workers are read through their loads endpoint, falling back to parsing vLLM or SGLang `/metrics`.

Core gauges, one series per DP rank:

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `smg_engine_running_requests` | Gauge | `worker`, `model`, `dp_rank` | Requests running in the engine |
| `smg_engine_waiting_requests` | Gauge | `worker`, `model`, `dp_rank` | Requests waiting in the engine |
| `smg_engine_token_usage` | Gauge | `worker`, `model`, `dp_rank` | KV-cache token usage (0.0-1.0) |
| `smg_engine_gen_throughput` | Gauge | `worker`, `model`, `dp_rank` | Generation throughput (tokens/s) |
| `smg_engine_cache_hit_rate` | Gauge | `worker`, `model`, `dp_rank` | Prefix-cache hit rate (0.0-1.0) |

PD gauges, emitted only for ranks whose report carries a disaggregation section:

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `smg_engine_pd_kv_transfer_latency_ms` | Gauge | `worker`, `role`, `dp_rank` | KV transfer latency (ms) |
| `smg_engine_pd_kv_transfer_speed_gb_s` | Gauge | `worker`, `role`, `dp_rank` | KV transfer speed (GB/s) |
| `smg_engine_pd_prefill_queue_reqs` | Gauge | `worker`, `role`, `dp_rank` | Requests in the prefill queue |
| `smg_engine_pd_decode_queue_reqs` | Gauge | `worker`, `role`, `dp_rank` | Requests in the decode queue |

`role` is the engine-reported role: `prefill`, `decode`, or `null`. Values are whatever the engine reports, so which fields are meaningful depends on the engine. When a worker is removed or leaves Ready, its gauges are set to `-1`. The same load snapshot is available as JSON from `GET /loads` on the main port.

#### `smg_engine_kv_capacity_tokens`

Engine-reported KV cache capacity in tokens, by worker, model and DP rank: the denominator of `smg_engine_token_usage`. Absent while the engine has not reported it (a load record that carries only the core figures keeps the last reported value); divided by `smg_kv_block_size_tokens` it is the engine's total KV blocks.

| Type | Labels |
|------|--------|
| Gauge | `worker`, `model`, `dp_rank` |

#### `smg_engine_load_polls_total`

Load-monitor tick decisions, by worker and mode: `poll` (no pushed load record on file, the worker is asked), `fallback` (the pushed records are older than the tick interval, the worker is asked), `skipped_fresh_push` (the worker's KV event stream pushed its load within the interval; no load query).

| Type | Labels |
|------|--------|
| Counter | `worker`, `mode` |

```promql
# KV-cache usage per worker (removed and non-Ready workers report -1)
max by (worker) (smg_engine_token_usage >= 0)

# Requests waiting in engines, per model
sum by (model) (smg_engine_waiting_requests >= 0)
```

---

## Layer 4: Discovery Metrics

Metrics for Kubernetes service discovery, emitted when `--service-discovery` is enabled. The `source` label is always `kubernetes`.

### `smg_discovery_registrations_total`

Worker registrations submitted by the discovery reconciler.

| Type | Labels |
|------|--------|
| Counter | `source`, `result` |

Results: `success` (the registration job was queued), `failed`

---

### `smg_discovery_deregistrations_total`

Worker removals submitted by the reconciler.

| Type | Labels |
|------|--------|
| Counter | `source`, `reason` |

Reasons: `reconciled`

---

### `smg_discovery_sync_duration_seconds`

Duration of a reconcile pass that added or removed workers.

| Type | Labels |
|------|--------|
| Histogram | `source` |

---

### `smg_discovery_workers_discovered`

Workers the reconciler currently wants registered.

| Type | Labels |
|------|--------|
| Gauge | `source` |

---

### `smg_discovery_snapshot_errors_total`

Discovery source observations rejected, by source and reason: `unreadable` (the source could not be read: a failed request, a failed command, an unreadable file) or `invalid` (it was read but did not parse as a snapshot). A read that fails leaves the last good snapshot in place, so a broken source never removes a worker.

| Type | Labels |
|------|--------|
| Counter | `source`, `reason` |

---

### `smg_discovery_skipped_total`

Discovered workers left out of an otherwise good snapshot, by source and reason: `invalid` (a record the provider could not turn into a worker) or `conflict` (a record that collides with another). Counted on every read; a skip is logged when it first appears.

| Type | Labels |
|------|--------|
| Counter | `source`, `reason` |

---

## Layer 5: MCP Tool Metrics

Metrics for Model Context Protocol tool execution.

### `smg_mcp_tool_calls_total`

MCP tool invocations.

| Type | Labels |
|------|--------|
| Counter | `model`, `tool_name`, `result` |

Results: `success`, `error`

```promql
# Tool success rate
sum(rate(smg_mcp_tool_calls_total{result="success"}[5m])) / sum(rate(smg_mcp_tool_calls_total[5m]))

# Most used tools
topk(10, sum by (tool_name) (rate(smg_mcp_tool_calls_total[5m])))
```

---

### `smg_mcp_tool_duration_seconds`

Tool execution duration.

| Type | Labels |
|------|--------|
| Histogram | `model`, `tool_name` |

---

### `smg_mcp_servers_active`

MCP servers the gateway is connected to, updated when a server is registered.

| Type | Labels |
|------|--------|
| Gauge | None |

---

### `smg_mcp_tool_iterations_total`

Tool-loop iterations in the Responses API and Anthropic Messages MCP loops.

| Type | Labels |
|------|--------|
| Counter | `model` |

---

## Routing Policy Metrics

Decision counters and state gauges of the routing policies. See [Load Balancing](../concepts/routing/load-balancing.md) for the policies themselves.

### `smg_policy_inflight_reconciled_total`

Bookings a policy released at the per-poll reconciliation of its in-flight accounting because their completion never arrived, by policy name (as `smg_worker_selection_total` carries it).

| Type | Labels |
|------|--------|
| Counter | `policy` |

### Cache-Aware Policy Metrics

The branch counter and the match-ratio histogram are recorded for each decision made from the approximate prefix trees (`--cache-index tree`, the default). Decisions made from KV events, in hash mode, or while KV-cache imbalance suspends affinity are not counted. The tree gauges are refreshed after each eviction pass (`--eviction-interval`, default `120` seconds; the pip `smg launch` names it `--eviction-interval-secs` and defaults to `60`). See [Cache-Aware Routing](../concepts/routing/cache-aware.md).

#### `smg_cache_aware_policy_branch_total`

Outcome of each tree-mode routing decision (smg-project/smg#2515).

| Type | Labels |
|------|--------|
| Counter | `branch` |

| `branch` | Meaning |
|----------|---------|
| `tree_match` | Routed to a worker that holds the matched prefix |
| `spill` | The match ratio was above `--cache-threshold`, but the request went to a worker that does not hold the prefix |
| `expected_wait_fallback` | The match ratio was at or below `--cache-threshold`, so the worker was chosen by load |
| `first_healthy_fallback` | Selection returned no worker and the request fell back to the first healthy worker |

#### `smg_cache_aware_match_ratio`

Best prefix match ratio of each tree-mode decision: matched length divided by input length, 0.0-1.0. Buckets are the deciles `0, 0.1, ..., 1.0`; the `le="0"` bucket counts requests with no cached prefix at all.

| Type | Labels |
|------|--------|
| Histogram | None |

#### `smg_cache_tree_chars`

Characters cached in the string tree (HTTP requests) per model, summed across tenants (smg-project/smg#2134). A prefix held by three workers counts three times.

| Type | Labels |
|------|--------|
| Gauge | `model` |

#### `smg_cache_tree_tokens`

Tokens cached in the token tree (gRPC requests) per model, summed across tenants.

| Type | Labels |
|------|--------|
| Gauge | `model` |

#### `smg_cache_tree_tenants`

Tenants (workers) present in each tree.

| Type | Labels |
|------|--------|
| Gauge | `model`, `tree` |

`tree`: `string`, `token`

#### `smg_cache_placement_entries`

Placement keys with at least one live holder, per model, in hash mode (`--cache-index hash`). Refreshed by the sweep that runs every `--eviction-interval` seconds and drops holders not touched within `--cache-ttl-secs`.

| Type | Labels |
|------|--------|
| Gauge | `model` |

#### `smg_cache_aware_prefix_margin_held_total`

Cache-aware affinity picks that the spill gate's swing-sized margin alone would have spilled, kept on their holder by the price of the prefix it holds (`--balance-prefix-margin-tokens`).

| Type | Labels |
|------|--------|
| Counter | None |

```promql
# Tree-mode decisions by branch
sum by (branch) (rate(smg_cache_aware_policy_branch_total[5m]))

# Median prefix match ratio
histogram_quantile(0.5, sum by (le) (rate(smg_cache_aware_match_ratio_bucket[5m])))

# Token-tree size per model
smg_cache_tree_tokens
```

---

### Sticky Session and Manual Policy Metrics

The `manual` policy and sticky sessions (`--routing-key-override`) share these metrics. See [Sticky Sessions](../concepts/routing/sticky-sessions.md).

#### `smg_manual_policy_branch_total`

Decision branch for keyed routing.

| Type | Labels |
|------|--------|
| Counter | `branch` |

| `branch` | Meaning |
|----------|---------|
| `occupied_hit` | The key is pinned to a healthy worker, which gets the request |
| `occupied_miss` | The key is pinned, but no pinned worker is healthy; a new worker is chosen |
| `vacant` | First request for this key; a worker is assigned and pinned |
| `cap_respill` | Sticky sessions only: the pinned worker is at the per-key in-flight cap, so the request is reassigned |
| `no_routing_id` | `manual` policy only: the request carries no routing key |
| `no_healthy_workers` | No healthy worker is available |

#### `smg_manual_policy_cache_entries`

Entries in the routing-key map of the `manual` policy, or of the sticky-session map when `--routing-key-override` is on.

| Type | Labels |
|------|--------|
| Gauge | None |

#### `smg_routing_key_source_total`

Which source supplied the sticky routing key for a keyed request (smg-project/smg#2206). Recorded when `--routing-key-override` is on and the policy is not `manual` or `consistent_hashing` (those read the key themselves).

| Type | Labels |
|------|--------|
| Counter | `source` |

Sources: `rid` (the request body's `rid`, with per-turn and per-retry suffixes stripped), `header` (the `--routing-key-headers`, default `x-smg-routing-key`)

#### `smg_worker_routing_keys_active`

Distinct routing keys with requests in flight on each worker.

| Type | Labels |
|------|--------|
| Gauge | `worker` |

---

### Hash Policy Metrics

#### `smg_consistent_hashing_policy_branch_total`

Decision branch of the `consistent_hashing` policy.

| Type | Labels |
|------|--------|
| Counter | `branch` |

| `branch` | Meaning |
|----------|---------|
| `target_worker_hit` | `X-SMG-Target-Worker` gave the index of a healthy worker |
| `target_worker_miss` | `X-SMG-Target-Worker` gave an invalid index or an unhealthy worker; no worker is selected |
| `routing_key_hit` | Routed by the consistent hash of the routing key |
| `random_fallback` | No key to hash; a random healthy worker was chosen |
| `no_healthy_workers` | No workers were available |

#### `smg_prefix_hash_policy_branch_total`

Decision branch of the `prefix_hash` policy.

| Type | Labels |
|------|--------|
| Counter | `branch` |

| `branch` | Meaning |
|----------|---------|
| `ring_hit` | The hash-ring owner of the prefix had acceptable load |
| `load_balance_walk` | The ring owners were overloaded, so the request went to the least-loaded acceptable worker |
| `fallback_least_load` | No hash ring was available; the least-loaded worker was chosen |
| `no_routing_key` | Nothing to hash; the least-loaded worker was chosen |
| `no_healthy_workers` | No healthy worker was available |

---

## Priority Scheduler Metrics

With `--priority-scheduler-enabled` (a Rust-binary flag the pip `smg launch` does not accept), the priority scheduler replaces the concurrency limiter and exports its own metrics: `smg_scheduler_admit_total`, `smg_scheduler_queue_wait_seconds`, `smg_scheduler_preemption_total`, `smg_scheduler_clamp_total`, `smg_scheduler_unknown_priority_value_total`, `smg_scheduler_starvation_promotion_total`, `smg_scheduler_inflight`, `smg_scheduler_queue_depth`, `smg_scheduler_queue_size_limit`, `smg_scheduler_utilization`, and `smg_scheduler_class_capacity_pressure`. Their types, labels, and values are listed in the [Priority Scheduler Reference](priority-scheduler.md#metrics).

`smg_scheduler_queue_wait_seconds` has no configured buckets, so it is exported as a summary (`quantile` series plus `_sum` and `_count`).

---

## RL Control Plane Metrics

Emitted only when the RL control plane is mounted with `--enable-rl`. See [RL Control Plane](../getting-started/rl-control-plane.md).

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `smg_rl_control_calls_total` | Counter | `op`, `result` | Control calls proxied to engines |
| `smg_rl_control_call_duration_seconds` | Histogram | `op` | Latency of one proxied control call |
| `smg_rl_fanout_total` | Counter | `result` | Fan-out requests |
| `smg_rl_fanout_duration_seconds` | Histogram | None | Wall time of one fan-out request |

- `op`: one of `pause_generation`, `continue_generation`, `update_weights_from_disk`, `update_weights_from_tensor`, `update_weights_from_distributed`, `init_weights_update_group`, `destroy_weights_update_group`, `update_weight_version`, `flush_cache`, `abort_request`, `release_memory_occupation`, `resume_memory_occupation`, `pause`, `resume`, `sleep`, `wake_up`, `collective_rpc`, `server_info`, `get_server_info`, `health`; any other path is `other`
- `result` for control calls: `ok` (2xx from the engine), `upstream_error` (non-2xx), `timeout` (no answer within `--rl-control-timeout-secs`), `unreachable`
- `result` for fan-outs: `ok` (every target succeeded), `partial` (at least one target failed), `no_match` (the selector matched no worker)

```promql
# Failed control calls by operation
sum by (op, result) (rate(smg_rl_control_calls_total{result!="ok"}[5m]))
```

---

## HA Mesh Metrics

Emitted when the HA mesh is enabled (`--enable-mesh`). These names use the `router_` prefix rather than `smg_`. See [High Availability](../concepts/architecture/high-availability.md).

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `router_mesh_peer_connections` | Gauge | `peer` | Recorded by the node that accepts a sync stream: `1` while the stream from the peer is open, `0` after it ends |
| `router_mesh_peer_reconnects_total` | Counter | `peer` | Accepted sync streams from the peer that ended |
| `router_mesh_sync_round_duration_seconds` | Histogram | `peer` | Duration of one outbound sync round to the peer, recorded by the dialing node |

When a stream opens, `router_mesh_peer_connections` is first set to `1` under an empty `peer` label, before the peer identifies itself; once the peer has identified itself, that empty-`peer` series stays at `1`. Exclude it with `peer!=""`:

```promql
# Active peer links
count(router_mesh_peer_connections{peer!=""} == 1)
```

---

## Metric Names That Never Appear

The following names do not exist in SMG and never appear on `/metrics`. Remove them from dashboards and alerts carried over from older releases:

- `smg_router_stage_duration_seconds`
- `smg_worker_connections_active`
- `smg_db_operations_total`, `smg_db_operation_duration_seconds`, `smg_db_connections_active`, `smg_db_items_stored`
- `router_mesh_store_cardinality`, `router_mesh_store_hash`, `router_rl_drift_ratio`, `router_lb_drift_ratio`

`router_mesh_peer_ack_total` and `router_mesh_peer_nack_total` have recording code, but they count legacy ACK and NACK stream messages, which v1.11.0 nodes never send, so they stay absent in a v1.11.0 mesh.

---

## Dashboard Queries Summary

| Metric | Query |
|--------|-------|
| Request rate | `sum(rate(smg_http_requests_total[5m]))` |
| Error rate | `sum(rate(smg_http_responses_total{status_code=~"5.."}[5m])) / sum(rate(smg_http_responses_total[5m]))` |
| P99 latency | `histogram_quantile(0.99, sum by (le) (rate(smg_http_request_duration_seconds_bucket[5m])))` |
| TTFT P50 | `histogram_quantile(0.5, sum by (le) (rate(smg_router_ttft_seconds_bucket[5m])))` |
| Tokens/sec | `sum(rate(smg_router_tokens_total[5m]))` |
| Healthy workers | `count(smg_worker_health == 1)` |
| Open circuits | `count(smg_worker_cb_state == 1)` |
| Rate limit rejections | `sum(rate(smg_http_rate_limit_total{result="rejected"}[5m]))` |
| Admission queue depth | `smg_admission_queue_depth` |
| Overload sheds | `sum by (stage) (rate(smg_worker_overload_shed_total[5m]))` |
| PD admission sheds | `rate(smg_pd_admission_sheds_total[5m])` |
| Tokenizer cache hit ratio | `sum by (layer) (rate(smg_tokenizer_cache_lookups_total{result="hit"}[5m])) / sum by (layer) (rate(smg_tokenizer_cache_lookups_total[5m]))` |
| Allocator resident memory | `smg_allocator_resident_bytes` |
| Event-loop stalls | `rate(smg_tokio_event_loop_stalls_total[5m])` |
| MCP tool success rate | `sum(rate(smg_mcp_tool_calls_total{result="success"}[5m])) / sum(rate(smg_mcp_tool_calls_total[5m]))` |

---

## Histogram Buckets

Default buckets (29 buckets from 1 ms to 7200 s) apply to every histogram whose name ends in `duration_seconds`, `ttft_seconds`, or `tpot_seconds`:

```
0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0,
10.0, 15.0, 30.0, 45.0, 60.0, 90.0, 120.0, 180.0, 240.0, 300.0,
480.0, 900.0, 1200.0, 1800.0, 2700.0, 3600.0, 5400.0, 7200.0
```

Configure custom buckets for these histograms via CLI:

```bash
smg launch --prometheus-duration-buckets 0.01 0.1 0.5 1 5 10
```

Histograms with buckets of their own, fixed in the code:

| Histogram | Buckets |
|-----------|---------|
| `smg_tokio_event_loop_delay_seconds` | 0, 1 ms, 5 ms, 10 ms, 25 ms, 50 ms, 100 ms, 250 ms, 500 ms, 1 s |
| `smg_cache_aware_match_ratio` | deciles, 0 to 1 |
| `smg_router_time_to_first_token_seconds` | 10 ms, 15 ms, 20 ms, 30 ms, 50 ms, 75 ms, 100 ms, 150 ms, 200 ms, 300 ms, 500 ms, 750 ms, 1 s, 1.5 s, 2 s, 3 s, 5 s, 7.5 s, 10 s, 20 s, 30 s, 60 s, 120 s |
| `smg_router_request_time_per_output_token_seconds` | 1 ms, 1.5 ms, 2 ms, 3 ms, 5 ms, 7.5 ms, 10 ms, 15 ms, 20 ms, 30 ms, 50 ms, 75 ms, 100 ms, 150 ms, 200 ms, 300 ms, 500 ms, 750 ms, 1 s, 2.5 s, 10 s |
| `smg_kv_event_lag_seconds` | 0.1 ms, 0.25 ms, 0.5 ms, 1 ms, 2.5 ms, 5 ms, 10 ms, 25 ms, 50 ms, 100 ms, 250 ms, 500 ms, 1 s, 2.5 s, 5 s, 10 s, 30 s, 60 s |
| `smg_kv_index_lookup_seconds`, `smg_kv_event_apply_seconds` | doubling from 0.5 µs to 16.384 ms, then 65.536 ms and 262.144 ms |
| `smg_worker_retry_backoff_seconds` | 10 ms, 25 ms, 50 ms, 100 ms, 250 ms, 500 ms, 1 s, 2.5 s, 5 s, 10 s, 30 s, 60 s |
| `smg_symphony_malformed_bytes` | 1, 4, 16, 64, 256, 1024, 4096, 16384, 65536 bytes |
| `smg_mm_processing_seconds`, `smg_mm_media_fetch_seconds` | 0.5 ms, 1 ms, 2.5 ms, 5 ms, 10 ms, 25 ms, 50 ms, 100 ms, 250 ms, 500 ms, 1 s, 2.5 s, 5 s, 10 s, 30 s |
| `smg_mm_media_per_request` | 1, 2, 3, 4, 5, 6, 8, 10, 16, 32, 64 items |

A histogram that matches none of these rules is exported as a Prometheus summary instead (`quantile` series over a rolling one-minute window, plus `_sum` and `_count`): `smg_scheduler_queue_wait_seconds` and `smg_pd_prefill_admission_wait_seconds`.
