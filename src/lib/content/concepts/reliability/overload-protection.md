---
title: Overload Protection
---

# Overload Protection

Inference engines accept work past saturation. Once an engine's running batch is full, new requests wait in its scheduler queue, and every request routed there waits a little longer. Load balancing spreads traffic *relative* to the other workers, so when the whole fleet saturates, every engine's queue deepens together until latency collapses, and routing alone never turns a request away. **Worker overload protection** puts an *absolute*, per-worker ceiling on the load each engine reports: a worker at or above its ceiling is taken out of routing until its load drops back under, and a request whose every candidate worker is over the ceiling is rejected immediately with a `503` and a `Retry-After` hint instead of joining an engine queue.

Overload protection is **opt-in**. The load monitoring that feeds it is on by default.

---

## Overview

<div class="grid" markdown>

<div class="card" markdown>

### :material-gauge-full: Absolute Ceilings

Per-worker thresholds on queued requests and KV cache usage, not a spread relative to the rest of the fleet.

</div>

<div class="card" markdown>

### :material-lightning-bolt: Zero Per-Request Cost

The check runs once per load report, off the request path. Selection only reads a flag it already loads.

</div>

<div class="card" markdown>

### :material-close-octagon: Immediate Shed

When every candidate worker is overloaded, the request gets a `503` with `Retry-After` right away instead of queueing.

</div>

<div class="card" markdown>

### :material-tune: Per-Worker Tuning

An `overload` block on a worker spec overrides the gateway thresholds for that worker.

</div>

</div>

---

## Why Overload Protection?

Without it:

1. **Engines queue past saturation**: a worker whose batch is full keeps accepting requests into its waiting queue, so each request routed to it waits longer before its first token.
2. **Relative balancing never refuses**: load-aware policies pick the least-loaded worker, but when every worker is saturated they still pick one.
3. **Gateway admission only counts its own traffic**: [rate limiting](rate-limiting.md) and the [priority scheduler](priority-scheduling.md) count requests in flight through this gateway instance. They cannot see a KV cache filled by long prompts or by another gateway replica sharing the same workers.

Overload protection reads what the engine itself reports, which is the same no matter how many gateway replicas share a worker.

### How It Differs from Other Load Controls

SMG has several controls that react to load. They act at different points and do different things when they trip:

| Control | Where it acts | Signal | When it trips |
|---------|---------------|--------|---------------|
| [Rate limiting](rate-limiting.md) and the [priority scheduler](priority-scheduling.md) | Admission, before routing | Requests in flight through this gateway | Queues the request, then rejects it: `429` when the queue is full, `503` when the queue wait times out, both with `Retry-After: 2` |
| Cache-aware de-ranking (`--overload-token-usage-threshold`) | Inside the `cache_aware` policy | KV usage of the hottest backend, compared with `>` | Drops prefix affinity for that decision and picks by load. The hot worker stays eligible and nothing is rejected |
| `least_load` waiting cap (`--least-load-max-waiting-requests`) | Inside the `least_load` policy | Reported waiting requests plus requests dispatched since the last poll | Skips capped workers. When every candidate is capped, the policy picks none and the request fails with `503 no_available_workers` |
| **Worker overload protection** (`--worker-overload-*`) | Worker eligibility, for every policy, on the HTTP and gRPC routers | Waiting requests (summed across DP ranks) and mean KV token usage, compared with `>=` | Excludes the worker until a report comes back under every threshold. When every candidate is excluded, the request is shed immediately with `503 worker_overload_protection_shed` |

The layers stack: admission control bounds what the gateway accepts, the routing policy decides where a request goes, and overload protection decides which workers may receive it at all.

---

## How It Works

### Signals

Each load report is scored on two signals:

| Signal | Computed from the report | Threshold flag |
|--------|--------------------------|----------------|
| Waiting requests | `num_waiting_reqs`, summed across the worker's DP ranks | `--worker-overload-waiting-requests` |
| KV token usage | `token_usage` (0.0–1.0), averaged across the worker's DP ranks | `--worker-overload-token-usage` |

A worker is **overloaded** when either signal is at or above its threshold. Because token usage is a mean, one saturated DP rank does not veto a worker whose other ranks still have room. It is the same signal `--balance-token-usage-threshold` reads, applied as an absolute ceiling instead of a fleet-wide spread.

