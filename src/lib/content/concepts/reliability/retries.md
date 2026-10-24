---
title: Retries
---

# Retries

SMG retries failed attempts with exponential backoff and jitter, so transient worker failures are absorbed by the gateway instead of reaching clients, without overwhelming workers that are recovering.

---

## Overview

<div class="grid" markdown>

<div class="card" markdown>

### :material-refresh: Automatic Retries

Retry a failed attempt without client intervention. For local workers, each retry runs worker selection again over the workers that are currently available.

</div>

<div class="card" markdown>

### :material-chart-timeline: Exponential Backoff

Space out retry attempts with increasing delays to give services time to recover.

</div>

<div class="card" markdown>

### :material-shuffle-variant: Jitter

Add randomness to backoff timing to prevent thundering herd problems.

</div>

<div class="card" markdown>

### :material-filter: Smart Selection

Only retry status codes that are likely to succeed on another attempt, and never a response whose cause cannot clear within a backoff window. A worker's own `500` is never replayed on that worker.

</div>

</div>

---

## Why Retries?

Transient failures are common in distributed systems:

- **Network timeouts**: Temporary network congestion or packet loss
- **Worker overload**: Temporary capacity limits (429 responses)
- **Intermittent errors**: Brief service interruptions during deployments
- **Connection issues**: Worker restart or network partition

Without retries, every transient failure becomes a client-visible error. With retries, SMG handles these automatically.

---

## How a Retry Works

1. SMG dispatches the attempt and checks the response status as soon as the worker answers, before any of the body reaches the client.
2. If the status is retryable, the response is not marked terminal, and attempts remain, SMG waits for the backoff delay.
3. The next attempt runs worker selection again. Workers that are unhealthy, have an open circuit breaker, or are vetoed by overload protection are skipped. So are the workers the request already failed on, as long as another available worker is left.
4. When a response is not retried, or no attempts remain, SMG returns it to the client.

Skipping the workers a request failed on applies even under deterministic affinity: when `cache_aware` has the prompt's prefix cached on the failed worker, or a `consistent_hashing` routing key maps to it, the retry moves to another worker instead of going straight back. When every available worker has already been tried, including when the pool has a single worker, the retry selects among all of them again — except a worker that answered the request with its own `500`, which is never selected for that request again (see [When a Worker Answers 500](#when-a-worker-answers-500)).

