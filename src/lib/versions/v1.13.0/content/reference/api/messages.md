---
title: Anthropic Messages API
---

# Anthropic Messages API

SMG serves the Anthropic Messages API at `/v1/messages` and counts input tokens at `/v1/messages/count_tokens`. What happens to a request depends on the backend: with gRPC workers SMG implements the API itself, with HTTP workers it forwards the request to an engine that implements the Messages API, and with the Anthropic backend it forwards the request to the Anthropic API.

---

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/v1/messages` | Create a message. Set `"stream": true` to receive Server-Sent Events. |
| `POST` | `/v1/messages/count_tokens` | Count the input tokens of a Messages request without generating. |

Both endpoints go through the same authentication and admission control as the other inference endpoints.

---

## Supported Backends

| Backend | Configure with | `/v1/messages` | `/v1/messages/count_tokens` |
|---------|----------------|----------------|-----------------------------|
| [gRPC workers](../../getting-started/grpc-workers.md) | `grpc://` worker URLs | SMG renders, tokenizes, and parses ([details](#grpc-and-zmq-workers)) | Counted by SMG itself ([details](#backend-support)) |
| [ZMQ workers](../../getting-started/zmq-workers.md) | `ipc://` worker URLs | Same as gRPC workers | Same as gRPC workers |
| HTTP workers | `http://` or `https://` worker URLs | Forwarded to the worker | Forwarded to one worker |
| [HTTP PD](../../getting-started/pd-disaggregation.md) | `--pd-disaggregation` with HTTP workers | Prefill/decode dual dispatch | Forwarded to one prefill worker |
| Anthropic API | `--backend anthropic` | Forwarded to the Anthropic API, with an optional SMG-run [MCP tool loop](#mcp-tools) | `501 Not Implemented` |

HTTP workers must implement the Messages API themselves; SMG forwards the request without translating it.

In IGW mode (`--enable-igw`), SMG picks the backend per request from the workers that serve the requested `model`. A model served by an external Anthropic worker goes to the Anthropic API backend (see [External Providers](../../getting-started/external-providers.md)). The other external providers answer `/v1/messages` with `501`.

---

## Authentication

SMG's own authentication is separate from the credentials it passes to backends:

| Hop | Headers | Behavior |
|-----|---------|----------|
| Client to SMG | `Authorization: Bearer <key>` | Checked when `--api-key` or `--tenant-api-key` is set (both the Rust binary and the pip `smg launch` accept `--tenant-api-key`). SMG does not read `x-api-key`. A missing or unknown bearer token gets `401 Unauthorized`. |
| SMG to HTTP workers | `Authorization` | The client's header is forwarded with the other [allowlisted headers](#forwarded-headers); `x-api-key` is not. See [Authentication](../../concepts/security/authentication.md) for worker API keys. |
| SMG to the Anthropic API | `x-api-key`, `Authorization` | Forwarded exactly as the client sent them. SMG adds no key of its own, so the client supplies the Anthropic key. |

Anthropic SDKs send their key in `x-api-key`, which the Anthropic API backend passes upstream. When gateway authentication is on, clients must also send the gateway key as `Authorization: Bearer <key>`; on the Anthropic API backend that header is forwarded to Anthropic as well.

!!! note "Browser clients"
    Cross-origin browser calls need `--cors-allowed-origins`: without it SMG sends no `access-control-allow-*` headers, so the browser refuses the call. With explicit origins, cross-origin requests may carry the `Content-Type`, `Authorization`, `anthropic-version`, `anthropic-beta`, `traceparent`, and `tracestate` headers; `x-api-key` is not on that list. A lone `*` allows any origin and header.

---

## Create a Message

```text
POST /v1/messages
```

### Request Body

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `model` | string | Yes | Model ID. Must not be empty. |
| `messages` | array | Yes | At least one message, each with a `role` (`user`, `assistant`, or `system`) and `content` (a string or an array of content blocks). |
| `max_tokens` | integer | Yes | Maximum number of tokens to generate. `0` is accepted; the response depends on the backend — see [`max_tokens` of 0](#max_tokens-of-0). |
| `system` | string or array | No | System prompt, as a string or an array of `text` blocks. |
| `stream` | boolean | No | Stream the response as Server-Sent Events. |
| `temperature` | number | No | Sampling temperature, 0 to 1. |
| `top_p` | number | No | Nucleus sampling, 0 to 1. |
| `top_k` | integer | No | Sample only from the top K tokens. |
| `stop_sequences` | array of strings | No | Sequences that stop generation; each must contain non-whitespace text. |
| `tools` | array | No | Tool definitions; see [Tool Use](#tool-use). |
| `tool_choice` | object | No | `{"type": "auto"}`, `{"type": "any"}`, `{"type": "tool", "name": "..."}`, or `{"type": "none"}`. Every value except `none` requires `tools`, and `tool` must name one of them. |
| `thinking` | object | No | `{"type": "enabled", "budget_tokens": N}`, `{"type": "adaptive"}`, or `{"type": "disabled"}`. `budget_tokens` must be at least 1024 and, unless `anthropic-beta` includes `interleaved-thinking`, less than `max_tokens`. `enabled` and `adaptive` also take `display` (`summarized` or `omitted`). |
| `metadata` | object | No | Request metadata (`user_id`). |
| `service_tier` | string | No | `auto` or `standard_only`. |
| `container` | object | No | Container for code execution (beta). |
| `mcp_servers` | array | No | MCP servers for the request (beta). Required when `tools` has an `mcp_toolset` entry; see [MCP Tools](#mcp-tools). |
| `output_config` | object | No | `effort` is the reasoning effort the prompt renders with on gRPC and ZMQ workers (a string, or a number read as its text); its other keys, such as `format`, ride through to HTTP workers and the Anthropic API. See [gRPC and ZMQ Workers](#grpc-and-zmq-workers). |
| `rid` | string | No | Extension: request ID passed to the backend for log correlation. |

SMG validates the body before routing and answers `400` when a required field is missing or a rule above is broken. It also refuses the request shapes the public API refuses: a message with empty content (except a final assistant prefill, which may be empty but must not end in whitespace), an empty `text` block, a `tool_use` call without its `tool_result` in the next user turn — or a `tool_result` nobody asked for, or one answering the same id twice — duplicate tool names, a custom tool name outside `^[a-zA-Z0-9_-]{1,128}$`, an `input_schema` whose `type` is not `object`, and a custom tool definition with fields the public API does not take. The one exception is a body that the HTTP router streams through unparsed; see [HTTP and PD Workers](#http-and-pd-workers). Fields SMG does not model, such as `context_management`, are kept and forwarded to HTTP workers and the Anthropic API.

Content blocks in `messages` may be `text`, `image`, `document`, `tool_use`, `tool_result`, `thinking`, `redacted_thinking`, `server_tool_use`, `search_result`, `web_search_tool_result`, `tool_search_tool_result`, or `tool_reference`. Any other block type is rejected with `400`. A `system`-role message inside `messages` is accepted, because some clients send one mid-conversation.

### Example Request

=== "Self-hosted model"

    ```bash
    curl http://localhost:30000/v1/messages \
      -H "Content-Type: application/json" \
      -H "anthropic-version: 2023-06-01" \
      -d '{
        "model": "meta-llama/Llama-3.1-8B-Instruct",
        "max_tokens": 1024,
        "system": "You are a concise assistant.",
        "messages": [
          {"role": "user", "content": "What is the meaning of life?"}
        ]
      }'
    ```

=== "Anthropic API backend"

    ```bash
    curl http://localhost:30000/v1/messages \
      -H "Content-Type: application/json" \
      -H "x-api-key: $ANTHROPIC_API_KEY" \
      -H "anthropic-version: 2023-06-01" \
      -d '{
        "model": "claude-sonnet-4-20250514",
        "max_tokens": 1024,
        "messages": [
          {"role": "user", "content": "What is the meaning of life?"}
        ]
      }'
    ```

### Response

```json
{
  "id": "msg_abc123",
  "type": "message",
  "role": "assistant",
  "content": [
    {"type": "text", "text": "There is no single answer..."}
  ],
  "model": "meta-llama/Llama-3.1-8B-Instruct",
  "stop_reason": "end_turn",
  "stop_sequence": null,
  "usage": {
    "input_tokens": 27,
    "output_tokens": 112,
    "cache_creation_input_tokens": 0,
    "cache_read_input_tokens": 0
  }
}
```

With gRPC workers SMG builds this response ([details](#grpc-and-zmq-workers)). With HTTP workers and the Anthropic API it comes from the backend.

### `max_tokens` of 0

A `max_tokens` of `0` passes validation. On gRPC and ZMQ workers, SMG answers the request itself without generating: an empty message with `stop_reason` of `max_tokens`, whose `usage` reports the prompt's token count, counted exactly as [`/v1/messages/count_tokens`](#count-tokens) counts it. No worker takes part, and no tenant rate-limit budget is reserved, though the request still counts in the gateway's request metrics.

```json
{
  "id": "msg_abc123",
  "type": "message",
  "role": "assistant",
  "content": [],
  "model": "meta-llama/Llama-3.1-8B-Instruct",
  "stop_reason": "max_tokens",
  "stop_sequence": null,
  "usage": {
    "input_tokens": 27,
    "output_tokens": 0,
    "cache_creation_input_tokens": 0,
    "cache_read_input_tokens": 0
  }
}
```

With `"stream": true`, the answer is a three-event stream: `message_start` with the empty message — its `usage` already carries the prompt's token count — then `message_delta` with the stop reason and the usage counts, and `message_stop`.

A request whose prompt cannot be counted is refused as `/v1/messages/count_tokens` refuses it — an unknown model gets `404`, a request that does not render gets `400`, and the other refusals under [Backend Support](#backend-support) apply unchanged. When the model's workers advertise a context window, a prompt with more tokens than the smallest advertised window is refused with `400` `context_length_exceeded`, as a generating request would be.

On HTTP and PD workers, SMG refuses `max_tokens: 0` with `400` (code `max_tokens_zero_not_relayed`) before any worker sees the request: the relay forwards a Messages request as sent and renders no prompt, so it cannot build the empty message. As with the other request rules, a body the HTTP router streams through unparsed reaches the worker unvalidated.

The Anthropic API backend forwards the request like any other and relays the upstream response.

In IGW mode, a `max_tokens: 0` request for a model served over both gRPC and HTTP always goes to a gRPC router, so it gets the empty message; a model only HTTP workers serve gets the relay's `400`.

### Tool Use

Define each tool with a JSON Schema `input_schema`:

```json
{
  "tools": [
    {
      "name": "get_weather",
      "description": "Get the current weather for a city",
      "input_schema": {
        "type": "object",
        "properties": {"city": {"type": "string"}},
        "required": ["city"]
      }
    }
  ],
  "tool_choice": {"type": "auto"}
}
```

A tool call comes back as a `tool_use` block, with `stop_reason` set to `tool_use`. Its `caller` names who issued the call: a call the model issued itself is `{"type": "direct"}`.

```json
{
  "content": [
    {
      "type": "tool_use",
      "id": "toolu_0b7e5f1c9a2d4e6f8a1b3c5d",
      "name": "get_weather",
      "input": {"city": "Paris"},
      "caller": {"type": "direct"}
    }
  ],
  "stop_reason": "tool_use"
}
```

Return the result in the next `user` message as a `tool_result` block whose `tool_use_id` is the `id` of the call.

---

## Streaming

Set `"stream": true` to receive the response as Server-Sent Events. Each event has an `event:` line naming its type and a `data:` line with the JSON payload. The stream ends with `message_stop`; there is no `data: [DONE]` line.

| Event | Payload |
|-------|---------|
| `message_start` | The message, with empty `content` |
| `content_block_start` | A new content block at `index`: `thinking`, `text`, or `tool_use` |
| `content_block_delta` | A `text_delta`, `thinking_delta`, or `input_json_delta` (a fragment of the tool input JSON) for the block at `index` |
| `content_block_stop` | The block at `index` is complete |
| `message_delta` | `stop_reason`, `stop_sequence`, and the final `usage` |
| `message_stop` | End of the message |
| `ping` | Keep-alive. SMG writes one itself after 15 seconds of upstream silence (`--sse-keepalive-secs`; `0` disables), on every backend, and relays upstream pings |
| `error` | A failure after the stream started; see [Errors](#errors) |

HTTP workers and the Anthropic API produce their own events, which SMG relays; the Anthropic API can send block and delta types beyond this table. A gRPC worker's stream for a short text answer looks like this:

```text
event: message_start
data: {"type":"message_start","message":{"id":"msg_abc123","type":"message","role":"assistant","content":[],"model":"meta-llama/Llama-3.1-8B-Instruct","stop_reason":null,"stop_sequence":null,"usage":{"input_tokens":0,"output_tokens":0,"cache_creation_input_tokens":0,"cache_read_input_tokens":0}}}

event: content_block_start
data: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}

event: content_block_delta
data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"There is no single answer"}}

event: content_block_stop
data: {"type":"content_block_stop","index":0}

event: message_delta
data: {"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":112,"input_tokens":27,"cache_creation_input_tokens":0,"cache_read_input_tokens":0}}

event: message_stop
data: {"type":"message_stop"}
```

---

## Count Tokens

```text
POST /v1/messages/count_tokens
```

Returns the number of input tokens a Messages request would use, without generating. Who counts depends on the backend: on gRPC and ZMQ workers, SMG counts the tokens itself from the model's tokenizer; on HTTP workers, it forwards the request to a worker that implements the endpoint and relays the worker's response. See [Backend Support](#backend-support).

### Request Body

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `model` | string | Yes | Model ID; selects the worker (HTTP) or the tokenizer (gRPC/ZMQ). |
| `messages` | array | Yes | Conversation, in the same format as `/v1/messages`. |
| `system` | string or array | No | System prompt. |
| `tools` | array | No | Tool definitions. |
| `tool_choice` | object | No | Tool selection. |
| `thinking` | object | No | Thinking configuration. |
| `output_config` | object | No | `effort` renders into the count on gRPC and ZMQ workers, exactly as `/v1/messages` renders it; its other keys ride through. |

There is no `max_tokens`. Any other field, such as `context_management` or `mcp_servers`, is forwarded unchanged to HTTP workers; on gRPC and ZMQ backends such fields are accepted but take no part in the count, which uses only the fields above.

### Example

```bash
curl http://localhost:30000/v1/messages/count_tokens \
  -H "Content-Type: application/json" \
  -H "anthropic-version: 2023-06-01" \
  -d '{
    "model": "meta-llama/Llama-3.1-8B-Instruct",
    "system": "You are a concise assistant.",
    "messages": [
      {"role": "user", "content": "What is the meaning of life?"}
    ]
  }'
```

```json
{
  "input_tokens": 27
}
```

### Backend Support

| Deployment | Behavior |
|------------|----------|
| HTTP workers | Routed like any other request (routing policy, retries, circuit breaker, load tracking) to one worker's `/v1/messages/count_tokens`. `404` (`model_not_found`) when no worker serves the model. |
| HTTP PD | Sent to one prefill worker. No decode worker is involved and no bootstrap fields are added. `503` (`no_prefill_servers`) when no prefill worker is available. |
| gRPC or ZMQ workers | Answered by SMG itself from the model's tokenizer; no worker takes part, so regular, PD (`--pd-disaggregation`), and EPD (`--epd-disaggregation`) deployments all serve it. See below. |
| Anthropic API backend | `501 Not Implemented` |
| IGW mode | A model with an external worker goes to that provider's backend (`501`). Otherwise any of the model's self-hosted pools answers — gRPC, ZMQ, HTTP, and PD alike, a disaggregated pool qualifying by its prefill workers alone. A model no pool serves, including an unknown model, gets `404` (`model_not_found`). |

On gRPC and ZMQ backends, SMG renders the request through the model's chat template exactly as `/v1/messages` renders it — the same tool filtering by `tool_choice` and the same media placeholders — and returns the rendered prompt's token count. Media is not fetched: an image counts as the placeholder the template writes for it, and a document counts as its text (a document source with no text is refused, as `/v1/messages` refuses it).

That path refuses a request the way `/v1/messages` would. An unknown model gets `404` (`model_not_found`), even when a removed worker's tokenizer is still registered.

A served model whose tokenizer the gateway is still loading gets `503` (`tokenizer_not_ready`) with a `Retry-After: 1` header. A served model whose tokenizer will not load on its own — no path to load it from, the load failed, or the tokenizer was removed — gets `503` (`tokenizer_load_failed`) with no `Retry-After` and a message naming the reason. Retrying changes nothing until a tokenizer registers for the model, either by a worker re-registering with a usable path or by `POST /v1/tokenizers`. Under `--disable-tokenizer-autoload`, a served model with no tokenizer and no such load failure gets `500` (`tokenizer_not_found`).

A request that does not render gets `400` (`process_messages_failed`), and a rendered prompt that fails to encode gets `500` (`tokenization_failed`).

---

## Request Headers

Headers specific to the Messages API:

| Header | Effect |
|--------|--------|
| `X-SMG-MCP` | `enabled` makes SMG run the MCP tool loop itself for a request whose `tools` include an `mcp_toolset` entry, on the Anthropic API backend. See [MCP Tools](#mcp-tools) and [MCP](../../concepts/extensibility/mcp.md). Other backends ignore it, and SMG does not forward it. |
| `anthropic-version` | Checked on both endpoints: a value that is not a real calendar date of 2023 or later (`YYYY-MM-DD`) is refused with `400` (`invalid_anthropic_version`); a request without the header is served as the current version. The value selects nothing in SMG itself. Forwarded to HTTP and PD workers and to the Anthropic API. |
| `anthropic-beta` | Read only for the `interleaved-thinking` beta, which lifts the thinking-budget rule (see [Request Body](#request-body)). Forwarded to HTTP and PD workers and to the Anthropic API. |
| `x-api-key` | Not read by SMG. Forwarded only to the Anthropic API; see [Authentication](#authentication). |

### Forwarded Headers

For HTTP and PD workers, SMG forwards only these request headers (it sets `Content-Type` itself):

| Header | Purpose |
|--------|---------|
| `Authorization` | Credentials for the worker; see [Authentication](#authentication) |
| `anthropic-version`, `anthropic-beta` | API version and beta features for engines that serve `/v1/messages` |
| `x-request-id`, `x-correlation-id`, and any header starting with `x-request-id-` | Request correlation |
| `traceparent`, `tracestate` | W3C trace context |
| `x-smg-routing-key` | SMG routing hint |

Every other request header, including `x-api-key`, is dropped. The Anthropic API backend forwards a different set: `Authorization`, `x-api-key`, `anthropic-version`, and `anthropic-beta`, and nothing else. gRPC and ZMQ workers receive a tokenized request, not HTTP headers.

---

## gRPC and ZMQ Workers

With gRPC workers (SGLang, vLLM, TensorRT-LLM, TokenSpeed, or MLX), and ZMQ workers that use the same pipeline, SMG implements the Messages API itself. It renders the conversation with the model's chat template, tokenizes it, sends token IDs to the engine, and builds the Anthropic response from the generated tokens. The pipeline serves regular, PD (`--pd-disaggregation`), and EPD (`--epd-disaggregation`) deployments. SMG needs the model's tokenizer (`--model-path`); see [gRPC Workers](../../getting-started/grpc-workers.md).

| Request field | Handling |
|---------------|----------|
| `system`, `messages` | Rendered with the chat template. Text and `image` blocks in user turns, `tool_use` and `thinking` blocks in assistant turns, and `tool_result` blocks are converted. A `system`-role message inside `messages` keeps its position. Images go through SMG's [multimodal pipeline](../../concepts/architecture/multimodal.md). |
| `max_tokens`, `temperature`, `top_p`, `top_k` | Sent to the engine as sampling parameters. A `max_tokens` of `0` never reaches the engine; see [`max_tokens` of 0](#max_tokens-of-0). |
| `stop_sequences` | Sent as stop strings to vLLM, TensorRT-LLM, and TokenSpeed gRPC workers. For SGLang gRPC workers and ZMQ workers, SMG matches them itself and sends only single-token stops to the engine, as stop token IDs. |
| `tools` | Custom tools (those with an `input_schema`) are passed to the chat template and the tool parser; with a `tool_choice` of `tool`, only that tool is passed to the chat template. Other tool types, such as `mcp_toolset`, bash, text editor, web search, and tool search, are dropped. |
| `tool_choice` | Mapped to Chat Completions semantics: `auto` to `auto`, `any` to `required`, `tool` to that function, `none` to `none`. SMG enforces `any` and `tool` with constrained decoding. |
| `thinking` | `enabled` and `adaptive` turn on the chat template's thinking mode and run the model's reasoning parser. `disabled` turns thinking mode off. Without `thinking`, the template default applies. `budget_tokens` is checked against the rules under [Request Body](#request-body) but not sent to the engine, and `display` is not used. |
| `output_config`, `output_format` | `output_config.effort` is rendered into the prompt as the chat template's reasoning effort. A request that asks for a structured output format — `output_format`, or `format` inside `output_config` — is refused with `400` (`unsupported_output_format`): this pipeline cannot constrain the answer to a format. |
| `metadata`, `service_tier`, `container`, `mcp_servers`, extra fields such as `context_management` | Not sent to the engine. |

MLX workers answer `400` to a request with images, with `stop_sequences`, or with a `tool_choice` of `any` or `tool`.

How the response is built:

- **Tool calls and reasoning** are extracted by SMG's tool and reasoning parsers, which SMG picks for the model or you set with `--tool-call-parser` and `--reasoning-parser`. A `thinking` block comes first, then text, then `tool_use` blocks. Thinking blocks carry an empty `signature`.
- **`tool_use` ids** use Anthropic's `toolu_` prefix: a parser id of the form `call_<suffix>` is returned as `toolu_<suffix>`, the same in streaming and non-streaming responses. Model-specific id formats are returned unchanged.
- **`stop_reason`** is `tool_use` when the output has tool calls, `stop_sequence` when one of `stop_sequences` ended generation (the matched string is in `stop_sequence`), `max_tokens` when the token limit was reached, and `end_turn` otherwise.
- **`usage`** reports `cache_creation_input_tokens` and `cache_read_input_tokens` as `0`, never `null`. In a stream, `message_start` carries zero counters, and the final `message_delta` carries `output_tokens` and, once the engine has reported it, `input_tokens`.
- **Context window**: when the selected worker advertises its context length, SMG rejects a prompt with more tokens than the window with `400` `context_length_exceeded` before dispatch. In PD mode the smaller of the prefill and decode windows applies. Whether the prompt plus `max_tokens` must fit is left to the engine.
- **Tenant rate limiting**, when enabled, covers this endpoint; see [Tenant Rate Limiting](../tenant-rate-limiting.md).

---

## HTTP and PD Workers

With HTTP workers, SMG forwards `/v1/messages` to the selected worker. It normally buffers the request, validates it, and re-serializes it: fields SMG does not model are kept, a [`--model-alias`](../configuration.md) name is replaced by the canonical model ID, and numbers keep the values the client sent. When the HTTP router instead streams the body through (see [Request Streaming](../../concepts/performance/request-streaming.md)), it forwards the raw body and leaves validation to the worker. Either way, only the [allowlisted headers](#forwarded-headers) are forwarded. The worker's SSE streams and success bodies are relayed unchanged; an error response keeps its status but its body leaves in the Messages envelope (see [Errors](#errors)).

With `--pd-disaggregation` and HTTP workers, `/v1/messages` uses the same prefill/decode dual dispatch as chat completions: SMG selects a prefill and decode pair and dispatches the request to both. See [PD Disaggregation](../../concepts/routing/pd-disaggregation.md).

On both deployments, a parsed body with `max_tokens: 0` is refused with `400` before a worker is selected; see [`max_tokens` of 0](#max_tokens-of-0).

---

## Anthropic API Backend

Point SMG at the Anthropic API:

```bash
smg launch --backend anthropic --worker-urls https://api.anthropic.com
```

Clients then call SMG as in the [Anthropic API example](#example-request).

- SMG sends each request to `<worker URL>/v1/messages`, so the worker URL is the API origin without `/v1`. The request carries the client's `x-api-key`, `Authorization`, `anthropic-version`, and `anthropic-beta` headers; SMG adds none of them.
- Streaming responses are relayed as they arrive. Non-streaming responses are parsed into SMG's Messages schema and re-serialized; a body that does not parse returns `502` with code `parse_error`.
- An upstream error is returned with its status; the body, already in Anthropic's shape, keeps its `error` and gains a `request_id` (see [Errors](#errors)).
- `/v1/messages/count_tokens` returns `501`.

---

## MCP Tools

SMG can run MCP tools for Messages requests on the Anthropic API backend. gRPC workers drop `mcp_toolset` tools, and HTTP workers receive them in the request body.

SMG runs the tool loop itself when the request has the `X-SMG-MCP: enabled` header and `tools` contains at least one `mcp_toolset` entry. Without the header, `mcp_servers` and the `mcp_toolset` entries are forwarded to Anthropic, together with the client's `anthropic-beta` header, and Anthropic's MCP connector runs the tools.

```bash
curl http://localhost:30000/v1/messages \
  -H "Content-Type: application/json" \
  -H "x-api-key: $ANTHROPIC_API_KEY" \
  -H "anthropic-version: 2023-06-01" \
  -H "X-SMG-MCP: enabled" \
  -d '{
    "model": "claude-sonnet-4-20250514",
    "max_tokens": 1024,
    "messages": [
      {"role": "user", "content": "Search the web for SMG and summarize the top result."}
    ],
    "mcp_servers": [
      {"type": "url", "name": "search", "url": "https://mcp.example.com/mcp"}
    ],
    "tools": [
      {"type": "mcp_toolset", "mcp_server_name": "search"}
    ]
  }'
```

| Field | Description |
|-------|-------------|
| `mcp_servers[].name` | Server name that `mcp_toolset` entries refer to. |
| `mcp_servers[].url` | `http://` or `https://` URL. SMG connects over Streamable HTTP. A URL containing `/sse` selects the legacy SSE transport, which SMG can no longer connect to. |
| `mcp_servers[].authorization_token` | Sent to the MCP server as `Authorization: Bearer <token>`. |
| `mcp_toolset.mcp_server_name` | The server whose tools the model may call. |
| `mcp_toolset.default_config.enabled` | Whether the server's tools are enabled (default `true`). |
| `mcp_toolset.configs` | Per-tool settings keyed by tool name. When present, only the tools listed here are exposed, each if its `enabled` is `true` (or unset, falling back to `default_config.enabled`). |
| `defer_loading` in `default_config` or `configs` | Copied onto the tools SMG passes to the model. |

What SMG does:

1. Connects to the servers in `mcp_servers`. If none connects, it returns `502` with code `mcp_connection_failed`.
2. Replaces `mcp_servers` and the `mcp_toolset` entries with the enabled MCP tools as regular tools, and sets `tool_choice` to `auto` if the request had none.
3. Runs each tool the model calls, sends the results back to the model, and repeats until the model stops calling tools, for at most 10 rounds of tool calls. If the model still calls tools after the 10th round, a non-streaming request fails with `502` (`mcp_max_iterations`). A stream ends with an `error` event right after the 10th round, without calling the model again.
4. Returns each call as an `mcp_tool_use` block (id prefixed `mcptoolu_`) followed by its `mcp_tool_result` block, then the model's final content:

```json
{
  "content": [
    {
      "type": "mcp_tool_use",
      "id": "mcptoolu_01AbCdEf",
      "name": "web_search",
      "server_name": "search",
      "input": {"query": "SMG"}
    },
    {
      "type": "mcp_tool_result",
      "tool_use_id": "mcptoolu_01AbCdEf",
      "content": [{"type": "text", "text": "..."}],
      "is_error": null
    },
    {"type": "text", "text": "The top result describes..."}
  ],
  "stop_reason": "end_turn"
}
```

In a stream, SMG forwards the upstream events, emits the model's tool calls as `mcp_tool_use` blocks and each result as an `mcp_tool_result` block, and ends with a single `message_delta` and `message_stop`. SMG does not accept `mcp_tool_use` or `mcp_tool_result` blocks in `messages` (see the accepted block types under [Request Body](#request-body)).

Tool calls use policy-only approval: SMG never pauses the request to ask the client, and its default policy allows every tool. See [MCP](../../concepts/extensibility/mcp.md) for server configuration.

---

## Errors

Every error answered on `/v1/messages` and `/v1/messages/count_tokens` — a body that does not parse or fails validation, an authentication or admission refusal, a routing or pipeline error, a relayed worker or Anthropic API error — leaves in Anthropic's error envelope, whatever produced it inside. For example, an over-long prompt on a gRPC worker:

```json
{
  "type": "error",
  "error": {
    "type": "invalid_request_error",
    "message": "This model's maximum context length is 32768 tokens. However, your request has 40210 input tokens. Please reduce the length of the input."
  },
  "request_id": "req_abc123"
}
```

`request_id` is the id the gateway echoes in the `x-request-id` response header. `error.type` follows the HTTP status:

| Status | `error.type` |
|--------|--------------|
| `401` | `authentication_error` |
| `403` | `permission_error` |
| `404` | `not_found_error` |
| `413` | `request_too_large` |
| `429` | `rate_limit_error` |
| `503`, `529` | `overloaded_error` |
| `504` | `timeout_error` |
| Any other 4xx | `invalid_request_error` |
| Any other 5xx | `api_error` |

The re-shaped response keeps the inner error's status and headers: `Retry-After`, the `WWW-Authenticate` challenge on a `401`, and — when SMG itself minted the error (routing, the gRPC pipeline, the MCP tool loop, the header checks) — an `X-SMG-Error-Code` header naming a machine-readable code such as `model_not_found`, `context_length_exceeded`, or `max_tokens_zero_not_relayed`. A body the request validation refused carries no `X-SMG-Error-Code`; its `error.message` names the offending field and rule.

An error from an HTTP or PD worker or the Anthropic API keeps its upstream status. An upstream body already in the Anthropic shape keeps its `error` and gains the `request_id`; any other body is re-shaped into the envelope with its message carried over.

Errors that SMG detects before the stream starts are returned as ordinary HTTP error responses, not as a stream, even when `stream` is `true`. After the stream has started, the gRPC path and SMG's MCP tool loop report a failure as an Anthropic `error` event and end the stream. The MCP tool loop starts its stream before it first calls the model, so even an upstream error on that first call arrives this way:

```text
event: error
data: {"type":"error","error":{"type":"api_error","message":"..."}}
```

HTTP workers and the Anthropic API stream their own error events, which SMG relays.
