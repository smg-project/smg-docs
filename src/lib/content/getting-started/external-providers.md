---
title: External Providers
---

# External Providers

SMG can route requests to external LLM provider APIs (OpenAI, Anthropic, xAI, Google Gemini), acting as a unified gateway. This enables provider-agnostic applications, load balancing across providers, and centralized observability.

<div class="prerequisites" markdown>

#### Before you begin

- Completed the [Getting Started](index.md) guide
- An API key for at least one external provider

</div>

---

## Supported Providers

SMG serves each provider through a provider router. Register a worker with the provider's base URL, without a `/v1` suffix: SMG appends each API path itself (a worker URL ending in `/v1` produces paths such as `/v1/v1/chat/completions`).

| Provider | Worker URL | Router | Endpoints |
|----------|-----------|--------|-----------|
| OpenAI | `https://api.openai.com` | OpenAI-compatible | `/v1/chat/completions`, `/v1/responses`, Realtime API |
| xAI | `https://api.x.ai` | OpenAI-compatible | Same as OpenAI |
| Anthropic | `https://api.anthropic.com` | Anthropic | `/v1/messages` |
| Google Gemini | `https://generativelanguage.googleapis.com` | Gemini | `/v1/interactions`, non-streaming only |
| Any OpenAI-compatible API | The API's base URL | OpenAI-compatible | Same as OpenAI |

- **External workers.** SMG treats a worker as external when its URL host ends in `openai.com`, `x.ai`, `anthropic.com`, or `googleapis.com`, or when its spec sets `"runtime_type": "external"`. Set it for any other host, such as a proxy or another OpenAI-compatible API.
- **Router by model.** When SMG lists a worker's models at registration (see [Model Discovery](#model-discovery)), it tags each model with a provider from its ID. In [IGW mode](multiple-workers.md#dynamic-workers-with-igw-mode), a request for a `claude*` model goes to the Anthropic router, a `gemini*` model to the Gemini router, and every other model to the OpenAI-compatible router. With `--backend`, the gateway runs the one router you chose.
- **Upstream key header.** The OpenAI-compatible router picks the header from the worker URL: `x-api-key` plus `anthropic-version: 2023-06-01` for a URL that contains `anthropic`, `x-goog-api-key` for a URL that contains `googleapis.com`, and `Authorization: Bearer` for any other URL. The Anthropic router forwards the caller's key headers as sent, or attaches the stored key as `x-api-key` (see [API Key Handling](#api-key-handling)), and the Gemini router sends `x-goog-api-key`.
- **Gemini limits.** The Gemini router answers `501` for a streaming request, for a request that carries `previous_interaction_id`, and for a model request with `store` left at its default (`true`), so send `"store": false`.
- **Build features.** Each provider router is a Cargo feature of the gateway (`provider-openai`, `provider-anthropic`, `provider-gemini`), all in the default build: the pip wheels, the container images, and `cargo install smg` include them. A build without one refuses workers that need it, and `--backend` for it fails at startup.

---

## Quick Start

Register an external worker via IGW mode:

```bash
# Start SMG in IGW mode
smg --enable-igw
```

```bash
# Register an OpenAI worker
curl -X POST http://localhost:30000/workers \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://api.openai.com",
    "api_key": "sk-...",
    "runtime_type": "external"
  }'
```

While registering the worker, SMG lists the models the key can use (`GET https://api.openai.com/v1/models`) and routes those models to the worker.

Send a request through the gateway:

```bash
curl http://localhost:30000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-4o",
    "messages": [{"role": "user", "content": "Hello!"}]
  }'
```

---

## Model Discovery

### At registration

SMG lists an external worker's models while registering it, when it has a key for the worker:

1. The key is the provider's admin key variable (`OPENAI_ADMIN_KEY`, `XAI_ADMIN_KEY`, `ANTHROPIC_ADMIN_KEY`, or `GEMINI_ADMIN_KEY`, picked from the worker URL's host) if it is set, and otherwise the worker's `api_key`. Workers from `--worker-urls` take their `api_key` from `--api-key`.
2. SMG calls `GET <url>/v1/models`, sending the key as `x-api-key` for an `anthropic.com` URL and as `Authorization: Bearer` otherwise, and parses an OpenAI-format list (a `data` array of objects with an `id`).
3. It registers the worker with those models. IDs that differ only by a date suffix (such as `gpt-4o` and `gpt-4o-2024-08-06`) form one model: the shortest ID, with the others as its aliases.

If the call fails or returns no models, the worker isn't registered. Without any key, SMG registers the worker with no model list. In IGW mode, SMG routes requests to external workers by model, so a worker without a model list receives no traffic there. Under `--backend`, such a worker accepts any model, and callers send their own key.

### `GET /v1/models`

SMG supports fan-out model discovery across all registered external workers. When a caller sends a `GET /v1/models` request with a bearer token (or an `x-api-key` header), SMG:

1. Fans out the request to all healthy external workers concurrently
2. Forwards the caller's token to each upstream provider, in that provider's header format
3. Returns the first non-empty model inventory from the fanned-out upstream responses

```bash
curl http://localhost:30000/v1/models \
  -H "Authorization: Bearer sk-..."
```

This supports BYOK (bring your own key) — the caller's token is forwarded to the upstream providers, so each caller can discover models available under their own account. If no upstream returns models, or the token is one of the gateway's own keys (`--api-key` or `--tenant-api-key`), SMG answers with the models of its self-hosted workers instead. A request without a token also gets the self-hosted models only.

---

## API Key Handling

SMG supports two methods for providing API keys to external providers:

- **Stored key** — Set at worker registration time via the `api_key` field. Workers from `--worker-urls` get the `--api-key` value.
- **Caller key (BYOK)** — Passed by the caller in its request headers

How the two combine depends on the router:

| Router | Key sent upstream |
|--------|-------------------|
| OpenAI-compatible | The caller's `Authorization` header, else the stored key as a bearer token. On `/v1/responses`, a caller's `x-api-key` (worker URL containing `anthropic`) or `x-goog-api-key` (worker URL containing `googleapis.com`) is preferred over `Authorization`; `/v1/chat/completions` reads only `Authorization`. |
| Gemini | The caller's `x-goog-api-key`, else the caller's `Authorization` token, else the stored key |
| Anthropic | The caller's `x-api-key` or `Authorization`, forwarded as sent along with `anthropic-version` and `anthropic-beta`. When the caller sends neither, the stored key goes out as `x-api-key`, with `anthropic-version: 2023-06-01` added when the caller sent no version header. |

!!! note "Gateway keys stop at the gateway"
    With `--api-key` or `--tenant-api-key` set, every inference request must carry `Authorization: Bearer <gateway key>`. A matched gateway key ends at authentication: the `Authorization` header is removed, and an `x-api-key` or `x-goog-api-key` header whose value is a gateway key is removed with it, so the gateway key is never sent upstream. The provider receives the worker's stored key, just as when a caller sends no credential; a worker registered without a key is reached with no key.

    Callers can still bring their own provider key under gateway authentication by sending it as `x-api-key` or `x-goog-api-key` on the endpoints that read those headers (`/v1/responses`, `/v1/interactions`, `/v1/messages`). `/v1/chat/completions` reads only `Authorization`, which the gateway key occupies, so it always uses the stored key.

!!! warning "Caller keys can reach every registered provider"
    `GET /v1/models` sends the caller's token to every healthy external worker, whatever its provider. When the OpenAI-compatible router finds no worker for a requested model, it also refreshes the model lists of the healthy external workers with the caller's token before it answers. Register only providers you trust with your callers' keys.

---

## Provider Error Responses

When a provider refuses a request — on chat completions, Responses, Messages, Gemini Interactions, or a realtime call — SMG relays the refusal under the provider's status but removes the provider-side account identifiers from the body, so callers cannot learn which provider account the gateway uses:

- Phrases naming an organization (`organization org-…`) or a project (`project proj_…`) in a JSON body's `error.message` are removed, whatever their case.
- The message of an error whose `code` is `invalid_image_url` is replaced with `Invalid Image URL`.
- Everything else is relayed as the provider sent it: the error's `type` and `code`, the rest of the message, and any non-JSON body, which passes through unchanged.

The same scrubbing applies where a provider's error text reaches the caller indirectly: in the error frames of a realtime WebSocket session (see [Upstream Errors](../reference/api/openai.md#upstream-errors)) and in the failure message when a provider refuses a model call in SMG's MCP tool loop.

On chat completions, Messages, and realtime calls, the gateway logs a preview of the provider's original error at `warn`.

---

## Multiple Providers

Register workers for multiple providers to route across them by model name:

```bash
# Register OpenAI
curl -X POST http://localhost:30000/workers \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://api.openai.com",
    "api_key": "sk-..."
  }'

# Register xAI
curl -X POST http://localhost:30000/workers \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://api.x.ai",
    "api_key": "xai-..."
  }'
```

SMG sends each request to the worker whose model list contains the requested model:

```bash
# Routes to OpenAI
curl http://localhost:30000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-4o",
    "messages": [{"role": "user", "content": "Hello"}]
  }'

# Routes to xAI
curl http://localhost:30000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "grok-3",
    "messages": [{"role": "user", "content": "Hello"}]
  }'
```

Anthropic and Gemini models are served on their own APIs, not on `/v1/chat/completions`: send Anthropic models to `/v1/messages` and Gemini models to `/v1/interactions`. To serve one of these providers from a gateway, start it with `--backend anthropic` or `--backend gemini` (see [Cloud API Workers](multiple-workers.md#cloud-api-workers)).

---

## Next Steps

- [Multiple Workers](multiple-workers.md) — Load balancing, worker types, and IGW configuration options
- [Load Balancing](load-balancing.md) — Routing policies for distributing traffic across workers
- [Monitoring](monitoring.md) — Track request rates, latency, and worker health across providers