There are two exceptions. A request targeted with [`X-SMG-Target-Worker`](../routing/load-balancing.md#routing-headers) is retried on the worker it targets. PD mode does not skip tried workers: each attempt selects its prefill/decode pair from all available workers, so a retry can pick the same pair again. A worker's own `500` overrides both exceptions; only HTTP PD mode retries a `500` like any other status (see [When a Worker Answers 500](#when-a-worker-answers-500)).

`--retry-max-retries` counts **attempts, including the first**: the default `5` allows the initial attempt plus up to four retries, and `1` disables retries.

A worker that has returned a success status and started streaming its response is never retried, even if the stream fails later.

HTTP-router responses relayed from a worker carry an `x-smg-routed-worker-id` header with the URL of the worker that produced them (the decode worker in PD mode), so a client can see where the final attempt landed.

Responses served by gRPC and ZMQ workers carry the same header when the final attempt succeeds, naming that attempt's worker by its registered `grpc://` or `ipc://` URL (the decode worker in PD mode). Their error responses, and Responses API responses, do not carry it. The [Routed Worker Header](../routing/sticky-sessions.md#routed-worker-header) section covers the header in detail, including version availability.

### When a Worker Answers 500

A `500` a worker produced itself — its own response on the HTTP wire, or a gRPC `INTERNAL` status from the engine — is the worker's definitive answer for the request: replaying the same payload on the same worker costs the same work for the same answer. Retries of the request move to other available workers and never return to a worker that answered `500`, however many attempts remain. This exclusion never relaxes: it holds when every other worker has already been tried, for a request targeted with `X-SMG-Target-Worker`, and in PD mode on gRPC workers, where the prefill/decode pair that gave the answer is excluded together. It is also per request: the worker stays selectable for other requests, and its circuit breaker takes the request's one failure as usual.

When no other worker can take the attempt — the pool has a single worker, or every alternative is unavailable, unable to serve the request, or has itself answered `500` — the `500` is returned to the client at once, skipping the remaining attempts and their backoff. The HTTP router relays the response as the worker sent it, body included.

Only the worker's own `500` is definitive. A `500` the gateway synthesizes for a failed connection, and a worker-sent `502`, `503`, or `504` stay replayable on the same worker under the normal rules. The rule covers HTTP workers in regular mode and gRPC and ZMQ workers; for HTTP workers in PD mode and for external providers, a `500` is retried like any other [retryable status](#retryable-status-codes).

### Where Retries Apply

| Traffic | Router retries |
|---------|----------------|
| HTTP workers: JSON requests with a buffered body (regular and PD mode) | Yes. Each retry selects a worker again, or a prefill/decode pair in PD mode |
| HTTP workers: JSON requests with a streamed body | No. One attempt; see [Retries and Request Bodies](#retries-and-request-bodies) |
| HTTP workers: audio transcriptions | No |
| gRPC and ZMQ workers: Chat Completions, Completions, `/generate`, Messages | Yes. The request is prepared once (tokenization, tenant rate limiting); each retry selects workers again and re-sends the prepared request |
| gRPC and ZMQ workers: Responses, Embeddings, Classify | No |
| External providers: OpenAI Chat Completions, Gemini Interactions | Yes. OpenAI Chat Completions retries against the same provider endpoint |

---

## Exponential Backoff with Jitter

SMG uses exponential backoff with jitter to space out retry attempts:

```text
base  = min(initial_backoff_ms * backoff_multiplier ^ n, max_backoff_ms)
delay = base * (1 + uniform(-jitter_factor, +jitter_factor))
```

`n` is `0` before the first retry, `1` before the second, and so on. The cap applies before jitter, and SMG refuses to start if `--retry-jitter-factor` is outside `0.0`–`1.0`.

### Example Progression

With default settings (5 attempts, so up to 4 retries):

| Retry | Before attempt | Base delay | With ±20% jitter |
|-------|----------------|------------|------------------|
| 1 | 2 | 50 ms | 40–60 ms |
| 2 | 3 | 75 ms | 60–90 ms |
| 3 | 4 | 112 ms | 90–134 ms |
| 4 | 5 | 168 ms | 134–202 ms |

### Why Jitter?

Without jitter, if multiple requests fail simultaneously, they all retry at exactly the same time—potentially overwhelming the recovering service. Jitter spreads out retries randomly to prevent this "thundering herd" problem.

---

## Retryable Status Codes

SMG retries responses with these status codes. The list is global: per-worker `resilience` settings cannot add or remove codes from it.

| Code | Meaning | Why Retryable |
|------|---------|---------------|
| `408` | Request Timeout | Temporary network issue |
| `429` | Too Many Requests | Worker temporarily overloaded |
| `500` | Internal Server Error | Transient server issue; never replayed on a worker that answered it |
| `502` | Bad Gateway | Upstream temporarily unavailable |
| `503` | Service Unavailable | Service temporarily down |
| `504` | Gateway Timeout | Upstream timeout |

SMG's own errors use these codes too. In regular HTTP mode, a failed connection to the worker is a `500`, and a connect timeout, or a worker connection the OS gives up on, is a `504`; in PD mode a transport failure on either leg, those timeouts included, is a `502`; on the HTTP and gRPC routers, "no available workers" (every candidate unhealthy or circuit-open) is a `503`. The request's own timeout — `--request-timeout-secs`, or the worker's `timeout_secs`, running out — is a `504` in both modes, returned without a retry (see [Never Retried](#never-retried)).

A `500` the gateway synthesizes is not the worker's own answer and stays replayable on that worker; see [When a Worker Answers 500](#when-a-worker-answers-500).

Requests with other status codes (e.g., 400 Bad Request, 401 Unauthorized) are **not retried** because they would likely fail again.

### Never Retried

Some responses carry a retryable status but go straight back to the client, because another attempt cannot succeed within a backoff window:

- **Worker overload sheds**: `503` with error code `worker_overload_protection_shed` and a `Retry-After` header. The overload veto only changes when worker loads are polled again. See [Overload Protection](overload-protection.md).
- **Tenant rate-limit denials** (gRPC router): `429` with `tenant_rate_limit_exceeded`. A tenant's budget is reserved once per request, before dispatch, so a denial is returned with its `Retry-After`, and retry attempts never reserve again. See [Tenant Rate Limiting](tenant-rate-limiting.md).
- **Admission rejections**: `429` `admission_queue_full` and `503` `admission_queue_timeout` come from the admission layer in front of the router and never reach the retry loop. See [Rate Limiting](rate-limiting.md).
- **Request timeouts**: `504` when the request's own timeout (`--request-timeout-secs`, or the worker's `timeout_secs`) runs out. The timeout bounds how long the request may take, so a replay on another worker could only hold the client through a second timeout. A `504` from a connect timeout, or from a worker connection the OS gives up on, is the worker's failure, not the request's bound, and is retried.
- A few other gateway errors that another attempt cannot fix are marked terminal the same way.

---

## Configuration

```bash
smg launch \
  --worker-urls http://w1:8000 http://w2:8000 \
  --retry-max-retries 5 \
  --retry-initial-backoff-ms 50 \
  --retry-max-backoff-ms 30000 \
  --retry-backoff-multiplier 1.5 \
  --retry-jitter-factor 0.2
```

### Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `--retry-max-retries` | `5` | Maximum attempts per request, including the first; `1` disables retries |
| `--retry-initial-backoff-ms` | `50` | Base delay before the first retry (milliseconds) |
| `--retry-max-backoff-ms` | `30000` | Cap on the base delay (milliseconds), applied before jitter |
| `--retry-backoff-multiplier` | `1.5` | Factor applied to the base delay for each further retry |
| `--retry-jitter-factor` | `0.2` | Random jitter factor (0.0-1.0) to prevent thundering herd |
| `--disable-retries` | `false` | Disable router retries (same as `--retry-max-retries 1`) |

### Per-Worker Overrides

The worker spec's `resilience` block accepts retry fields (`max_retries`, `initial_backoff_ms`, `max_backoff_ms`, `backoff_multiplier`, `jitter_factor`, and `disable_retry`), but in v1.11.0 they have no effect. Worker registration leaves them out of the spec it stores on the worker, and a per-model retry policy is only created from retry fields in that stored spec, so none is created and every request uses the router flags above. Use `--retry-*` and `--disable-retries` instead.

The same block's circuit-breaker settings do apply, including two status-code lists that affect the circuit breaker, not retries: `retryable_status_codes` (statuses counted as circuit-breaker failures) and `capacity_status_codes` (statuses treated as capacity pushback). Narrowing either list never makes a status non-retryable. See [Interaction with Circuit Breakers](#interaction-with-circuit-breakers).

---

## Recommended Configurations

<div class="grid" markdown>

<div class="card" markdown>

### :material-lightning-bolt: Latency-Sensitive

At most one retry, after a short delay.

```bash
smg launch \
  --retry-max-retries 2 \
  --retry-initial-backoff-ms 10 \
  --retry-max-backoff-ms 100
```

**Use when**: Real-time chat, interactive UIs

</div>

<div class="card" markdown>

### :material-server-network: High-Availability

Up to two retries with doubling delays.

```bash
smg launch \
  --retry-max-retries 3 \
  --retry-initial-backoff-ms 100 \
  --retry-backoff-multiplier 2.0
```

**Use when**: Production APIs, multi-worker deployments

</div>

<div class="card" markdown>

### :material-cog: Batch Processing

Up to nine retries for offline workloads.

```bash
smg launch \
  --retry-max-retries 10 \
  --retry-initial-backoff-ms 100 \
  --retry-max-backoff-ms 60000 \
  --retry-backoff-multiplier 2.0
```

**Use when**: Batch inference, non-interactive pipelines

</div>

<div class="card" markdown>

### :material-close-circle: No Retries

Disable retries entirely.

```bash
smg launch --disable-retries
```

**Use when**: Client handles retries, testing failure scenarios

</div>

</div>

---

## Retries and Request Bodies

The HTTP router can only retry a request whose body it buffered. For each request it decides whether to buffer the body or stream it to the worker unread:

- With retries enabled, a body that nothing else needs to parse is buffered up to `--max-buffered-request-bytes` (default 1 MiB) so it stays retryable. A larger body streams to the worker and gets a **single attempt**.
- Requests the router must parse anyway, for example under the default `cache_aware` policy, always buffer and keep their retries.
- With retries disabled, eligible bodies stream at any size.

Raise `--max-buffered-request-bytes` to keep larger requests retryable, at the cost of router memory. `smg_router_request_body_path_total{reason="retry_forfeited"}` counts requests that gave up their retries to stream. See [Request Streaming and Upstream Connections](../performance/request-streaming.md#how-the-router-decides) for the full decision.

### Pre-Response Resends

Separately from these retries, the HTTP router resends a request once, immediately, when the send failed before the worker produced any response, most often because the backend had already closed the pooled connection. The resend uses no backoff and no retry attempt, happens even with `--disable-retries`, and is counted in `smg_router_upstream_send_retries_total`. Timeouts, streamed bodies, and upstream bodies of 1 MiB or more are not resent. See [Resending Pre-Response Failures](../performance/request-streaming.md#resending-pre-response-failures).

---

## Interaction with Circuit Breakers

Retries and circuit breakers meet in worker selection:

- **Open circuits are skipped.** Every attempt, including the first, chooses only among workers whose circuit is closed or half-open, so a retry never goes to a worker whose circuit has opened in the meantime. A half-open circuit does not throttle traffic: its worker is as selectable as one with a closed circuit.
- **"No available workers."** If every candidate is unhealthy or circuit-open, the attempt fails with `503` `no_available_workers`. The HTTP router (regular and PD) retries it like any other retryable failure: it backs off and selects again, and a circuit may have moved to half-open by then. The gRPC pipeline selects workers for the first attempt before its retry loop, so there the `503` is returned at once unless it happens on a retry.
- **A failed request charges a worker once.** Every attempt's success is recorded, but however many of a request's attempts fail on the same worker, that worker's breaker takes one failure: `--cb-failure-threshold` counts failed requests, not attempts. This covers HTTP workers in regular mode and gRPC and ZMQ workers; for HTTP workers in PD mode, for external providers, and on every path in v1.12.0 and earlier, each failed attempt counts separately.
- **Capacity pushback is retried but not counted.** A `429` (by default) is retried, but records neither a failure nor a success on the breaker, so a busy worker's circuit does not open under load. Per worker, `resilience.capacity_status_codes` replaces that list, for example to treat `503` as pushback. Whether a capacity status is retried still follows the global [retryable list](#retryable-status-codes).
- **Client-caused aborts are not counted.** Aborted streamed uploads (`408`, `413`, `400`) say nothing about the worker.

See [Circuit Breakers](circuit-breakers.md) for thresholds and state transitions.

---

## Monitoring

### Metrics

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `smg_worker_retries_total` | Counter | `worker_type`, `endpoint` | Retries performed, one per backoff. PD mode counts each retry under both `prefill` and `decode` |
| `smg_worker_retries_exhausted_total` | Counter | `worker_type`, `endpoint` | Requests whose last attempt still returned a retryable status; also counted when retries are disabled |
| `smg_worker_retry_backoff_seconds` | Histogram | `attempt` | Backoff delay before each retry; `attempt` is the retry number, `1` for the first retry. Buckets of its own, from 10 ms to 60 s |
| `smg_router_upstream_send_retries_total` | Counter | `router_type` | Immediate resends after a pre-response transport failure |

`worker_type` is `regular`, `prefill`, or `decode` for local workers and `external` for the OpenAI provider router.

### Useful PromQL Queries

<div class="grid" markdown>

<div class="card" markdown>

#### Retry Rate

```promql
# Retries per second
sum(rate(smg_worker_retries_total[5m]))

# Requests that ran out of attempts, per second
sum(rate(smg_worker_retries_exhausted_total[5m]))
```

</div>

<div class="card" markdown>

#### Backoff Distribution

```promql
# Average backoff delay
sum(rate(smg_worker_retry_backoff_seconds_sum[5m]))
  / sum(rate(smg_worker_retry_backoff_seconds_count[5m]))

# 99th percentile backoff per retry number
histogram_quantile(0.99, sum by (attempt, le) (rate(smg_worker_retry_backoff_seconds_bucket[5m])))
```

</div>

</div>

### What to Watch

| Signal | Query | What it suggests |
|--------|-------|------------------|
| Retry rate rising | `sum(rate(smg_worker_retries_total[5m]))` | Workers are returning retryable errors; check worker health and circuit breaker state |
| Exhausted retries | `sum(rate(smg_worker_retries_exhausted_total[5m]))` | Failures are reaching clients despite retries |
| Long backoffs | `histogram_quantile(0.99, sum by (le) (rate(smg_worker_retry_backoff_seconds_bucket[5m])))` | Requests spend noticeable time waiting between attempts |
| Retries forfeited | `sum(rate(smg_router_request_body_path_total{reason="retry_forfeited"}[5m]))` | Large bodies are streaming with a single attempt |
| Pre-response resends | `sum(rate(smg_router_upstream_send_retries_total[5m]))` | Pooled connections are going stale; check `--upstream-pool-idle-timeout-secs` |

---

## Tuning Guidelines

| Symptom | Potential Adjustment |
|---------|---------------------|
| Excessive latency from retries | Reduce `--retry-max-retries`, decrease backoff times |
| Thundering herd on recovery | Increase `--retry-jitter-factor` |
| Retries exhausted too quickly | Increase `--retry-max-retries`, `--retry-max-backoff-ms` |
| Clients seeing too many errors | Increase retry count, check worker health |
| Large requests fail without a retry | Raise `--max-buffered-request-bytes` so they stay buffered and retryable |
| Steady pre-response resends | Lower `--upstream-pool-idle-timeout-secs` below the backend's keep-alive timeout |

---

## What's Next?

<div class="grid" markdown>

<div class="card" markdown>

### :material-electric-switch: Circuit Breakers

Isolate failing workers to prevent cascade failures.

[Circuit Breakers →](circuit-breakers.md)

</div>

<div class="card" markdown>

### :material-heart-pulse: Health Checks

Proactive worker monitoring and failure detection.

[Health Checks →](health-checks.md)

</div>

<div class="card" markdown>

### :material-traffic-light: Rate Limiting

Protect workers from overload with token bucket rate limiting.

[Rate Limiting →](rate-limiting.md)

</div>

<div class="card" markdown>

### :material-swap-horizontal: Request Streaming

Which request bodies stream, and the upstream connection settings behind retries.

[Request Streaming →](../performance/request-streaming.md)

</div>

</div>
