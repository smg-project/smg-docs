---
title: Circuit Breakers
---

# Circuit Breakers

Circuit breakers prevent cascade failures by stopping traffic to unhealthy workers. They're essential for maintaining system stability when workers fail.

---

## Overview

<div class="grid" markdown>

<div class="card" markdown>

### :material-electric-switch: Automatic Isolation

Automatically isolate unhealthy workers to prevent cascade failures across your inference fleet.

</div>

<div class="card" markdown>

### :material-lightning-bolt: Fail Fast

Detect failing workers and stop sending traffic immediately—no wasted requests or timeout waits.

</div>

<div class="card" markdown>

### :material-refresh: Self-Healing

Automatically test recovery and restore traffic when workers become healthy again.

</div>

<div class="card" markdown>

### :material-chart-line: Observable

Prometheus metrics for breaker state, state transitions, and recorded outcomes.

</div>

</div>

---

## Why Circuit Breakers?

Without circuit breakers, a failing worker can cause:

1. **Wasted requests**: Requests sent to failing workers timeout
2. **Increased latency**: Clients wait for timeouts before retry
3. **Resource exhaustion**: Connections pile up to dead workers
4. **Cascade failures**: Retry storms overwhelm remaining workers

Circuit breakers **fail fast**—they detect failing workers and stop sending traffic immediately.

---

## How It Works

Each worker has its own circuit breaker with three states:

<div class="grid" markdown>

<div class="card" markdown>

### :material-check-circle: Closed State

**Normal operation** - requests flow through.

- Each failure increments a consecutive-failure counter
- A success resets the counter to zero
- Opens when the counter reaches `--cb-failure-threshold`

</div>

<div class="card" markdown>

### :material-close-circle: Open State

**Circuit tripped** - the worker is out of routing.

- No new requests are sent to the worker
- With no other worker available, requests get 503 `no_available_workers`
- Moves to half-open after `--cb-timeout-duration-secs`

</div>

<div class="card" markdown>

### :material-help-circle: Half-Open State

**Testing recovery** - the worker takes traffic again.

- Traffic to the worker is not throttled
- `--cb-success-threshold` consecutive successes close the circuit
- Any failure reopens it

</div>

</div>

---

## State Transitions

### Closed → Open

The circuit **opens** when:

```
consecutive_failures >= failure_threshold
```

A single successful request resets `consecutive_failures` to zero. `--cb-window-duration-secs` is accepted and validated but is not consumed by the state machine — failures are tracked with a running consecutive-failure counter rather than a sliding window.

### Open → Half-Open

Once the circuit has been open for `--cb-timeout-duration-secs`, the next check of the worker's state (for example, during worker selection) moves it to half-open.

### Half-Open → Closed

If `--cb-success-threshold` consecutive requests succeed, the circuit closes and normal operation resumes.

### Half-Open → Open

If any request fails during half-open, the circuit reopens immediately and the open timeout starts over.

---

## What Counts as a Failure

The breaker learns from the status of each response a worker sends back. Since v1.10, capacity pushback is not a failure:

| Worker outcome | Recorded as |
|----------------|-------------|
| `2xx`, or any status not listed below (for example `400` or `404`) | Success |
| `408`, `500`, `502`, `503`, `504` | Failure |
| No response (connection error or timeout), recorded as the 5xx the gateway returns for it | Failure |
| `429` (capacity pushback) | Nothing: neither a failure nor a success |

