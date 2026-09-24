---
title: RL Control Plane
---

# RL Control Plane

Reinforcement learning (RL) training loops send rollout traffic through SMG, but between training steps they also have to operate each inference engine directly: pause generation, load new weights, flush the KV cache, and resume. The RL control plane lets a trainer do this through the gateway. It lists the engines SMG already manages, forwards an engine's native control route to one worker, and fans the same call out to every worker that matches a label selector.

<div class="prerequisites" markdown>

#### Before you begin

- Completed the [Getting Started](index.md) guide
- SMG v1.11.0 or later (the RL control plane first shipped in v1.11.0)
- Engines registered as HTTP workers (`http://` or `https://` URLs); the control plane cannot proxy gRPC or ZMQ workers
- An admin credential if the gateway uses [Control Plane Auth](control-plane-auth.md)

</div>

---

## Overview

SMG v1.11.0 ships milestone M1 of the RL control plane: three route groups under `/v1/rl`.

| Capability | Route | What it does |
|---|---|---|
| Discovery | `GET /v1/rl/workers`, `GET /v1/rl/workers/{id}` | Lists workers with engine, parallelism, health, weight version, and a capability table |
| Passthrough | `GET` or `POST /v1/rl/workers/{id}/engine/{path}` | Sends one engine-native request to one worker and returns the engine's answer |
| Fan-out | `GET` or `POST /v1/rl/engine/{path}?selector=...` | Sends the same request to every worker that matches a label selector, with bounded concurrency and a per-worker report |

Control calls stay out of the inference path. They skip routing policies, retries, circuit breakers, load counters, and admission control, so a slow or failed control call never opens a breaker or counts as load for inference routing.

### What M1 Does Not Do

