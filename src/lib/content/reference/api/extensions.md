---
title: Extension API
---

# Extension API Reference

This page documents non-OpenAI extension endpoints exposed by SMG, aligned to route registration in `model_gateway/src/server.rs`.

---

## Auth Model

SMG endpoint auth is route-group based:

| Route group | Auth behavior |
|---|---|
| Public routes | No auth: `/health`, `/liveness`, `/readiness`, `/health_generate`, `/engine_metrics`, `/loads`, `/v1/models`, `/v1/models/{model}`, `/get_model_info`, and `/get_server_info` |
| Protected routes | `Authorization: Bearer <key>` with the shared `--api-key` or a per-tenant `--tenant-api-key` key; open when neither is set (`/v1/tokenize`, `/generate`, `/v1/chat/completions`, and the other inference routes) |
| Control-plane routes | With [control-plane auth](../../getting-started/control-plane-auth.md) configured (`--control-plane-api-keys`, or `--jwt-issuer` with `--jwt-audience`), a control-plane API key or JWT: the admin role for every method, the user role for `GET` and `HEAD` only — except `GET /config`, a read kept to the admin role. Otherwise only the shared `--api-key` is accepted: per-tenant keys are rejected, a gateway with tenant keys but no `--api-key` answers every control-plane request with `401`, and a gateway with no keys at all leaves these routes open |

