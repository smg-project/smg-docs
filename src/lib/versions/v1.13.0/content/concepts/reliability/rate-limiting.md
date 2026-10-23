---
title: Rate Limiting
---

# Rate Limiting

Rate limiting in SMG is gateway admission control. The gateway caps how many requests it runs at once, keeps a bounded number of extra requests waiting in a first-in, first-out queue, and turns the rest away with a status code and a `Retry-After` header that tell clients to back off. It is on by default: without `--max-concurrent-requests` the cap is derived from the host (1024 per available core, at least 4096), so an overloaded gateway sheds instead of queueing without bound. Set the flag to size the cap for your fleet; `0` turns admission control off.

---

## Overview

<div class="grid" markdown>

<div class="card" markdown>

### :material-gauge: Standing-Concurrency Cap

At most `--max-concurrent-requests` requests (host-derived when unset) hold a permit at once, from admission until the last byte of the response.

</div>

<div class="card" markdown>

### :material-tray-full: Bounded FIFO Queue

Up to `--queue-size` requests wait for a permit. Each freed permit goes straight to the oldest waiter.

</div>

<div class="card" markdown>

### :material-timer-outline: Back-Off Signals

Sheds answer **429** or **503** with `Retry-After: 2` and an error code, so clients wait instead of retrying at once.

</div>

<div class="card" markdown>

### :material-chart-line: Observable

Gauges for permits in use and queue depth, and counters for every shed.

</div>

</div>

---

## Why Limit Admission?

Without a cap, the gateway forwards every request it receives straight to the workers. During a traffic spike the extra work piles up inside the engines, latency climbs for every request, and clients that time out retry, which adds even more load.

With a cap, the amount of work in the fleet stays bounded. Excess requests wait briefly in the gateway's queue, or are turned away with a clear signal to retry later.

Admission control is gateway-wide: it limits how many requests run at once across all workers, before any worker is chosen. [Overload protection](overload-protection.md) works per worker: it stops routing to a worker whose own load signal (queued requests or KV-cache usage) crosses a threshold while another worker is under them. When every candidate worker is over its threshold, the request goes to the least-loaded routable worker by default; with `--worker-overload-shed`, it is shed with a 503 instead.

---

## How It Works

Admission control covers the inference routes, such as `/v1/chat/completions`, `/v1/completions`, `/v1/responses`, `/v1/messages`, `/generate`, `/v1/embeddings`, and `/v1/rerank`. Health, model-listing, admin, and worker-management endpoints bypass it.

For each request:

1. **Take a permit.** If a permit is free, the request is admitted at once.
2. **Wait in the queue.** Otherwise, if a queue slot is free, the request waits there for a permit. Its body is not read until it is admitted, unless a WASM `OnRequest` module is attached, which reads the body first.
3. **Shed.** If the queue is full, or queueing is disabled, the request is rejected at once with **429**. If it waits longer than `--queue-timeout-secs`, it is rejected with **503**.

### Permit Lifetime

A permit is held from admission until the response finishes:

| Case | Permit is released when |
|------|-------------------------|
| Streaming response | The last chunk is sent, the stream fails, or the client disconnects |
| Non-streaming response | The response body has been sent |
| Client disconnects before the response starts | The gateway drops the request |

A request that is still waiting in the queue gives up its queue slot once the gateway notices that its client disconnected. With a small request body that is immediate, but a larger HTTP/1.1 request body that the gateway has not read yet can hide the disconnect until the request is admitted or times out.

Because a stream keeps its permit for as long as it streams, `--max-concurrent-requests` bounds *standing* concurrency: the number of requests in flight at any moment, long streams included.

### Queue

- **Hard bound.** A request that finds no free permit takes one of the `--queue-size` queue slots, or is shed with 429 at once. The queue never holds more than `--queue-size` requests.
- **First in, first out.** A freed permit is handed directly to the oldest waiting request. A new arrival cannot take a permit while others are waiting.
- **Timeout from queue entry.** `--queue-timeout-secs` counts from the moment the request starts waiting. A request that times out is shed with 503.
- **No queue.** `--queue-size 0` disables queueing: a request that finds no free permit is shed with 429 at once.

On the gRPC router, `--queue-timeout-secs` also bounds how long an admitted request may sit inside the gateway ahead of worker selection, counted from its admission grant (or its acceptance when admission control is off). A request held past the bound — the prefill admission wait of a PD or EPD request included — is refused with **503** `selection_queue_timeout` before any worker is chosen. The HTTP relay and the HTTP PD router are not bounded this way.

