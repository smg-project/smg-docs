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
| Non-streaming Responses on gRPC workers, including gpt-oss (Harmony) models | Yes |
| Non-streaming Responses on an OpenAI-compatible provider | Yes |
| The stored copy of a streamed response on an OpenAI-compatible provider | Yes |
| Live streaming events, on every path | No |
| Hosted-tool items from built-in routing (`web_search_call`, `code_interpreter_call`, `file_search_call`, `image_generation_call`) | No |

If an internal server also sets `builtin_type`, its hosted-tool items stay visible. Its `mcp_list_tools` item is hidden either way, as it is for every server with `builtin_type` set.

The flag is generic. It does not imply any vendor-specific behavior.
