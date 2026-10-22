---
title: Metrics Reference
---

# Metrics Reference

Complete reference for the Prometheus metrics SMG exports. Metrics are grouped by layer, from the gateway's own runtime through HTTP, routing, workers, discovery, and MCP, followed by groups that belong to specific features: routing policies, the priority scheduler, the RL control plane, and the HA mesh.

A series appears on `/metrics` only after the first event that records it, and several groups exist only when the feature that produces them is enabled. Each entry says when it is emitted.

Several families are published at zero before their first event, so `absent()` alerts stay quiet on a healthy process and `increase()` catches the first event after a restart: the overload-shed and fallback counters and the retry families at gateway start-up, each worker's circuit-breaker transition and health-readmission series when the worker is registered, and a worker's KV-event integrity counters when its KV-event subscription starts. Their entries below list the exact series.

---

## Metrics Endpoint

Metrics are served at `GET /metrics` on a dedicated listener, `--prometheus-host`:`--prometheus-port` (default port `29000`; the host defaults to the unspecified address of `--host`'s family — `::` for an IPv6 host, `0.0.0.0` otherwise):

```bash
curl http://localhost:29000/metrics
```

Configure via CLI:

```bash
smg launch --prometheus-port 29000 --prometheus-host 0.0.0.0
```

- The port must be at least `1`; configuration validation rejects `--prometheus-port 0` at startup. Every listener (`--port`, `--prometheus-port`, `--health-check-port`, `--mesh-port`) needs its own port.
- The listener binds at startup and logs `Metrics server listening on <address> (/metrics)`. If the port is already taken, startup fails instead of continuing without metrics.
- `--prometheus-duration-buckets` changes the buckets of the latency histograms (see [Histogram Buckets](#histogram-buckets)).

### Engine Metrics Passthrough

`GET /engine_metrics` returns the engines' own Prometheus metrics in a single exposition. It is served on the main API listener (`--host`:`--port`, default port `30000`), not on the metrics port, and like `/health` it requires no API key.

```bash
curl http://localhost:30000/engine_metrics
```

- SMG fetches `<worker URL>/metrics` from every registered worker, once per base URL (data-parallel ranks that share a URL are scraped once), and adds a `worker_addr` label holding the worker's base URL to every sample.
- Metric names, HELP text, and label values are passed through unchanged, colons included (`vllm:num_requests_running`, `sglang:...`), so queries written against an engine's own `/metrics` work against the gateway.
- Workers whose scrape fails or returns a non-success status are left out. If no worker is registered, or every scrape fails, the endpoint returns `500` with a plain-text reason (`No available workers` or `All backend requests failed`).

---

## Label Cardinality

Labels whose values a client can influence are bounded, so unexpected input cannot create an unbounded number of series:

| Label | Bound |
|-------|-------|
| `path` (HTTP metrics) | The matched route template, for example `/v1/responses/{response_id}`. Requests that match no route are recorded with `path="other"`. |
| `method` (HTTP metrics) | `GET`, `POST`, `PUT`, `DELETE`, `PATCH`, `HEAD`, `OPTIONS`; any other method is `OTHER`. |
| `model` | The served model's ID (an alias keeps its own value). A model the gateway does not serve is reported as `other`, and past 1,024 distinct admitted values every later new value is `other` too. |
| `tool_name` (MCP metrics) | Same rule as `model`: 1,024 distinct values, then `other`. |
| `op` (RL control plane) | A fixed list of engine control operations; any other path is `other`. |

The `model` and `tool_name` caps never evict a value once it is admitted, so the set of series stops growing at the cap instead of churning. Worker URLs (`worker`), status codes, and gateway error codes come from the gateway itself and are not capped. For the priority scheduler's `tenant` label, see the [Priority Scheduler Reference](priority-scheduler.md#metrics).

### Removed workers

When a worker's registration is removed, every series labelled with its worker URL — `smg_worker_*`, `smg_engine_*`, and the per-worker KV-event series — is dropped from `/metrics`, so the scrape and the time-series database stop growing with pod churn. Writes that arrive after the removal (a racing health check, a late outcome) are discarded until the same address registers again, which starts fresh series.

A worker that stays registered but leaves Ready keeps its series; its `smg_engine_*` gauges are set to `-1` until it reports load again. Filter on the value (for example `>= 0`) when you aggregate the engine gauges.

---

## Layer 0: Runtime Metrics

Health of the gateway process itself: the Tokio async runtime that serves requests, the memory allocator, and the log writer queues.

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

SMG uses jemalloc as its global allocator and exports jemalloc's own accounting, refreshed every 60 seconds by a dedicated thread. The `smg` binary and the Python package both export these gauges: they use jemalloc and build with the `jemalloc-stats` feature, which is on by default. Builds for musl or MSVC targets use the system allocator and do not export them.

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `smg_allocator_allocated_bytes` | Gauge | None | Bytes in live Rust allocations |
| `smg_allocator_active_bytes` | Gauge | None | Bytes in active pages |
| `smg_allocator_resident_bytes` | Gauge | None | Upper bound on resident bytes of the jemalloc heap |
| `smg_allocator_metadata_bytes` | Gauge | None | jemalloc metadata bytes |

Resident bytes that grow while allocated bytes stay flat point at memory the allocator retains; allocated bytes that grow with them point at data the gateway itself holds. The gauges cover only Rust allocations made through SMG's jemalloc instance: memory allocated by the Python interpreter or by bundled C libraries is not included.

Builds with the opt-in `jemalloc-profiling` feature also export `smg_allocator_prof_dumps_total` (`outcome`: `ok`, `error`), published at zero, counting heap-profile dumps requested through `POST /heap_profile`.

```promql
# Memory the allocator holds beyond live allocations
smg_allocator_resident_bytes - smg_allocator_allocated_bytes
```

### Logging Metrics

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `smg_log_dropped_lines` | Gauge | `sink` | Log lines the lossy writer queue dropped since the process started, by sink (`stdout`, `file`) |

### Chat History Metrics

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `smg_history_memory_records` | Gauge | `kind` | Records the in-memory history backend holds (`responses`, `conversations`, `items`), sampled every 10 seconds; database backends set nothing |

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

HTTP responses by path, status code, and gateway error code, counted once per request when its outcome is known: the response head's status once the response has gone out in full (its last frame, or the length it declared).

| Type | Labels |
|------|--------|
| Counter | `path`, `status_code`, `error_code` |

- A request whose client closed the connection before the response completed counts as `status_code="499"` with `error_code="client_closed_request"` — as does the rare request the server dropped unanswered (a handler panic, a forced shutdown).
- A response whose body failed after its head went out (an upstream stream that broke mid-body) keeps its head's status with `error_code="stream_error"`, unless the head already carried a gateway error code.
- Otherwise `error_code` is the code carried by error responses the gateway generates (for example `admission_queue_full` or `worker_overload_protection_shed`); it is empty for other responses. A body the JSON extractor refused carries that refusal's own code: `invalid_json` for a JSON syntax error, the failing field's code (such as `missing_required_parameter` or `integer_below_min_value`) for a deserialization or validation failure, `request_body_too_large` for a body over the payload limit, and `json_parse_error` only as the catch-all (for example, a missing JSON `Content-Type`).

```promql
# Error rate (5xx responses)
sum(rate(smg_http_responses_total{status_code=~"5.."}[5m])) / sum(rate(smg_http_responses_total[5m]))

# Success rate for /v1/responses
sum(rate(smg_http_responses_total{path="/v1/responses",status_code=~"2.."}[5m]))
/
sum(rate(smg_http_responses_total{path="/v1/responses"}[5m]))

# Clients disconnecting before their response finished
sum(rate(smg_http_responses_total{status_code="499"}[5m]))

# Gateway-generated errors by code
sum by (error_code) (rate(smg_http_responses_total{error_code!=""}[5m]))
```

---

### `smg_http_connections_active`

Requests currently in flight on the main listener. A streaming response stays in flight until its body finishes or the client disconnects. Despite the name, it counts requests, not TCP connections.

| Type | Labels |
|------|--------|
| Gauge | None |

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

### `smg_http_connection_timeouts_total`

Connections closed for carrying no request, by the timeout that closed them. A request in flight — an open streaming response included — is never cut by these bounds.

| Type | Labels |
|------|--------|
| Counter | `timeout` |

- `header_read`: no request head arrived, or it stalled, within `--header-read-timeout-secs` (default `30`)
- `idle`: an idle keep-alive connection exceeded `--idle-connection-timeout-secs` (default `90`)

Setting either flag to `0` disables that timeout.

---

### `smg_http_rate_limit_total`

Admission decisions of the concurrency limiter.

| Type | Labels |
|------|--------|
| Counter | `result` |

Values: `allowed`, `rejected`

Recorded on inference routes whenever admission control is on, which it is by default (see the next section). With `--priority-scheduler-enabled` the concurrency limiter is not installed and only rejections by an explicit `--rate-limit-tokens-per-second` limit are counted here (the refused request gets `429 scheduler_queue_full`); the scheduler's own outcomes are in `smg_scheduler_admit_total`.

```promql
# Rejection ratio
sum(rate(smg_http_rate_limit_total{result="rejected"}[5m])) / sum(rate(smg_http_rate_limit_total[5m]))
```

---

### Admission Queue Metrics

When the priority scheduler is off, every request to an inference route holds an admission token for its full lifetime, streaming body included. Admission control is on by default: with `--max-concurrent-requests` unset (`-1`), the cap is host-derived — 1,024 in-flight requests per available core, at least 4,096. A positive value sets the cap explicitly, and `0` disables admission control. A request that finds no free token waits in a FIFO queue of `--queue-size` slots (default `100`) for up to `--queue-timeout-secs` (default `60`). These metrics expose that queue.

#### `smg_admission_inflight`

Requests currently holding an admission token. Compare it with the configured or host-derived cap to see how close the gateway is to its concurrency bound.

| Type | Labels |
|------|--------|
| Gauge | None |

#### `smg_admission_queue_depth`

Requests currently waiting in the admission queue.

| Type | Labels |
|------|--------|
| Gauge | None |

#### `smg_admission_queue_rejected_total`

Requests rejected at admission, or refused later for having already waited out the queue timeout inside the gateway.

| Type | Labels |
|------|--------|
| Counter | `reason` |

| `reason` | Response | Cause |
|----------|----------|-------|
| `full` | `429 admission_queue_full` | Every queue slot was taken |
| `timeout` | `503 admission_queue_timeout` | The request waited `--queue-timeout-secs` without getting a token |
| `selection_timeout` | `503 selection_queue_timeout` | The request was admitted, then waited longer than the queue timeout inside the gateway before worker selection (gRPC pipeline) |
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
- `model`: the served model's ID; `unknown` for a request that names no model; `other` for a model the gateway does not serve
- `endpoint`: `chat`, `generate`, `completions`, `responses`, `messages`, `decisions`, `systemone`, `embeddings`, `classify`, `rerank`, `audio_transcriptions`, `realtime`, `realtime_sessions`, `realtime_client_secrets`, `realtime_transcription`, or `other` for routes without a dedicated label
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

Error types: `no_workers`, `timeout`, `backend_error`, `validation_error`, `rate_limit_error` (capacity pushback, a 429 from the worker or the gateway's own admission), `unavailable` (a refusal the gateway answered on its own: every worker unavailable, an overload shed, a wait past the selection queue's bound), `engine_error` (the engine itself reported that the request failed), and `internal_error`. The external providers also report `parse_error` and `streaming_error`.

```promql
# Error rate by type
sum by (error_type) (rate(smg_router_request_errors_total[5m]))
```

---

### `smg_router_ttft_seconds`

Time to first token, recorded for streaming responses on the gRPC router. For `backend_type="pd"` chat and generate streams the clock starts at the prefill dispatch — the same anchor as [`smg_pd_ttft_seconds`](#smg_pd_ttft_seconds), so sequential PD does not report a near-zero value. PD streams on other endpoints measure from the start of their streaming task.

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

Total generation time of a streaming response on the gRPC router. For PD chat and generate streams it shares the prefill-dispatch base with `smg_router_ttft_seconds`.

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

Requests the HTTP router sent a second time because the first send failed before any response arrived, for example on a pooled connection the worker had already closed. There is at most one such resend per send, it covers both regular and PD HTTP dispatch, and it does not apply to timeouts or to request bodies that cannot be replayed.

| Type | Labels |
|------|--------|
| Counter | `router_type` |

---

### `smg_router_request_body_path_total`

Per-request decision whether the router buffers the request body or streams it to the worker unread. Recorded for `/generate`, `/v1/chat/completions`, `/v1/completions`, `/v1/messages`, `/v1/embeddings`, and `/v1/classify`. See [Request Body Streaming](../concepts/performance/request-streaming.md).

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

Serialized size of request buffers the router freed as soon as the request was dispatched, instead of holding them until the response completed. The HTTP routers release at dispatch when router retries are disabled; the gRPC router counts the built request's wire size when it drops the parsed request at dispatch.

| Type | Labels |
|------|--------|
| Counter | None |

---

### PD Disaggregation Metrics

Signals that only the gateway can measure, because it is the one component that sees both the prefill and the decode leg of a disaggregated request. Durations are recorded once per request, not per retry attempt. See [PD Disaggregation](../concepts/routing/pd-disaggregation.md).

SMG splits the PD time to first token into phases it bounds itself. Under sequential dispatch (the gRPC router drains the prefill response before sending decode) the three phase histograms add up to `smg_pd_ttft_seconds` exactly:

- prefill: `smg_pd_prefill_duration_seconds` — prefill dispatch to the prefill response drained
- relay: `smg_pd_kv_transfer_duration_seconds` — prefill drained to decode dispatch (gateway work only)
- decode: `smg_pd_decode_ttft_seconds` — decode dispatch to the first decode token

The engines' own KV transfer runs inside the decode phase. Parallel dispatch sends both legs together: the decode phase is the whole TTFT and there is no relay phase.

The four histograms share the labels `backend_type` (always `pd`), `model`, and `runtime`, the worker's runtime type (for example `sglang`, `vllm`, or `tokenspeed`).

#### `smg_pd_ttft_seconds`

End-to-end time to first token of a disaggregated request, from the prefill dispatch to the first decode token. On the gRPC router it is recorded, together with `smg_pd_decode_ttft_seconds` off one clock reading, at the first decode token of streaming chat and generate requests; completions, Messages, and Harmony streams do not record the pair. The HTTP PD router records it for every PD request, from the dispatch of the paired legs to the decode response head — the first user-visible decode output, since the decode body is forwarded unbuffered.

| Type | Labels |
|------|--------|
| Histogram | `backend_type`, `model`, `runtime` |

#### `smg_pd_decode_ttft_seconds`

PD decode phase: decode dispatch to the first decode token, the engines' KV transfer and the decode scheduling included. Under parallel dispatch this is the whole TTFT. Recorded with `smg_pd_ttft_seconds` (gRPC chat and generate streams only).

| Type | Labels |
|------|--------|
| Histogram | `backend_type`, `model`, `runtime` |

#### `smg_pd_prefill_duration_seconds`

PD prefill phase: prefill dispatch to the end of the prefill response, the prefill engine's queueing and compute included. Recorded by the HTTP PD router and, on the gRPC router, for sequential PD once the decode leg is established, whatever the endpoint.

| Type | Labels |
|------|--------|
| Histogram | `backend_type`, `model`, `runtime` |

#### `smg_pd_kv_transfer_duration_seconds`

PD relay phase of sequential dispatch on the gRPC router: from the prefill response drained to the decode request sent. This is the gateway's own work, not the engines' KV transfer, which runs inside the decode phase; the family name predates the phase model.

| Type | Labels |
|------|--------|
| Histogram | `backend_type`, `model`, `runtime` |

#### `smg_pd_kv_connector_mode_total`

KV connector mode chosen for a vLLM prefill-decode dispatch. The gRPC router records it for sequential dispatch once the decode leg is established; the HTTP PD router records it at dispatch, on both the sequential and the concurrent-write MoRI-IO paths, so over HTTP it counts attempts, including requests later rejected (for example `502 moriio_handoff_invalid`).

| Type | Labels |
|------|--------|
| Counter | `mode` |

Modes: `mooncake`, `nixl`, `moriio`, `store`, `passthrough`

#### `smg_pd_bootstrap_failures_total`

HTTP PD requests whose body could not take the bootstrap fields (the body is not a JSON object).

| Type | Labels |
|------|--------|
| Counter | None |

#### `smg_pd_rendezvous_failures_total`

PD rendezvous failures, by the worker type of the leg that failed and the phase that failed.

| Type | Labels |
|------|--------|
| Counter | `leg`, `phase` |

| `phase` | Meaning |
|---------|---------|
| `probe` | The leg's bootstrap port refused the gateway's connection probe at registration: the leg is registered, but its disaggregated partners cannot rendezvous there |
| `bootstrap` | The engines report (gRPC path) that the legs never met |
| `transfer` | The engines report (gRPC path) that the KV handoff failed after the legs met |

#### `smg_pd_kv_transfer_failures_total`

PD dispatches whose KV handoff parameters were missing or unusable. A vLLM NIXL prefill that returned no `kv_transfer_params` increments it and the decode worker recomputes the prompt itself — the request continues. Over HTTP with MoRI-IO, a selected pair that fails validation (`moriio_pair_misconfigured`) or a prefill whose KV handoff is missing or invalid (`502 moriio_handoff_invalid`) also increments it and the request is rejected, on both the sequential and the concurrent-write MoRI-IO paths. An increment therefore does not always mean the prompt was recomputed.

| Type | Labels |
|------|--------|
| Counter | None |

#### `smg_pd_admission_waits_total`

gRPC prefill-decode dispatches that waited for room in the decode engine's running window (its `--max-num-seqs` / `--max-running-requests`) before being sent.

| Type | Labels |
|------|--------|
| Counter | None |

#### `smg_pd_admission_sheds_total`

gRPC prefill-decode dispatches shed with `503 worker_overload_protection_shed`, because no decode room freed within `--pd-admission-wait-secs` (default `30`) or because the request needs more rooms than the window holds. Each shed also increments `smg_worker_overload_shed_total{stage="pd_admission"}`. Decode engines that report no running window are never gated.

| Type | Labels |
|------|--------|
| Counter | None |

#### `smg_pd_admission_window`

Running window the PD admission gate bounds a decode worker by; `0` when the engine reports none and the gate abstains for that worker.

| Type | Labels |
|------|--------|
| Gauge | `worker` |

```promql
# P95 end-to-end TTFT for PD requests, by model
histogram_quantile(0.95, sum by (model, le) (rate(smg_pd_ttft_seconds_bucket[5m])))

# PD dispatches that waited for, or were shed by, decode admission
rate(smg_pd_admission_waits_total[5m])
rate(smg_pd_admission_sheds_total[5m])
```

#### Prefill Admission Metrics

With `--prefill-max-inflight-requests-per-worker` set to a positive value (non-positive, the default, disables the gate), the gateway bounds each prefill worker's in-flight requests in PD or EPD mode. A request with no worker under the bound waits in a router-wide FIFO queue of `--prefill-queue-size` slots (default `100`) for up to `--prefill-queue-timeout-secs` (default `60`).

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `smg_pd_prefill_admission_inflight` | Gauge | `worker` | Prefill requests SMG has admitted to the worker |
| `smg_pd_prefill_admission_queued` | Gauge | None | Requests waiting in the prefill admission queue |
| `smg_pd_prefill_admission_wait_seconds` | Summary | None | Time a request waited in the prefill admission queue |
| `smg_pd_prefill_admission_rejections_total` | Counter | `reason` | Rejections: `unavailable` (no healthy, model-matching worker), `queue_full`, `queue_timeout` |

No bucket rule matches `smg_pd_prefill_admission_wait_seconds`, so it is exported as a Prometheus summary (`quantile` series plus `_sum` and `_count`), not a bucketed histogram (see [Histogram Buckets](#histogram-buckets)).

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

#### Image Decoder Gauges

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `smg_mm_turbojpeg_available` | Gauge | None | `1` when JPEGs decode through libjpeg-turbo, `0` through the pure-Rust fallback |
| `smg_mm_webp_available` | Gauge | None | `1` when WebP files decode through libwebp, `0` through the pure-Rust fallback |

#### Pixel Cache Metrics

The router-side pixel cache (`--mm-pixel-cache-mb`) holds preprocessed encoder inputs: one cache per process, shared by every model, keyed by image and model configuration. The occupancy gauges and the eviction counter are published at zero when the cache is built; a model's hit and miss series both exist from its first lookup, so a hit rate reads from the start.

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `smg_mm_pixel_cache_hits_total` | Counter | `model` | Lookups that found the image's preprocessed encoder input |
| `smg_mm_pixel_cache_misses_total` | Counter | `model` | Lookups that had to preprocess the image |
| `smg_mm_pixel_cache_evictions_total` | Counter | None | Entries dropped to stay within the byte budget |
| `smg_mm_pixel_cache_bytes` | Gauge | None | Bytes of preprocessed encoder inputs the cache holds |
| `smg_mm_pixel_cache_entries` | Gauge | None | Images the cache holds |

---

### Responses API Metrics

#### `smg_responses_stream_failures_total`

Responses API streams that ended with a `response.failed` terminal event.

| Type | Labels |
|------|--------|
| Counter | `model`, `reason` |

Reasons: `stream_error`, `server_error`, `other`

#### `smg_responses_not_stored_total`

Responses with `store: true` whose record or conversation linking was not written.

| Type | Labels |
|------|--------|
| Counter | `mode`, `step` |

- `mode`: `foreground` (the request was answered with the failure), `background` (the record keeps what it had)
- `step`: `record` (nothing is stored), `linking` (the record is stored, its items are not in the conversation)

---

### Tokenizer Metrics

Process-wide activity of the tokenizer [L0/L1 caches](../concepts/performance/tokenizer-caching.md) and the encode and decode paths, summed across all tokenizers. The totals are read on every scrape, so both cache layers always appear; a disabled layer stays at zero. Labels carry no model IDs or prompt text.

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

#### `smg_tokenizer_encode_path_loads_total`

Hugging Face tokenizers loaded, by the encode path chosen at load; a tokenizer loaded twice counts twice.

| Type | Labels |
|------|--------|
| Counter | `path` |

Paths: `v1` (the opt-in tokenizers v1 pipeline), `native` (the direct byte-level encoder), `tokenizers` (the reference implementation)

#### `smg_tokenizer_encode_fallbacks_total`

Inputs the direct byte-level encoder gave up to the reference implementation after the load's choice.

| Type | Labels |
|------|--------|
| Counter | `reason` |

Reasons: `not_nfc` (text not in NFC under an NFC normalizer), `piece_declined` (the model declined a piece)

#### `smg_detokenize_prefix_mismatch_total`

Incremental decode steps, and end-of-input finishes, whose output no longer started with the text already decoded and went on with the remainder. Several per stream are possible, and unary responses count too.

| Type | Labels |
|------|--------|
| Counter | `kind` |

Kinds: `byte_run` (an invalid byte collapsed a ByteFallback run whose characters were already decoded), `decoder` (another decoder step rewrote the decoded prefix)

```promql
# Hit ratio per layer (each layer against its own lookups)
sum by (layer) (rate(smg_tokenizer_cache_lookups_total{result="hit"}[5m]))
/
sum by (layer) (rate(smg_tokenizer_cache_lookups_total[5m]))

# Evictions per second, a sign the cache is too small
sum by (layer) (rate(smg_tokenizer_cache_evictions_total[5m]))
```

---

## Layer 3: Worker Metrics

Metrics for worker pool management and resilience. Every family labelled `worker` is dropped from the scrape when the worker's registration is removed (see [Removed workers](#removed-workers)).

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

Requests dispatched to each worker, counted at each send (a retry counts again). Published at zero when the worker registers, so a worker that gets no traffic shows as such instead of being absent.

| Type | Labels |
|------|--------|
| Counter | `worker`, `model` |

---

### `smg_worker_health`

Worker health status.

| Type | Labels | Values |
|------|--------|--------|
| Gauge | `worker` | `1` = Ready, `0` = any other status (pending, not ready, failed, or draining) |

A removed worker's series disappears from the scrape rather than reporting a sentinel value.

```promql
# Count healthy workers
count(smg_worker_health == 1)

# Alert on unhealthy workers
smg_worker_health == 0
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

Worker-level errors by type. The HTTP routers record one when a request to a worker ends in a server error (a 5xx status from the worker or a failed send). The gRPC pipeline, which also serves ZMQ workers, records one per failed dispatch or stream whose status is the worker's failure; request rejections (4xx) and capacity pushback (429) are not charged to the worker.

| Type | Labels |
|------|--------|
| Counter | `worker_type`, `connection_mode`, `error_type` |

Error types: `timeout` (status `504`), `backend_error` (other server errors), and `engine_error` (the engine itself reported the request failed; gRPC pipeline only).

---

### KV Event and KV Index Metrics

Per-worker metrics of the KV-event subscriptions and the KV indexes behind event-driven cache-aware routing. See [Cache-Aware Routing with KV Events](../getting-started/kv-events-cache-aware.md). When a worker's KV-event subscription starts, its integrity counters (`smg_kv_event_gaps_total`, `smg_kv_event_missed_batches_total`, `smg_kv_event_resyncs_total`, `smg_kv_event_subscription_failures_total`, the parentless counters) and the degraded-ranks gauge are published at zero, so a scrape tells "no gap yet" from "no series".

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `smg_kv_event_subscriptions_total` | Counter | `worker` | KV event streams connected; a reconnect counts again |
| `smg_kv_event_stream_errors_total` | Counter | `worker`, `error` | Streams that failed after connecting, by the error's gRPC status code (`out_of_range`, `unavailable`, `data_loss`, ...) |
| `smg_kv_event_subscription_failures_total` | Counter | `worker`, `reason` | Subscription task failures: `panic`, `join_error`, `intern_failed` |
| `smg_kv_events_unavailable` | Gauge | `worker` | `1` while the worker's backend serves no KV events (the subscription answered UNIMPLEMENTED); cache-aware routing is blind to such a worker's cache |
| `smg_kv_event_batches_total` | Counter | `worker`, `disposition` | Batches by what the subscriber did with them: `applied`, `stale`, `snapshot`, `load_only` |
| `smg_kv_event_gaps_total` | Counter | `worker`, `outcome` | Sequence gaps: `replay_requested`, `unrecovered_kept`, `unrecovered_cleared` |
| `smg_kv_event_missed_batches_total` | Counter | `worker` | Batches the publisher skipped and could not replay |
| `smg_kv_event_resyncs_total` | Counter | `worker`, `reason` | Rank resyncs: `out_of_range`, `data_loss`, `publisher_restart`, `gap_cleared`, `snapshot` |
| `smg_kv_event_lag_seconds` | Histogram | `worker` | Age of a batch when applied: now minus the publisher timestamp |
| `smg_kv_event_apply_seconds` | Histogram | `worker` | Time to apply one batch to the index |
| `smg_kv_event_blocks_total` | Counter | `worker`, `op` | Blocks named by applied events: `stored`, `removed`; `snapshot` is the live set a relay state snapshot listed |
| `smg_kv_event_parentless_stores_total` | Counter | `worker` | Stores whose parent block the index did not hold, placed as a new chain from the root |
| `smg_kv_event_parentless_blocks_total` | Counter | `worker` | Blocks of the parent-less stores |
| `smg_kv_event_degraded_ranks` | Gauge | `worker` | Ranks whose index may be stale after an unreplayed gap |
| `smg_kv_index_lookup_seconds` | Histogram | `index` | Time of one KV index lookup (overlap scoring of a request's block hashes), by index kind: `positional`, `chain` |
| `smg_kv_index_blocks` | Gauge | `worker` | Blocks the positional index holds for the worker |
| `smg_kv_index_memberships` | Gauge | `model` | Blocks the KV index holds across a model's workers (a block two workers hold counts twice); published every 30 s |
| `smg_kv_index_entries` | Gauge | `model` | Distinct entries: (position, content hash) pairs in the positional indexer, distinct blocks on a chain in the chain index |
| `smg_kv_index_runs_live` | Gauge | `model` | Runs linked in the chain index (`blocks_live` over `runs_live` is the mean run length; a falling ratio under steady traffic is fragmentation) |
| `smg_kv_index_blocks_live` | Gauge | `model` | Content hashes held by the chain index's live runs |
| `smg_kv_index_arena_bytes` | Gauge | `model` | Bytes the chain index's word arena has handed out |
| `smg_kv_index_arena_free_bytes` | Gauge | `model` | Bytes of the word arena sitting in free lists |
| `smg_kv_index_slab_bytes` | Gauge | `model` | Bytes the chain index's run slab holds from the allocator |
| `smg_kv_index_moved_hashes` | Gauge | `model` | Stores that moved a held engine hash to another place in the chain index (cumulative) |
| `smg_kv_index_engine_conflicts` | Gauge | `model` | Stored blocks whose engine hash differed from the one the chain index holds — a fleet whose engines do not name content alike (cumulative) |

`smg_kv_event_lag_seconds`, `smg_kv_event_apply_seconds`, and `smg_kv_index_lookup_seconds` have fixed buckets of their own (see [Histogram Buckets](#histogram-buckets)).

---

### Liveness Veto Metrics

The liveness tracker vetoes a worker from routing when its transport fails and nothing is heard from it, or when it answers polls but its requests make no progress.

#### `smg_worker_stalled`

`1` while routing skips the worker under a liveness veto. A veto that changes reason leaves one gauge up, not two.

| Type | Labels |
|------|--------|
| Gauge | `worker`, `reason` |

| `reason` | Meaning |
|----------|---------|
| `unreachable` | The worker's transport fails and nothing has been heard from it for the stall threshold |
| `wedged` | The worker answers polls but holds requests that make no progress while its queue grows |

#### `smg_worker_stall_transitions_total`

Times the liveness veto was set on a worker.

| Type | Labels |
|------|--------|
| Counter | `worker`, `reason` |

---

### Overload Protection Metrics

A worker whose load report crosses an overload threshold is excluded from routing until the signal recovers. Protection is on by default: a worker counts as overloaded at or above `--worker-overload-waiting-requests` queued requests (default `8`, summed across DP ranks) or `--worker-overload-token-usage` mean KV-cache usage (default `0.8`). `--disable-worker-overload-protection` switches the gateway thresholds off; per-worker `overload` blocks on a worker spec override the gateway values per signal. When every candidate is excluded, the request goes to the least-loaded worker — unless `--worker-overload-shed` is set, in which case it is shed instead. See [Overload Protection](../concepts/reliability/overload-protection.md).

#### `smg_workers_overloaded`

Workers currently flagged overloaded and excluded from routing, per model. The flag is evaluated on each load poll, and the gauge changes only when a worker's flag flips.

| Type | Labels |
|------|--------|
| Gauge | `model` |

#### `smg_worker_overload_shed_total`

Requests shed with `503 worker_overload_protection_shed`. The response carries `Retry-After` set to the load-monitor interval, and the gateway does not retry it internally. All three `stage` series are published at zero when the metrics listener starts.

| Type | Labels |
|------|--------|
| Counter | `stage` |

| `stage` | Cause |
|---------|-------|
| `selection` | Every worker the request could use was flagged overloaded (requires `--worker-overload-shed`) |
| `dispatch` | The selected worker became overloaded between selection and dispatch (requires `--worker-overload-shed`) |
| `pd_admission` | A gRPC prefill-decode dispatch found no room on the decode engine (see `smg_pd_admission_sheds_total`); this stage does not depend on `--worker-overload-shed` |

#### `smg_worker_overload_fallback_total`

Requests routed to the least-loaded worker because every worker for the model was flagged overloaded and shedding is off (the default). Published at zero at start-up.

| Type | Labels |
|------|--------|
| Counter | `stage` |

Stage: `selection`

#### `smg_worker_liveness_fallback_total`

Requests routed to the least-loaded ready worker because every worker for the model was vetoed and the ready ones were vetoed by the liveness tracker. Published at zero at start-up.

| Type | Labels |
|------|--------|
| Counter | `stage` |

Stage: `selection`

```promql
# Overload sheds per second, by stage
sum by (stage) (rate(smg_worker_overload_shed_total[5m]))

# Models with vetoed workers
smg_workers_overloaded > 0
```

---

### Circuit Breaker Metrics

#### `smg_worker_cb_state`

Circuit breaker state per worker, set to `0` (closed) when the worker registers.

| Type | Labels | Values |
|------|--------|--------|
| Gauge | `worker` | `0` = closed, `1` = open, `2` = half-open |

```promql
# Workers with open circuits
count(smg_worker_cb_state == 1)
```

#### `smg_worker_cb_transitions_total`

Circuit breaker state transitions. The five transitions a breaker can make (`closed` → `open`, `open` → `half_open`, `open` → `closed`, `half_open` → `closed`, `half_open` → `open`) are published at zero for each worker when it is registered, so `increase()` sees a worker's first trip.

| Type | Labels |
|------|--------|
| Counter | `worker`, `from`, `to` |

States: `closed`, `open`, `half_open`

#### `smg_worker_cb_outcomes_total`

Request outcomes tracked by the circuit breaker. Appears per worker with its first recorded outcome.

| Type | Labels |
|------|--------|
| Counter | `worker`, `outcome` |

Outcomes: `success`, `failure`

#### `smg_worker_cb_health_readmissions_total`

Times a worker's open circuit breaker went half-open because the worker's health returned (its promotion to Ready) rather than at the breaker's timeout. Published at zero when the worker registers.

| Type | Labels |
|------|--------|
| Counter | `worker` |

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

---

### Retry Metrics

At gateway start-up, `smg_worker_retries_total` and `smg_worker_retries_exhausted_total` are published at zero for every combination of the `regular`, `prefill`, and `decode` worker types and the ten `endpoint` values, and the `attempt="1"` series of `smg_worker_retry_backoff_seconds` is registered as an empty distribution (a `_count` of 0; later attempts appear on their first backoff). The external-provider series (`worker_type="external"`) are not pre-published: each appears on its first respective event.

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

Backoff slept before each retry, by retry number: `attempt="1"` is the wait before the first retry, which is the second attempt. The histogram has fixed buckets of its own, from 10 ms to 60 s (see [Histogram Buckets](#histogram-buckets)), spanning the exponential schedule from the configured initial backoff (50 ms by default) to the configured maximum (30 s by default).

| Type | Labels |
|------|--------|
| Histogram | `attempt` |

```promql
# P99 backoff before the second retry (the third attempt)
histogram_quantile(0.99, sum by (le) (rate(smg_worker_retry_backoff_seconds_bucket{attempt="2"}[5m])))
```

---

### Engine Load Metrics

SMG re-exports every worker load report it receives as `smg_engine_*` gauges. By default the load monitor polls every Ready worker group every `--load-monitor-interval` seconds (default `10`); a worker whose KV-event stream pushes its load records is not polled while they flow. With `--disable-load-monitoring`, a worker group is polled only when a load-aware routing policy, worker overload protection, or `--engine-metrics` needs the data; `--engine-metrics`, a Rust-binary flag the pip `smg launch` does not accept, forces polling for these gauges alone (see [Load Monitoring Configuration](configuration.md#load-monitoring-configuration)). gRPC and ZMQ workers answer the `GetLoads` load query; HTTP workers are read through their loads endpoint, falling back to parsing vLLM or SGLang `/metrics`.

#### `smg_engine_load_polls_total`

One decision per load-monitor tick and worker.

| Type | Labels |
|------|--------|
| Counter | `worker`, `mode` |

| `mode` | Meaning |
|--------|---------|
| `poll` | No pushed load record on file; the worker was polled |
| `fallback` | Pushed records were older than the tick interval; the worker was polled |
| `skipped_fresh_push` | The worker's KV-event stream pushed its load within the interval; no `GetLoads` RPC |

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

`role` is the engine-reported role: `prefill`, `decode`, or `null`. Values are whatever the engine reports, so which fields are meaningful depends on the engine. When a registered worker leaves Ready or is replaced, its gauges are set to `-1`; when a worker is removed, its series are dropped from the scrape. The same load snapshot is available as JSON from `GET /loads` on the main port.

```promql
# KV-cache usage per worker (non-Ready workers report -1)
max by (worker) (smg_engine_token_usage >= 0)

# Requests waiting in engines, per model
sum by (model) (smg_engine_waiting_requests >= 0)
```

---

## Layer 4: Discovery Metrics

Metrics for service discovery, emitted when discovery is enabled. The `source` label is the provider that produced the observation: `kubernetes` or `file`.

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

Discovery source observations rejected: the source could not be read (`unreadable`) or was read and rejected as invalid (`invalid`). The provider keeps reconciling its last good snapshot meanwhile.

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

### Cache-Aware Policy Metrics

`smg_cache_aware_policy_branch_total` counts the selections the cache-aware policy makes from its prefix indexes: decisions from the approximate prefix trees (`--cache-index tree`, the default, for models without a KV-event index) and decisions from the event-driven KV index. Decisions in hash mode (`--cache-index hash`) and the fleet-wide fallback taken while KV-cache imbalance suspends affinity bypass the counter, so it does not cover every routing decision. The match-ratio histogram is recorded only for tree decisions. The tree gauges are refreshed after each eviction pass (`--eviction-interval`, default `120` seconds; the pip `smg launch` names it `--eviction-interval-secs` and defaults to `60`). See [Cache-Aware Routing](../concepts/routing/cache-aware.md).

#### `smg_cache_aware_policy_branch_total`

Outcome of each tree-mode or event-driven routing decision.

| Type | Labels |
|------|--------|
| Counter | `branch` |

Tree-mode branches:

| `branch` | Meaning |
|----------|---------|
| `tree_match` | Routed to a worker that holds the matched prefix |
| `spill` | The match ratio was above `--cache-threshold`, but the request went to a worker that does not hold the prefix |
| `expected_wait_fallback` | The match ratio was at or below `--cache-threshold`, so the worker was chosen by load |
| `first_healthy_fallback` | Selection returned no worker and the request fell back to the first healthy worker |

Event-driven branches:

| `branch` | Meaning |
|----------|---------|
| `event_hit` | The selected worker holds blocks that overlap the request |
| `event_spill` | Overlap existed, but the selected worker holds none of it |
| `event_miss` | No worker held any of the request's blocks |
| `warmup_slice` | A request with no usable overlap was routed to a warming worker |
| `warmup_divert` | A cache hit was diverted to a thin (warming) worker |

#### `smg_cache_aware_match_ratio`

Best prefix match ratio of each tree-mode decision: matched length divided by input length, 0.0-1.0. Buckets are the deciles `0, 0.1, ..., 1.0`; the `le="0"` bucket counts requests with no cached prefix at all. Event-driven decisions do not record it.

| Type | Labels |
|------|--------|
| Histogram | None |

#### `smg_cache_tree_chars`

Characters cached in the string tree (HTTP requests) per model, summed across tenants. A prefix held by three workers counts three times.

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

```promql
# Cache-aware decisions by branch
sum by (branch) (rate(smg_cache_aware_policy_branch_total[5m]))

# Median prefix match ratio (tree mode)
histogram_quantile(0.5, sum by (le) (rate(smg_cache_aware_match_ratio_bucket[5m])))

# Token-tree size per model
smg_cache_tree_tokens
```

---

### `smg_policy_inflight_reconciled_total`

Policy bookings released at the per-poll in-flight reconciliation because their completion never arrived, by policy.

| Type | Labels |
|------|--------|
| Counter | `policy` |

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

Which source supplied the sticky routing key for a keyed request. Recorded when `--routing-key-override` is on and the policy is not `manual` or `consistent_hashing` (those read the key themselves).

| Type | Labels |
|------|--------|
| Counter | `source` |

Sources: `header` (the `--routing-key-headers`, default `x-smg-routing-key`), `rid` (the request body's `rid`, with per-turn and per-retry suffixes stripped). An explicit header key outranks the `rid`-derived key.

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
| `routing_key_hit` | Routed by the consistent hash of an explicit routing key (the routing-key header or, under the routing-key override, the body's `rid`) |
| `implicit_key_hit` | No explicit key; routed by the consistent hash of the `x-forwarded-for` or `cookie` header value |
| `random_fallback` | Neither an explicit nor an implicit key; a random healthy worker was chosen |
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

With `--priority-scheduler-enabled`, the priority scheduler replaces the concurrency limiter on inference routes and exports its own metrics: `smg_scheduler_admit_total`, `smg_scheduler_queue_wait_seconds`, `smg_scheduler_preemption_total`, `smg_scheduler_clamp_total`, `smg_scheduler_unknown_priority_value_total`, `smg_scheduler_starvation_promotion_total`, `smg_scheduler_inflight`, `smg_scheduler_queue_depth`, `smg_scheduler_queue_size_limit`, `smg_scheduler_utilization`, and `smg_scheduler_class_capacity_pressure`. Their types, labels, and values are listed in the [Priority Scheduler Reference](priority-scheduler.md#metrics).

`smg_scheduler_queue_wait_seconds` has no configured buckets, so it is exported as a summary (`quantile` series plus `_sum` and `_count`; see [Histogram Buckets](#histogram-buckets)).

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

## Declared but Not Emitted

SMG registers descriptions for the following names, but nothing records values for them, so they never appear on `/metrics`. Remove them from dashboards and alerts:

- `smg_router_stage_duration_seconds`
- `smg_worker_connections_active`
- `smg_db_operations_total`, `smg_db_operation_duration_seconds`, `smg_db_connections_active`, `smg_db_items_stored`
- `router_mesh_store_cardinality`, `router_mesh_store_hash`, `router_rl_drift_ratio`, `router_lb_drift_ratio`

`router_mesh_peer_ack_total` and `router_mesh_peer_nack_total` have recording code, but they count legacy ACK and NACK stream messages that nodes on this version never send, so they stay absent in a mesh of same-version nodes.

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

Histograms with fixed buckets of their own:

| Metric | Buckets |
|--------|---------|
| `smg_tokio_event_loop_delay_seconds` | `0, 0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0` |
| `smg_cache_aware_match_ratio` | Deciles `0, 0.1, ..., 1.0` |
| `smg_worker_retry_backoff_seconds` | `0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0` |
| `smg_kv_event_lag_seconds` | `0.0001` to `60` (18 edges, roughly 1-2.5-5 steps) |
| `smg_kv_event_apply_seconds`, `smg_kv_index_lookup_seconds` | `0.0000005` to `0.262144` (doubling) |

A histogram that matches none of these rules is exported as a Prometheus summary instead — `quantile` series (`0`, `0.5`, `0.9`, `0.95`, `0.99`, `0.999`, `1`) over a rolling window, plus `_sum` and `_count`. That applies to `smg_pd_prefill_admission_wait_seconds` and `smg_scheduler_queue_wait_seconds`.