The FIFO handoff applies in both refill modes. A positive `--rate-limit-tokens-per-second` changes where admission budget comes from, as described next.

### Token Bucket and Refill

Permits come from a token bucket whose capacity is the concurrency cap. Admission spends a token; `--rate-limit-tokens-per-second` decides where tokens come back from:

| Behavior | Unset or `0` (default) | Positive rate `N` |
|----------|------------------------|-------------------|
| Tokens come back when | A response finishes | Only from the refill: `N` per second, up to the cap. A finished response frees its concurrency slot but returns no budget |
| `--max-concurrent-requests` bounds | Requests in flight | Requests in flight, still; `N` additionally bounds the sustained admission rate, with bursts up to the cap |
| Queue order | Strict FIFO | Strict FIFO: a queued request is admitted once a slot is free and the refill covers it |
| Longest queue wait | `--queue-timeout-secs` | `--queue-timeout-secs` |

With the default, the bucket's free tokens always equal the cap minus the requests in flight. Leave the rate unset unless you also want to bound the admission rate.

### With the Priority Scheduler

When `--priority-scheduler-enabled` is set and the scheduler starts, it replaces this limiter on the same routes:

- The scheduler's slots and per-class queues handle concurrency and queueing, each class with its own queue bound and timeout; `--queue-size` is not used. On the gRPC router, `--queue-timeout-secs` still bounds how long an admitted request may sit ahead of worker selection, counted from the scheduler's admission.
- `--max-concurrent-requests` no longer caps concurrency. The scheduler only uses it as a fallback capacity while no healthy worker is registered.
- A positive `--rate-limit-tokens-per-second` (unless `--max-concurrent-requests` is `0`) becomes a plain request-rate check in front of the scheduler. Tokens refill at that rate and are not returned when responses finish; a request that finds the bucket empty is shed with 429 `scheduler_queue_full` and `Retry-After: 2`.

If the scheduler fails to start, the gateway logs an error and falls back to the limiter described on this page. See [Priority Scheduling](priority-scheduling.md).

---

## Configuration

```bash
smg launch \
  --worker-urls http://w1:8000 http://w2:8000 \
  --max-concurrent-requests 100 \
  --queue-size 200 \
  --queue-timeout-secs 30
```

### Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `--max-concurrent-requests` | `-1` (host-derived) | Maximum requests holding a permit at once. A permit lasts for the full response, streaming included. `-1` derives the cap from the host: 1024 per available core, at least 4096. `0` turns admission control off. |
| `--queue-size` | `100` | Maximum requests waiting for a permit. `0` disables the queue. |
| `--queue-timeout-secs` | `60` | Longest time a request waits in the queue before it is shed with 503, and on the gRPC router the longest an admitted request may sit ahead of worker selection. Must be `> 0` when `--queue-size` is above `0`. |
| `--rate-limit-tokens-per-second` | unset (no rate limit) | Sustained admission rate in requests per second, bursting up to the concurrency cap. A finished response frees its slot but returns no budget. Unset or `0` means no rate limit, so the cap alone bounds requests in flight. Must be `>= 0`. |

The limits apply per gateway process. With several gateway replicas in front of the same workers, the fleet-wide cap is the per-replica value times the number of replicas.

`--request-timeout-secs` (default `1800`) is the default timeout for requests the gateway sends to HTTP workers, so it also bounds how long a stalled HTTP request can keep its permit.

---

## What the Client Sees

Every admission shed uses the gateway's standard error format: a JSON body, an `X-SMG-Error-Code` header with the same code, and `Retry-After: 2`.

| Situation | Status | Error code | `Retry-After` |
|-----------|--------|------------|---------------|
| No free permit, and queueing is disabled | 429 | `admission_queue_full` | `2` |
| No free permit, and the queue is full | 429 | `admission_queue_full` | `2` |
| Waited in the queue longer than `--queue-timeout-secs` | 503 | `admission_queue_timeout` | `2` |

```json
{
  "error": {
    "type": "server_error",
    "code": "admission_queue_timeout",
    "message": "timed out waiting for an admission slot",
    "param": null
  }
}
```

The `type` field follows the status code: `rate_limit_error` for a 429, `server_error` for a 503.

Other layers also answer 429 or 503. The error code tells them apart:

