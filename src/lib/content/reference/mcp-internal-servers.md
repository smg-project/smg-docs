---
title: Internal MCP Servers
---

# Internal MCP Servers

Mark a static MCP server as internal when the model should use its tools but clients should not see them in responses, for example a memory or retrieval server that supports the model behind the scenes. For MCP in general, see [Model Context Protocol](../concepts/extensibility/mcp.md).

```yaml
servers:
  - name: internal-memory
    protocol: streamable
    url: http://127.0.0.1:28080/mcp
    internal: true
```

`internal` defaults to `false`. It applies only to static servers declared under `servers:` in the MCP config file. A server that a request supplies by URL is never internal.

---

## What Changes

The flag changes only what SMG puts in client-visible Responses output. It does not change transport setup, tool discovery, approval, or execution. The model still sees the server's tools, SMG still runs the calls and returns the results to the model, and the `smg_mcp_*` metrics still count them.

For an internal server, SMG removes these items from the Responses `output`:

- The server's `mcp_list_tools` item
- `mcp_call` items for the server's tools
- `mcp_approval_request` items for the server's tools

A client-declared function tool that has the same name as an internal tool stays visible.

---

## Where It Applies

| Output | Filtered |
|--------|----------|
| Non-streaming Responses on gRPC or ZMQ workers, including gpt-oss (Harmony) models | Yes |
| Non-streaming Responses on an OpenAI-compatible provider | Yes |
| The `mcp_list_tools` items a live stream announces on an OpenAI-compatible provider | Yes |
| The final `response.completed` event and the stored copy of a streamed response on an OpenAI-compatible provider | Yes |
| The stored copy of a streamed response on gRPC or ZMQ workers, including gpt-oss (Harmony) models | No |
| Other live streaming events (an internal server's `mcp_call` events on an OpenAI-compatible provider, and every live event on gRPC or ZMQ workers) | No |
| Hosted-tool items from built-in routing (`web_search_call`, `code_interpreter_call`, `file_search_call`, `image_generation_call`) | No |

On an OpenAI-compatible provider, a streamed response announces at its start only the `mcp_list_tools` items that the final `response.completed` event and the stored record carry: an internal server's item never appears on the stream, and each announced item reaches the final response with the same `id`. An internal server's tool calls still produce live `mcp_call` events; those items are removed only from the final event and the stored record.

On gRPC or ZMQ workers, a streamed response is stored with the items its live events carried, so the record returned by `GET /v1/responses/{response_id}` includes an internal server's `mcp_list_tools` and `mcp_call` items. To keep an internal server's items out of the stored record on these workers, send the request non-streaming.

If an internal server also sets `builtin_type`, its hosted-tool items stay visible. Its `mcp_list_tools` item is hidden either way, as it is for every server with `builtin_type` set.

The flag is generic. It does not imply any vendor-specific behavior.
