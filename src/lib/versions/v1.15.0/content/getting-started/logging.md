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
| `--log-dir` | None | Directory for daily log files (enables file logging); created at start-up when missing — an existing path must be a writable directory (see [File Logging](#file-logging)) |
| `--log-mm-timing` | `false` | Log per-request multimodal timing at INFO (see [Multimodal Timing Logs](#multimodal-timing-logs)) |

### Environment Variable

Without `RUST_LOG`, SMG builds its log filter from `--log-level`: SMG's own crates log at that level and every dependency (hyper, tonic, and so on) at `warn`. Setting `RUST_LOG` replaces that filter entirely, and `--log-level` is then ignored; a `RUST_LOG` value that fails to parse is itself ignored, and `--log-level` applies. A target that matches no `RUST_LOG` directive is not logged at all, so start the value with a base level:

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

`trace` is not accepted by `--log-level`; set it through `RUST_LOG`.

The log level does not gate [OpenTelemetry tracing](monitoring.md#opentelemetry-tracing): with `--enable-trace`, the trace layer has its own filter, so the request spans are exported at every log level — `warn` and `error` included — and under any `RUST_LOG`. The log output still follows the log filter, so at `warn` and `error` the INFO-level request span does not appear in the logs, and log lines carry no span fields such as `request_id`.

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

Human-readable lines: UTC timestamp, level, the enclosing request span with its fields, target, source location, and message. On stdout the lines carry ANSI color codes when stdout is a terminal, not when it is redirected into a file or a pipe (`NO_COLOR` set to a non-empty value turns color off on a terminal too; `FORCE_COLOR` or `CLICOLOR_FORCE` set to a non-empty value other than `0` turns it on into a pipe); files written with `--log-dir` never do.

```
2026-09-24 10:30:45  INFO http_request{method=POST path=/v1/chat/completions version=HTTP/1.1 module="smg" request_id="chatcmpl-4qD9pXvR2mT7sLk0aZ3nB8wE" status_code=200 latency=1523}: smg::response: model_gateway/src/middleware/logging.rs:465: finished processing request
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
  "line_number": 465,
  "span": {
    "latency": 1523,
    "method": "POST",
    "module": "smg",
    "path": "/v1/chat/completions",
    "request_id": "chatcmpl-4qD9pXvR2mT7sLk0aZ3nB8wE",
    "status_code": 200,
    "version": "HTTP/1.1",
    "name": "http_request"
  },
  "spans": [
    {
      "latency": 1523,
      "method": "POST",
      "module": "smg",
      "path": "/v1/chat/completions",
      "request_id": "chatcmpl-4qD9pXvR2mT7sLk0aZ3nB8wE",
      "status_code": 200,
      "version": "HTTP/1.1",
      "name": "http_request"
    }
  ]
}
```

The event's own fields (`message` and any structured fields) sit at the top level. Fields of the enclosing spans are under `span` (the innermost span) and `spans` (all of them, outermost first), so the request ID is at `span.request_id`. `latency` is in microseconds.

---

## Request and Response Logs

Every request to the main listener is logged inside an `http_request` span that carries `method`, `path`, `version`, `request_id` and, when tracing is on, `trace_id`, plus `status_code` and `latency` (microseconds) once the response is ready. That is the whole access record: no headers, no bodies, no prompts, and the path without its query string (a credential a client puts in a query parameter does not reach the log). `--access-log-fields` adds named optional fields to the record (`client_addr`, `forwarded_for`, `user_agent`, `tenant`, `key_id`, `request_bytes`, `response_bytes`, `worker`); see the [Configuration Reference](../reference/configuration.md#logging-configuration). When a Symphony table reads the response's output and the reading is not clean, the span also records [`symphony.*` fields](#symphony-outcome-fields).

| Target | Level | Message | When |
|--------|-------|---------|------|
| `smg::request` | INFO | `started processing request` | A request arrives |
| `smg::response` | INFO | `finished processing request` | Any response below 400 |
| `smg::response` | INFO | `finished streaming the response` | A streamed response's body ended; written only when `response_bytes` is among `--access-log-fields`, and records the bytes streamed, known only at the stream's end |
| `smg::response` | WARN | `request failed with client error` | A 4xx response |
| `smg::response` | ERROR | `request failed with server error` | A 5xx response |
| `smg::response` | ERROR | `response stream failed after the head was sent` | The response body failed after streaming began |
| `smg::response` | INFO | `client closed the connection before the response` | The client disconnected before the gateway answered; logged with `status_code` 499 |
| `smg::response` | INFO | `client closed the connection before the response stream ended` | The client disconnected while the response body was still streaming |
| `smg::response` | DEBUG | `request refused; reported by the refusing router` | A refusal the routing layer already logged with its own WARN line |

The logging layer writes one ERROR line per 5xx response. A request to an unknown path is logged like any other: it is answered with a 404 JSON error envelope (code `unknown_url`; a known path with the wrong method gets a 405, `method_not_allowed`) and its response line is the 4xx WARN.

A client that disconnects first — an SDK timeout, a user who navigated away, a proxy cutting an idle stream — is a normal outcome, not a gateway failure, so its one line is INFO. A disconnect before the response takes the place of the response line and records `status_code` 499 and the latency so far; a disconnect mid-stream follows the response line already written when the response head went out. The same lines cover the rare request the server itself dropped unanswered, such as on a handler panic or a connection cut by a forced shutdown. All of these count in `smg_http_responses_total` as a `499` with `error_code="client_closed_request"`; see the [Metrics Reference](../reference/metrics.md#layer-1-http-metrics).

A refusal the gateway answers on its own — for example a 503 `no_available_workers` or a 404 `model_not_found` — is logged by the routing layer as one WARN line with the request ID, the model, and the state of its worker pools, rate-limited while the condition lasts. The response line for such a refusal drops to DEBUG, so a client retrying against a model whose workers are all down does not fill the log with extra error lines per attempt.

### Symphony Outcome Fields

When a [Symphony table](../concepts/architecture/grpc-pipeline.md#symphony-parsing) reads a response's output and the reading is not clean, the `http_request` span records the reading as `symphony.*` fields. A reading of a gRPC response that panics in the gateway is recorded the same way, as a `panicked` reading, whichever parser was reading — the tool and reasoning parsers included, not only a Symphony table:

| Field | Value |
|-------|-------|
| `symphony.table` | The table that read the output; `none` for a `panicked` reading where no table had read it |
| `symphony.outcome` | `malformed` (the table refused part of the output or kept a stray marker as text), `empty` (whitespace and framing alone), `unknown_tool` (a call named a function the request did not declare), `failed` (the output could not be read and the request answered with an error), or `panicked` (the reading itself failed in the gateway). Never `clean`: a clean output records nothing |
| `symphony.choice` | The choice whose reading is recorded; absent for a `failed` or `panicked` reading |
| `symphony.reason` | The first malformed run's reason, such as `unterminated_region` (`unknown_tool` for an `unknown_tool` outcome); absent where the outcome names none (`empty`, `failed`, `panicked`) |
| `symphony.bytes` | The output's bytes read; absent for a `panicked` reading |
| `symphony.malformed_bytes` | The bytes in the runs the table refused; absent when there were none |
| `symphony.finish` | The engine's finish (`stop`, `length`, `tool_calls`, `abort`, `other`); when the request forbade calls with `tool_choice: none`, a `tool_calls` finish is recorded as the `stop` the client gets; absent where the output never reached one (`failed` and `panicked` readings) |

The fields are recorded when the output ends, once per request: the first reading that was not clean stands, and a later choice's reading of the same request does not replace it, so the span never mixes two readings' fields. A `panicked` reading is the one exception — caught at the request's boundary, its outcome and table overwrite an earlier reading's, while the earlier reading's other fields stay as recorded. Every line logged under the span from that point carries the fields, and with [tracing](monitoring.md#opentelemetry-tracing) on they are exported as attributes of the request span, so a request can be found by its outcome, table, or reason even when the reader's own WARN line about the output was rate-limited away. Nothing is added to the response itself: the client receives the same bytes whatever was recorded.

A whole response's output is read before its response line is written, so its `finished processing request` line carries the fields. A streamed response's line goes out with the response head, before the body drives the reading, so it never carries them; the `finished streaming the response` end line (written when `response_bytes` is among `--access-log-fields`) goes out at the stream's end and does carry them.

### Health Probe Logs

Requests to `/health`, `/readiness`, and `/liveness` log both lines at DEBUG, whatever the status; the response line reads `finished probe request`. A `503` from `/readiness` while workers are still loading is an expected state, so polling probes do not fill the log with ERROR lines. Probes served on the dedicated `--health-check-port` listener bypass the logging layer entirely.

### Other Structured Logs

| Target | Level | Message | When |
|--------|-------|---------|------|
| `smg::policies::*` | DEBUG | Routing decisions | See [Routing Decision Logs](#routing-decision-logs) |
| `smg::audit` | INFO | `control_plane_audit`, `control_plane_mutation` | Control-plane requests, when control-plane authentication is configured: every request gets an admission line (`control_plane_audit`), and each admitted request other than a `GET` or `HEAD` also gets an outcome line (`control_plane_mutation`) with the handler's status; see [Audit Log Format](../concepts/security/authentication.md#audit-log-format) for the fields. The Rust binary logs them by default (turn off with `--disable-audit-logging`); the pip `smg launch` logs them only with `--control-plane-audit-enabled` |
| `smg_rl` | INFO | `rl.proxy`, `rl.fanout` | RL control plane calls, with `--enable-rl` |

---

## Routing Decision Logs

At DEBUG, the cache-aware, sticky-session, and prefix-hash policies log one structured line per selection decision, naming the branch taken and the worker chosen, so you can see why a request landed on a given worker. `least_load` and `power_of_two` log a plain line per selection instead, naming the worker chosen but no branch; `random`, `round_robin`, and `consistent_hashing` log no selection line. The lines are off at the default `info` level. Turn them on for the policy targets only:

```bash
RUST_LOG=warn,smg=info,smg::policies=debug smg launch --worker-urls http://worker:8000 --policy cache_aware
```

| Message | Target | Fields |
|---------|--------|--------|
| `Cache-aware selection` | `smg::policies::cache_aware` | `index` (`tree` or `hash`), `branch`, `worker`, `model_id`; tree decisions add `matched_ratio` and `threshold`, hash decisions add `level` |
| `Sticky routing decision` | `smg::policies::registry` | `source` (`rid` or `header`), `key`, `branch`, `worker`, `model_id` |
| `Prefix-hash selection` | `smg::policies::prefix_hash` | `branch`, `level`, `worker`, `model_id` |
| `<policy> selected <worker> (score …, in_flight …)` | `smg::policies::least_load` | None — the policy (`least_load` or `power_of_two`, which share the scorer and its target), the chosen worker's URL, its score, and its in-flight request count are all in the message text |

Branch values:

- **Cache-aware, tree index**: `tree_match`, `spill`, `expected_wait_fallback`, `first_healthy_fallback`; the `smg_cache_aware_policy_branch_total` counter records the same branches.
- **Cache-aware, hash index**: `hash_hit`, `hash_spill`, `short_request` (shorter than the smallest `--cache-boundaries` value), `expected_wait_fallback`, `kv_pressure_expected_wait`.
- **Cache-aware under KV-cache imbalance**: `kv_pressure_expected_wait`; this line has no `index` field.
- **Sticky sessions** (`--routing-key-override`): `occupied_hit`, `occupied_miss`, `vacant`, `cap_respill`, `no_healthy_workers`. `key` is the internal map key, which combines the model, the PD leg, and the routing key.
- **Prefix hash**: `ring_hit`, `load_balance_walk`, `fallback_least_load`, `no_routing_key`, `no_healthy_workers`.

When cache-aware routing uses KV events, the selection line is `Event-driven routing` (`branch` is `event_hit`, `event_spill`, or `event_miss`), with `worker`, `overlap_blocks`, `request_blocks`, `policy`, and `model_id`. A miss placed on a worker that is still building its cache logs `Cache miss routed to a warming worker` (`branch` `warmup_slice`), and a hit diverted to such a worker logs `Cache hit diverted to a thin worker` (`branch` `warmup_divert`).

See the [Metrics Reference](../reference/metrics.md#routing-policy-metrics) for the matching decision counters.

---

## Multimodal Timing Logs

`--log-mm-timing` logs per-request multimodal timing at INFO. Each line's message starts with `smg_mm_timing`, followed by the stage:

| Message | Contents |
|---------|----------|
| `smg_mm_timing process_multimodal_plan` | Media counts (`image_count`, `audio_count`, `video_count`, `video_frame_count`), per-phase times (`media_fetch_decode_ms`, `config_lookup_ms`, `preprocess_ms`, `token_expand_ms`, `total_ms`), and token counts before and after placeholder expansion (`original_tokens`, `expanded_tokens`) |
| `smg_mm_timing mm_tensor_payload_inline`, `smg_mm_timing mm_shm_write` | Tensor transport: inline sends and shared-memory writes |
| `smg_mm_timing assemble_tokenspeed`, `smg_mm_timing assemble_tokenspeed_item`, `smg_mm_timing tokenspeed_shm_write_direct` | TokenSpeed request assembly |
| `smg_mm_timing video_decode_backend`, `smg_mm_timing video_tempfile_write` | Video decoding (target `llm_multimodal::media`). These two lines are enabled only by the `SMG_LOG_MM_TIMING` environment variable; `--log-mm-timing` does not turn them on |

```bash
smg launch --worker-urls grpc://worker:50051 --model-path Qwen/Qwen2.5-VL-7B-Instruct --log-mm-timing
```

The older `SMG_LOG_MM_TIMING` environment variable (`1`, `true`, `yes`, or `on`) still turns the logs on when the flag is absent, and it is the only switch for the two video-decoding lines, but it is deprecated: SMG logs a warning, and support ends in the next minor release.

The timing lines do not record where each request's media is processed; the `smg_mm_processing_total` metric counts that, labeled with the model, the processing location (`router` or `worker`), and the reason. See the [Metrics Reference](../reference/metrics.md#multimodal-metrics) for the reason values and [Multimodal](../concepts/architecture/multimodal.md) for the processing pipeline.

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
- **Non-blocking I/O**: dedicated writer threads write the files and stdout; a writer that falls more than 128,000 lines behind drops new lines for its sink rather than slowing requests
- **Dropped-line reporting**: drops are counted per sink in the `smg_log_dropped_lines` metric; while drops continue, SMG logs a WARN line every 30 seconds (`log lines dropped: the writer queue was full`, with the total and the count since the last report); see the [Metrics Reference](../reference/metrics.md#log-writer-metrics)
- **Directory creation**: SMG creates the directory if it does not exist

### Log Directory Requirements

An existing `--log-dir` must be a directory SMG can write. A missing one is created at start-up, so it must sit under a directory SMG can write; a relative path resolves against the working directory.

Any other path — an existing file, a directory SMG cannot write, a path under a file or under an unwritable directory — is refused at startup with a configuration error naming `log_dir`, the path, and the reason, before the gateway binds its port.

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

The image runs as the non-root user `smg` (UID 65532), so `--log-dir` must be writable by that user. `/var/log/smg` does not exist in the image, so Docker creates the new named volume owned by root; change its owner to UID 65532 before the first start, or SMG refuses to start with a configuration error naming `log_dir` as not writable (see [Log Directory Requirements](#log-directory-requirements)).

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

Every request to the main listener gets an ID. SMG takes it from the first of these request headers that is present: `x-request-id`, `x-correlation-id`, `x-trace-id`, `request-id` (replace the list with `--request-id-headers`). Otherwise it generates one with an OpenAI-style prefix (`chatcmpl-`, `cmpl-`, `gnt-`, `resp-`, `msg_`, or `req-`) followed by 24 random letters and digits. The ID is returned in the `x-request-id` response header and recorded as `request_id` on the request's `http_request` span, so it appears on the lines logged while the request is handled, as long as SMG logs at `info` or more verbose. A request in the Messages API's shape — one to `/v1/messages`, or one carrying an `anthropic-version` header — is answered with the same ID in a `request-id` header too, which the Anthropic SDKs read; a `request-id` the response already carries, such as one relayed from a proxied upstream, is kept.

SMG honors an inbound ID only when it is 1 to 128 visible ASCII characters. A value that is empty, longer, or contains a space, control character, or non-ASCII byte is dropped (never echoed or logged) and a fresh ID is generated instead. Either way, the chosen ID is written back onto the request header as its only value, so the worker receives the same ID the gateway logs and returns.

```bash
# Send request with custom ID
curl -H "X-Request-ID: my-trace-123" \
  -H "Content-Type: application/json" \
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

# Verify JSON format (the startup banner before the logs is plain text, so skip non-JSON lines)
smg launch --worker-urls http://worker:8000 --log-json | jq -R 'fromjson?'

# Test log level filtering: no INFO lines should appear (stop with Ctrl-C)
smg launch --worker-urls http://worker:8000 --log-level warn | grep INFO

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

    4. An unusable `--log-dir` does not silently disable logging: SMG refuses to start with a configuration error naming `log_dir`, so a gateway that is running still logs to stdout; see [Log Directory Requirements](#log-directory-requirements).

??? question "Logs not in JSON format"

    1. Ensure `--log-json` flag is set:
    ```bash
    smg launch --log-json --worker-urls http://worker:8000
    ```

    2. Lines printed before logging is set up are plain text: the startup banner goes to stdout ahead of the JSON logs, and startup errors, such as a refused `--log-dir`, go to stderr

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
