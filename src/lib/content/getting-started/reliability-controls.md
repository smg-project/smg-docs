---
title: Reliability Controls
---

# Reliability Controls

This guide provides command-first setup for request protection and failure handling: concurrency limits, retries, and circuit breakers.

<div class="prerequisites" markdown>

#### Before you begin

- Completed the [Getting Started](index.md) guide
- Two or more workers recommended for retry failover

</div>

---

## 1. Concurrency and Queue Limits

Cap how many requests the gateway runs at once, and how many can wait:

```bash
smg launch \
  --worker-urls http://w1:8000 http://w2:8000 \
  --max-concurrent-requests 100 \
  --queue-size 200 \
  --queue-timeout-secs 30
```

- Each admitted request holds a permit until its response finishes, streaming included, so at most 100 requests are in flight.
- Up to 200 more wait in arrival order. A request that finds the queue full gets **429**; one that waits longer than 30 seconds gets **503**. Both carry `Retry-After: 2`.
- Size `--max-concurrent-requests` from what your workers can run at once; see [Sizing Guidelines](../concepts/reliability/rate-limiting.md#sizing-guidelines).

Leave `--rate-limit-tokens-per-second` unset. A positive rate also refills the bucket over time, so requests in flight can exceed the cap. Set it only to keep the pre-v1.10 burst-rate behavior:

```bash
smg launch \
  --worker-urls http://w1:8000 http://w2:8000 \
  --max-concurrent-requests 100 \
  --rate-limit-tokens-per-second 100 \
  --queue-size 200 \
  --queue-timeout-secs 30
```

For per-worker load thresholds that take a saturated worker out of routing, see [Overload Protection](../concepts/reliability/overload-protection.md).

---

## 2. Retries

Enable retries with explicit backoff settings:

```bash
smg launch \
  --worker-urls http://w1:8000 http://w2:8000 \
  --retry-max-retries 5 \
  --retry-initial-backoff-ms 50 \
  --retry-max-backoff-ms 30000 \
  --retry-backoff-multiplier 1.5 \
  --retry-jitter-factor 0.2
```

Disable retries when client handles them:

```bash
smg launch \
  --worker-urls http://w1:8000 http://w2:8000 \
  --disable-retries
```

---

## 3. Circuit Breakers

Protect traffic from repeatedly failing workers:

```bash
smg launch \
  --worker-urls http://w1:8000 http://w2:8000 \
  --cb-failure-threshold 10 \
  --cb-success-threshold 3 \
  --cb-timeout-duration-secs 60
```

A worker's circuit opens after 10 consecutive failures (`408`, `500`, `502`, `503`, `504`, or no response). A `429` from a worker is capacity pushback: it can still be retried, but it never opens or closes the circuit. See [What Counts as a Failure](../concepts/reliability/circuit-breakers.md#what-counts-as-a-failure). `--cb-window-duration-secs` is accepted but not used by the breaker.

Disable only for controlled testing:

```bash
smg launch \
  --worker-urls http://w1:8000 http://w2:8000 \
  --disable-circuit-breaker
```

---

## Production Baseline

A practical starting profile:

```bash
smg launch \
  --worker-urls http://w1:8000 http://w2:8000 http://w3:8000 \
  --max-concurrent-requests 150 \
  --queue-size 300 \
  --queue-timeout-secs 30 \
  --retry-max-retries 3 \
  --retry-initial-backoff-ms 50 \
  --retry-max-backoff-ms 5000 \
  --retry-backoff-multiplier 2.0 \
  --retry-jitter-factor 0.2 \
  --cb-failure-threshold 10 \
  --cb-success-threshold 3 \
  --cb-timeout-duration-secs 60
```

---

## Verify

```bash
curl http://localhost:30000/health
curl http://localhost:30000/workers

# Admission and circuit breaker metrics (Prometheus port, default 29000)
curl -s http://localhost:29000/metrics | grep -E '^smg_(admission|http_rate_limit|worker_cb)'
```

Admission metrics appear once requests start going through admission control.

---

## Next Steps

- [Rate Limiting Concepts](../concepts/reliability/rate-limiting.md)
- [Overload Protection Concepts](../concepts/reliability/overload-protection.md)
- [Retries Concepts](../concepts/reliability/retries.md)
- [Circuit Breakers Concepts](../concepts/reliability/circuit-breakers.md)
- [Configuration Reference](../reference/configuration.md#rate-limiting-configuration)