With control-plane auth configured, a missing or invalid credential gets `401` and a valid credential whose role does not allow the method gets `403`. The shared `--api-key` is not accepted on control-plane routes in that mode. `GET /config`, the [effective-configuration view](admin.md#effective-configuration), exists only in that mode and the admin role alone may read it; a gateway without control-plane auth does not mount it, so the path answers `404` instead of falling back to the shared key.

If JWT validation fails to initialize at startup (for example, when OIDC discovery fails), the control-plane rules stay in force: keys from `--control-plane-api-keys` keep working, and JWTs and every other token get `401` until the gateway is restarted with a working JWT setup.

---

## Public Extension Endpoints

These endpoints are registered without any auth middleware:

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | Same as `/liveness` |
| `GET` | `/liveness` | Process liveness probe |
| `GET` | `/readiness` | Traffic readiness probe; see [Gateway Probe Endpoints](../../concepts/reliability/health-checks.md#gateway-probe-endpoints) |
| `GET` | `/health_generate` | Generation health check, forwarded to HTTP workers; gRPC and ZMQ deployments answer `501` |
| `GET` | `/engine_metrics` | The engines' own Prometheus metrics in one exposition; see [Engine Metrics Passthrough](../metrics.md#engine-metrics-passthrough) |
| `GET` | `/loads` | Fleet engine load from the gateway's cached load snapshot (optional `?model=` filter); see [Get Loads](admin.md#get-loads) |
| `GET` | `/v1/models` | List models; see [List Models](openai.md#list-models) |
| `GET` | `/v1/models/{model}` | One model's card; `{model}` takes the rest of the path, so IDs with slashes work raw or percent-encoded; see [Retrieve Model](openai.md#retrieve-model) |
| `GET` | `/get_model_info` | Model metadata, forwarded to an HTTP worker; gRPC and ZMQ deployments answer `501` |
| `GET` | `/get_server_info` | Server metadata, forwarded to an HTTP worker; gRPC and ZMQ deployments answer `501` |

---

## Protected Utility Endpoints

These run behind protected-route auth middleware:

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/v1/tokenize` | Convert text to token IDs |
| `POST` | `/v1/detokenize` | Convert token IDs to text |
| `POST` | `/generate` | Native generate endpoint |
| `POST` | `/rerank` | Native rerank endpoint; `model` required |
| `POST` | `/v1/rerank` | OpenAI-compatible rerank endpoint; routes by its optional `model` as `/v1/embeddings` does |
| `POST` | `/v1/messages` | Messages endpoint |
| `POST` | `/v1/messages/count_tokens` | Count the input tokens of a Messages request; see [Count Tokens](messages.md#count-tokens) |
| `POST` | `/v1/classify` | Classification endpoint |
| `POST` | `/v1/decisions` | OpenAI Decisions API; regular HTTP workers and regular SGLang gRPC workers serve it, other backends answer `501`. See [Decisions](openai.md#decisions) |
| `POST` | `/v1/embeddings` | OpenAI-compatible embeddings endpoint |
| `POST` | `/v1/interactions` | Gemini Interactions API; only the Gemini provider backend serves it, other backends answer `501` |
| `POST` | `/v1/systemone` | Native System One API; only regular HTTP workers serve it, deployments without a regular HTTP worker for the model answer `501` |

`/rerank` and `/v1/rerank` score a request's `documents` against its `query` and answer the ranked results. Regular HTTP workers serve the request at their own `POST /v1/rerank`, and a disaggregated HTTP deployment serves it through its prefill/decode pair (see [PD disaggregation](../../concepts/routing/pd-disaggregation.md)); other backends answer `501` with code `rerank_not_implemented`.

The native `/rerank` body requires `model` (which selects the worker), a non-blank `query`, and a non-empty `documents` array of strings, and takes an optional `top_k` (at least 1; all documents when absent), `return_documents` (default `true`), `rid` (a request ID, string or array), and `user`; a body that fails these checks answers `400`.

`/v1/rerank` takes the OpenAI-compatible shape: the same fields with `model` optional and the limit spelled `top_n` (the native `top_k` spelling is read too; absent and `0` both mean all documents).

`/v1/rerank` routes by `model` exactly as `/v1/embeddings` does. A body that names a model goes to a worker serving it, and answers `404` with code `model_not_found` when none does; a body that names no model is routed as the unknown-model wildcard, to any worker. The wildcard is not written to the body forwarded to the worker, so a single-model rerank server applies its own model instead of refusing a name it does not serve.

When a regular HTTP worker answers, the gateway builds the response itself rather than passing the worker's through: `object` (`"rerank"`), `model` (the request's model, the canonical one when the request addressed a `--model-alias` name, and `unknown` when a `/v1/rerank` body named none), `id` (the request's `rid`, else `null`), `created`, `usage` (always `null`; the worker's usage is not read), and `results`, each result carrying `score`, `index` (the document's position in the request), the `document` text when requested and the worker echoed one, and the worker's `meta_info` when it sent one. The results keep the worker's order — rerank workers answer sorted by score, highest first — and the gateway itself truncates to `top_k`/`top_n` and drops documents when `return_documents` is `false`, whatever the worker made of those fields.

The worker's answer is read in any HTTP rerank contract's shape: a bare JSON array of results, or a Cohere-style `{"results": [...]}` object, with the score spelled `score` or `relevance_score` and the document spelled `document` or `text`, given as a string or as a `{"text": ...}` object. Everything else in the worker's body (its `id`, `model`, and `usage`) is ignored. A success-status worker body in none of these shapes answers `500` with code `rerank_response_build_failed`, and one larger than `--max-payload-size` answers `502` with code `upstream_response_too_large`; a worker error status passes through unchanged.

`/v1/systemone` relays a non-streaming System One request to a regular HTTP worker's own `/v1/systemone`. Request fields SMG does not know are forwarded unchanged, and the worker's response — status, headers, and body, backend errors included — is returned as is.

Before relaying, SMG checks that `model` names a served model (unknown models answer `404`), that `state` is text or structured JSON, and that `questions` maps at least one name to a question, with at least one criterion on each `score` question; a body that fails these checks answers `400`.

These pages cover the other inference endpoints, which use the same auth:

- [OpenAI-Compatible API](openai.md): `/v1/chat/completions`, `/v1/completions`, `/v1/decisions`, `/v1/audio/transcriptions`, and the `/v1/realtime` routes
- [Responses API](responses.md): `/v1/responses` and `/v1/conversations`
- [Anthropic Messages API](messages.md): `/v1/messages` and `/v1/messages/count_tokens`

---

## Control-Plane Endpoints

These endpoints are for gateway operations and administration. They use the control-plane auth described in [Auth Model](#auth-model).

By default they answer on the data listener (`--port`), alongside the serving routes. With `--admin-port` set, the endpoints in every section below and the [RL control-plane routes](#rl-control-plane-endpoints) answer on a dedicated admin listener instead, and the data listener answers `404` for them. The probe routes `/health`, `/liveness`, and `/readiness` answer on both listeners, so the admin listener can be checked on its own. The public `/loads` stays on the data listener, while its deprecated control-plane alias `/get_loads` moves with the rest.

The split changes where the routes answer, not who may call them: the auth above applies on the admin listener unchanged. See the [Listeners reference](../configuration.md#listeners) and [Admin Listener](../../getting-started/control-plane-auth.md#admin-listener).

### Worker Management

| Method | Path |
|---|---|
| `GET`, `POST` | `/workers` |
| `GET`, `PUT`, `PATCH`, `DELETE` | `/workers/{worker_id}` |

### Tokenizer Management

| Method | Path |
|---|---|
| `GET`, `POST` | `/v1/tokenizers` |
| `GET`, `DELETE` | `/v1/tokenizers/{tokenizer_id}` |
| `GET` | `/v1/tokenizers/{tokenizer_id}/status` |

### Parser Utilities

| Method | Path |
|---|---|
| `POST` | `/parse/function_call` |
| `POST` | `/parse/reasoning` |

### WASM Management

| Method | Path |
|---|---|
| `GET`, `POST` | `/wasm` |
| `DELETE` | `/wasm/{module_uuid}` |

The WASM routes need `--enable-wasm`; without it they answer `503`. Their refusals use the control plane's `{"error", "code"}` envelope; see [WASM Module Management](admin.md#wasm-module-management) for the statuses and codes.

### Profiling

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/start_profile` | Start the engine profiler on every worker, or only on the worker whose URL equals `url` in the optional JSON body |
| `POST` | `/stop_profile` | Stop the profiler, with the same optional `url` |

`/start_profile` also accepts the engine's profiler options (`output_dir`, `start_step`, `num_steps`, `activities`, `with_stack`, `record_shapes`, `profile_by_stage`). SMG sends them as the JSON body of an HTTP worker's own `/start_profile`, or through the `StartProfile` RPC to SGLang, TokenSpeed, and vLLM gRPC workers. A TokenSpeed [EPD](../../concepts/routing/pd-disaggregation.md#epd-encode-prefill-decode) encode worker refuses `StartProfile` and `StopProfile`, and is reported under `failed` (`UNIMPLEMENTED`). TensorRT-LLM gRPC, MLX gRPC, and ZMQ workers do not support profiling and are reported as failed. Both routes answer `200` when every targeted worker succeeds, `206` with `successful` and `failed` lists when any fails, and `404` when no worker matches.

A vLLM gRPC worker starts and stops the engine's own torch profiler on every rank; the trace directory and the profiler options are fixed by the engine's launch configuration and cannot be set per request. A `/start_profile` that sets any of the options above therefore lists vLLM workers under `failed`; to profile them, send a start that sets none of these options (a body with only `url` is fine).

### Diagnostics

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/inflight` | The requests this gateway process is serving right now: its instance id and their sequence numbers |
| `POST` | `/heap_profile` | A jemalloc heap profile of this gateway process, written under `--jemalloc-prof-dir` (a `jemalloc-profiling` build; `501` otherwise) |
| `GET` | `/config` | The effective configuration with every secret redacted: the build identity, the secret sources, and the configuration with its keys named by fingerprint |

All three are control-plane routes, and `GET /config` is stricter: it is mounted only with control-plane auth configured (`404` otherwise) and read by the admin role alone. See [Diagnostics](admin.md#diagnostics) for the response shapes, the preconditions a heap profile needs, and the configuration view's redaction rules.

### Cache and Load Utilities

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/flush_cache` | Flush the KV prefix cache on every worker that supports it |
| `GET` | `/loads` | Fleet engine load from the gateway's cached load snapshot (optional `?model=` filter) |
| `GET` | `/get_loads` | Deprecated alias of `/loads` |

`/loads` is registered with the public routes and needs no auth; `/flush_cache` and the deprecated `/get_loads` alias are control-plane routes. See [Cache Management](admin.md#cache-management) for request and response details.

---

## HA / Mesh Management Endpoints

SMG has no HTTP endpoints for mesh management; a request to any `/ha/*` path gets `404`. Mesh routers talk to each other over gRPC on `--mesh-port`. To inspect a mesh, use the mesh metrics, the router logs, and each router's `GET /workers`; see [High Availability](../../concepts/architecture/high-availability.md#monitoring).

---

## RL Control Plane Endpoints

Mounted only when SMG starts with `--enable-rl`; without it, every `/v1/rl/*` path returns `404`. These routes use the same auth as the other control-plane routes (see [Auth Model](#auth-model)).

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/v1/rl/workers` | List workers with engine, parallelism, health, weight version, and capabilities |
| `GET` | `/v1/rl/workers/{id}` | One worker (`404 worker_not_found` for an unknown ID) |
| `GET`, `POST` | `/v1/rl/workers/{id}/engine/{path}` | Forward one engine-native request to one worker's control endpoint; the response status mirrors the engine's |
| `GET`, `POST` | `/v1/rl/engine/{path}?selector=...` | Send the same request to every worker matching a label selector; `200` when every target succeeds, `207` with per-worker `failed[]` otherwise |

See [RL Control Plane](../../getting-started/rl-control-plane.md) for request and response shapes, selectors, errors, timeouts, metrics, and the `smg.rl` Python client.

---

## Quick Examples

Tokenize:

```bash
curl -X POST http://localhost:30000/v1/tokenize \
  -H "Content-Type: application/json" \
  -d '{"model":"meta-llama/Llama-3.1-8B-Instruct","prompt":"hello"}'
```

List workers. `ADMIN_TOKEN` is an admin control-plane API key or JWT when control-plane auth is configured, and the shared `--api-key` otherwise. With `--admin-port` set, this request and the tokenizer listing below go to the admin listener's port instead of the data port used here:

```bash
curl http://localhost:30000/workers \
  -H "Authorization: Bearer ${ADMIN_TOKEN}"
```

List tokenizers:

```bash
curl http://localhost:30000/v1/tokenizers \
  -H "Authorization: Bearer ${ADMIN_TOKEN}"
```

---

## Related Pages

- [Admin API Reference](admin.md)
- [Configuration Reference](../configuration.md#control-plane-authentication)
- [Getting Started: Control Plane Auth](../../getting-started/control-plane-auth.md)
- [Getting Started: Control Plane Operations](../../getting-started/control-plane-operations.md)