- **No engine-neutral operations.** SMG forwards each engine's own route names and request bodies unchanged (for example SGLang's `update_weights_from_disk`). It does not translate a pause or a refit from one engine's API to another's. The [capability table](#capabilities) describes what each engine supports, but SMG does not check it before forwarding a call.
- **HTTP workers only.** A gRPC or ZMQ worker fails with `unsupported_connection_mode`.
- **No retries and no streaming.** A refit is not idempotent, so a failed call is reported, not retried. Each call returns one buffered response.
- **No pause or refit tracking.** Routing is unchanged: inference requests keep reaching a paused or refitting engine according to the routing policy.
- **Registration-time weight version.** `weight_version` in discovery is the value recorded when the worker registered. SMG does not refresh it after a refit.

---

## Enable the Control Plane

The control plane is compiled into every SMG build but is off by default. Without `--enable-rl`, SMG mounts nothing under `/v1/rl`: every `/v1/rl/*` request returns `404` with an empty body, and no `smg_rl_*` metrics are emitted.

| Flag | Default | Description |
|---|---|---|
| `--enable-rl` | off | Mount the control plane under `/v1/rl` |
| `--rl-control-timeout-secs` | `600` | Total timeout for one proxied engine call; weight refits can take minutes |
| `--rl-fanout-concurrency` | `32` | Maximum concurrent engine calls in one fan-out |

With `--enable-rl` set, both numeric flags must be at least `1`, or startup fails with a configuration error.

The RL examples in the smg repository use this launch profile:

```bash
smg launch \
  --worker-urls http://rollout-0:30000 http://rollout-1:30000 \
  --policy cache_aware \
  --enable-rl \
  --disable-health-check \
  --disable-circuit-breaker \
  --request-timeout-secs 14400
```

`--disable-health-check` and `--disable-circuit-breaker` match what the slime and vime training frameworks set on their own routers, so a transient engine error does not open a breaker for a whole training step. `--request-timeout-secs 14400` covers multi-hour agentic rollouts. Raise `--rl-control-timeout-secs` if a refit from disk takes longer than 10 minutes.

With `smg serve`, add the `--router-` prefix to the flags and start the workers over HTTP. The `smg serve` default connection mode, `grpc`, cannot be proxied.

```bash
smg serve \
  --backend sglang \
  --model-path meta-llama/Llama-3.1-8B-Instruct \
  --connection-mode http \
  --data-parallel-size 2 \
  --host 0.0.0.0 \
  --port 30000 \
  --router-enable-rl
```

Both examples run without auth and listen on all interfaces, so anyone who can reach the gateway can call engine routes through `/v1/rl/*`; set up [auth](#security) outside local development.

See the [Configuration Reference](../reference/configuration.md#rl-control-plane-configuration) for all gateway settings.

---

## Security

`/v1/rl/*` is in the same auth group as `/workers` and the other control-plane routes:

| Gateway auth setup | What `/v1/rl/*` accepts |
|---|---|
| Control-plane auth (`--control-plane-api-keys`, or `--jwt-issuer` with `--jwt-audience`) | A control-plane API key or JWT with the `admin` role, as `Authorization: Bearer <token>`. A missing or unknown token gets `401`; a valid token without the admin role gets `403`. |
| `--api-key` without control-plane auth | `Authorization: Bearer <api-key>`; anything else gets `401` |
| `--tenant-api-key` without `--api-key` or control-plane auth | Nothing: every request gets `401` |
| No keys | Every request; use this only for local development |

Per-tenant keys (`--tenant-api-key`) are never accepted on these routes. When control-plane auth is on, its audit log (enabled by default) records these calls like any other control-plane operation.

When SMG calls an engine, it sends:

- The request method, the engine path, the query string without `selector`, and the body bytes, unchanged, to the worker's base URL.
- The caller's `Content-Type`, or `application/json` when the request has a body but no content type.
- Only the `x-request-id`, `traceparent`, and `tracestate` headers from the caller. The caller's `Authorization` header is never forwarded.
- The worker's own API key, if it has one, as `Authorization: Bearer <key>`. Workers added with `--worker-urls` or service discovery use `--api-key`; `POST /workers` takes an `api_key` field.

Calls go through the HTTP client SMG already negotiated for that worker, so they keep its HTTP version, TLS identity and CA roots, and connection pool settings.

!!! warning "No route allowlist"
    The control plane forwards any `GET` or `POST` engine path that passes [path validation](#engine-paths), including inference routes. A credential that can reach `/v1/rl/*` can call any of those routes on every engine behind SMG, so treat it as an engine admin credential.

---

## Discover Workers

```bash
curl http://localhost:30000/v1/rl/workers \
  -H "Authorization: Bearer ${ADMIN_TOKEN}"
```

Response for one SGLang worker (`labels` trimmed):

```json
{
  "protocol_version": 1,
  "workers": [
    {
      "id": "0199a1c2-7e3f-7b10-8c4d-2e5f6a7b8c90",
      "url": "http://rollout-0:30000",
      "base_url": "http://rollout-0:30000",
      "engine": "sglang",
      "engine_version": "0.5.15.post1",
      "model_id": "meta-llama/Llama-3.1-8B-Instruct",
      "worker_type": "regular",
      "connection_mode": "http",
      "tp_size": 1,
      "dp_size": 1,
      "pp_size": 1,
      "dp_ranks": 1,
      "role": null,
      "health": "ready",
      "weight_version": "default",
      "labels": {
        "model_path": "meta-llama/Llama-3.1-8B-Instruct",
        "tp_size": "1",
        "dp_size": "1",
        "pp_size": "1",
        "version": "0.5.15.post1",
        "weight_version": "default"
      },
      "capabilities": {
        "source": "static",
        "pause_modes": ["abort", "retract", "in_place"],
        "update_from": ["disk", "tensor", "distributed"],
        "abort": true,
        "flush_cache": true,
        "sleep_wake": true,
        "reports_weight_version": true
      }
    }
  ],
  "total": 1
}
```

`protocol_version` is the version of the `/v1/rl` wire contract, currently `1`; it changes only for incompatible changes. `total` is the number of rows. Rows are sorted by `base_url`, and DP-aware ranks that share an engine address collapse into one row.

| Field | Description |
|---|---|
| `id` | Worker ID, the same one `GET /workers` reports. Use it in the per-worker routes. |
| `url` | Registered URL. DP-aware ranks carry an `@<rank>` suffix; a collapsed row shows its lowest rank. |
| `base_url` | Address that control calls are sent to |
| `engine` | `sglang`, `vllm`, `trtllm`, `tokenspeed`, `mlx`, `generic`, `external`, or `unknown` |
| `engine_version` | The worker's `version` label, or `null` |
| `model_id` | Model the worker serves |
| `worker_type` | `regular`, `prefill`, `decode`, or `encode` |
| `connection_mode` | `http`, `grpc`, or `zmq`. Only `http` workers can be proxied. |
| `tp_size`, `dp_size`, `pp_size` | From the worker's labels; `null` when missing or not an integer |
| `dp_ranks` | Number of DP-aware ranks collapsed into this row; `1` for other workers |
| `role` | The worker's `role` label, or `null` |
| `health` | Registry status: `pending`, `ready`, `not_ready`, `failed`, or `draining` |
| `weight_version` | The worker's `weight_version` label, recorded at registration |
| `labels` | All worker labels: metadata SMG discovered at registration, overridden by any labels you set |
| `capabilities` | What the engine supports; see [Capabilities](#capabilities) |

`GET /v1/rl/workers/{id}` returns one row, or `404` with `worker_not_found` for an unknown ID. For a DP-aware worker, any rank's ID works, and `dp_ranks` counts the ranks that share its engine address.

SMG reads labels from the engine when a worker registers. For an HTTP SGLang worker it reads `tp_size`, `version`, `weight_version`, and more from `/server_info`. For an HTTP vLLM worker it reads only model and version metadata (`/v1/models` and `/version`), so `tp_size`, `dp_size`, and `pp_size` stay `null` unless you set them. Set or override labels with the `labels` field of `POST /workers` (see [Multiple Workers](multiple-workers.md)):

```bash
curl -X POST http://localhost:30000/workers \
  -H "Authorization: Bearer ${ADMIN_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
    "url": "http://rollout-2:30000",
    "labels": {"role": "policy", "tp_size": "2"}
  }'
```

### Capabilities

`capabilities` comes from a static per-engine table:

| Field | SGLang | vLLM | Other engines |
|---|---|---|---|
| `pause_modes` | `abort`, `retract`, `in_place` | `abort`, `wait`, `keep` | none |
| `update_from` | `disk`, `tensor`, `distributed` | `disk`, `distributed` | none |
| `abort` | `true` | `false` | `false` |
| `flush_cache` | `true` | `false` | `false` |
| `sleep_wake` | `true` | `true` | `false` |
| `reports_weight_version` | `true` | `false` | `false` |

`pause_modes` lists the `mode` values the engine's pause route accepts, `update_from` lists the weight sources it can refit from, and `reports_weight_version` says whether it reports a weight version after a refit.

Worker labels override the table. `rl.pause_modes` and `rl.update_from` take comma-separated lists. `rl.abort`, `rl.flush_cache`, `rl.sleep_wake`, and `rl.reports_weight_version` are `true` when the value is `true` (in any case) and `false` for any other value. `source` is `static`, or `label` when at least one `rl.*` label overrode the table.

---

## Call One Worker

`GET` or `POST /v1/rl/workers/{id}/engine/{path}` sends one request to `<base_url>/<path>` on that worker:

```bash
curl -X POST "http://localhost:30000/v1/rl/workers/${WORKER_ID}/engine/update_weights_from_disk" \
  -H "Authorization: Bearer ${ADMIN_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"model_path": "/ckpt/step-42", "weight_version": "42"}'
```

The response's HTTP status is the engine's status, and the body wraps the engine's answer (`body` trimmed here):

```json
{
  "worker_id": "0199a1c2-7e3f-7b10-8c4d-2e5f6a7b8c90",
  "url": "http://rollout-0:30000",
  "status": 200,
  "latency_ms": 1840,
  "body": {"success": true}
}
```

| Field | Description |
|---|---|
| `worker_id` | Worker that was called |
| `url` | Worker's registered URL |
| `status` | Engine's HTTP status |
| `latency_ms` | Time from sending the request to reading the engine's response |
| `body` | Engine's response: parsed JSON when the engine sent JSON, otherwise a string |
| `body_truncated` | Present and `true` only when the engine's response exceeded 1 MiB; `body` then holds the first 1 MiB as a string |

An engine error, such as a `400` from SGLang, comes back in the same envelope with the engine's status. A failure on the SMG side returns `{"error": "<code>", "message": "..."}` instead; see [Errors](#errors).

### Engine Paths

The per-worker and fan-out routes accept the same `{path}`:

- 1 to 4 segments separated by `/`, such as `flush_cache` or `inference/v1/generate`. A leading `/` is ignored.
- Each segment uses only `A-Z`, `a-z`, `0-9`, `.`, `_`, and `-`. Empty, `.`, and `..` segments are rejected.
- Only `GET` and `POST` are accepted (a `HEAD` request is served by the `GET` route and forwarded as `HEAD`); other methods get `405`.
- The query string is forwarded as-is, except that `selector` parameters are removed.
- The request body is forwarded byte for byte, up to `--max-payload-size`.

A path that breaks these rules gets `400` with `invalid_engine_path`.

---

## Fan Out to Many Workers

`GET` or `POST /v1/rl/engine/{path}?selector=<selector>` sends the same request to every worker that matches the selector. A refit is three fan-outs:

```bash
SMG=http://localhost:30000
AUTH="Authorization: Bearer ${ADMIN_TOKEN}"
SEL="selector=engine%3Dsglang"   # engine=sglang, URL-encoded

# 1. Pause generation (SGLang requires a JSON body, even an empty one)
curl -X POST "$SMG/v1/rl/engine/pause_generation?$SEL" \
  -H "$AUTH" -H "Content-Type: application/json" -d '{}'

# 2. Load new weights from a checkpoint every engine can read
curl -X POST "$SMG/v1/rl/engine/update_weights_from_disk?$SEL" \
  -H "$AUTH" -H "Content-Type: application/json" \
  -d '{"model_path": "/ckpt/step-42", "weight_version": "42"}'

# 3. Resume generation
curl -X POST "$SMG/v1/rl/engine/continue_generation?$SEL" \
  -H "$AUTH" -H "Content-Type: application/json" -d '{}'
```

How a fan-out runs:

1. SMG parses the selector and validates the path. A missing or empty `selector` gets `400 selector_required`, and a malformed one gets `400 invalid_selector`.
2. It resolves the targets: every registered worker whose labels match, with DP-aware ranks that share an engine address counted once. The ranks collapse into their lowest rank before matching, so the selector sees only that rank's `id`, `url`, and `health`. Worker health is not considered; add `health=ready` to the selector to skip unhealthy workers. If nothing matches, the answer is `400 no_workers_match`.
3. It calls the targets with at most `--rl-fanout-concurrency` calls in flight. Each call has its own `--rl-control-timeout-secs` deadline.
4. It waits for every call, then answers once: `200` when every target returned 2xx, and `207 Multi-Status` otherwise, even when every target failed.

The fan-out runs inside the request. If the caller disconnects before it finishes, SMG cancels the engine calls still in flight, so keep the client connected until the response arrives.

Response with one failed target (`body` trimmed):

```json
{
  "results": {
    "0199a1c2-7e3f-7b10-8c4d-2e5f6a7b8c90": {
      "worker_id": "0199a1c2-7e3f-7b10-8c4d-2e5f6a7b8c90",
      "url": "http://rollout-0:30000",
      "status": 200,
      "latency_ms": 1840,
      "body": {"success": true}
    }
  },
  "failed": [
    {
      "worker_id": "0199a1c2-8a41-7d22-9e5b-4c6d7e8f9a01",
      "url": "http://rollout-1:30000",
      "error": "upstream_unreachable",
      "message": "upstream `http://rollout-1:30000` unreachable: error sending request for url (http://rollout-1:30000/update_weights_from_disk)"
    }
  ],
  "total": 2,
  "succeeded": 1
}
```

| Field | Description |
|---|---|
| `results` | Every engine that answered, keyed by worker ID, whatever its status. Each value is a call envelope. |
| `failed` | Targets that did not return 2xx, sorted by worker ID |
| `total` | Number of targets |
| `succeeded` | Targets that returned 2xx |

Each `failed[]` entry has `worker_id`, `url`, `error`, and `message`, plus `status` for `upstream_error` and `connection_mode` for `unsupported_connection_mode`:

| `error` | Meaning | Also in `results` |
|---|---|---|
| `upstream_error` | The engine answered with a non-2xx status; `message` is `HTTP <status>` | Yes |
| `upstream_unreachable` | SMG could not connect to the engine or read its response | No |
| `upstream_timeout` | Connecting timed out, or the engine sent no response within `--rl-control-timeout-secs` | No |
| `unsupported_connection_mode` | The worker is gRPC or ZMQ | No |

SMG does not retry or roll back. Engines that succeeded keep the change; use `failed[]` to decide what to do for the others.

### Selectors

A selector is one or more terms separated by commas. Every term must match; there is no OR.

| Term | Matches when |
|---|---|
| `key=value` | The key exists and equals `value` |
| `key!=value` | The key is missing or differs from `value` |
| `key in (a,b)` | The key exists and equals one of the values |
| `key notin (a,b)` | The key is missing or equals none of the values |

- Matching is exact and case-sensitive. Whitespace around keys, operators, and values is ignored.
- Keys use only `A-Z`, `a-z`, `0-9`, `_`, `.`, `/`, and `-`.
- Double-quote a value that contains a comma, space, or parenthesis. Inside quotes, `\"` and `\\` are escapes.
- A selector that does not parse gets `400 invalid_selector`, with the byte `offset` of the problem.
- URL-encode the selector in the query string; `engine=sglang` becomes `engine%3Dsglang`. The Python client does this for you.

Keys are the worker's labels plus these keys that SMG derives from the registry, which take precedence over a label with the same name: `id`, `url`, `base_url`, `engine`, `model`, `worker_type`, `connection_mode`, and `health`.

| Selector | Targets |
|---|---|
| `engine=sglang` | Every SGLang worker |
| `engine in (sglang,vllm)` | Every SGLang and vLLM worker |
| `role!=reward` | Workers whose `role` label is missing or not `reward` |
| `engine=sglang, health=ready` | SGLang workers that are ready |
| `base_url=http://rollout-0:30000` | One engine, including all of its DP ranks |
| `model=meta-llama/Llama-3.1-8B-Instruct, tp_size=1` | Workers serving that model with `tp_size` 1 |
| `connection_mode=http` | Only workers the control plane can proxy |

---

## Errors

A failure on the SMG side returns JSON `{"error": "<code>", "message": "<text>"}` plus the context fields listed here:

| Status | `error` | Applies to | When | Extra fields |
|---|---|---|---|---|
| `400` | `invalid_engine_path` | Per-worker call, fan-out | The path breaks the [path rules](#engine-paths) | — |
| `400` | `selector_required` | Fan-out | `selector` is missing or empty | — |
| `400` | `invalid_selector` | Fan-out | The selector does not parse | `offset` |
| `400` | `no_workers_match` | Fan-out | No worker matches the selector | `selector` |
| `404` | `worker_not_found` | Worker lookup, per-worker call | Unknown worker ID | `id` |
| `422` | `unsupported_connection_mode` | Per-worker call | The worker is gRPC or ZMQ | `worker_id`, `url`, `connection_mode` |
| `502` | `upstream_unreachable` | Per-worker call | SMG could not connect to the engine or read its response | `worker_id`, `url` |
| `504` | `upstream_timeout` | Per-worker call | Connecting timed out, or the engine sent no response within `--rl-control-timeout-secs` | `worker_id`, `url` |

In a fan-out, a failed target never changes the status beyond `207`; it is reported in `failed[]`. Auth failures (`401`, `403`) and the `404` for a disabled control plane come from the gateway and do not use this format.

---

## Timeouts and Limits

| Limit | Value | Notes |
|---|---|---|
| Engine call deadline | `--rl-control-timeout-secs` (`600`) | Total time for one engine call, from connecting to reading the last byte of the response. It applies instead of `--request-timeout-secs`. |
| Fan-out concurrency | `--rl-fanout-concurrency` (`32`) | Calls in flight at once; the next call starts as soon as one finishes. With more targets than this, a fan-out can take longer than one deadline. |
| Fan-out deadline | None | A fan-out ends when every call has answered or hit its own deadline |
| Engine response size | 1 MiB | A larger response is cut to its first 1 MiB and flagged `body_truncated` |
| Request body size | `--max-payload-size` | The same limit as every other SMG route |

---

## Python Client

`smg.rl` is a client for these routes. It ships in the `smg` package from v1.11.0 and uses only the Python standard library:

```bash
pip install smg
```

```python
from smg.rl import RL, FanoutError, RlError, paused

rl = RL("http://smg:30000", api_key=None, timeout=600.0)
```

| Constructor argument | Default | Description |
|---|---|---|
| `base_url` | required | SMG base URL |
| `api_key` | `None` | Sent as `Authorization: Bearer <api_key>`; set it when auth is on |
| `timeout` | `600.0` | Client timeout for each request, in seconds; must be greater than 0. Keep it longer than the slowest call or fan-out you expect. |

| Method | Returns | Route |
|---|---|---|
| `workers()` | `list[Worker]` | `GET /v1/rl/workers` |
| `worker(worker_id)` | `Worker` | `GET /v1/rl/workers/{id}` |
| `call(worker_id, path, body=None, *, method="POST", params=None, timeout=None)` | `CallResult` | `/v1/rl/workers/{id}/engine/{path}` |
| `fanout(path, body=None, *, selector, method="POST", params=None, timeout=None, allow_partial=False)` | `FanoutResult` | `/v1/rl/engine/{path}` |

- `body` is sent as JSON with `Content-Type: application/json`. `body=None` sends no body, which SGLang's `pause_generation` and `continue_generation` reject with `400`, so pass `{}` to them.
- `params` becomes the engine's query string; `fanout` adds `selector` to it.
- `timeout` overrides the client timeout for one call.
- `call` returns a `CallResult` whenever the engine answered, even with a `4xx` or `5xx`, so check `result.status`. It raises `RlError` for SMG-side errors and auth failures.
- `fanout` raises `FanoutError` when any target failed, unless `allow_partial=True`. The exception's `result` is the full `FanoutResult`, so `err.result.failed` names the failed workers. Request errors such as `no_workers_match` raise `RlError`.
- `RlError` carries `status` (the HTTP status), `code` (the `error` field, or `http_error` when the response body was not a JSON object), and `payload`. `FanoutError` is a subclass whose `code` is `fanout_partial`.

The results are dataclasses:

| Type | Fields |
|---|---|
| `Worker` | The discovery row: `id`, `url`, `base_url`, `engine`, `engine_version`, `model_id`, `worker_type`, `connection_mode`, `tp_size`, `dp_size`, `pp_size`, `dp_ranks`, `role`, `health`, `weight_version`, `labels`, and `capabilities` (a dict) |
| `CallResult` | `worker_id`, `url`, `status`, `latency_ms`, `body` |
| `FanoutResult` | `results` (worker ID to `CallResult`), `failed` (a list of `FanoutFailure`), `total`, `succeeded` |
| `FanoutFailure` | `worker_id`, `url`, `status`, `error`, `message` |

### Pause, Refit, Resume

`paused(rl, selector, *, mode=None, timeout=None)` is a context manager for SGLang's pause routes. It fans out `pause_generation` (with `{"mode": mode}` when `mode` is set, otherwise `{}`), runs the block, and always fans out `continue_generation` with `{}` afterwards:

- If the pause fails on some workers, the block does not run, the workers that did pause are resumed, and the `FanoutError` is raised.
- If the block raises, the workers are resumed and the block's exception propagates. A resume failure is attached to it as `__context__`.
- If only the resume fails, its error is raised.

The resume is a new fan-out with the same selector, so it reaches the workers that match when it runs. A worker that stopped matching during the block, for example one whose `health` changed under a `health=ready` term, stays paused.

For other engines, call `fanout` with their own route names.

This example is adapted from `examples/rl/refit_from_disk.py` in the smg repository:

```python
import os

from smg.rl import RL, FanoutError, paused

# Longer than --rl-control-timeout-secs, so SMG reports a timeout before the client gives up
rl = RL("http://smg:30000", api_key=os.environ.get("SMG_ADMIN_TOKEN"), timeout=1200)

workers = rl.workers()
for w in workers:
    print(w.id, w.engine, w.tp_size, w.health, w.weight_version)

selector = "engine=sglang"
try:
    with paused(rl, selector):
        res = rl.fanout(
            "update_weights_from_disk",
            {"model_path": "/ckpt/step-42", "weight_version": "42", "flush_cache": True},
            selector=selector,
        )
        for wid, r in res.results.items():
            print(f"{wid}: HTTP {r.status} in {r.latency_ms} ms")
except FanoutError as err:
    for f in err.result.failed:
        print(f"{f.worker_id} ({f.url}): {f.error}: {f.message}")
    raise

# One engine, one native route
info = rl.call(workers[0].id, "server_info", method="GET")
print(info.status, info.body)
```

The full example then sends a `/generate` request through SMG and checks that `meta_info.weight_version` reports the new version, because discovery's `weight_version` is not refreshed after a refit. See [`examples/rl`](https://github.com/smg-project/smg/tree/main/examples/rl).

---

## Metrics

| Metric | Type | Labels | Description |
|---|---|---|---|
| `smg_rl_control_calls_total` | Counter | `op`, `result` | Engine calls, one per target. `result` is `ok` (2xx), `upstream_error` (any other status), `timeout`, or `unreachable`. |
| `smg_rl_control_call_duration_seconds` | Histogram | `op` | Latency of one engine call |
| `smg_rl_fanout_total` | Counter | `result` | Fan-out requests. `result` is `ok`, `partial` (at least one target failed), or `no_match`. |
| `smg_rl_fanout_duration_seconds` | Histogram | — | Wall time of one fan-out request |

- `op` is the engine path when it is one of the control operations SMG recognizes, and `other` for any other path, so arbitrary paths cannot create new label values. The recognized operations are `pause_generation`, `continue_generation`, `update_weights_from_disk`, `update_weights_from_tensor`, `update_weights_from_distributed`, `init_weights_update_group`, `destroy_weights_update_group`, `update_weight_version`, `flush_cache`, `abort_request`, `release_memory_occupation`, `resume_memory_occupation`, `pause`, `resume`, `sleep`, `wake_up`, `collective_rpc`, `server_info`, `get_server_info`, and `health`.
- A worker skipped as `unsupported_connection_mode` is not counted in `smg_rl_control_calls_total`. A fan-out rejected before target resolution (`selector_required`, `invalid_selector`, or `invalid_engine_path`) is not counted in `smg_rl_fanout_total`.
- Both histograms use SMG's duration buckets, which `--prometheus-duration-buckets` overrides.
- Each series appears the first time SMG records it. Without `--enable-rl`, there are none.

```promql
# Fan-outs where at least one engine failed
sum(rate(smg_rl_fanout_total{result="partial"}[5m]))

# Engine calls that timed out or could not connect, by operation
sum by (op, result) (rate(smg_rl_control_calls_total{result=~"timeout|unreachable"}[5m]))

# p99 latency of update_weights_from_disk calls (one call per engine)
histogram_quantile(0.99,
  sum by (le) (rate(smg_rl_control_call_duration_seconds_bucket{op="update_weights_from_disk"}[5m])))
```

See [Monitoring](monitoring.md) for scraping SMG metrics.

### Logs

Every engine call that gets a response logs an `rl.proxy` event with `worker_id`, `url`, `method`, `path`, `status`, and `latency_ms`. Every fan-out that reaches its targets logs an `rl.fanout` event with `path`, `selector`, `total`, `succeeded`, `failed`, and `latency_ms`.

Both are INFO events on the `smg_rl` log target. SMG's default log filter includes them at the default `--log-level info`, because its `smg=info` entry matches every target that starts with `smg`. `RUST_LOG` replaces the default filter; to see only these events and warnings:

```bash
RUST_LOG=warn,smg_rl=info smg launch \
  --worker-urls http://rollout-0:30000 \
  --enable-rl
```

---

## Troubleshooting

??? question "Every /v1/rl/* request returns 404 with an empty body"

    The control plane is not mounted. Start SMG with `--enable-rl` (`--router-enable-rl` with `smg serve`). With the Python client, this shows up as `RlError` with code `http_error` and status 404.

    An unknown worker ID also returns `404`, but with a JSON body: `{"error": "worker_not_found", ...}`.

??? question "401 or 403 from /v1/rl/*"

    `401` means the bearer token is missing or not accepted. With control-plane auth, use an admin control-plane API key or JWT; the shared `--api-key` is not accepted. Without control-plane auth, use the `--api-key` value. Per-tenant keys are never accepted, and a gateway configured with only `--tenant-api-key` rejects every control-plane request.

    `403` means the token is valid but its role is not `admin`.

??? question "A fan-out returns 207, or the Python client raises FanoutError"

    At least one target failed; the others completed. Check each `failed[]` entry:

    - `upstream_unreachable`: the engine is down or unreachable. Once it is back, the next call reaches it; control calls do not use circuit breakers.
    - `upstream_timeout`: the call took longer than `--rl-control-timeout-secs`, or connecting to the engine timed out. Raise the flag for long refits.
    - `upstream_error`: the engine rejected the call. Its answer is in `results[<worker_id>].body`.
    - `unsupported_connection_mode`: the selector matched a gRPC or ZMQ worker. Add `connection_mode=http` to the selector.

??? question "SGLang returns 400 to pause_generation or continue_generation"

    SGLang requires a JSON body on these routes, and a bodyless `POST` gets `400`. Send `{}`: `-d '{}'` with curl, or `body={}` with `call` and `fanout`. The `paused` helper already does.

??? question "400 no_workers_match"

    No worker's labels match the selector. List the workers with `GET /v1/rl/workers` and compare their `labels`, `engine`, `model_id` (the `model` key), and `health` with your terms. Matching is exact and case-sensitive, and the selector must be URL-encoded in the query string.

??? question "weight_version in discovery does not change after a refit"

    This is expected. Discovery reports the `weight_version` label recorded when the worker registered, and the control plane does not refresh it. Ask the engine instead: SGLang reports `meta_info.weight_version` on `/generate` responses.

??? question "Long refits fail with upstream_timeout or a client timeout"

    Raise `--rl-control-timeout-secs` (default 600) on SMG and `timeout` on the Python client (default 600). If the client gives up first and disconnects, SMG cancels the engine calls still in flight.

---

## Next Steps

- [Control Plane Auth](control-plane-auth.md) — admin API keys and JWT roles for `/v1/rl/*`
- [Extension API Reference](../reference/api/extensions.md#rl-control-plane-endpoints) — endpoint summary
- [Monitoring](monitoring.md) — scrape and alert on SMG metrics
- [Configuration Reference](../reference/configuration.md#rl-control-plane-configuration) — RL control plane settings
