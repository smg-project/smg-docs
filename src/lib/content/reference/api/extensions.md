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
| Public routes | No auth: `/health`, `/liveness`, `/readiness`, `/health_generate`, `/engine_metrics`, `/loads`, `/v1/models`, `/get_model_info`, and `/get_server_info` |
| Protected routes | `Authorization: Bearer <key>` with the shared `--api-key` or a per-tenant `--tenant-api-key` key; open when neither is set (`/v1/tokenize`, `/generate`, `/v1/chat/completions`, and the other inference routes) |
| Control-plane routes | With [control-plane auth](../../getting-started/control-plane-auth.md) configured (`--control-plane-api-keys`, or `--jwt-issuer` with `--jwt-audience`), a control-plane API key or JWT with the admin role. Otherwise only the shared `--api-key` is accepted: per-tenant keys are rejected, a gateway with tenant keys but no `--api-key` answers every control-plane request with `401`, and a gateway with no keys at all leaves these routes open |

With control-plane auth configured, a missing or invalid credential gets `401` and a valid credential without the admin role gets `403`. The shared `--api-key` is not accepted on control-plane routes in that mode. If control-plane auth fails to initialize at startup (for example, when OIDC discovery fails), SMG logs an error and applies the `--api-key` rules instead.

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
| `GET` | `/v1/models` | List models |
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
| `POST` | `/rerank` | Native rerank endpoint |
| `POST` | `/v1/rerank` | OpenAI-style rerank endpoint |
| `POST` | `/v1/messages` | Messages endpoint |
| `POST` | `/v1/messages/count_tokens` | Count the input tokens of a Messages request; see [Count Tokens](messages.md#count-tokens) |
| `POST` | `/v1/classify` | Classification endpoint |
| `POST` | `/v1/embeddings` | OpenAI-compatible embeddings endpoint |
| `POST` | `/v1/interactions` | Gemini Interactions API; only the Gemini provider backend serves it, other backends answer `501` |

These pages cover the other inference endpoints, which use the same auth:

- [OpenAI-Compatible API](openai.md): `/v1/chat/completions`, `/v1/completions`, `/v1/audio/transcriptions`, and the `/v1/realtime` routes
- [Responses API](responses.md): `/v1/responses` and `/v1/conversations`
- [Anthropic Messages API](messages.md): `/v1/messages` and `/v1/messages/count_tokens`

---

## Control-Plane Endpoints

These endpoints are for gateway operations and administration. They use the control-plane auth described in [Auth Model](#auth-model).

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

The WASM routes need `--enable-wasm`; without it they answer `500`.

### Profiling

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/start_profile` | Start the engine profiler on every worker, or only on the worker whose URL equals `url` in the optional JSON body |
| `POST` | `/stop_profile` | Stop the profiler, with the same optional `url` |

`/start_profile` also accepts the engine's profiler options (`output_dir`, `start_step`, `num_steps`, `activities`, `with_stack`, `record_shapes`, `profile_by_stage`). SMG sends them as the JSON body of an HTTP worker's own `/start_profile`, or through the `StartProfile` RPC to SGLang and TokenSpeed gRPC workers. gRPC vLLM, TensorRT-LLM, and MLX workers and ZMQ workers do not support profiling and are reported as failed. Both routes answer `200` when every targeted worker succeeds, `206` with `successful` and `failed` lists when any fails, and `404` when no worker matches.

### Cache and Load Utilities

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/flush_cache` | Flush the KV prefix cache on every worker that supports it |
| `GET` | `/loads` | Fleet engine load from the gateway's cached load snapshot (optional `?model=` filter) |
| `GET` | `/get_loads` | Deprecated alias of `/loads` |

`/loads` is registered with the public routes and needs no auth; `/flush_cache` and the deprecated `/get_loads` alias are control-plane routes. See [Cache Management](admin.md#cache-management) for request and response details.

---

## HA / Mesh Management Endpoints

SMG has no HTTP endpoints for mesh management. The `/ha/*` routes of earlier releases were removed in v1.5.0 (smg-project/smg#1476), so a request to one gets `404`. Mesh routers talk to each other over gRPC on `--mesh-port`. To inspect a mesh, use the mesh metrics, the router logs, and each router's `GET /workers`; see [High Availability](../../concepts/architecture/high-availability.md#monitoring).

---

## RL Control Plane Endpoints

Mounted only when SMG starts with `--enable-rl`; without it, every `/v1/rl/*` path returns `404`. These routes use the same auth as the other control-plane routes (see [Auth Model](#auth-model)).

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/v1/rl/workers` | List workers with engine, parallelism, health, weight version, and capabilities |
| `GET` | `/v1/rl/workers/{id}` | One worker (`404 worker_not_found` for an unknown ID) |
| `GET`, `POST` | `/v1/rl/workers/{id}/engine/{path}` | Forward one engine-native request to one HTTP worker; the response status mirrors the engine's |
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

List workers. `ADMIN_TOKEN` is an admin control-plane API key or JWT when control-plane auth is configured, and the shared `--api-key` otherwise:

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
