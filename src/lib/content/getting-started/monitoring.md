---
title: Monitoring
---

# Monitoring

Set up Prometheus monitoring, OpenTelemetry tracing, and Grafana dashboards for SMG.

<div class="prerequisites" markdown>

#### Before you begin

- Completed the [Getting Started](index.md) guide
- Prometheus server (or follow steps below to deploy)
- Grafana (optional, for dashboards)
- OTLP collector (optional, for distributed tracing)

</div>

---

## Enable Metrics

SMG always serves Prometheus metrics on a dedicated port, `29000` by default. The flags below set the listener explicitly.

### Start SMG with metrics

```bash
smg launch \
  --worker-urls http://worker:8000 \
  --prometheus-port 29000 \
  --prometheus-host 0.0.0.0
```

At startup SMG logs `Metrics server listening on <address> (/metrics)`. The port must be greater than `0`: `--prometheus-port 0` is rejected at startup even though `--help` describes it as an OS-assigned port. If the port is already in use, SMG fails at startup with `failed to bind metrics server on <address>`.

### Verify metrics endpoint

```bash
curl http://localhost:29000/metrics
```

You should see Prometheus-formatted metrics:

```
# HELP smg_http_requests_total Total HTTP requests by method and path
# TYPE smg_http_requests_total counter
smg_http_requests_total{method="POST",path="/v1/chat/completions"} 1234
...
```

The engines' own metrics are available separately at `/engine_metrics` on the main port; see [Engine Metrics Passthrough](../reference/metrics.md#engine-metrics-passthrough).

---

## OpenTelemetry Tracing

SMG supports distributed tracing via OpenTelemetry.

### Enable tracing

```bash
smg launch \
  --worker-urls http://worker:8000 \
  --enable-trace \
  --otlp-traces-endpoint localhost:4317
```

### Configuration

| Flag | Default | Description |
|------|---------|-------------|
| `--enable-trace` | `false` | Enable OpenTelemetry tracing |
| `--otlp-traces-endpoint` | `localhost:4317` | OTLP gRPC collector endpoint, as `host:port` (`http://` is added when no scheme is given) |

Spans are batched and exported over OTLP gRPC with the service name `smg`. The gateway exports its own request spans: `http_request` for each HTTP request (method, URI, request ID, status code, and latency in microseconds) and `grpc_execute` for each dispatch through the gRPC pipeline (request type, request ID, model, and mode).

### Trace propagation

With `--enable-trace`, SMG uses W3C TraceContext headers:

- `traceparent` and `tracestate` on an incoming client request make the gateway's spans part of the caller's trace.
- Requests the HTTP routers (regular and PD) send to workers carry the current `traceparent` and `tracestate`. Requests to gRPC workers do not carry trace context in v1.11.0.

Without `--enable-trace`, SMG neither reads nor adds these headers.

---

## Prometheus Configuration

### Basic configuration

```yaml title="prometheus.yml"
global:
  scrape_interval: 15s
  evaluation_interval: 15s

scrape_configs:
  - job_name: 'smg'
    static_configs:
      - targets: ['localhost:29000']
    metrics_path: /metrics
```

To also collect the engines' metrics through the gateway, add a job for `/engine_metrics` on the main port. Every sample carries a `worker_addr` label:

```yaml title="prometheus.yml"
  - job_name: 'smg-engines'
    static_configs:
      - targets: ['localhost:30000']
    metrics_path: /engine_metrics
```

### Kubernetes ServiceMonitor

For Prometheus Operator:

```yaml title="smg-servicemonitor.yaml"
apiVersion: monitoring.coreos.com/v1
kind: ServiceMonitor
metadata:
  name: smg
  namespace: inference
  labels:
    app: smg
spec:
  selector:
    matchLabels:
      app: smg
  endpoints:
    - port: metrics
      interval: 15s
      path: /metrics
  namespaceSelector:
    matchNames:
      - inference
```

If you deploy with the SMG Helm chart, set `router.metrics.serviceMonitor.enabled=true` and the chart creates a ServiceMonitor for the router's `metrics` port.

---

## Key Metrics by Layer

### Layer 0: Runtime Metrics

