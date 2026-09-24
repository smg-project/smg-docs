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

Only retry status codes that are likely to succeed on another attempt, and never a response whose cause cannot clear within a backoff window.

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
3. The next attempt runs worker selection again. Workers that are unhealthy, have an open circuit breaker, or are vetoed by overload protection are skipped. The policy can still pick the same worker if it remains eligible, for example under `cache_aware` or `consistent_hashing` affinity.
4. When a response is not retried, or no attempts remain, SMG returns it to the client.

`--retry-max-retries` counts **attempts, including the first**: the default `5` allows the initial attempt plus up to four retries, and `1` disables retries.

A worker that has returned a success status and started streaming its response is never retried, even if the stream fails later.

HTTP-router responses relayed from a worker carry an `x-smg-routed-worker-id` header with the URL of the worker that produced them (the decode worker in PD mode), so a client can see where the final attempt landed.

### Where Retries Apply

| Traffic | Router retries |
|---------|----------------|
| HTTP workers: JSON requests with a buffered body (regular and PD mode) | Yes. Each attempt selects a new worker, or a new prefill/decode pair |
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

`n` is `0` before the first retry, `1` before the second, and so on. `jitter_factor` is clamped to `0.0`–`1.0`, and the cap applies before jitter.

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
| `500` | Internal Server Error | Transient server issue |
| `502` | Bad Gateway | Upstream temporarily unavailable |
| `503` | Service Unavailable | Service temporarily down |
| `504` | Gateway Timeout | Upstream timeout |

SMG's own errors use these codes too. On the HTTP router, a failed connection to the worker is a `500` and an upstream timeout is a `504`; on the HTTP and gRPC routers, "no available workers" (every candidate unhealthy or circuit-open) is a `503`.

Requests with other status codes (e.g., 400 Bad Request, 401 Unauthorized) are **not retried** because they would likely fail again.

### Never Retried

Some responses carry a retryable status but go straight back to the client, because another attempt cannot succeed within a backoff window:

- **Worker overload sheds**: `503` with error code `worker_overload_protection_shed` and a `Retry-After` header. The overload veto only changes when worker loads are polled again. See [Overload Protection](overload-protection.md).
- **Tenant rate-limit denials** (gRPC router): `429` with `tenant_rate_limit_exceeded`. A tenant's budget is reserved once per request, before dispatch, so a denial is returned with its `Retry-After`, and retry attempts never reserve again. See [Tenant Rate Limiting](tenant-rate-limiting.md).
- **Admission rejections**: `429` `admission_queue_full` and `503` `admission_queue_timeout` come from the admission layer in front of the router and never reach the retry loop. See [Rate Limiting](rate-limiting.md).
- A few other gateway errors that another attempt cannot fix are marked terminal the same way.

---

## Configuration

```bash
smg \
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

The worker spec's `resilience` block accepts retry fields (`max_retries`, `initial_backoff_ms`, `max_backoff_ms`, `backoff_multiplier`, `jitter_factor`, and `disable_retry`), but in v1.11.0 they have no effect. Worker registration resolves them but does not store them on the worker, so no per-model retry policy is created and every request uses the router flags above. Use `--retry-*` and `--disable-retries` instead.

The same block's circuit-breaker settings do apply, including two status-code lists that affect the circuit breaker, not retries: `retryable_status_codes` (statuses counted as circuit-breaker failures) and `capacity_status_codes` (statuses treated as capacity pushback). Narrowing either list never makes a status non-retryable. See [Interaction with Circuit Breakers](#interaction-with-circuit-breakers).

---

## Recommended Configurations

<div class="grid" markdown>

<div class="card" markdown>

### :material-lightning-bolt: Latency-Sensitive

At most one retry, after a short delay.

```bash
smg \
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
smg \
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
smg \
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
smg --disable-retries
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

Raise `--max-buffered-request-bytes` to keep larger requests retryable, at the cost of router memory. `smg_router_request_body_path_total{reason="retry_forfeited"}` counts requests that streamed without retries. See [Request Streaming and Upstream Connections](../performance/request-streaming.md#how-the-router-decides) for the full decision.

### Pre-Response Resends

Separately from these retries, the HTTP router resends a request once, immediately, when the send failed before the worker produced any response, most often because the backend had already closed the pooled connection. The resend uses no backoff and no retry attempt, happens even with `--disable-retries`, and is counted in `smg_router_upstream_send_retries_total`. Timeouts, streamed bodies, and upstream bodies of 1 MiB or more are not resent. See [Resending Pre-Response Failures](../performance/request-streaming.md#resending-pre-response-failures).

---

## Interaction with Circuit Breakers

Retries and circuit breakers meet in worker selection:

- **Open circuits are skipped.** Every attempt, including the first, chooses only among workers whose circuit is closed or half-open, so a retry never goes to a worker whose circuit has opened in the meantime. A half-open circuit does not throttle traffic: its worker is as selectable as one with a closed circuit.
- **"No available workers."** If every candidate is unhealthy or circuit-open, the attempt fails with `503` `no_available_workers`. The HTTP router (regular and PD) retries it like any other retryable failure: it backs off and selects again, and a circuit may have moved to half-open by then. The gRPC pipeline selects workers for the first attempt before its retry loop, so there the `503` is returned at once unless it happens on a retry.
- **Every attempt is recorded.** Each attempt's status counts toward the circuit breaker of the worker that served it.
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
| `smg_worker_retry_backoff_seconds` | Histogram | `attempt` | Backoff delay before each retry; `attempt` is the retry number, `1` for the first retry |
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

# 99th percentile backoff
histogram_quantile(0.99,
  sum by (le) (rate(smg_worker_retry_backoff_seconds_bucket[5m])))
```

</div>

</div>

### What to Watch

| Signal | Query | What it suggests |
|--------|-------|------------------|
| Retry rate rising | `sum(rate(smg_worker_retries_total[5m]))` | Workers are returning retryable errors; check worker health and circuit breaker state |
| Exhausted retries | `sum(rate(smg_worker_retries_exhausted_total[5m]))` | Failures are reaching clients despite retries |
| Long backoffs | 99th percentile of `smg_worker_retry_backoff_seconds` | Requests spend noticeable time waiting between attempts |
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
