---
title: Responses API
---

# Responses API Reference

The Responses API provides an OpenAI-compatible interface for agentic workflows with built-in support for multi-turn conversations, tool execution, and MCP (Model Context Protocol) integration.

---

## Overview

### Purpose vs Chat Completions API

The Responses API differs from the Chat Completions API in several key ways:

| Feature | Chat Completions | Responses API |
|---------|------------------|---------------|
| Conversation State | Stateless | Server-managed state |
| Tool Execution | Client-side | Server-side with MCP support |
| Multi-turn | Manual | Automatic with `previous_response_id` |
| Persistence | None | Built-in response/conversation storage |
| Agentic Workflows | Manual orchestration | Built-in tool loop execution |

### Agentic Workflow Concepts

The Responses API enables agentic workflows where the model can:

1. **Reason** about tasks using optional reasoning parameters
2. **Plan** tool usage with automatic tool selection
3. **Execute** tools via MCP servers or function calling
4. **Iterate** through multiple tool calls in a single request
5. **Persist** conversation history for multi-session workflows

### Backend Support

How SMG serves `/v1/responses` depends on the worker behind the model:

| Worker | Behavior |
|--------|----------|
| gRPC workers | SMG runs the request through its chat pipeline, executes MCP tools, and stores responses and conversations. gpt-oss models use SMG's Harmony pipeline |
| gRPC EPD (encode-prefill-decode) | Not served (`501`) |
| HTTP workers, including PD | Forwarded to the worker's own `/v1/responses`; storage, chaining, and tools are the worker's |
| External OpenAI-compatible providers | Forwarded upstream; SMG executes MCP tools and stores responses |

Unless noted otherwise, the behavior on this page is that of gRPC workers. Stored
responses and conversations live in SMG's history backend (`--history-backend`, default
`memory`); see [Chat History](../../concepts/data/chat-history.md).

---

## Base URL

```
http://localhost:30000/v1
```

---

## Create Response

Create a new response with optional tool execution and conversation management.

```
POST /v1/responses
```