### Where the Signals Come From

The gateway's load monitor collects one report per worker per poll. The source depends on how the worker is connected:

| Worker | Load source | Notes |
|--------|-------------|-------|
| HTTP, engine serves a native load route | `GET /v1/loads?include=core,disagg,queues,memory` | The monitor asks `/loads` first (served by an SMG gateway registered as a worker), then `/v1/loads`. The route that answers is remembered, so discovery costs one `404` per worker, once. |
| HTTP vLLM or SGLang without a native route | `GET /metrics` | Waiting requests from `vllm:num_requests_waiting` or SGLang `num_queue_reqs`; KV usage from `vllm:kv_cache_usage_perc` (`vllm:gpu_cache_usage_perc` on older vLLM) or SGLang `token_usage` (`sglang:` or `sglang_` prefix). A scrape without the KV usage gauge counts as no report. |
| HTTP, any other runtime without a native route | None | No report. |
| gRPC SGLang, vLLM, TokenSpeed | `GetLoads` RPC | SGLang reports every DP rank. vLLM reports a single rank, and zeros while it has no stats snapshot (before its first engine step, or always with `--disable-log-stats`). |
| gRPC TRT-LLM, MLX | None | `GetLoads` is not implemented for these backends. |
| ZMQ (`ipc://`) vLLM, TokenSpeed | Load attached to the engine's output batches | No request is sent. A rank has no report until it has produced output, and a TokenSpeed build that does not attach load reports nothing. |

### Polling

- Workers are polled in groups (same model, worker type, and connection mode) every `--load-monitor-interval` seconds (default `10`). A worker spec's `load_monitor_interval_secs` overrides the interval for its group; the smallest override in a group wins, with a floor of 1 second.
- Only `Ready` workers are polled. HTTP load requests time out after 5 seconds.
- The overload check runs once per received report, against that worker's own thresholds, and the verdict is stored on the worker. Selection only reads the stored flag, so the check adds nothing per request, and a verdict can only change when a new report arrives.
- There is no hysteresis: the first report under every threshold clears the veto.

### When a Worker Does Not Report

Overload protection **fails open**. A poll that produces no report (an unsupported backend, a timeout, an error, or an empty report) clears the worker's veto: no fresh signal means no opinion, and the worker stays routable. The veto is also cleared when a worker leaves `Ready` or is removed, and for every worker when the monitor has to rebuild its state (logged as a warning).

A field the backend leaves out reads as `0`, so that signal never trips. `--worker-overload-token-usage` only works for backends that report KV usage.

---

## Configuration

```bash
smg \
  --worker-urls http://w1:8000 http://w2:8000 \
  --worker-overload-protection \
  --worker-overload-waiting-requests 16
```

### Gateway Thresholds

| Parameter | Default | Description |
|-----------|---------|-------------|
| `--worker-overload-protection` | off | Enables protection with the gateway default `--worker-overload-token-usage 0.9`. Leaves the waiting-requests signal unset: KV usage means the same thing on every engine, but a sensible queue ceiling depends on the workload. |
| `--worker-overload-waiting-requests` | unset | Waiting requests, summed across DP ranks, at or above which a worker is overloaded. Integer `>= 1`. |
| `--worker-overload-token-usage` | unset (`0.9` with `--worker-overload-protection`) | Mean KV token usage at or above which a worker is overloaded. Fraction in `(0.0, 1.0]`. |

Either threshold on its own enables protection; `--worker-overload-protection` is not required. An explicit `--worker-overload-token-usage` overrides the `0.9` default. With all three unset (and no per-worker blocks), protection is off and routing is unchanged.

!!! note "Validation"
    Both comparisons are inclusive (`>=`), so the values that would veto every worker unconditionally are rejected at startup: `--worker-overload-waiting-requests 0`, and any `--worker-overload-token-usage` outside `(0.0, 1.0]`, including `0`. A token usage threshold of `1.0` is accepted and vetoes a worker only when its KV cache is full.

