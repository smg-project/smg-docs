---
title: MCP in Responses API
---

# MCP in Responses API

Connect SMG to an MCP server and let the model call its tools through `/v1/responses`. SMG runs the tool loop itself on gRPC and ZMQ workers and on OpenAI-compatible providers. It does not run MCP tools for Chat Completions or for HTTP workers. The same servers can also be used from the Anthropic Messages API; see [Messages API](#messages-api).

<div class="prerequisites" markdown>

#### Before you begin

- Completed the [Getting Started](index.md) guide
- SMG serving a model that supports tool calling, from gRPC workers or an OpenAI-compatible provider
- An MCP server that speaks Streamable HTTP, or a local MCP server that runs over stdio

</div>

---

## 1. Create `mcp.yaml`

```yaml title="mcp.yaml"
servers:
  - name: brave
    protocol: streamable
    url: "https://mcp.example.com/mcp"
    token: "${BRAVE_API_KEY}"  # literal placeholder — substitute before loading
    required: true

    tools:
      brave_web_search:
        alias: web_search
        response_format: web_search_call

    # Optional: route the built-in web_search_preview tool to this server
    # builtin_type: web_search_preview
    # builtin_tool_name: brave_web_search

    # Optional: HTTP proxy for this server's traffic
    # proxy:
    #   https: "http://proxy.internal:8080"
    #   no_proxy: "localhost,127.0.0.1"

  # Optional: a local server over stdio
  # - name: filesystem
  #   protocol: stdio
  #   command: npx
  #   args: ["-y", "@modelcontextprotocol/server-filesystem", "/workspace"]
```

- Use `protocol: streamable` for remote servers. The legacy `sse` transport has been removed, and a server configured with it fails to connect.
- SMG parses `mcp.yaml` as plain YAML and does not expand `${VAR}` placeholders inside server `token` values. Substitute credentials externally before the gateway loads the file: render the YAML through `envsubst` from a shell wrapper, use a templating tool (Helm, Kustomize, Jinja), or inject the final config via a secret mount. See [Keeping Credentials Out of Config Files](../concepts/extensibility/mcp.md#keeping-credentials-out-of-config-files).
- The gateway applies only the `servers` list. It accepts the `pool`, `policy`, `inventory`, and `warmup` sections and a top-level `proxy` section, but does not apply them, so settings such as `pool.call_timeout` and approval policies in this file have no effect; see [Accepted but Not Applied](../concepts/extensibility/mcp.md#accepted-but-not-applied).

---

## 2. Start SMG with MCP config

```bash
smg launch \
  --worker-urls grpc://localhost:50051 \
  --model-path meta-llama/Llama-3.1-8B-Instruct \
  --mcp-config-path /path/to/mcp.yaml
```

SMG refuses to start if it can't read or parse `mcp.yaml`. After startup it registers each server in the background, with up to 100 connection attempts, and keeps serving meanwhile. When a server is ready, the log shows:

```text
Successfully connected and registered MCP server: brave
```

---

## 3. Call `/v1/responses` with MCP tools

### Static MCP server by label

Set `server_label` to the server's `name` from `mcp.yaml` and leave out `server_url`:

```bash
curl http://localhost:30000/v1/responses \
  -H "Content-Type: application/json" \
  -d '{
    "model": "meta-llama/Llama-3.1-8B-Instruct",
    "input": "Find Rust 2024 edition highlights",
    "tools": [
      {
        "type": "mcp",
        "server_label": "brave"
      }
    ]
  }'
```

### Dynamic MCP server by URL

```bash
curl http://localhost:30000/v1/responses \
  -H "Content-Type: application/json" \
  -d '{
    "model": "meta-llama/Llama-3.1-8B-Instruct",
    "input": "Search for SMG docs",
    "tools": [
      {
        "type": "mcp",
        "server_label": "tenant-search",
        "server_url": "https://mcp.example.com/mcp",
        "authorization": "token-value",
        "headers": {
          "X-Tenant-ID": "tenant-a"
        }
      }
    ]
  }'
```

- SMG sends `authorization` as `Authorization: Bearer <value>`, so pass the bare token.
- `server_url` must be an `http://` or `https://` Streamable HTTP endpoint. A URL that contains `/sse` is treated as a legacy SSE endpoint and fails.
- `server_label` must start with a letter and contain only letters, digits, `-`, and `_`. Labels must be unique within a request, ignoring case.
- SMG pools the connection by URL and credentials. Sending different credentials to the same `server_url` makes calls to it fail closed; see [Ambiguous Pooled Connections](../concepts/extensibility/mcp.md#ambiguous-pooled-connections).

### Built-in tool routed to MCP

```bash
curl http://localhost:30000/v1/responses \
  -H "Content-Type: application/json" \
  -d '{
    "model": "meta-llama/Llama-3.1-8B-Instruct",
    "input": "Search latest release notes",
    "tools": [
      {
        "type": "web_search_preview"
      }
    ]
  }'
```

When a static server in `mcp.yaml` sets `builtin_type: web_search_preview` and `builtin_tool_name`, SMG routes the built-in tool to that server and returns `web_search_call` items. Without such a server, the tool passes through without MCP.

### Limit and filter tools

```bash
curl http://localhost:30000/v1/responses \
  -H "Content-Type: application/json" \
  -d '{
    "model": "meta-llama/Llama-3.1-8B-Instruct",
    "input": "Find Rust 2024 edition highlights",
    "max_tool_calls": 3,
    "tools": [
      {
        "type": "mcp",
        "server_label": "brave",
        "allowed_tools": ["web_search"]
      }
    ]
  }'
```

- `allowed_tools` lists the tools to expose, by the server's tool name or its configured alias. The object form `{"tool_names": [...]}` also works. A filter that sets `read_only` exposes no tools, because SMG can't evaluate it yet.
- `max_tool_calls` caps the MCP calls SMG executes for the request. SMG never runs more than 10. See [Tool Loop Limits](../concepts/extensibility/mcp.md#tool-loop-limits) for what happens at the cap.

A tool call that fails does not fail the request: the model gets the error as the tool result, and on gRPC workers the call shows up as an `mcp_call` item with `status: "failed"` and a structured `error`. Each call has a 120-second timeout. See [Call Timeouts and Failures](../concepts/extensibility/mcp.md#call-timeouts-and-failures).

---

## Approval Behavior

SMG checks every MCP call before running it:

- By default it decides by policy, without asking the client. The gateway currently runs the built-in default policy, which allows every tool. The `policy` section of `mcp.yaml` is not applied.
- `require_approval: "always"` on an `mcp` tool pauses before a matching call only in non-streaming requests to an OpenAI-compatible provider. The response ends with an `mcp_approval_request` item instead of running the call, and SMG does not currently resume from an `mcp_approval_response`.
- On gRPC workers, in streaming requests, and in the Messages API, every call is decided by policy.

See [Approval](../concepts/extensibility/mcp.md#approval) for details.

---

## Messages API

With an Anthropic provider (`--backend anthropic`, or an external worker in IGW mode whose discovered models are named `claude-*`), the Messages API can run MCP tools too. Send the `X-SMG-MCP: enabled` header, list the servers in `mcp_servers`, and add an `mcp_toolset` tool for each server. SMG then runs at most 10 rounds of tool calls per request.

See the [Messages API reference](../reference/api/messages.md) for a full request and the response format, and [MCP in the Messages API](../concepts/extensibility/mcp.md#mcp-in-the-messages-api) for how this loop differs from the Responses API.

---

## Verify

```bash
curl http://localhost:30000/health
curl http://localhost:29000/metrics | grep smg_mcp
```

`smg_mcp_tool_calls_total` counts calls by `tool_name` and `result`, and `smg_mcp_tool_duration_seconds` records how long each call took.

---

## Next Steps

- [MCP Concepts](../concepts/extensibility/mcp.md) — tool loop limits, timeouts, approval, and the full configuration reference
- [Internal MCP Servers](../reference/mcp-internal-servers.md) — hide a server's tools and calls from responses
- [Responses API Reference](../reference/api/responses.md)
- [Anthropic Messages API Reference](../reference/api/messages.md)
- [Configuration Reference](../reference/configuration.md)
