---
title: Configure Logging
---

# Configure Logging

Configure structured logging with multiple output formats and integrate with log aggregation systems.

<div class="prerequisites" markdown>

#### Before you begin

- SMG [installed](index.md#install)
- Completed the [Getting Started](index.md) guide
- Log aggregation system (optional): Elasticsearch, Loki, or similar

</div>

---

## Configuration Options

SMG supports flexible logging configuration via CLI flags or environment variables. The gateway writes its logs to stdout, and also to files when `--log-dir` is set. Timestamps are in UTC.

### CLI Flags

| Flag | Default | Description |
|------|---------|-------------|
| `--log-level` | `info` | Log level for SMG's own crates: `debug`, `info`, `warn`, `error` |
| `--log-json` | `false` | Output logs as JSON |
| `--log-dir` | None | Directory for daily log files (enables file logging) |
| `--log-mm-timing` | `false` | Log per-request multimodal timing at INFO (see [Multimodal Timing Logs](#multimodal-timing-logs)) |

### Environment Variable

Without `RUST_LOG`, SMG builds its log filter from `--log-level`: SMG's own crates log at that level and every dependency (hyper, tonic, and so on) at `warn`. Setting `RUST_LOG` replaces that filter entirely, and `--log-level` is then ignored. A target that matches no `RUST_LOG` directive is not logged at all, so start the value with a base level:

```bash
# Everything at debug, dependencies included
RUST_LOG=debug smg launch --worker-urls http://worker:8000

# Dependencies at warn, SMG at info, routing policies at debug
RUST_LOG=warn,smg=info,smg::policies=debug smg launch --worker-urls http://worker:8000
```

A directive matches every target that starts with it, so `smg` covers the gateway's `smg::...` targets and crates such as `smg_mesh` and `smg_rl`. Other SMG crates, such as `tool_parser`, `reasoning_parser`, `llm_tokenizer`, and `llm_multimodal`, log under their own names and follow the base level unless you name them.

---

## Log Levels

| Level | Description | Use Case |
|-------|-------------|----------|
| `error` | Error conditions only | Production (minimal) |
| `warn` | Warnings and errors | Production (recommended) |
| `info` | Informational messages, including a line for each request and response | Production (verbose) |
| `debug` | Debug information, including routing decisions | Development, troubleshooting |

`trace` is not accepted by `--log-level`; set it through `RUST_LOG`. With OpenTelemetry tracing enabled, keep SMG at `info` or more verbose: the request spans are INFO-level, and the same filter applies to them.

### Set Log Level

```bash
# Via flag
smg launch --worker-urls http://worker:8000 --log-level debug

# Via environment
RUST_LOG=info smg launch --worker-urls http://worker:8000
```

---

## Output Formats

### Plain Text (Default)

Human-readable lines: UTC timestamp, level, the enclosing request span with its fields, target, source location, and message. On stdout the lines carry ANSI color codes; files written with `--log-dir` do not.

```
2026-09-24 10:30:45  INFO http_request{method=POST uri=/v1/chat/completions version=HTTP/1.1 module="smg" request_id="chatcmpl-4qD9pXvR2mT7sLk0aZ3nB8wE" status_code=200 latency=1523}: smg::response: model_gateway/src/middleware/logging.rs:138: finished processing request
```

### JSON Format

Machine-readable format for log aggregation, one JSON object per line:

```bash
smg launch --worker-urls http://worker:8000 --log-json
```

Output (pretty-printed here):

```json
{
  "timestamp": "2026-09-24 10:30:45",
  "level": "INFO",
  "message": "finished processing request",
  "target": "smg::response",
  "filename": "model_gateway/src/middleware/logging.rs",
  "line_number": 138,
  "span": {
    "latency": 1523,
    "method": "POST",
    "module": "smg",
    "request_id": "chatcmpl-4qD9pXvR2mT7sLk0aZ3nB8wE",
    "status_code": 200,
    "uri": "/v1/chat/completions",
    "version": "HTTP/1.1",
    "name": "http_request"
  },
  "spans": [
    {
      "latency": 1523,
      "method": "POST",
      "module": "smg",
      "request_id": "chatcmpl-4qD9pXvR2mT7sLk0aZ3nB8wE",
      "status_code": 200,
      "uri": "/v1/chat/completions",
      "version": "HTTP/1.1",
      "name": "http_request"
    }
  ]
}
```

The event's own fields (`message` and any structured fields) sit at the top level. Fields of the enclosing spans are under `span` (the innermost span) and `spans` (all of them, outermost first), so the request ID is at `span.request_id`. `latency` is in microseconds.

---

## Request and Response Logs

Every request on the main listener is logged inside an `http_request` span that carries `method`, `uri`, `version`, and `request_id`, plus `status_code` and `latency` (microseconds) once the response is ready:

| Target | Level | Message | When |
|--------|-------|---------|------|
| `smg::request` | INFO | `started processing request` | A request arrives |
| `smg::response` | INFO | `finished processing request` | Any response below 400 |
| `smg::response` | WARN | `request failed with client error` | A 4xx response |
| `smg::response` | ERROR | `request failed with server error` | A 5xx response |
| `smg::response` | ERROR | `response stream failed after the head was sent` | The response body failed after streaming began |

The logging layer writes one ERROR line per 5xx response; releases before v1.10.0 also logged a duplicate `tower_http` failure line for it (smg-project/smg#2124).

### Health Probe Logs

Requests to `/health`, `/readiness`, and `/liveness` log both lines at DEBUG, whatever the status (smg-project/smg#2124). A `503` from `/readiness` while workers are still loading is an expected state, so polling probes no longer fill the log with ERROR lines. Probes served on the dedicated `--health-check-port` listener bypass the logging layer entirely.

### Other Structured Logs

| Target | Level | Message | When |
|--------|-------|---------|------|
| `smg::policies::*` | DEBUG | Routing decisions | See [Routing Decision Logs](#routing-decision-logs) |
| `smg::audit` | INFO | `control_plane_audit` | Control-plane requests, when control-plane authentication is configured (turn off with `--disable-audit-logging`) |
| `smg_rl` | INFO | `rl.proxy`, `rl.fanout` | RL control plane calls, with `--enable-rl` |

---

## Routing Decision Logs

At DEBUG, the routing policies log one structured line per selection decision, naming the branch taken and the worker chosen, so you can see why a request landed on a given worker (smg-project/smg#2210). The lines are off at the default `info` level. Turn them on for the policy targets only:

```bash
RUST_LOG=warn,smg=info,smg::policies=debug smg launch --worker-urls http://worker:8000 --policy cache_aware
```

| Message | Target | Fields |
|---------|--------|--------|
| `Cache-aware selection` | `smg::policies::cache_aware` | `index` (`tree` or `hash`), `branch`, `worker`, `model_id`; tree decisions add `matched_ratio` and `threshold`, hash decisions add `level` |
| `Sticky routing decision` | `smg::policies::registry` | `source` (`rid` or `header`), `key`, `branch`, `worker`, `model_id` |
| `Prefix-hash selection` | `smg::policies::prefix_hash` | `branch`, `level`, `worker`, `model_id` |

Branch values:

- **Cache-aware, tree index**: `tree_match`, `spill`, `expected_wait_fallback`, `first_healthy_fallback`, the same values as `smg_cache_aware_policy_branch_total`.
- **Cache-aware, hash index**: `hash_hit`, `hash_spill`, `short_request` (shorter than the smallest `--cache-boundaries` value), `expected_wait_fallback`, `kv_pressure_expected_wait`.
- **Cache-aware under KV-cache imbalance**: `kv_pressure_expected_wait`; this line has no `index` field.
- **Sticky sessions** (`--routing-key-override`): `occupied_hit`, `occupied_miss`, `vacant`, `cap_respill`, `no_healthy_workers`. `key` is the internal map key, which combines the model, the PD leg, and the routing key.
- **Prefix hash**: `ring_hit`, `load_balance_walk`, `fallback_least_load`, `no_routing_key`, `no_healthy_workers`.

When cache-aware routing uses KV events, it logs `Event-driven routing: overlap match` (`branch` is `event_hit` or `event_spill`) or `Event-driven routing: no overlap, expected-wait fallback`, each with `worker` and `model_id`.

See the [Metrics Reference](../reference/metrics.md#routing-policy-metrics) for the matching decision counters.

---

## Multimodal Timing Logs

`--log-mm-timing` logs per-request multimodal timing at INFO (smg-project/smg#2625). Each line's message starts with `smg_mm_timing`, followed by the stage:

| Message | Contents |
|---------|----------|
| `smg_mm_timing process_multimodal_plan` | Media counts (`image_count`, `audio_count`, `video_count`, `video_frame_count`), per-phase times (`media_fetch_decode_ms`, `config_lookup_ms`, `preprocess_ms`, `token_expand_ms`, `total_ms`), and token counts before and after placeholder expansion (`original_tokens`, `expanded_tokens`) |
| `smg_mm_timing mm_tensor_payload_inline`, `smg_mm_timing mm_shm_write` | Tensor transport: inline sends and shared-memory writes |
| `smg_mm_timing assemble_tokenspeed`, `smg_mm_timing assemble_tokenspeed_item`, `smg_mm_timing tokenspeed_shm_write_direct` | TokenSpeed request assembly |
| `smg_mm_timing video_decode_backend`, `smg_mm_timing video_tempfile_write` | Video decoding (target `llm_multimodal`) |

```bash
smg launch --worker-urls grpc://worker:50051 --model-path Qwen/Qwen2.5-VL-7B-Instruct --log-mm-timing
```

The older `SMG_LOG_MM_TIMING` environment variable (`1`, `true`, `yes`, or `on`) still turns the logs on when the flag is absent, but it is deprecated: SMG logs a warning, and support ends in the next minor release. v1.11.0 also stopped logging where each request's media is processed (smg-project/smg#2594); that is counted by `smg_mm_processing_total` instead. See [Multimodal](../concepts/architecture/multimodal.md) for the processing pipeline.

---

## File Logging

Enable persistent log files with automatic daily rotation.

### Enable File Logging

```bash
smg launch \
  --worker-urls http://worker:8000 \
  --log-dir /var/log/smg \
  --log-level info
```

### Features

- **Same content as stdout**: plain text or JSON (with `--log-json`), without ANSI colors
- **Daily rotation**: a new file starts at midnight UTC
- **File naming**: `smg.YYYY-MM-DD`, with no extension
- **No deletion**: old files are kept; see [Log Rotation](#log-rotation)
- **Non-blocking I/O**: a background thread writes the files; if it falls more than 128,000 lines behind, new file lines are dropped rather than slowing requests
- **Directory creation**: SMG creates the directory if it does not exist

### Example Directory Structure

```
/var/log/smg/
├── smg.2026-09-22
├── smg.2026-09-23
└── smg.2026-09-24
```

---

## Docker Logging

### Basic Configuration

```yaml title="docker-compose.yml"
services:
  smg:
    image: lightseekorg/smg:latest
    command: >
      --worker-urls http://worker:8000
      --log-json
    logging:
      driver: json-file
      options:
        max-size: "100m"
        max-file: "3"
```

### Production Configuration

```yaml title="docker-compose.yml"
services:
  smg:
    image: lightseekorg/smg:latest
    command: >
      --worker-urls http://worker:8000
      --log-level info
      --log-json
      --log-dir /var/log/smg
    volumes:
      - smg-logs:/var/log/smg
    logging:
      driver: json-file
      options:
        max-size: "50m"
        max-file: "5"

volumes:
  smg-logs:
```

---

## Kubernetes Logging

### Basic Pod Logging

```bash
# Follow logs
kubectl logs -n inference -l app=smg -f

# Previous container logs
kubectl logs -n inference -l app=smg --previous

# All containers in pod
kubectl logs -n inference <pod-name> --all-containers
```

### Deployment Configuration

```yaml title="smg-deployment.yaml"
apiVersion: apps/v1
kind: Deployment
metadata:
  name: smg
spec:
  template:
    spec:
      containers:
        - name: smg
          args:
            - --worker-urls=http://worker:8000
            - --log-level=info
            - --log-json
```

To turn on debug output for one area only, set `RUST_LOG` on the container (for example `warn,smg=info,smg::policies=debug`); it replaces the `--log-level` filter.

---

## Log Aggregation

### Grafana Loki

```yaml title="promtail-config.yaml"
server:
  http_listen_port: 9080

clients:
  - url: http://loki:3100/loki/api/v1/push

scrape_configs:
  - job_name: smg
    kubernetes_sd_configs:
      - role: pod
    relabel_configs:
      - source_labels: [__meta_kubernetes_pod_label_app]
        regex: smg
        action: keep
      - source_labels: [__meta_kubernetes_pod_label_app]
        target_label: app
      - source_labels: [__meta_kubernetes_namespace]
        target_label: namespace
      - source_labels: [__meta_kubernetes_pod_name]
        target_label: pod
    pipeline_stages:
      - json:
          expressions:
            level: level
            target: target
            request_id: span.request_id
      - labels:
          level:
          target:
```

### Elasticsearch + Fluentd

```yaml title="fluentd-config.yaml"
<source>
  @type tail
  path /var/log/containers/smg*.log
  pos_file /var/log/fluentd-smg.pos
  tag smg.*
  <parse>
    @type json
  </parse>
</source>

<filter smg.**>
  @type parser
  key_name log
  <parse>
    @type json
    time_key timestamp
    time_format %Y-%m-%d %H:%M:%S
    utc true
  </parse>
</filter>

<match smg.**>
  @type elasticsearch
  host elasticsearch
  port 9200
  index_name smg-logs
  include_timestamp true
</match>
```

SMG's `timestamp` has second precision and no time-zone suffix; it is always UTC. The filter above parses it as the event time, and `include_timestamp` stores that time as `@timestamp`.

---

## Log Queries

### Loki/LogQL

LogQL's `json` parser flattens nested fields with `_`, so the request ID is `span_request_id`.

```logql
# All SMG logs
{app="smg"}

# Error logs only
{app="smg"} | json | level="ERROR"

# Specific request
{app="smg"} | json | span_request_id="chatcmpl-4qD9pXvR2mT7sLk0aZ3nB8wE"

# Server errors returned to clients
{app="smg"} | json | target="smg::response" | level="ERROR"

# Cache-aware decisions that spilled away from the prefix holder (needs smg::policies=debug)
{app="smg"} | json | message="Cache-aware selection" | branch="spill"

# Multimodal timing (needs --log-mm-timing)
{app="smg"} |= "smg_mm_timing"
```

### Elasticsearch/Kibana

```json
{
  "query": {
    "bool": {
      "must": [
        { "match": { "target": "smg::response" } },
        { "match": { "level": "ERROR" } }
      ],
      "filter": [
        { "range": { "@timestamp": { "gte": "now-1h" } } }
      ]
    }
  }
}
```

---

## OpenTelemetry Integration

When OpenTelemetry is enabled, the gateway's request events are logged at a higher level:

| OTEL Enabled | Event Level | Behavior |
|--------------|-------------|----------|
| Yes | INFO | Exported to OTLP collector |
| No | DEBUG | Not exported (local only) |

These are the HTTP routers' `Sending request`, `Sending concurrent requests`, and `Received concurrent requests` events, which are attached to the request's trace. Other log lines are not sent to the collector; only the request spans and these events are exported. See [OpenTelemetry Tracing](monitoring.md#opentelemetry-tracing).

```bash
# Enable OTEL with logging
smg launch \
  --worker-urls http://worker:8000 \
  --enable-trace \
  --otlp-traces-endpoint localhost:4317 \
  --log-level info
```

---

## Request Correlation

### Request ID Propagation

Every request gets an ID. SMG takes it from the first of these request headers that is present: `x-request-id`, `x-correlation-id`, `x-trace-id`, `request-id` (replace the list with `--request-id-headers`). Otherwise it generates one with an OpenAI-style prefix (`chatcmpl-`, `cmpl-`, `gnt-`, `resp-`, `msg_`, or `req-`) followed by 24 random letters and digits. The ID is returned in the `x-request-id` response header and recorded as `request_id` on the request's `http_request` span, so it appears on the lines logged while the request is handled.

```bash
# Send request with custom ID
curl -H "X-Request-ID: my-trace-123" \
  http://localhost:30000/v1/chat/completions \
  -d '{"model": "llama", "messages": [{"role": "user", "content": "Hi"}]}'
```

Backend (engine) request ids derive from the same id, so gateway and worker
logs correlate: gRPC requests are dispatched as `{request-id}-{uuid}` (the
suffix keeps retries and tool-loop iterations unique), and the response body
`id` matches. A protocol-level `rid` field (chat, completions, messages,
generate, embeddings, classify) overrides this and is used verbatim outside
PD mode.

### Trace a Request

```bash
# Find all logs for a request
kubectl logs -n inference -l app=smg | grep "my-trace-123"

# In Loki
{app="smg"} | json | span_request_id="my-trace-123"
```

---

## Log Rotation

### File Retention

SMG starts a new file at midnight UTC but never deletes old ones. Prune them on a schedule, for example from cron:

```bash
# Delete SMG log files older than 7 days
find /var/log/smg -name 'smg.*' -mtime +7 -delete
```

### Docker

```yaml
logging:
  driver: json-file
  options:
    max-size: "100m"
    max-file: "5"
```

---

## Verification

```bash
# Check log output
smg launch --worker-urls http://worker:8000 --log-level debug | head -20

# Verify JSON format
smg launch --worker-urls http://worker:8000 --log-json | jq .

# Test log level filtering
smg launch --worker-urls http://worker:8000 --log-level warn | grep -c INFO
# Should output: 0

# Check file logging
smg launch --worker-urls http://worker:8000 --log-dir /tmp/smg-logs &
ls -la /tmp/smg-logs/
```

---

## Troubleshooting

??? question "No logs appearing"

    1. Check log level is not too restrictive:
    ```bash
    smg launch --log-level debug ...
    ```

    2. The gateway logs to stdout, not stderr (the Python launchers may print their own messages to stderr):
    ```bash
    smg launch --worker-urls http://worker:8000 | head
    ```

    3. Check RUST_LOG isn't overriding:
    ```bash
    unset RUST_LOG
    smg launch --log-level info ...
    ```

    4. If `--log-dir` names a directory SMG cannot create, SMG prints `Failed to create log directory: <error>` to stderr and runs with logging disabled, stdout included.

??? question "Logs not in JSON format"

    1. Ensure `--log-json` flag is set:
    ```bash
    smg launch --log-json --worker-urls http://worker:8000
    ```

    2. Lines printed before logging is set up, such as `Failed to create log directory`, go to stderr as plain text; read stdout for the JSON logs

??? question "File logs not appearing"

    1. Check directory exists and is writable:
    ```bash
    mkdir -p /var/log/smg
    chmod 755 /var/log/smg
    ```

    2. Verify `--log-dir` flag is set correctly

    3. Files are named `smg.YYYY-MM-DD` with no `.log` extension, so a `*.log` glob does not match them

??? question "Log aggregator not receiving logs"

    1. Verify JSON format is enabled
    2. Check network connectivity to aggregator
    3. Verify log format matches parser expectations: span fields such as `request_id` are nested under `span`, and `timestamp` uses `%Y-%m-%d %H:%M:%S` in UTC

---

## What's Next?

- [Monitoring](monitoring.md) — Metrics, tracing, and alerting
- [Configuration Reference](../reference/configuration.md) — Full CLI options