The Python launchers accept the same flags; under `smg serve` they take the `--router-` prefix (for example `--router-worker-overload-protection`). See the [Configuration Reference](../../reference/configuration.md#worker-overload-protection) for every flag.

### Per-Worker Thresholds

Fleets that mix GPU types or model sizes saturate at different points. Give a worker its own thresholds with an `overload` block in its worker spec when you register it through the [Admin API](../../reference/api/admin.md):

```bash
curl -X POST http://localhost:30000/workers \
  -H "Authorization: Bearer ${ADMIN_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
    "url": "http://gpu-small:8000",
    "overload": {
      "waiting_requests": 8,
      "token_usage": 0.85
    }
  }'
```

| Field | Type | Description |
|-------|------|-------------|
| `overload.waiting_requests` | integer | Overrides `--worker-overload-waiting-requests` for this worker. Must be `>= 1`. |
| `overload.token_usage` | number | Overrides `--worker-overload-token-usage` for this worker. Must be in `(0.0, 1.0]`. |

- **Per signal**: a field set in the block wins; an omitted field falls back to the gateway value.
- **Enables protection on its own**: a block turns protection on for its worker even when every gateway flag is unset.
- **Resolved at registration**: out-of-range values fail that worker's registration. The `POST` is still answered with `202 Accepted`, but the worker is not added.
- **Not patchable**: `PATCH /workers/{worker_id}` cannot change the block and keeps it as is. To change it, send the complete spec with `PUT /workers/{worker_id}`, which re-runs registration (data-parallel workers need `DELETE` and a new `POST` instead).
- **Visible**: `GET /workers` returns the block as part of each worker's spec.

Workers without a block, including those from `--worker-urls` and service discovery, use the gateway thresholds. External API workers (OpenAI and other providers) have no load feed, so gateway thresholds are not applied to them.

---

## Behavior

### Excluding a Worker

While a worker is overloaded:

- **Every routing policy skips it**, on the HTTP and gRPC routers, for regular workers and for each leg of a prefill/decode pair. Hash-based and sticky policies treat it like an unhealthy worker and route the key elsewhere while the veto lasts.
- **Requests already running on it are unaffected**. The veto only applies to selection.
- **It returns to routing** on the first poll whose report is under every threshold.

### Shedding Requests

When every worker the request could have been routed to (the pool selection drew from: same model, worker type, and transport) is overloaded, the gateway rejects the request at once instead of queueing it:

```text
HTTP/1.1 503 Service Unavailable
Content-Type: application/json
Retry-After: 10
X-SMG-Error-Code: worker_overload_protection_shed
```

```json
{
  "error": {
    "type": "Service Unavailable",
    "code": "worker_overload_protection_shed",
    "message": "All workers for model 'meta-llama/Llama-3.1-8B-Instruct' are overloaded",
    "param": null
  }
}
```

- **`Retry-After`** is the gateway's `--load-monitor-interval` in whole seconds (at least `1`): a veto cannot clear before the next poll. Clients should wait that long before retrying.
- **Not retried internally**: the gateway's retry layer never retries a shed, so the answer comes back immediately rather than after rounds of backoff against the same verdict.
- **Trustworthy code**: `X-SMG-Error-Code` is set only by the gateway and is stripped from responses forwarded from workers, so it always marks a decision this gateway made.
- **Dispatch-time re-check**: if the chosen worker is flagged between selection and dispatch, the request is shed with the message `Worker '<url>' for model '<model>' became overloaded before dispatch`. The gateway sheds instead of re-selecting; the flag only moves at poll cadence, so this window is rare.
- **Mixed causes are not a shed**: if some candidates are out for another reason (unhealthy, or circuit breaker open) and the rest are overloaded, the request gets the ordinary `503 no_available_workers`.

!!! note "Changed in v1.11.0"
    In v1.10.x an overload shed used the generic `503 no_available_workers` code. Since v1.11.0 sheds carry their own `worker_overload_protection_shed` code, so alerts and upstream proxies can tell a shed apart from an availability failure.

---

## Load Monitoring

The load monitor feeds overload protection, the load-aware routing policies (`cache_aware`, `power_of_two`, `least_load`), [`GET /loads`](../../reference/api/admin.md#get-loads), the `engine_load` field of `GET /workers`, and the `smg_engine_*` gauges. Since v1.10.0 it polls every worker group from registration onward, whether or not anything consumes the data. That was a behavior change: gateways running a load-blind policy, which never polled before, now poll every worker at `--load-monitor-interval`.

| Parameter | Default | Description |
|-----------|---------|-------------|
| `--load-monitor-interval` | `10` | Seconds between polls of each worker group. Must be `> 0`. Also the `Retry-After` value on overload sheds. |
| `--disable-load-monitoring` | off | Restores the conditional gate used before v1.10.0: a group is polled only when a load-aware policy (or `--dp-minimum-tokens-scheduler`), `--engine-metrics`, or overload protection on one of its workers needs the data. It never stops polling that routing or protection depends on; with the default `cache_aware` policy, groups are still polled. |
| `--engine-metrics` | off | Forces polling so the `smg_engine_*` gauges are populated when nothing else needs the data. Only matters together with `--disable-load-monitoring`: by default every successful poll is already exported. |

See [Load Monitoring Configuration](../../reference/configuration.md#load-monitoring-configuration) for the full reference.

---

## PD Decode Admission Window

In gRPC prefill/decode disaggregation, SGLang-lineage engines (SGLang, TokenSpeed) start a bootstrap deadline on the prefill leg as soon as a request lands, and it only clears once the decode engine has admitted the request. Decode admission is bounded by the engine's running window (`--max-running-requests` / `--max-num-seqs`), so a burst wider than that window lets prefill deadlines expire while requests wait in the decode queue.

The gateway therefore never has more disaggregated requests in flight to a decode worker than that worker's running window holds (counted per gateway instance):

- **Room in the window**: the request is dispatched immediately.
- **Window full**: the request waits in the gateway for up to `--pd-admission-wait-secs` (default `30`). If no room frees, it is shed with the same `503 worker_overload_protection_shed` response and `Retry-After` header.
- **Wider than the window**: a batched request that needs more rooms than the window holds is shed immediately.
- **`0`** sheds as soon as the window is full, without waiting.
- **No window reported**: workers whose engine reports no running window are never gated.

This gate is on by default and independent of the overload thresholds. Keep the wait well under the engine's bootstrap deadline (120 s on TokenSpeed) so a request that waits still dispatches with time to spare. `smg_pd_admission_waits_total` counts dispatches that waited and were admitted; `smg_pd_admission_sheds_total` counts sheds. See [PD Disaggregation](../routing/pd-disaggregation.md) for the rest of the PD request path.

---

## Monitoring

### Metrics

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `smg_workers_overloaded` | Gauge | `model` | Workers of the model currently excluded by overload protection. Updated when a worker's verdict changes. |
| `smg_worker_overload_shed_total` | Counter | `stage` | Requests shed with `worker_overload_protection_shed`. `stage` is `selection` (every candidate overloaded), `dispatch` (chosen worker flagged before dispatch), or `pd_admission` (decode window full). |
| `smg_pd_admission_waits_total` | Counter | none | gRPC PD dispatches that waited for decode room and were admitted. |
| `smg_pd_admission_sheds_total` | Counter | none | gRPC PD dispatches shed by the decode admission window. |
| `smg_engine_waiting_requests` | Gauge | `worker`, `model`, `dp_rank` | Engine-reported waiting requests per DP rank, from the load poll. |
| `smg_engine_token_usage` | Gauge | `worker`, `model`, `dp_rank` | Engine-reported KV token usage (0.0–1.0) per DP rank, from the load poll. |

The other `smg_engine_*` gauges (running requests, generation throughput, prefix cache hit rate, and the PD transfer gauges) come from the same poll. When a worker is removed, its `smg_engine_*` series are set to `-1`, because metric series cannot be deleted. The full list is in the [Metrics Reference](../../reference/metrics.md).

### Useful PromQL Queries

<div class="grid" markdown>

<div class="card" markdown>

#### Vetoes and Sheds

```promql
# Workers currently excluded, per model
smg_workers_overloaded

# Shed rate by stage
sum by (stage) (
  rate(smg_worker_overload_shed_total[5m])
)
```

</div>

<div class="card" markdown>

#### Headroom per Worker

```promql
# Mean KV usage per worker (the token usage signal)
avg by (worker, model) (smg_engine_token_usage >= 0)

# Waiting requests per worker (the queue signal)
sum by (worker, model) (smg_engine_waiting_requests >= 0)
```

</div>

</div>

### Alerting Example

```yaml
groups:
  - name: smg-overload-protection
    rules:
      - alert: WorkersOverloaded
        expr: smg_workers_overloaded > 0
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "{{ $value }} worker(s) for {{ $labels.model }} excluded by overload protection"

      - alert: OverloadShedding
        expr: sum(rate(smg_worker_overload_shed_total[5m])) > 0
        for: 2m
        labels:
          severity: critical
        annotations:
          summary: "SMG is shedding requests because every candidate worker is overloaded"
```

---

## Production Tuning

### Recommended Configurations

<div class="grid" markdown>

<div class="card" markdown>

#### :material-shield-check: KV Ceiling Only

The engine-agnostic default: exclude a worker at 90% KV usage.

```bash
smg \
  --worker-overload-protection
```

**Use when**: Turning protection on for the first time

</div>

<div class="card" markdown>

#### :material-tray-full: KV and Queue Ceilings

Also cap the waiting queue, sized from your own traffic.

```bash
smg \
  --worker-overload-protection \
  --worker-overload-waiting-requests 16
```

**Use when**: Latency-sensitive traffic, where a long queue is a failure even with KV room left

</div>

<div class="card" markdown>

#### :material-server-network: Mixed Fleet

Gateway defaults plus `overload` blocks on the workers that saturate earlier.

```bash
smg --worker-overload-protection
# then register small workers with
# "overload": {"waiting_requests": 8}
```

**Use when**: Workers differ in GPU memory or batch size

</div>

<div class="card" markdown>

#### :material-timer-outline: Faster Reaction

Poll more often, so vetoes set and clear sooner and `Retry-After` is shorter.

```bash
smg \
  --worker-overload-protection \
  --load-monitor-interval 5
```

**Use when**: Bursty traffic, and workers can take more frequent load polls

</div>

</div>

The values above are starting points, not recommendations for your hardware. Size the waiting-requests ceiling from `smg_engine_waiting_requests` at healthy peak load, and set it above that normal peak.

### Tuning Guidelines

| Symptom | Potential Adjustment |
|---------|---------------------|
| Requests shed while engines still have headroom | Raise the thresholds, or give larger workers their own `overload` block |
| Latency climbs but no worker is ever vetoed | Lower `--worker-overload-token-usage`, or add `--worker-overload-waiting-requests` |
| Bursts overshoot the ceiling before the veto lands | Lower `--load-monitor-interval`, or use `least_load` with `--least-load-max-waiting-requests`, which also counts requests dispatched since the last poll |
| Protection never engages on some workers | Check that they report load: `GET /loads` omits workers without a report (for example gRPC TRT-LLM and MLX), and a vLLM gRPC worker started with `--disable-log-stats` reports zeros |
| Cache-aware keeps piling onto a hot worker until it is vetoed | Set `--overload-token-usage-threshold` below `--worker-overload-token-usage`, so cache-aware spreads load before the veto fires |
| gRPC PD requests shed at stage `pd_admission` | Add decode capacity, or raise `--pd-admission-wait-secs` while keeping it under the engine's bootstrap deadline |

Overload protection sheds; it does not queue. Keep [rate limiting](rate-limiting.md) or the [priority scheduler](priority-scheduling.md) in front of it to bound what the gateway accepts, and make sure clients honor `Retry-After`.

---

## What's Next?

<div class="grid" markdown>

<div class="card" markdown>

### :material-tray-full: Rate Limiting

Gateway-side concurrency limits and queuing, applied before routing.

[Rate Limiting →](rate-limiting.md)

</div>

<div class="card" markdown>

### :material-priority-high: Priority Scheduling

Admit higher-priority traffic first when the gateway is at capacity.

[Priority Scheduling →](priority-scheduling.md)

</div>

<div class="card" markdown>

### :material-scale-balance: Load Balancing

The load-aware policies that read the same load reports.

[Load Balancing →](../routing/load-balancing.md)

</div>

<div class="card" markdown>

### :material-call-split: PD Disaggregation

Prefill/decode routing, where the decode admission window applies.

[PD Disaggregation →](../routing/pd-disaggregation.md)

</div>

<div class="card" markdown>

### :material-api: Admin API

Read the gateway's cached load snapshot with `GET /loads`.

[Get Loads →](../../reference/api/admin.md#get-loads)

</div>

<div class="card" markdown>

### :material-chart-box: Metrics Reference

Overload, PD admission, and engine load metrics.

[Metrics Reference →](../../reference/metrics.md)

</div>

</div>
