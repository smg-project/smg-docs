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
| [gRPC workers](../../getting-started/grpc-workers.md) | `grpc://` worker URLs | SMG renders, tokenizes, and parses ([details](#grpc-and-zmq-workers)) | `501 Not Implemented` |
| [ZMQ workers](../../getting-started/zmq-workers.md) | `ipc://` worker URLs | Same as gRPC workers | `501 Not Implemented` |
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
| Client to SMG | `Authorization: Bearer <key>` | Checked when `--api-key` or `--tenant-api-key` is set (`--tenant-api-key` is a Rust binary flag; the pip `smg launch` does not accept it). SMG does not read `x-api-key`. A missing or unknown bearer token gets `401 Unauthorized`. |
| SMG to HTTP workers | `Authorization` | The client's header is forwarded with the other [allowlisted headers](#forwarded-headers); `x-api-key` is not. See [Authentication](../../concepts/security/authentication.md) for worker API keys. |
| SMG to the Anthropic API | `x-api-key`, `Authorization` | Forwarded exactly as the client sent them. SMG adds no key of its own, so the client supplies the Anthropic key. |

Anthropic SDKs send their key in `x-api-key`, which the Anthropic API backend passes upstream. When gateway authentication is on, clients must also send the gateway key as `Authorization: Bearer <key>`; on the Anthropic API backend that header is forwarded to Anthropic as well.

!!! note "Browser clients"
    With `--cors-allowed-origins` set, cross-origin requests may carry the `Content-Type`, `Authorization`, `anthropic-version`, and `anthropic-beta` headers; `x-api-key` is not on that list. Without the flag, SMG allows any origin and header.

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
| `max_tokens` | integer | Yes | Maximum number of tokens to generate. Must be at least 1. |
| `system` | string or array | No | System prompt, as a string or an array of `text` blocks. |
| `stream` | boolean | No | Stream the response as Server-Sent Events. |
| `temperature` | number | No | Sampling temperature. |
| `top_p` | number | No | Nucleus sampling. |
| `top_k` | integer | No | Sample only from the top K tokens. |
| `stop_sequences` | array of strings | No | Sequences that stop generation. |
| `tools` | array | No | Tool definitions; see [Tool Use](#tool-use). |
| `tool_choice` | object | No | `{"type": "auto"}`, `{"type": "any"}`, `{"type": "tool", "name": "..."}`, or `{"type": "none"}`. Every value except `none` requires `tools`, and `tool` must name one of them. |
| `thinking` | object | No | `{"type": "enabled", "budget_tokens": N}`, `{"type": "adaptive"}`, or `{"type": "disabled"}`. `enabled` and `adaptive` also take `display` (`summarized` or `omitted`). |
| `metadata` | object | No | Request metadata (`user_id`). |
| `service_tier` | string | No | `auto` or `standard_only`. |
| `container` | object | No | Container for code execution (beta). |
| `mcp_servers` | array | No | MCP servers for the request (beta). Required when `tools` has an `mcp_toolset` entry; see [MCP Tools](#mcp-tools). |
| `rid` | string | No | Extension: request ID passed to the backend for log correlation. |

SMG validates the body before routing and answers `400` when a required field is missing or a rule above is broken. The one exception is a body that the HTTP router streams through unparsed; see [HTTP and PD Workers](#http-and-pd-workers). Fields SMG does not model, such as `context_management` or `output_config`, are kept and forwarded to HTTP workers and the Anthropic API.

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

A tool call comes back as a `tool_use` block, with `stop_reason` set to `tool_use`:

```json
{
  "content": [
    {
      "type": "tool_use",
      "id": "toolu_0b7e5f1c9a2d4e6f8a1b3c5d",
      "name": "get_weather",
      "input": {"city": "Paris"}
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
| `ping` | Keep-alive; relayed from upstream, never sent by the gRPC path |
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

Returns the number of input tokens a Messages request would use, without generating. SMG does not count the tokens itself: it forwards the request to a worker that implements the endpoint and relays the worker's response (smg-project/smg#2638).

### Request Body

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `model` | string | Yes | Model ID; selects the worker. |
| `messages` | array | Yes | Conversation, in the same format as `/v1/messages`. |
| `system` | string or array | No | System prompt. |
| `tools` | array | No | Tool definitions. |
| `tool_choice` | object | No | Tool selection. |
| `thinking` | object | No | Thinking configuration. |

There is no `max_tokens`. Any other field, such as `context_management` or `mcp_servers`, is forwarded to the worker unchanged.

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
| gRPC or ZMQ workers | `501 Not Implemented` |
| Anthropic API backend | `501 Not Implemented` |
| IGW mode | A model with an external worker goes to that provider's backend (`501`). Otherwise SMG chooses between the model's HTTP and HTTP PD workers; a model with neither, including an unknown model, gets `404` (smg-project/smg#2659). |

---

## Request Headers

Headers specific to the Messages API:

| Header | Effect |
|--------|--------|
| `X-SMG-MCP` | `enabled` makes SMG run the MCP tool loop itself for a request whose `tools` include an `mcp_toolset` entry, on the Anthropic API backend. See [MCP Tools](#mcp-tools) and [MCP](../../concepts/extensibility/mcp.md). Other backends ignore it, and SMG does not forward it. |
| `anthropic-version`, `anthropic-beta` | Not read by SMG. Forwarded to HTTP and PD workers (smg-project/smg#2638) and to the Anthropic API. |
| `x-api-key` | Not read by SMG. Forwarded only to the Anthropic API; see [Authentication](#authentication). |

### Forwarded Headers

For HTTP and PD workers, SMG forwards only these request headers (it sets `Content-Type` itself):

| Header | Purpose |
|--------|---------|
| `Authorization` | Credentials for the worker; see [Authentication](#authentication) |
| `anthropic-version`, `anthropic-beta` | API version and beta features for engines that serve `/v1/messages` (smg-project/smg#2638) |
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
| `max_tokens`, `temperature`, `top_p`, `top_k` | Sent to the engine as sampling parameters. |
| `stop_sequences` | Sent as stop strings to vLLM, TensorRT-LLM, and TokenSpeed gRPC workers. For SGLang gRPC workers and ZMQ workers, SMG matches them itself and sends only single-token stops to the engine, as stop token IDs. |
| `tools` | Custom tools (those with an `input_schema`) are passed to the chat template and the tool parser; with a `tool_choice` of `tool`, only that tool is passed to the chat template. Other tool types, such as `mcp_toolset`, bash, text editor, web search, and tool search, are dropped. |
| `tool_choice` | Mapped to Chat Completions semantics: `auto` to `auto`, `any` to `required`, `tool` to that function, `none` to `none`. SMG enforces `any` and `tool` with constrained decoding. |
| `thinking` | `enabled` and `adaptive` turn on the chat template's thinking mode and run the model's reasoning parser. `disabled` turns thinking mode off. Without `thinking`, the template default applies. `budget_tokens` and `display` are not used. |
| `metadata`, `service_tier`, `container`, `mcp_servers`, extra fields such as `output_config` | Not sent to the engine. |

MLX workers answer `400` to a request with images, with `stop_sequences`, or with a `tool_choice` of `any` or `tool`.

How the response is built:

- **Tool calls and reasoning** are extracted by SMG's tool and reasoning parsers, which SMG picks for the model or you set with `--tool-call-parser` and `--reasoning-parser`. A `thinking` block comes first, then text, then `tool_use` blocks. Thinking blocks carry an empty `signature`.
- **`tool_use` ids** use Anthropic's `toolu_` prefix: a parser id of the form `call_<suffix>` is returned as `toolu_<suffix>`, the same in streaming and non-streaming responses. Model-specific id formats are returned unchanged (smg-project/smg#2111).
- **`stop_reason`** is `tool_use` when the output has tool calls, `stop_sequence` when one of `stop_sequences` ended generation (the matched string is in `stop_sequence`), `max_tokens` when the token limit was reached, and `end_turn` otherwise.
- **`usage`** reports `cache_creation_input_tokens` and `cache_read_input_tokens` as `0`, never `null`. In a stream, `message_start` carries zero counters, and the final `message_delta` carries `output_tokens` and, once the engine has reported it, `input_tokens` (smg-project/smg#2269).
- **Context window**: when the selected worker advertises its context length, SMG rejects a prompt with more tokens than the window with `400` `context_length_exceeded` before dispatch. In PD mode the smaller of the prefill and decode windows applies. Whether the prompt plus `max_tokens` must fit is left to the engine (smg-project/smg#2618).
- **Tenant rate limiting**, when enabled, covers this endpoint; see [Tenant Rate Limiting](../tenant-rate-limiting.md).

---

## HTTP and PD Workers

With HTTP workers, SMG forwards `/v1/messages` to the selected worker. It normally buffers the request, validates it, and re-serializes it: fields SMG does not model are kept, a [`--model-alias`](../configuration.md) name is replaced by the canonical model ID, and numbers keep the values the client sent. When the HTTP router instead streams the body through (see [Request Streaming](../../concepts/performance/request-streaming.md)), it forwards the raw body and leaves validation to the worker. Either way, only the [allowlisted headers](#forwarded-headers) are forwarded, and the worker's response, including error responses and SSE streams, is relayed unchanged.

With `--pd-disaggregation` and HTTP workers, `/v1/messages` uses the same prefill/decode dual dispatch as chat completions: SMG selects a prefill and decode pair and dispatches the request to both (smg-project/smg#2250). See [PD Disaggregation](../../concepts/routing/pd-disaggregation.md).

---

## Anthropic API Backend

Point SMG at the Anthropic API:

```bash
smg launch --backend anthropic --worker-urls https://api.anthropic.com
```

Clients then call SMG as in the [Anthropic API example](#example-request).

- SMG sends each request to `<worker URL>/v1/messages`, so the worker URL is the API origin without `/v1`. The request carries the client's `x-api-key`, `Authorization`, `anthropic-version`, and `anthropic-beta` headers; SMG adds none of them.
- Streaming responses are relayed as they arrive. Non-streaming responses are parsed into SMG's Messages schema and re-serialized; a body that does not parse returns `502` with code `parse_error`.
- An upstream error is returned with its status and body.
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

Tool calls use policy-only approval: SMG never pauses the request to ask the client. The gateway runs its built-in default policy, which allows every tool, because the `policy` section of the MCP configuration file is not applied in v1.11.0. See [MCP](../../concepts/extensibility/mcp.md) for server configuration.

---

## Errors

SMG produces Anthropic's error format (`{"type": "error", ...}`) only inside an SSE stream. Elsewhere the shape depends on where the error comes from:

| Source | Status | Body |
|--------|--------|------|
| Body is not valid JSON, a required field is missing, or a field has the wrong type | `400` | `{"error": {"message": "...", "type": "invalid_request_error", "code": "json_parse_error"}}` |
| Body fails validation (for example `max_tokens` of 0, empty `messages`, `tool_choice` without `tools`) | `400` | `{"error": {"message": "...", "type": "invalid_request_error", "code": 400}}` |
| Gateway authentication fails | `401` | Empty |
| SMG itself: routing, the gRPC pipeline, the MCP tool loop | Varies | The standard JSON error envelope, plus an `X-SMG-Error-Code` header |
| HTTP or PD worker, or the Anthropic API | Upstream status | The upstream body, unchanged |

The standard envelope names the HTTP status in `type` and repeats `code` in the `X-SMG-Error-Code` header. For example, an over-long prompt on a gRPC worker:

```json
{
  "error": {
    "type": "Bad Request",
    "code": "context_length_exceeded",
    "message": "This model's maximum context length is 32768 tokens. However, your request has 40210 input tokens. Please reduce the length of the input.",
    "param": null
  }
}
```

A few gateway answers are plain text instead: the `404` when no router serves the request in IGW mode, the `501` from a backend that does not serve the endpoint, and the `/v1/messages/count_tokens` answer to a body that does not parse (`400` for invalid JSON, `415` without `Content-Type: application/json`, `422` for a missing field or a field of the wrong type).

Errors that SMG detects before the stream starts are returned as ordinary HTTP error responses, not as a stream, even when `stream` is `true`. After the stream has started, the gRPC path and SMG's MCP tool loop report a failure as an Anthropic `error` event and end the stream. The MCP tool loop starts its stream before it first calls the model, so even an upstream error on that first call arrives this way:

```text
event: error
data: {"type":"error","error":{"type":"api_error","message":"..."}}
```

HTTP workers and the Anthropic API stream their own error events, which SMG relays.