| Error code | Status | Source |
|------------|--------|--------|
| `selection_queue_timeout` | 503 | The gRPC router refused a request that sat inside the gateway longer than `--queue-timeout-secs` ahead of worker selection; carries `Retry-After: 2` |
| `pd_prefill_queue_full`, `pd_prefill_queue_timeout` | 429 | The [PD disaggregation](../routing/pd-disaggregation.md) prefill admission queue is full, or the request waited out its prefill admission timeout; no `Retry-After` |
| `scheduler_queue_full`, `scheduler_queue_timeout`, `scheduler_preempted` | 429, 503 | The [priority scheduler](../../reference/priority-scheduler.md#response-codes), when enabled |
| `worker_overload_protection_shed` | 503 | [Overload protection](overload-protection.md): with `--worker-overload-shed`, every candidate worker (or the chosen worker at dispatch) was overloaded; in PD mode, regardless of the flag, the decode worker could not admit the request within the admission wait. Its `Retry-After` is the load-poll interval |
| `tenant_rate_limit_exceeded` | 429 | [Tenant rate limiting](tenant-rate-limiting.md) |
| `no_available_workers` | 503 | No worker for the model can take the request, for example because each one is unhealthy or has an open [circuit breaker](circuit-breakers.md); carries `Retry-After: 1` |

---

## Monitoring

### Metrics

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `smg_admission_inflight` | Gauge | none | Requests holding a permit. A stream counts until its body finishes. |
| `smg_admission_queue_depth` | Gauge | none | Requests waiting in the admission queue. Never exceeds `--queue-size`. |
| `smg_admission_queue_rejected_total` | Counter | `reason`: `full`, `timeout`, `selection_timeout` | Queue-full (429) and queue-timeout (503) sheds, plus the gRPC router's pre-selection refusals (503 `selection_queue_timeout`). |
| `smg_http_rate_limit_total` | Counter | `result`: `allowed`, `rejected` | Every admission decision. `rejected` also counts the 429s returned when queueing is disabled, which have no `smg_admission_queue_rejected_total` sample. |

These metrics are per gateway process; sum them across replicas. With the priority scheduler enabled, this limiter is replaced: the gauges and the `full`/`timeout` rejections are not recorded (the scheduler exports its own `smg_scheduler_*` metrics), and `smg_http_rate_limit_total{result="rejected"}` counts only its request-rate check.

Admission sheds also appear in `smg_http_responses_total` under their `error_code` label, and `smg_http_inflight_request_age_count` shows how long in-flight requests, streams included, have been running.

### Useful PromQL Queries

<div class="grid" markdown>

<div class="card" markdown>

#### Permits and Queue

```promql
# Permits in use (compare with --max-concurrent-requests)
max_over_time(smg_admission_inflight[5m])

# Requests waiting (compare with --queue-size)
max_over_time(smg_admission_queue_depth[5m])
```

</div>

<div class="card" markdown>

#### Sheds

```promql
# Sheds per second, by reason
sum by (reason) (rate(smg_admission_queue_rejected_total[5m]))

# Share of admission decisions that were rejected
sum(rate(smg_http_rate_limit_total{result="rejected"}[5m]))
  / sum(rate(smg_http_rate_limit_total[5m]))
```

</div>

</div>

### What to Watch

| Signal | Meaning | Action |
|--------|---------|--------|
| `smg_admission_inflight` stays at `--max-concurrent-requests` | Every permit is in use, so new requests queue | Add workers, or raise the cap if the workers have headroom |
| `smg_admission_queue_depth` reaches `--queue-size` | The queue is full, so new requests get 429 | Add capacity; a deeper queue only helps if waiters can be admitted before they time out |
| `smg_admission_queue_rejected_total{reason="timeout"}` rises | Requests wait longer than `--queue-timeout-secs` and get 503 | Add capacity, or shorten the queue so clients get a quick 429 instead of a long wait |

---

## Sizing Guidelines

### Concurrent Requests

A permit lasts for the whole response, so size the cap to the number of requests your workers can run at once:

```text
max_concurrent_requests ≈ total running capacity of the workers ÷ number of gateway replicas
```

- A worker's running capacity is its engine's limit on requests running at once, for example `--max-num-seqs` on vLLM or `--max-running-requests` on SGLang.
- The cap applies per gateway process, so divide by the number of replicas that share the workers.
- A higher cap moves queueing into the engines; a lower one leaves engine capacity unused.

### Queue Size

With no token refill, permits free up as responses finish and waiters are admitted in order. A request at queue position *k* waits roughly `k × average response time ÷ max_concurrent_requests`, so waiters deeper than this are likely to time out before they are admitted:

```text
queue_size ≈ max_concurrent_requests × queue_timeout_secs ÷ average_response_secs
```

Use the full response time, streaming included. A longer queue only delays the 503 for requests past this depth.

### Queue Timeout

Set `--queue-timeout-secs` below your clients' own request timeout, so a waiting request gets a 503 with `Retry-After` from SMG before the client gives up on its own.

### Token Refill Rate

Leave `--rate-limit-tokens-per-second` unset unless you also want to bound how fast requests are admitted. A positive rate caps sustained admissions per second, bursting up to the concurrency cap; finished responses return no budget, so the gateway's long-run throughput is at most the refill rate even when workers have headroom.

---

## Example Configurations

=== "Interactive API"

    Short queue and timeout, so excess traffic gets a quick answer:

    ```bash
    smg launch \
      --worker-urls http://w1:8000 http://w2:8000 \
      --max-concurrent-requests 50 \
      --queue-size 25 \
      --queue-timeout-secs 5
    ```

=== "Batch Processing"

    Deeper queue and longer timeout, so requests wait their turn:

    ```bash
    smg launch \
      --worker-urls http://w1:8000 http://w2:8000 \
      --max-concurrent-requests 200 \
      --queue-size 500 \
      --queue-timeout-secs 60
    ```

=== "Request-Rate Limit"

    At most 100 requests in flight, admitted at a sustained 50 per second (bursting up to 100):

    ```bash
    smg launch \
      --worker-urls http://w1:8000 http://w2:8000 \
      --max-concurrent-requests 100 \
      --rate-limit-tokens-per-second 50
    ```

=== "Disabled"

    Requests are forwarded without an admission cap:

    ```bash
    smg launch \
      --worker-urls http://w1:8000 http://w2:8000 \
      --max-concurrent-requests 0
    ```

---

## Client-Side Handling

### Retry Strategy

Retry 429 and 503 responses after the `Retry-After` delay, and use the `X-SMG-Error-Code` header to tell the cases apart. Add jitter so clients that were shed together don't all retry at the same moment:

```python
import random
import time

import requests

def request_with_retry(url, data, max_retries=5):
    for attempt in range(max_retries):
        response = requests.post(url, json=data)

        if response.status_code in (429, 503):
            # SMG sheds carry Retry-After in seconds; fall back to exponential backoff.
            retry_after = response.headers.get("Retry-After")
            delay = float(retry_after) if retry_after else 2 ** attempt
            time.sleep(delay + random.uniform(0, 1))
            continue

        return response

    raise Exception("Max retries exceeded")
```

### Adaptive Rate

Monitor 429 and 503 responses and adjust the request rate:

```python
class AdaptiveClient:
    def __init__(self, base_rate=10):
        self.rate = base_rate

    def on_success(self):
        self.rate = min(self.rate * 1.1, 100)  # Increase slowly

    def on_rate_limit(self):
        self.rate = self.rate * 0.5  # Decrease quickly
```

---

## What's Next?

<div class="grid" markdown>

<div class="card" markdown>

### :material-shield-alert: Overload Protection

Per-worker load thresholds that take a saturated worker out of routing.

[Overload Protection →](overload-protection.md)

</div>

<div class="card" markdown>

### :material-priority-high: Priority Scheduling

Priority-aware admission with per-class queues, reserved slots, and preemption.

[Priority Scheduling →](priority-scheduling.md)

</div>

<div class="card" markdown>

### :material-swap-horizontal: Tenant Rate Limiting

Cap per-tenant LLM token and request consumption per minute — a different axis than this page's worker concurrency limits.

[Tenant Rate Limiting →](tenant-rate-limiting.md)

</div>

<div class="card" markdown>

### :material-electric-switch: Circuit Breakers

Isolate failing workers to prevent cascade failures.

[Circuit Breakers →](circuit-breakers.md)

</div>

<div class="card" markdown>

### :material-refresh: Retries

Automatic retry with exponential backoff for transient failures.

[Retries →](retries.md)

</div>

<div class="card" markdown>

### :material-heart-pulse: Health Checks

Proactive worker monitoring and failure detection.

[Health Checks →](health-checks.md)

</div>

<div class="card" markdown>

### :material-chart-box: Metrics Reference

Complete list of gateway metrics.

[Metrics Reference →](../../reference/metrics.md)

</div>

</div>
