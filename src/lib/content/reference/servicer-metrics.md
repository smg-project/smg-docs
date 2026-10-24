---
title: Servicer Metrics Reference
---

# Servicer Metrics Reference

The Rust servicer, the worker-side process that serves an engine to the gateway, exports Prometheus families of its own, `smg_servicer_*`, on a listener of its own. They are not on the gateway's `/metrics` (see the [Metrics Reference](metrics.md)): the servicer is another process, with its own recorder, so a process that hosts a servicer beside another metrics user never shares a registry, and two servicers in one process keep two.

The listener serves `GET /metrics` when the servicer is given a metrics address. On the engine launchers that is `--metrics-port`, with `--metrics-host` for the host (the gRPC listener's host when unset, every interface when that is unset too); without the flags, the launcher's variables `SMG_<ENGINE>_SERVICER_METRICS_PORT` and `SMG_<ENGINE>_SERVICER_METRICS_HOST` (`ENGINE` the launcher's name in upper case); without either, no endpoint. Port `0` binds an ephemeral port; the gRPC port itself is refused as a configuration error, and so is a metrics port with the Python servicer selected (it needs `--servicer-impl rust`). The Python servicer classes take the address as `metrics_bind_address` (`host:port`). The listener binds beside the gRPC listener and before the engine connects, so a scrape during the engine's start answers; a busy port is a start-up error. Without a listener nothing is recorded beyond the token totals the servicer's own stats line reads. Histogram samples are drained into the rendered distributions every 5 seconds and at each scrape.

Every family carries `model` (the served model) and `wire` (the engine family the servicer serves: `vllm`, `sglang` or `tokenspeed`). The request families carry `rank`, the data-parallel rank the request ran on (`other` for a rank beyond the configured width, built on first use). Most series are registered when the servicer starts, per rank and per label value, so they are on the first scrape at zero; the exceptions are named in their entries: a gRPC code's error series at its first error, the string-stop family only on a servicer that matches stop strings itself, the media families only on one that processes media itself. The request path never builds a key, hashes a label or takes a lock: one clock read at arrival, one clock read plus one histogram push and two atomic adds at the first output, one atomic add (and, with the inter-token histogram on, one clock read and one push) per output chunk, and one clock read plus a handful of pushes when the stream ends.

---

## Request Families

### `smg_servicer_requests_total`

Requests whose stream ended, by how they ended. A fan-out (`n > 1`) is one request with the reason of its last choice to end, an abort winning.

| Type | Labels |
|------|--------|
| Counter | `rank`, `finish_reason` |

Finish reasons: `stop`, `length`, `abort`, `error` (the engine or the wire failed the request), `cut` (a shutdown cut the request while it was still open). An unknown wire value counts as `stop`.

```promql
# Requests ending per second, by reason
sum by (finish_reason) (rate(smg_servicer_requests_total[5m]))
```

### `smg_servicer_requests_refused_total`

Requests refused before they reached the engine, by reason: `duplicate_id` (the id names a request still in flight), `draining` (the servicer is draining), `invalid_argument` (the request itself was invalid), `media` (the request's media could not be processed).

| Type | Labels |
|------|--------|
| Counter | `reason` |

### `smg_servicer_requests_active`

Admitted requests still preprocessing, streaming, or unwinding an abort.

| Type | Labels |
|------|--------|
| Gauge | None |

### `smg_servicer_request_errors_total`

Requests that failed with a gRPC status, by its code in lower case (`internal`, `unavailable`, `cancelled`, `invalid_argument`, ...). A code's series appears at its first error; a code that never happens renders no series.

| Type | Labels |
|------|--------|
| Counter | `code` |

### `smg_servicer_prompt_tokens_total`

Prompt tokens as the engine reported them at each request's first output.

| Type | Labels |
|------|--------|
| Counter | `rank` |

### `smg_servicer_prompt_tokens_cached_total`

Prompt tokens the engine served from its prefix cache.

| Type | Labels |
|------|--------|
| Counter | `rank` |

### `smg_servicer_generation_tokens_total`

Generated tokens streamed to callers.

| Type | Labels |
|------|--------|
| Counter | `rank` |

```promql
# Generated tokens per second, by rank
sum by (rank) (rate(smg_servicer_generation_tokens_total[1m]))

# Prefix-cache share of the prompt tokens
sum(rate(smg_servicer_prompt_tokens_cached_total[5m])) / sum(rate(smg_servicer_prompt_tokens_total[5m]))
```

### `smg_servicer_string_stops_total`

Requests this servicer ended on a stop string it matched itself. Registered only when the servicer matches stop strings.

| Type | Labels |
|------|--------|
| Counter | `rank` |

### `smg_servicer_time_to_first_token_seconds`

Seconds from a request's arrival at the servicer to its first output. The buckets are the edges of the engines' own time-to-first-token families (1 ms to 2560 s), so a dashboard built for an engine's server reads this family by `le`.

| Type | Labels |
|------|--------|
| Histogram | `rank` |

```promql
# P99 time to first token at the servicer
histogram_quantile(0.99, sum by (le) (rate(smg_servicer_time_to_first_token_seconds_bucket[5m])))
```

### `smg_servicer_e2e_request_latency_seconds`

Seconds from a request's arrival at the servicer to the end of its stream. Buckets from 0.3 s to 7680 s.

| Type | Labels |
|------|--------|
| Histogram | `rank` |

### `smg_servicer_request_time_per_output_token_seconds`

Mean seconds per generated token after the first, per request, on the servicer's clock. Buckets from 10 ms to 80 s.

| Type | Labels |
|------|--------|
| Histogram | `rank` |

### `smg_servicer_inter_token_latency_seconds`

Seconds between consecutive output chunks of a choice, per token when a chunk carries several, on the servicer's clock. Buckets from 10 ms to 80 s. The observations sit behind a pressure sampler: above a bound of pending observations the next one is dropped and counted in `smg_servicer_metrics_dropped_total`, so a servicer nobody scrapes cannot grow the process.

| Type | Labels |
|------|--------|
| Histogram | `rank` |

### `smg_servicer_request_prompt_tokens`

Prompt tokens per request. The token-count histograms take a 1-2-5 series of edges up to the model's context length (32768 when the engine does not report one).

| Type | Labels |
|------|--------|
| Histogram | `rank` |

### `smg_servicer_request_generation_tokens`

Generated tokens per request.

| Type | Labels |
|------|--------|
| Histogram | `rank` |

### `smg_servicer_request_params_max_tokens`

The `max_tokens` parameter of the requests that set it.

| Type | Labels |
|------|--------|
| Histogram | `rank` |

### `smg_servicer_request_params_n`

The `n` parameter of requests. Buckets 1, 2, 5, 10, 20.

| Type | Labels |
|------|--------|
| Histogram | `rank` |

---

## Lifecycle and Scrape Families

### `smg_servicer_state`

The servicer's lifecycle state, one-hot: `1` on the series of the current state, `0` on the others. States: `starting` (the engine has not completed its handshake), `serving` (the engine is connected and alive, admission is open and readiness is reported), `warming_up` (readiness held back, the warm-up's own requests admitted only), `draining` (admission closed), `engine_down` (the engine connection failed or the engine is no longer alive).

| Type | Labels |
|------|--------|
| Gauge | `state` |

```promql
# Servicers not serving
smg_servicer_state{state="serving"} == 0
```

### `smg_servicer_start_time_seconds`

When the servicer started, in seconds since the Unix epoch.

| Type | Labels |
|------|--------|
| Gauge | None |

### `smg_servicer_metrics_scrapes_total`

Scrapes answered, by HTTP status code: `200`, or `500` for a scrape whose render failed (that scrape alone; the next one renders again).

| Type | Labels |
|------|--------|
| Counter | `code` |

### `smg_servicer_metrics_scrape_seconds`

How long the last scrape took to render.

| Type | Labels |
|------|--------|
| Gauge | None |

### `smg_servicer_metrics_dropped_total`

Observations the pressure sampler dropped, by family (`inter_token_latency`).

| Type | Labels |
|------|--------|
| Counter | `family` |

---

## Media Families

Requests whose media the worker side processes. Registered only when the servicer processes media itself: a wire that measures no media exports no media series.

### `smg_servicer_media_items_per_request`

Media references a request carried, by modality (`image`, `video`, `audio`), counted before they are checked (a refused request counts). Buckets 1 to 64 items.

| Type | Labels |
|------|--------|
| Histogram | `modality` |

### `smg_servicer_media_processing_seconds`

Seconds a request's worker-side media spent in a stage: `wait` (for a processing slot) or `process`. The buckets are the time-to-first-token edges.

| Type | Labels |
|------|--------|
| Histogram | `stage` |

### `smg_servicer_media_requests_total`

Requests with worker-side media, by outcome: `ok`, `refused` (before processing: references this worker does not take, a missing processor, no slot), or the processor's verdict, `client` (the caller's media), `transient` (a transient failure), `invalid` (an invalid request), `unavailable` (an unavailable processor), `internal` (a failed processor).

| Type | Labels |
|------|--------|
| Counter | `outcome` |