| Metric | Type | Description |
|--------|------|-------------|
| `smg_tokio_event_loop_stalls_total` | Counter | Event-loop stalls (async runtime blocked for more than 5 ms) |
| `smg_tokio_worker_busy_ratio` | Gauge | Busy fraction of each runtime worker thread |
| `smg_allocator_allocated_bytes` | Gauge | Bytes in live allocations (jemalloc) |
| `smg_allocator_resident_bytes` | Gauge | Resident bytes of the jemalloc heap |

### Layer 1: HTTP Metrics

| Metric | Type | Description |
|--------|------|-------------|
| `smg_http_requests_total` | Counter | Requests by method, path |
| `smg_http_request_duration_seconds` | Histogram | Request latency |
| `smg_http_responses_total` | Counter | Responses by path, status_code, error_code |
| `smg_http_connections_active` | Gauge | Requests in flight |
| `smg_http_rate_limit_total` | Counter | Concurrency-limit decisions |
| `smg_admission_queue_depth` | Gauge | Requests waiting for an admission token |
| `smg_admission_queue_rejected_total` | Counter | Admission rejections by reason |

### Layer 2: Router Metrics

| Metric | Type | Description |
|--------|------|-------------|
| `smg_router_requests_total` | Counter | Requests by router_type, model, endpoint |
| `smg_router_ttft_seconds` | Histogram | Time to first token (gRPC streaming) |
| `smg_router_tpot_seconds` | Histogram | Time per output token (gRPC streaming) |
| `smg_router_tokens_total` | Counter | Tokens by type (input/output) |
| `smg_pd_ttft_seconds` | Histogram | End-to-end time to first token of PD requests |
| `smg_pd_admission_sheds_total` | Counter | PD dispatches shed because the decode engine had no room |
| `smg_tokenizer_cache_lookups_total` | Counter | Tokenizer cache lookups by layer, result |

### Layer 3: Worker Metrics

| Metric | Type | Description |
|--------|------|-------------|
| `smg_worker_health` | Gauge | Health status (1=Ready, 0=not Ready, -1=removed) |
| `smg_worker_requests_active` | Gauge | Active requests per worker |
| `smg_worker_cb_state` | Gauge | Circuit breaker state |
| `smg_worker_retries_total` | Counter | Retry attempts |
| `smg_workers_overloaded` | Gauge | Workers excluded as overloaded, per model |
| `smg_worker_overload_shed_total` | Counter | Requests shed with `worker_overload_protection_shed`, by stage |
| `smg_engine_token_usage` | Gauge | Engine-reported KV-cache usage per worker |

### Routing Policy Metrics

| Metric | Type | Description |
|--------|------|-------------|
| `smg_cache_tree_tokens` | Gauge | Cache-aware token-tree size per model (gRPC) |
| `smg_cache_tree_chars` | Gauge | Cache-aware string-tree size per model (HTTP) |
| `smg_cache_aware_policy_branch_total` | Counter | Cache-aware routing decisions by outcome |

### Layer 5: MCP Metrics

| Metric | Type | Description |
|--------|------|-------------|
| `smg_mcp_tool_calls_total` | Counter | Tool invocations by tool_name, result |
| `smg_mcp_tool_duration_seconds` | Histogram | Tool execution time |
| `smg_mcp_servers_active` | Gauge | Connected MCP servers |

Several of these exist only when their feature is on: admission metrics need `--max-concurrent-requests`, overload metrics need worker overload protection, and PD metrics need PD mode.

[View all metrics →](../reference/metrics.md)

---

## Grafana Dashboards

### Essential panels

**Request Rate**
```promql
sum(rate(smg_http_requests_total[5m]))
```

**P99 Latency**
```promql
histogram_quantile(0.99, sum by (le) (rate(smg_http_request_duration_seconds_bucket[5m])))
```

**Error Rate**
```promql
sum(rate(smg_http_responses_total{status_code=~"5.."}[5m]))
/ sum(rate(smg_http_responses_total[5m]))
```

**`/v1/responses` Success Rate**

```promql
sum(rate(smg_http_responses_total{path="/v1/responses",status_code=~"2.."}[5m]))
/ sum(rate(smg_http_responses_total{path="/v1/responses"}[5m]))
```

**Time to First Token (TTFT)**
```promql
histogram_quantile(0.5, sum by (le) (rate(smg_router_ttft_seconds_bucket[5m])))
```