### Request Body

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `model` | string | Yes | Model identifier |
| `input` | string or array | Yes | Input text or array of input items. Must not be empty, and an array must contain at least one message |
| `instructions` | string | No | System instructions for the model |
| `max_output_tokens` | integer | No | Maximum tokens to generate (at least 1) |
| `max_tool_calls` | integer | No | Maximum tool calls SMG executes for this response (at least 1); see [Tool Call Limits](#tool-call-limits) |
| `temperature` | number | No | Sampling temperature (0-2), default: 1.0 |
| `top_p` | number | No | Nucleus sampling parameter, greater than 0 and at most 1 |
| `stream` | boolean | No | Enable streaming responses |
| `stream_options` | object | No | Streaming options, such as `include_obfuscation` |
| `store` | boolean | No | Store response for later retrieval, default: true |
| `tools` | array | No | Available tools: `function`, `namespace`, `mcp`, and built-in tool types |
| `tool_choice` | string/object | No | `auto`, `none`, `required`, a function (`{"type": "function", "name": "...", "namespace": "..."}`), `allowed_tools`, an MCP server (`{"type": "mcp", "server_label": "..."}`), or a built-in tool type. Anything except `none` requires `tools` |
| `parallel_tool_calls` | boolean | No | Allow parallel tool execution, default: true when `tools` is set |
| `previous_response_id` | string | No | Continue from a stored response |
| `conversation` | string or object | No | Conversation ID (`conv_...`) or `{"id": "conv_..."}`; mutually exclusive with `previous_response_id` |
| `reasoning` | object | No | Reasoning configuration |
| `text` | object | No | Text format for structured outputs |
| `include` | array | No | Extra output, such as `reasoning.encrypted_content` or `message.output_text.logprobs` |
| `top_logprobs` | integer | No | Most likely tokens per position (0-20); requires `message.output_text.logprobs` in `include` |
| `metadata` | object | No | Custom metadata, echoed in the response |
| `user` | string | No | End-user identifier |

SMG does not run responses in the background: a `background` field is ignored, and every
response is returned synchronously or streamed.

### Input Formats

**Simple text input:**

```json
{
  "input": "What is the capital of France?"
}
```

**Structured input items:**

```json
{
  "input": [
    {
      "type": "message",
      "role": "user",
      "content": [{"type": "input_text", "text": "Hello!"}]
    }
  ]
}
```

**Replaying a function call and its result:**

```json
{
  "input": [
    {"type": "message", "role": "user", "content": "What's the weather in Paris?"},
    {
      "type": "function_call",
      "call_id": "call_abc123",
      "name": "get_weather",
      "arguments": "{\"location\": \"Paris\"}"
    },
    {
      "type": "function_call_output",
      "call_id": "call_abc123",
      "output": [{"type": "input_text", "text": "18°C and sunny"}]
    }
  ]
}
```

A `function_call_output.output` is a string or an array of content parts, and an empty
result is allowed. gRPC workers concatenate the text parts in order and reject a result
that contains media; HTTP workers receive the array unchanged. A replayed `function_call`
keeps its `namespace` when it has one.

### Tool Configuration

**Function tools:**

```json
{
  "tools": [
    {
      "type": "function",
      "name": "get_weather",
      "description": "Get weather for a location",
      "parameters": {
        "type": "object",
        "properties": {
          "location": {"type": "string"}
        },
        "required": ["location"]
      }
    }
  ]
}
```

**Namespaced tools:**

```json
{
  "tools": [
    {
      "type": "namespace",
      "name": "github",
      "description": "GitHub repository tools",
      "tools": [
        {
          "type": "function",
          "name": "create_issue",
          "description": "Open an issue",
          "parameters": {
            "type": "object",
            "properties": {"title": {"type": "string"}},
            "required": ["title"]
          }
        }
      ]
    }
  ],
  "tool_choice": {"type": "function", "name": "create_issue", "namespace": "github"}
}
```

A namespace groups `function` and `custom` tools; namespaces do not nest. A function
`tool_choice` with `namespace` must name a function in that namespace. On gRPC workers SMG
presents each member to the model as `<namespace>.<name>` and maps the calls back, so a
generated `function_call` item carries the member `name` and a separate `namespace` field
in responses, streaming events, and replays. A top-level function whose own name contains
a dot keeps that literal name.

**MCP tools:**

```json
{
  "tools": [
    {
      "type": "mcp",
      "server_url": "http://localhost:8080/mcp",
      "server_label": "my-mcp-server",
      "server_description": "My MCP server for data access",
      "require_approval": "never",
      "allowed_tools": ["query_database", "search_files"]
    }
  ]
}
```

`server_label` is required: it must start with a letter, contain only letters, digits,
`-`, and `_`, and be unique within the request (ignoring case). `server_url` and
`connector_id` cannot both be set. `allowed_tools` takes a list of tool names or a
`{"read_only": ..., "tool_names": [...]}` filter. If a declared MCP server cannot be
reached, the request fails with `424` `connect_mcp_server_failed`.

**Built-in tools:** `web_search_preview`, `code_interpreter`, `file_search`, and
`image_generation` run through MCP servers configured to back those built-in types; see
[MCP](../../concepts/extensibility/mcp.md). The non-preview `web_search` tool is accepted
with all of its fields, including `external_web_access` (an explicit `false` is kept), and
forwarded to HTTP workers and external providers.

### Reasoning Configuration

```json
{
  "reasoning": {
    "effort": "medium",
    "summary": "auto"
  }
}
```

Effort tiers, lowest to highest: `none`, `minimal`, `low`, `medium`, `high`, `xhigh`,
`max`. `none` turns reasoning off; a `reasoning` object without `effort` defaults to
`medium`. Any other value is rejected with `400` `json_parse_error`. `summary` accepts
`auto`, `concise`, or `detailed`.

On gRPC workers the tier reaches the chat pipeline as the same `reasoning_effort` string,
where `none` and `minimal` turn thinking off (see
[Thinking and Reasoning Effort](openai.md#thinking-and-reasoning-effort)). gpt-oss
(Harmony) models know only `low`, `medium`, and `high`, so SMG clamps the outer tiers:
`none` and `minimal` become `low`, and `xhigh` and `max` become `high`.

With `include: ["reasoning.encrypted_content"]`, reasoning items also carry an
`encrypted_content` blob that a `store: false` client can send back on its next turn. SMG
fills it with the reasoning text in version-tagged base64: opaque, not encrypted.

### Text Format (Structured Outputs)

```json
{
  "text": {
    "format": {
      "type": "json_schema",
      "name": "user_info",
      "schema": {
        "type": "object",
        "properties": {
          "name": {"type": "string"},
          "age": {"type": "integer"}
        }
      },
      "strict": true
    }
  }
}
```

`format.type` is `text`, `json_object`, or `json_schema`. For `json_schema`, `name` must
not be empty and `schema` must be a JSON object; anything else is rejected with `400`
before the request reaches a worker.

### Tool Call Limits

`max_tool_calls` caps the tool calls SMG executes itself for one response: MCP tools and
MCP-backed built-in tools. Function tools are returned to the client and do not count.
SMG also enforces an internal cap of 10 executions per response, so a larger
`max_tool_calls` behaves as 10.

When the model asks for a batch of calls that exceeds the remaining allowance, SMG
executes the calls that fit and keeps their results:

- If your `max_tool_calls` is the limit, the response ends normally with the executed
  calls; calls past the cap are dropped.
- If the internal cap is the limit (no `max_tool_calls`, or a value above 10), the
  response ends `failed` with `error.code` `max_tool_calls_exceeded`.

A batch that fits, even exactly, gets another model turn, so the model can answer after its
last permitted call. When a turn mixes MCP and function calls, SMG executes the MCP calls
within the allowance and then returns the response with the function calls for the client
to run. A turn that ends with a `length` or error finish never dispatches tools, even if a
complete call was parsed before the stop. Streaming and non-streaming responses follow the
same rules.

gpt-oss (Harmony) models check the limit before a batch runs and do not execute a batch
that would exceed it; a non-streaming response then ends `failed` with
`max_tool_calls_exceeded`.

### Example Request

```bash
curl http://localhost:30000/v1/responses \
  -H "Content-Type: application/json" \
  -d '{
    "model": "meta-llama/Llama-3.1-8B-Instruct",
    "input": "Search for the latest news about AI",
    "instructions": "Be concise and factual",
    "max_output_tokens": 500,
    "temperature": 0.7,
    "tools": [
      {
        "type": "mcp",
        "server_url": "http://localhost:8080/mcp",
        "server_label": "search"
      }
    ],
    "tool_choice": "auto"
  }'
```

### Response

Abbreviated:

```json
{
  "id": "resp_abc123",
  "object": "response",
  "created_at": 1705312345,
  "status": "completed",
  "instructions": "Be concise and factual",
  "max_output_tokens": 500,
  "model": "meta-llama/Llama-3.1-8B-Instruct",
  "output": [
    {
      "type": "mcp_list_tools",
      "id": "mcpl_abc123",
      "server_label": "search",
      "tools": [
        {
          "name": "web_search",
          "description": "Search the web",
          "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}}
        }
      ]
    },
    {
      "type": "mcp_call",
      "id": "mcp_abc123",
      "status": "completed",
      "arguments": "{\"query\": \"latest AI news\"}",
      "name": "web_search",
      "output": "{\"results\": [...]}",
      "server_label": "search"
    },
    {
      "type": "message",
      "id": "msg_abc123",
      "role": "assistant",
      "content": [
        {
          "type": "output_text",
          "text": "Based on my search, here are the latest AI developments...",
          "annotations": []
        }
      ],
      "status": "completed"
    }
  ],
  "parallel_tool_calls": true,
  "store": true,
  "temperature": 0.7,
  "tools": [
    {
      "type": "mcp",
      "server_url": "http://localhost:8080/mcp",
      "server_label": "search"
    }
  ],
  "usage": {
    "input_tokens": 50,
    "output_tokens": 150,
    "total_tokens": 200,
    "input_tokens_details": {"cached_tokens": 0}
  },
  "metadata": {}
}
```

### Output Item Statuses

| Item | `status` |
|------|----------|
| `message` | `completed`; `in_progress` when generation failed partway (the partial text is kept) |
| `function_call` | `completed` once fully generated; `incomplete` when `max_output_tokens` cut its JSON `arguments` off; `in_progress` when generation failed |
| `reasoning` | `completed` |
| `mcp_call` | `completed`, or `failed` with a structured `error` (see [Tool Errors](#tool-errors)) |
| Built-in calls (`web_search_call`, `code_interpreter_call`, `file_search_call`, `image_generation_call`) | `completed`, also on failure; the failure details are in the item content |

The response's own `status`:

| `status` | Meaning |
|----------|---------|
| `completed` | Finished normally, including a stop at your `max_tool_calls` |
| `incomplete` | `max_output_tokens` was reached; `incomplete_details.reason` is `max_output_tokens` |
| `failed` | Generation failed or the internal tool-call cap was hit; `error` carries `code` and `message` |

For a failed generation, `error` keeps the upstream error's `code` (or `type`) and
`message` when the pipeline reported them; otherwise it is `server_error` with
`Upstream generation failed`. `stream_error` marks a failure to read the stream, and
`max_tool_calls_exceeded` the internal cap.

### Usage

Usage uses the Responses field names: `input_tokens`, `output_tokens`, `total_tokens`,
plus `input_tokens_details.cached_tokens` and `output_tokens_details.reasoning_tokens`
when the worker reports them. With MCP tools, usage is summed over every model turn of the
tool loop (input, output, total, cached, and reasoning tokens) in both streaming and
non-streaming mode; a turn that reports no usage leaves the earlier totals intact.

### Streaming Response

With `"stream": true`, responses are sent as Server-Sent Events:

```bash
curl http://localhost:30000/v1/responses \
  -H "Content-Type: application/json" \
  -d '{
    "model": "meta-llama/Llama-3.1-8B-Instruct",
    "input": "Hello!",
    "stream": true
  }'
```

**Event sequence:**

```
event: response.created
data: {"type":"response.created","sequence_number":0,"response":{"id":"resp_abc123","object":"response","created_at":1705312345,"status":"in_progress","model":"meta-llama/Llama-3.1-8B-Instruct","output":[]}}

event: response.in_progress
data: {"type":"response.in_progress","sequence_number":1,"response":{"id":"resp_abc123","object":"response","status":"in_progress"}}

event: response.output_item.added
data: {"type":"response.output_item.added","sequence_number":2,"output_index":0,"item":{"id":"msg_abc123","type":"message","role":"assistant","content":[]}}

event: response.content_part.added
data: {"type":"response.content_part.added","sequence_number":3,"output_index":0,"item_id":"msg_abc123","content_index":0,"part":{"type":"output_text","text":""}}

event: response.output_text.delta
data: {"type":"response.output_text.delta","sequence_number":4,"output_index":0,"item_id":"msg_abc123","content_index":0,"delta":"Hello! How can I help you?"}

event: response.output_text.done
data: {"type":"response.output_text.done","sequence_number":5,"output_index":0,"item_id":"msg_abc123","content_index":0,"text":"Hello! How can I help you?"}

event: response.content_part.done
data: {"type":"response.content_part.done","sequence_number":6,"output_index":0,"item_id":"msg_abc123","content_index":0,"part":{"type":"output_text","text":"Hello! How can I help you?"}}

event: response.output_item.done
data: {"type":"response.output_item.done","sequence_number":7,"output_index":0,"item":{"id":"msg_abc123","type":"message","role":"assistant","content":[{"type":"output_text","text":"Hello! How can I help you?"}],"status":"completed"}}

event: response.completed
data: {"type":"response.completed","sequence_number":8,"response":{"id":"resp_abc123","object":"response","created_at":1705312345,"status":"completed","model":"meta-llama/Llama-3.1-8B-Instruct","output":[...],"usage":{"input_tokens":10,"output_tokens":8,"total_tokens":18},...}}
```

The stream closes after the terminal event. Unlike Chat Completions, a Responses stream
has no `data: [DONE]` sentinel. Streams from HTTP workers are relayed as the worker sends
them.

**Paired item events.** Every item opens with `response.output_item.added` and closes with
`response.output_item.done`, with its content events in between:

| Item | Events between `added` and `done` |
|------|-----------------------------------|
| `reasoning` | `response.content_part.added` (a `reasoning_text` part), `response.reasoning_text.delta`, `response.reasoning_text.done`, `response.content_part.done` |
| `message` | `response.content_part.added`, `response.output_text.delta`, `response.output_text.done`, `response.content_part.done` |
| `function_call` | `response.function_call_arguments.delta`, `response.function_call_arguments.done` |
| `custom_tool_call` | `response.custom_tool_call_input.delta`, `response.custom_tool_call_input.done` |
| `mcp_list_tools` | `response.mcp_list_tools.in_progress`, `response.mcp_list_tools.completed` |
| `mcp_call` | `response.mcp_call.in_progress`, `response.mcp_call_arguments.delta`, `response.mcp_call_arguments.done`, then `response.mcp_call.completed` or `response.mcp_call.failed` |
| Built-in calls | `response.<type>.in_progress`, the searching/interpreting/generating event, `response.<type>.completed` |

Reasoning streams first, as its own item. A streamed `function_call` item opens with
`"status": "in_progress"` and closes with its final status.

**Terminal events.** Exactly one of these ends the stream:

| Event | `response.status` |
|-------|-------------------|
| `response.completed` | `completed` |
| `response.incomplete` | `incomplete`, with `incomplete_details: {"reason": "max_output_tokens"}` |
| `response.failed` | `failed`, with `error: {"code": "...", "message": "..."}` |

Before the terminal event, SMG closes every item that is still open, exactly once, even
when generation fails. Partial output stays in the terminal response with its unfinished
status.

**MCP-specific streaming events:**

```
event: response.output_item.added
data: {"type":"response.output_item.added","sequence_number":2,"output_index":0,"item":{"id":"mcpl_abc123","type":"mcp_list_tools","server_label":"search","status":"in_progress","tools":[]}}

event: response.mcp_list_tools.in_progress
data: {"type":"response.mcp_list_tools.in_progress","sequence_number":3,"output_index":0}

event: response.mcp_list_tools.completed
data: {"type":"response.mcp_list_tools.completed","sequence_number":4,"output_index":0,"tools":[...]}

event: response.output_item.done
data: {"type":"response.output_item.done","sequence_number":5,"output_index":0,"item":{"id":"mcpl_abc123","type":"mcp_list_tools","server_label":"search","status":"completed","tools":[...]}}

event: response.output_item.added
data: {"type":"response.output_item.added","sequence_number":6,"output_index":1,"item":{"id":"mcp_abc123","type":"mcp_call","name":"web_search","status":"in_progress","arguments":"","server_label":"search"}}

event: response.mcp_call.in_progress
data: {"type":"response.mcp_call.in_progress","sequence_number":7,"output_index":1,"item_id":"mcp_abc123"}

event: response.mcp_call_arguments.delta
data: {"type":"response.mcp_call_arguments.delta","sequence_number":8,"output_index":1,"item_id":"mcp_abc123","delta":"{\"query\": \"latest AI news\"}"}

event: response.mcp_call_arguments.done
data: {"type":"response.mcp_call_arguments.done","sequence_number":9,"output_index":1,"item_id":"mcp_abc123","arguments":"{\"query\": \"latest AI news\"}"}

event: response.mcp_call.completed
data: {"type":"response.mcp_call.completed","sequence_number":10,"output_index":1,"item_id":"mcp_abc123"}

event: response.output_item.done
data: {"type":"response.output_item.done","sequence_number":11,"output_index":1,"item":{"type":"mcp_call","id":"mcp_abc123","status":"completed","arguments":"{\"query\": \"latest AI news\"}","name":"web_search","output":"...","server_label":"search"}}
```

SMG sends the arguments of an MCP call as a single `response.mcp_call_arguments.delta`.
The model's own deltas for MCP-bound calls are not shown as `function_call` events; a
server-executed call appears only through its `mcp_call` (or built-in) events, while
function tools in the same turn keep their own item events. A failed call emits
`response.mcp_call.failed` with an `error` message string (an SMG extension) instead of
`response.mcp_call.completed`.

### Tool Errors

A failed MCP call does not fail the response. SMG passes the failure back to the model as
the tool result and records an `mcp_call` item with `status: "failed"` and a structured
`error`, following the current OpenAI schema:

```json
{
  "type": "mcp_call",
  "id": "mcp_abc123",
  "status": "failed",
  "arguments": "{\"query\": \"latest AI news\"}",
  "error": {"type": "mcp_tool_execution_error", "content": "upstream broke"},
  "name": "web_search",
  "output": "",
  "server_label": "search"
}
```

`error.type` is `mcp_protocol_error` (with `code` and `message`), `mcp_tool_execution_error`
(with `content`), or `http_error` (with `code` and `message`). SMG itself produces
`mcp_tool_execution_error`. An older stored item whose `error` is a plain string is read
as `mcp_tool_execution_error`, so replaying it sends the object form.

---

## Get Response

Retrieve a previously stored response by ID.

```
GET /v1/responses/{response_id}
```

### Path Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| `response_id` | string | The response ID (e.g., `resp_abc123`) |

### Example Request

```bash
curl http://localhost:30000/v1/responses/resp_abc123def456
```

### Response

Returns the stored response object as shown in the Create Response section. SMG reads it
from its own history backend, which the gRPC and external-provider paths fill when `store`
is `true`; responses produced by HTTP workers are stored by the worker and are not
retrievable through SMG. An unknown ID returns `404` with code `not_found`.

---

## Cancel Response

```
POST /v1/responses/{response_id}/cancel
```

Attempts to cancel an in-progress response. Behavior depends on the connection mode:

- **gRPC workers**: Background mode is not supported, so there is nothing to cancel. A stored `completed` response returns `400` `response_already_completed`, a `failed` one `400` `response_already_failed`, any other stored response `400` `cancellation_not_supported`, and an unknown ID `404` `response_not_found`.
- **HTTP workers**: The request is sent to every registered worker; SMG returns the first successful answer, otherwise the last error. Whether cancellation succeeds depends on backend support.

### Path Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| `response_id` | string | The response ID to cancel |

### Example Request

```bash
curl -X POST http://localhost:30000/v1/responses/resp_abc123def456/cancel
```

### Response

**HTTP workers**: Returns the response object from the backend.

**gRPC workers**: Returns a `400 Bad Request` error:

```json
{
  "error": {
    "type": "Bad Request",
    "code": "cancellation_not_supported",
    "message": "Background mode is not supported. Synchronous and streaming responses cannot be cancelled.",
    "param": null
  }
}
```

---

## Delete Response

Delete a stored response.

```
DELETE /v1/responses/{response_id}
```

### Path Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| `response_id` | string | The response ID to delete |

### Example Request

```bash
curl -X DELETE http://localhost:30000/v1/responses/resp_abc123def456
```

### Response

```json
{
  "id": "resp_abc123def456",
  "object": "response.deleted",
  "deleted": true
}
```

An unknown ID returns `404` with code `not_found`.

---

## List Response Input Items

List the input items that were sent with a response.

```
GET /v1/responses/{response_id}/input_items
```

### Path Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| `response_id` | string | The response ID |

### Example Request

```bash
curl http://localhost:30000/v1/responses/resp_abc123def456/input_items
```

### Response

```json
{
  "object": "list",
  "data": [
    {
      "id": "msg_abc123",
      "type": "message",
      "role": "user",
      "content": [{"type": "input_text", "text": "Hello!"}]
    }
  ],
  "first_id": "msg_abc123",
  "last_id": "msg_abc123",
  "has_more": false
}
```

All stored input items are returned in one page (`has_more` is always `false`). Items
stored without an ID get a generated `msg_` ID.

---

## Conversation Management

Conversations provide persistent storage for multi-turn interactions, enabling chat history to be maintained across multiple requests.

The conversation endpoints report errors as `{"error": "<message>"}`, except for the
structured `item_already_in_conversation` error described under
[Create Conversation Items](#create-conversation-items).

### Create Conversation

```
POST /v1/conversations
```

### Request Body

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `metadata` | object | No | Custom metadata (max 16 properties) |

### Example Request

```bash
curl http://localhost:30000/v1/conversations \
  -H "Content-Type: application/json" \
  -d '{
    "metadata": {
      "project": "customer-support",
      "user_id": "user_123"
    }
  }'
```

### Response

```json
{
  "id": "conv_abc123def456",
  "object": "conversation",
  "created_at": 1705312345,
  "metadata": {
    "project": "customer-support",
    "user_id": "user_123"
  }
}
```

---

### Get Conversation

```
GET /v1/conversations/{conversation_id}
```

### Example Request

```bash
curl http://localhost:30000/v1/conversations/conv_abc123def456
```

### Response

```json
{
  "id": "conv_abc123def456",
  "object": "conversation",
  "created_at": 1705312345,
  "metadata": {
    "project": "customer-support"
  }
}
```

---

### Update Conversation

Update conversation metadata. Uses merge semantics — set a key to `null` to delete it. The
merged metadata may hold at most 16 properties.

```
POST /v1/conversations/{conversation_id}
```

### Request Body

| Field | Type | Description |
|-------|------|-------------|
| `metadata` | object | Metadata to merge (null values delete keys) |

### Example Request

```bash
curl http://localhost:30000/v1/conversations/conv_abc123def456 \
  -H "Content-Type: application/json" \
  -d '{
    "metadata": {
      "status": "resolved",
      "project": null
    }
  }'
```

### Response

Returns the updated conversation object.

---

### Delete Conversation

```
DELETE /v1/conversations/{conversation_id}
```

### Example Request

```bash
curl -X DELETE http://localhost:30000/v1/conversations/conv_abc123def456
```

### Response

```json
{
  "id": "conv_abc123def456",
  "object": "conversation.deleted",
  "deleted": true
}
```

---

### List Conversation Items

```
GET /v1/conversations/{conversation_id}/items
```

### Query Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `limit` | integer | 100 | Maximum items to return |
| `order` | string | `desc` | Sort order: `asc` or `desc` |
| `after` | string | - | Cursor for pagination |

### Example Request

```bash
curl "http://localhost:30000/v1/conversations/conv_abc123/items?limit=20&order=asc"
```

### Response

```json
{
  "object": "list",
  "data": [
    {
      "id": "msg_abc123",
      "type": "message",
      "role": "user",
      "content": [{"type": "input_text", "text": "Hello"}],
      "status": "completed",
      "created_at": 1705312345
    },
    {
      "id": "msg_def456",
      "type": "message",
      "role": "assistant",
      "content": [{"type": "output_text", "text": "Hi there!"}],
      "status": "completed",
      "created_at": 1705312346
    }
  ],
  "has_more": false,
  "first_id": "msg_abc123",
  "last_id": "msg_def456"
}
```

`has_more` is `true` when the page is full (it holds `limit` items).

---

### Create Conversation Items

Add items to a conversation. Maximum 20 items per request.

```
POST /v1/conversations/{conversation_id}/items
```

### Request Body

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `items` | array | Yes | Array of items to add (max 20) |

### Supported Item Types

These item types are fully supported:

- `message` - User or assistant messages (requires `role`); an item without `type` is a message
- `reasoning` - Model reasoning content
- `mcp_list_tools` - MCP tool listing
- `mcp_call` - MCP tool invocation
- `item_reference` - Reference to an existing item by `id`

These types are stored, but the response adds a `warnings` entry because SMG does not
implement them yet: `function_call`, `function_call_output`, `file_search_call`,
`computer_call`, `computer_call_output`, `web_search_call`, `image_generation_call`,
`code_interpreter_call`, `local_shell_call`, `local_shell_call_output`,
`mcp_approval_request`, `mcp_approval_response`, `custom_tool_call`, and
`custom_tool_call_output`. Any other type is rejected with `400`.

An item whose `id` is already in the conversation is rejected with:

```json
{
  "error": {
    "message": "Item already in conversation",
    "type": "invalid_request_error",
    "param": "items",
    "code": "item_already_in_conversation"
  }
}
```

### Example Request

```bash
curl http://localhost:30000/v1/conversations/conv_abc123/items \
  -H "Content-Type: application/json" \
  -d '{
    "items": [
      {
        "type": "message",
        "role": "user",
        "content": [{"type": "input_text", "text": "What is 2+2?"}]
      },
      {
        "type": "message",
        "role": "assistant",
        "content": [{"type": "output_text", "text": "2+2 equals 4."}]
      }
    ]
  }'
```

### Response

```json
{
  "object": "list",
  "data": [
    {
      "id": "msg_abc123",
      "type": "message",
      "role": "user",
      "content": [{"type": "input_text", "text": "What is 2+2?"}],
      "status": "completed"
    },
    {
      "id": "msg_def456",
      "type": "message",
      "role": "assistant",
      "content": [{"type": "output_text", "text": "2+2 equals 4."}],
      "status": "completed"
    }
  ],
  "first_id": "msg_abc123",
  "last_id": "msg_def456",
  "has_more": false
}
```

---

### Get Conversation Item

```
GET /v1/conversations/{conversation_id}/items/{item_id}
```

### Example Request

```bash
curl http://localhost:30000/v1/conversations/conv_abc123/items/msg_abc123
```

### Response

Returns the item object. An item that exists but is not in this conversation returns
`404`.

---

### Delete Conversation Item

Remove an item from a conversation. This performs a soft delete — the item may still exist if referenced by other conversations.

```
DELETE /v1/conversations/{conversation_id}/items/{item_id}
```

### Example Request

```bash
curl -X DELETE http://localhost:30000/v1/conversations/conv_abc123/items/msg_abc123
```

### Response

Returns the updated conversation object.

---

## Examples

### Simple Agentic Workflow

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:30000/v1",
    api_key="your-api-key"
)

# Create a response with MCP tools
response = client.responses.create(
    model="meta-llama/Llama-3.1-8B-Instruct",
    input="Search for the weather in San Francisco and summarize it",
    tools=[
        {
            "type": "mcp",
            "server_url": "http://localhost:8080/mcp",
            "server_label": "weather-service"
        }
    ],
    tool_choice="auto"
)

# The response includes tool calls and final answer
for output in response.output:
    if output.type == "mcp_call":
        print(f"Tool called: {output.name} ({output.status})")
        print(f"Result: {output.output}")
    elif output.type == "message":
        for content in output.content:
            if content.type == "output_text":
                print(f"Answer: {content.text}")
```

### Multi-turn Conversation with Tools

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:30000/v1",
    api_key="your-api-key"
)

# Create a conversation
conversation = client.conversations.create(
    metadata={"session": "support-123"}
)

# First turn
response1 = client.responses.create(
    model="meta-llama/Llama-3.1-8B-Instruct",
    input="I need help with my order #12345",
    conversation=conversation.id,
    tools=[
        {
            "type": "mcp",
            "server_url": "http://localhost:8080/mcp",
            "server_label": "order-service"
        }
    ]
)
print(f"First response: {response1.id}")

# Second turn - continues the conversation
response2 = client.responses.create(
    model="meta-llama/Llama-3.1-8B-Instruct",
    input="Can you also check if there are any discounts available?",
    conversation=conversation.id,
    tools=[
        {
            "type": "mcp",
            "server_url": "http://localhost:8080/mcp",
            "server_label": "order-service"
        }
    ]
)
print(f"Second response: {response2.id}")

# List conversation history (tool items have no role)
items = client.conversations.items.list(conversation.id)
for item in items.data:
    print(item.type, getattr(item, "role", None))
```

### Streaming Response Handling

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:30000/v1",
    api_key="your-api-key"
)

# Stream a response
with client.responses.create(
    model="meta-llama/Llama-3.1-8B-Instruct",
    input="Explain quantum computing",
    stream=True
) as stream:
    for event in stream:
        if event.type == "response.output_text.delta":
            print(event.delta, end="", flush=True)
        elif event.type == "response.mcp_call.in_progress":
            print(f"\n[Calling tool: {event.item_id}]")
        elif event.type == "response.completed":
            print(f"\n\nTokens used: {event.response.usage.total_tokens}")
        elif event.type in ("response.incomplete", "response.failed"):
            print(f"\n\nStopped: {event.response.status}")
```

### Using Previous Response ID

```python
# Alternative to conversations - chain responses directly
response1 = client.responses.create(
    model="meta-llama/Llama-3.1-8B-Instruct",
    input="What are the main programming paradigms?",
    store=True
)

# Continue from previous response
response2 = client.responses.create(
    model="meta-llama/Llama-3.1-8B-Instruct",
    input="Can you elaborate on functional programming?",
    previous_response_id=response1.id,
    store=True
)
```

---

## Error Responses

### Error Format

Errors that SMG generates use the gateway envelope, with the HTTP reason phrase as `type`
and the code repeated in the `X-SMG-Error-Code` header:

```json
{
  "error": {
    "type": "Not Found",
    "code": "conversation_not_found",
    "message": "Conversation 'conv_abc123' not found. Please create the conversation first using the conversations API.",
    "param": null
  }
}
```

Request parsing and validation failures use `type: invalid_request_error` instead: a body
that cannot be parsed has `code` `json_parse_error`, and a failed validation rule has
`code` `400` with the rule's message in `message`. See
[Error Responses](openai.md#error-responses) for the full envelope description.

### Common Errors

| HTTP Status | `code` | Description |
|-------------|--------|-------------|
| 400 | `400`, `json_parse_error` | Malformed request or validation failure |
| 400 | `previous_response_not_found` | `previous_response_id` is not in SMG's storage |
| 400 | `convert_request_failed` | gRPC: an input item the chat pipeline cannot represent, such as media in a `function_call_output` |
| 401 | — | Invalid or missing API key (empty body) |
| 404 | `model_not_found` | No worker serves the model |
| 404 | `conversation_not_found` | The `conversation` does not exist |
| 404 | `not_found` | Unknown response ID on get, delete, or input items |
| 424 | `connect_mcp_server_failed` | A declared MCP server could not be reached |
| 429 | `admission_queue_full`, `scheduler_queue_full` | Admission control; see [Rate Limiting](openai.md#rate-limiting) |
| 500 | `load_previous_response_chain_failed`, `check_conversation_failed`, `storage_error` | Storage failures |
| 503 | `no_available_workers` | No healthy workers available |

### Validation Errors

```json
{
  "error": {
    "message": "conversation: Invalid 'conversation': 'invalid-id'. Expected an ID that begins with 'conv_'.",
    "type": "invalid_request_error",
    "code": 400
  }
}
```

```json
{
  "error": {
    "message": "__all__: Mutually exclusive parameters. Ensure you are only providing one of: 'previous_response_id' or 'conversation'.",
    "type": "invalid_request_error",
    "code": 400
  }
}
```

---

## SGLang Extensions

The Responses API includes additional sampling parameters specific to SGLang:

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `top_k` | integer | -1 | Top-k sampling (-1 = disabled) |
| `min_p` | number | 0.0 | Min-p sampling threshold |
| `repetition_penalty` | number | 1.0 | Repetition penalty (1.0 = disabled) |
| `frequency_penalty` | number | - | OpenAI-compatible frequency penalty |
| `presence_penalty` | number | - | OpenAI-compatible presence penalty |
| `stop` | string/array | - | Stop sequences |

Where they take effect:

- **HTTP workers** receive them with the request.
- **gpt-oss (Harmony) models on gRPC**: all but `stop` are applied; `stop` is not passed to the engine yet.
- **Other models on gRPC**: the request is converted to a chat request without these fields, so they have no effect.

Example:

```json
{
  "model": "meta-llama/Llama-3.1-8B-Instruct",
  "input": "Write a story",
  "top_k": 50,
  "min_p": 0.05,
  "repetition_penalty": 1.1
}
```