- **Why 429 is excluded.** A busy engine answers 429 to say "not now". Counting that as a failure would open the breakers of the busiest workers during a load spike and push their traffic onto the rest. The gateway still retries a 429 when retries are enabled, but the response records no breaker sample in either direction, so it cannot close a half-open breaker either.
- **gRPC workers.** gRPC status codes are mapped to HTTP statuses first: `RESOURCE_EXHAUSTED` becomes 429 (pushback), `UNAVAILABLE` becomes 503 and `DEADLINE_EXCEEDED` becomes 504 (failures), and `INVALID_ARGUMENT` becomes 400 (a success for the breaker).
- **Gateway sheds never count.** Responses the gateway produces without a worker answering, such as [admission sheds](rate-limiting.md#what-the-client-sees), [overload protection](overload-protection.md) sheds (`worker_overload_protection_shed`), and `no_available_workers`, record nothing on any breaker.
- **503 is still a failure by default**, because a 503 cannot be told apart from an outage. If a backend uses 503 for backpressure, add it to that worker's capacity codes.

### Per-Worker Overrides

The `resilience` block of a worker spec (for example, in a `POST /workers` request body; see the [Admin API](../../reference/api/admin.md)) overrides the breaker settings for that worker:

```json
{
  "url": "http://gpu1:8000",
  "resilience": {
    "capacity_status_codes": [429, 503],
    "cb_failure_threshold": 5,
    "cb_timeout_secs": 30
  }
}
```

| Field | Default | Description |
|-------|---------|-------------|
| `capacity_status_codes` | `[429]` | Statuses treated as capacity pushback, which record no breaker sample |
| `retryable_status_codes` | `[408, 429, 500, 502, 503, 504]` | Statuses that count as breaker failures (codes also listed in `capacity_status_codes` are skipped). Despite the name, it does not change which responses the gateway retries |
| `cb_failure_threshold` | `--cb-failure-threshold` | Consecutive failures that open the circuit |
| `cb_success_threshold` | `--cb-success-threshold` | Consecutive half-open successes that close the circuit |
| `cb_timeout_secs` | `--cb-timeout-duration-secs` | Seconds the circuit stays open before it moves to half-open |

A status list you set replaces the default instead of adding to it, so keep `429` in `capacity_status_codes` if you still want it treated as pushback.

---

## Configuration

```bash
smg launch \
  --worker-urls http://w1:8000 http://w2:8000 \
  --cb-failure-threshold 5 \
  --cb-success-threshold 2 \
  --cb-timeout-duration-secs 30
```

### Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `--cb-failure-threshold` | `10` | Consecutive failures before circuit opens |
| `--cb-success-threshold` | `3` | Consecutive successes in half-open state to close circuit |
| `--cb-timeout-duration-secs` | `60` | Seconds before open circuit transitions to half-open |
| `--cb-window-duration-secs` | `120` | Accepted and validated (must be `> 0`) but not consumed by the state machine; see *Closed → Open* |
| `--disable-circuit-breaker` | `false` | Breakers never open: the failure threshold is raised to its maximum. Outcomes are still counted in the metrics |

The thresholds must be at least `1`, and the durations must be greater than `0`.

### Configuration Examples

<div class="grid" markdown>

<div class="card" markdown>

#### :material-lightning-bolt: Fast Circuit Opening

Sensitive to failures—isolate workers quickly.

```bash
smg launch \
  --cb-failure-threshold 3 \
  --cb-timeout-duration-secs 30
```

**Use when**: Critical availability, latency-sensitive applications

</div>

<div class="card" markdown>

#### :material-shield: Tolerant Configuration

Allow occasional failures before tripping.

```bash
smg launch \
  --cb-failure-threshold 20 \
  --cb-success-threshold 5 \
  --cb-timeout-duration-secs 120
```

**Use when**: Flaky workers, network instability, batch processing

</div>

</div>

### Tuning Guidelines

| Scenario | Recommendation |
|----------|---------------|
| **Flaky workers** | Higher `failure_threshold`, shorter `timeout` |
| **Critical availability** | Lower `failure_threshold`, longer `timeout` |
| **Fast recovery workers** | Lower `timeout`, lower `success_threshold` |
| **Slow recovery workers** | Higher `timeout`, higher `success_threshold` |
| **Backends that answer 503 when busy** | Add `503` to the worker's `capacity_status_codes` |

---

## Example Scenarios

### Normal Operation

<div class="architecture-diagram" markdown>

![Circuit Breaker Normal Operation](../../assets/images/circuit-breaker-normal.svg)

</div>

### Worker Fails

<div class="architecture-diagram" markdown>

![Circuit Breaker Worker Failure](../../assets/images/circuit-breaker-failure.svg)

</div>

Once every worker for the model has an open circuit, requests get 503 `no_available_workers`. For external provider workers the code is `service_unavailable`.

### Recovery

<div class="architecture-diagram" markdown>

![Circuit Breaker Recovery](../../assets/images/circuit-breaker-recovery.svg)

</div>

---

## Monitoring

### Metrics

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `smg_worker_cb_state` | Gauge | `worker` | Current state: `0` closed, `1` open, `2` half-open. Set to `-1` after the worker is removed |
| `smg_worker_cb_transitions_total` | Counter | `worker`, `from`, `to` | State transitions; `from` and `to` are `closed`, `open`, or `half_open` |
| `smg_worker_cb_outcomes_total` | Counter | `worker`, `outcome` | Recorded outcomes, `success` or `failure`. Capacity pushback records nothing |
| `smg_worker_cb_consecutive_failures` | Gauge | `worker` | Current consecutive-failure count |
| `smg_worker_cb_consecutive_successes` | Gauge | `worker` | Current consecutive-success count |

The `worker` label is the worker URL.

### Useful PromQL Queries

<div class="grid" markdown>

<div class="card" markdown>

#### Current States

```promql
# Current circuit breaker states
smg_worker_cb_state

# Workers with open circuits
count(smg_worker_cb_state == 1)
```

</div>

<div class="card" markdown>

#### Transitions and Outcomes

```promql
# State transitions rate
rate(smg_worker_cb_transitions_total[5m])

# Failure share per worker
sum by (worker) (rate(smg_worker_cb_outcomes_total{outcome="failure"}[5m]))
  / sum by (worker) (rate(smg_worker_cb_outcomes_total[5m]))
```

</div>

</div>

### What to Watch

| Signal | Meaning | Action |
|--------|---------|--------|
| `smg_worker_cb_state == 1` for a worker | The worker is out of routing | Check the worker's logs and health |
| Every live worker at `1` | No worker can take traffic; requests get 503 `no_available_workers` | Treat it as an outage |
| Repeated `open` → `half_open` → `open` transitions | The worker fails as soon as traffic returns (flapping) | Investigate the worker before it rejoins |
| `smg_worker_cb_consecutive_failures` near the threshold | The worker is failing right now | Check it before the circuit opens |

### Alerting Example

```yaml
groups:
  - name: smg-circuit-breakers
    rules:
      - alert: CircuitBreakerOpen
        expr: smg_worker_cb_state == 1
        for: 1m
        labels:
          severity: warning
        annotations:
          summary: "Circuit breaker open for {{ $labels.worker }}"

      - alert: AllCircuitsOpen
        # Removed workers report -1, so compare against live workers only.
        expr: count(smg_worker_cb_state == 1) == count(smg_worker_cb_state >= 0)
        for: 30s
        labels:
          severity: critical
        annotations:
          summary: "All worker circuit breakers are open"
```

---

## Interaction with Other Features

### Retries

- For local workers, each retry attempt runs worker selection again, so a worker whose circuit is open is skipped.
- If every worker is unavailable, the attempt gets 503 `no_available_workers`.
- A half-open worker is selected like any other; its outcomes decide whether the circuit closes or reopens.
- A 429 from a worker is retried when retries are enabled, but leaves that worker's breaker untouched.

### Health Checks

Circuit breakers and health checks are independent gates: health checks probe workers in the background, while circuit breakers react to real request outcomes. A worker receives traffic only when both allow it, and when [overload protection](overload-protection.md), if enabled, has not vetoed it:

| Health Check | Circuit Breaker | Receives traffic? |
|--------------|-----------------|-------------------|
| Passing | Closed or half-open | Yes |
| Passing | Open | No, until the open timeout passes and the circuit moves to half-open |
| Failing | Any | No |

---

## Disabling Circuit Breakers

In some cases, you may want to disable circuit breakers:

```bash
smg launch --worker-urls http://w1:8000 --disable-circuit-breaker
```

The breakers keep recording outcomes, so the metrics still move, but no circuit opens unless a worker sets its own `cb_failure_threshold` in its `resilience` block.

!!! warning "Not Recommended"
    Disabling circuit breakers removes an important safety mechanism. Only do this if you have another layer providing similar protection.

---

## What's Next?

<div class="grid" markdown>

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

### :material-traffic-light: Rate Limiting

Cap how many requests the gateway runs at once, with a bounded queue.

[Rate Limiting →](rate-limiting.md)

</div>

<div class="card" markdown>

### :material-shield-alert: Overload Protection

Take a saturated worker out of routing before it fails.

[Overload Protection →](overload-protection.md)

</div>

<div class="card" markdown>

### :material-chart-box: Metrics Reference

Complete list of circuit breaker metrics.

[Metrics Reference →](../../reference/metrics.md)

</div>

</div>