**Tokens per Second**
```promql
sum(rate(smg_router_tokens_total[5m]))
```

**Healthy Workers**
```promql
count(smg_worker_health == 1)
```

### Capacity and overload panels

**Admission Queue** (depth and rejections by reason)
```promql
smg_admission_queue_depth
sum by (reason) (rate(smg_admission_queue_rejected_total[5m]))
```

**Overload Shedding** (sheds by stage and vetoed workers per model)
```promql
sum by (stage) (rate(smg_worker_overload_shed_total[5m]))
smg_workers_overloaded
```

**PD Admission** (dispatches that waited for, or were shed by, the decode engine's running window)
```promql
rate(smg_pd_admission_waits_total[5m])
rate(smg_pd_admission_sheds_total[5m])
```

**Allocator Memory** (live allocations vs. resident heap)
```promql
smg_allocator_allocated_bytes
smg_allocator_resident_bytes
```

**Cache Tree Size** (per model)
```promql
smg_cache_tree_tokens
smg_cache_tree_chars
```

**Tokenizer Cache Hit Rate** (per layer)
```promql
sum by (layer) (rate(smg_tokenizer_cache_lookups_total{result="hit"}[5m]))
/ sum by (layer) (rate(smg_tokenizer_cache_lookups_total[5m]))
```

**Event-Loop Stalls**
```promql
rate(smg_tokio_event_loop_stalls_total[5m])
```

---

## Alerting Rules

```yaml title="smg-alerts.yaml"
groups:
  - name: smg
    rules:
      - alert: SMGHighErrorRate
        expr: |
          sum(rate(smg_http_responses_total{status_code=~"5.."}[5m]))
          / sum(rate(smg_http_responses_total[5m])) > 0.05
        for: 5m
        labels:
          severity: critical
        annotations:
          summary: "High error rate on SMG"
          description: "Error rate is {{ $value | humanizePercentage }}"

      - alert: SMGWorkerUnhealthy
        expr: smg_worker_health == 0
        for: 1m
        labels:
          severity: warning
        annotations:
          summary: "SMG worker unhealthy"
          description: "Worker {{ $labels.worker }} is unhealthy"

      - alert: SMGHighLatency
        expr: |
          histogram_quantile(0.99, sum by (le) (rate(smg_http_request_duration_seconds_bucket[5m]))) > 5
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "High latency on SMG"
          description: "P99 latency is {{ $value }}s"

      - alert: SMGCircuitBreakerOpen
        expr: smg_worker_cb_state == 1
        for: 1m
        labels:
          severity: critical
        annotations:
          summary: "Circuit breaker open"
          description: "Circuit breaker for {{ $labels.worker }} is open"

      - alert: SMGHighTTFT
        expr: |
          histogram_quantile(0.95, sum by (le) (rate(smg_router_ttft_seconds_bucket[5m]))) > 2
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "High time to first token"
          description: "P95 TTFT is {{ $value }}s"

      - alert: SMGRateLimitRejections
        expr: sum(rate(smg_http_rate_limit_total{result="rejected"}[5m])) > 10
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "High rate limit rejections"
          description: "{{ $value }} rejections/sec"

      - alert: SMGAdmissionQueueBacklog
        expr: min_over_time(smg_admission_queue_depth[10m]) > 0
        labels:
          severity: warning
        annotations:
          summary: "Admission queue has not drained for 10 minutes"
          description: "At least {{ $value }} requests waiting for an admission token"

      - alert: SMGOverloadShedding
        expr: sum by (stage) (rate(smg_worker_overload_shed_total[5m])) > 0
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "SMG is shedding requests at stage {{ $labels.stage }}"
          description: "{{ $value }} requests/sec answered with 503 worker_overload_protection_shed"

      - alert: SMGEventLoopStalls
        expr: rate(smg_tokio_event_loop_stalls_total[5m]) > 1
        for: 10m
        labels:
          severity: warning
        annotations:
          summary: "SMG async runtime is stalling"
          description: "{{ $value }} event-loop stalls/sec"
```

`SMGOverloadShedding` also covers PD admission, whose sheds are counted with `stage="pd_admission"`. Tune the thresholds to your traffic.

---

## Useful Queries

### Request analysis

```promql
# Request rate by endpoint
sum by (path) (rate(smg_http_requests_total[5m]))

# Success rate
sum(rate(smg_http_responses_total{status_code="200"}[5m]))
/ sum(rate(smg_http_responses_total[5m]))

# Latency percentiles
histogram_quantile(0.50, sum by (le) (rate(smg_http_request_duration_seconds_bucket[5m])))
histogram_quantile(0.95, sum by (le) (rate(smg_http_request_duration_seconds_bucket[5m])))
histogram_quantile(0.99, sum by (le) (rate(smg_http_request_duration_seconds_bucket[5m])))

# Gateway-generated errors (sheds, rejections) by code
sum by (error_code) (rate(smg_http_responses_total{error_code!=""}[5m]))
```

### LLM performance

```promql
# Tokens per second by model
sum by (model) (rate(smg_router_tokens_total[5m]))

# TTFT by model
histogram_quantile(0.5, sum by (model, le) (rate(smg_router_ttft_seconds_bucket[5m])))

# Input/output token ratio
sum(rate(smg_router_tokens_total{token_type="output"}[5m]))
/ sum(rate(smg_router_tokens_total{token_type="input"}[5m]))
```

### Worker analysis

```promql
# Load distribution
smg_worker_requests_active / ignoring(worker) group_left sum(smg_worker_requests_active)

# Unhealthy workers
count(smg_worker_health == 0)

# Circuit breaker states
count by (worker) (smg_worker_cb_state == 1)

# Engine KV-cache usage per worker (removed workers report -1)
max by (worker) (smg_engine_token_usage >= 0)
```

### MCP tool analysis

```promql
# Tool success rate
sum(rate(smg_mcp_tool_calls_total{result="success"}[5m]))
/ sum(rate(smg_mcp_tool_calls_total[5m]))

# Most used tools
topk(10, sum by (tool_name) (rate(smg_mcp_tool_calls_total[5m])))

# Slowest tools
topk(5, histogram_quantile(0.95, sum by (tool_name, le) (rate(smg_mcp_tool_duration_seconds_bucket[5m]))))
```

---

## Verification

```bash
# Check metrics are being scraped
curl -s http://prometheus:9090/api/v1/targets | jq '.data.activeTargets[] | select(.labels.job=="smg")'

# Query a metric
curl -s 'http://prometheus:9090/api/v1/query?query=smg_http_requests_total' | jq

# Check alerts
curl -s http://prometheus:9090/api/v1/alerts | jq

# Engine load the gateway is routing on (cached snapshot, fleet totals)
curl -s http://localhost:30000/loads | jq '.aggregate'
```

---

## Troubleshooting

??? question "Metrics endpoint not responding"

    1. Check the startup log for `Metrics server listening on <address> (/metrics)`, which shows the address actually bound. A `failed to bind metrics server` error means the port is already in use.

    2. Check the port is listening:
    ```bash
    netstat -tlnp | grep 29000
    ```

    3. Check firewall rules allow access

??? question "Traces not appearing"

    1. Verify the OTLP endpoint is reachable from the gateway:
    ```bash
    nc -zv localhost 4317
    ```

    2. Check SMG was started with `--enable-trace`

    3. Verify the collector is receiving spans. Requests to gRPC workers do not carry trace context, so engine-side spans join the trace only for HTTP workers.

    4. Keep SMG's log level at `info` or more verbose. The log filter also applies to spans, and the request spans are INFO-level, so `--log-level warn` (or a `RUST_LOG` that drops INFO for `smg`) filters them out before they are exported.

??? question "Missing metrics"

    1. A series appears only after the first event that records it, so a metric can be absent on an idle gateway.

    2. Some groups need their feature enabled: admission metrics need `--max-concurrent-requests`, overload metrics need worker overload protection, and PD, discovery, RL, and mesh metrics need their modes. Allocator metrics are absent on musl and MSVC builds.

    3. Some metrics only appear for specific paths (for example, TTFT is recorded for streaming responses on the gRPC router).

    4. Verify metric names against the [Metrics Reference](../reference/metrics.md). Names such as `smg_router_stage_duration_seconds` and the `smg_db_*` metrics are declared in code but never emitted.

---

## What's Next?

- [Configure Logging](logging.md) — Structured log aggregation
- [Configure TLS](tls.md) — Secure client-to-gateway traffic
- [Metrics Reference](../reference/metrics.md) — Complete metrics documentation
