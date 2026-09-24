---
title: Model Context Protocol (MCP)
---

# Model Context Protocol (MCP)

The Model Context Protocol (MCP) gives models access to external tools such as search, databases, and internal APIs through a standard interface. SMG acts as the MCP client: it connects to MCP servers, offers their tools to the model as function tools, runs the calls the model makes, and feeds the results back until the model answers. The model never connects to an MCP server and never sees its credentials.

---

## Overview

<div class="grid" markdown>

<div class="card" markdown>

### :material-tools: Gateway-Side Tool Loop

SMG runs each MCP call and calls the model again, so the client gets the final answer plus a record of every call.

</div>

<div class="card" markdown>

### :material-shield-check: Credential Isolation

Server tokens and headers stay in the config file or the request. The model only sees tool names, schemas, and results.

</div>

<div class="card" markdown>

### :material-timer-sand: Bounded Execution

Every call has a deadline, an interrupted call is never silently re-run, and each request has a tool-call budget.

</div>

<div class="card" markdown>

### :material-server-network: Static and Dynamic Servers

Connect servers from a config file at startup, or per request by URL over a pooled connection.

</div>

</div>

---

## Where MCP Runs

SMG runs the MCP tool loop on these paths:

| API | Backend | How a request opts in |
|-----|---------|------------------------|
| Responses (`POST /v1/responses`) | gRPC or ZMQ workers, including gpt-oss (Harmony) models | An `mcp` tool, or a built-in tool that is routed to MCP |
| Responses (`POST /v1/responses`) | OpenAI-compatible provider (`--backend openai`, or an external worker registered with `provider: openai`) | Same as above |
| Messages (`POST /v1/messages`) | Anthropic provider (`--backend anthropic`, or an external worker registered with `provider: anthropic`) | The `X-SMG-MCP: enabled` header plus `mcp_servers` and `mcp_toolset` tools (see [MCP in the Messages API](#mcp-in-the-messages-api)) |

No other path runs MCP tools. Chat Completions has no MCP loop, HTTP workers receive `/v1/responses` requests unchanged, and on gRPC workers the Messages API ignores `mcp_toolset` tools.

---

## How It Works

1. **Resolve servers.** An `mcp` tool with only a `server_label` refers to a static server from the config file. An `mcp` tool with a `server_url` opens (or reuses) a pooled connection to a dynamic server. A built-in tool such as `web_search_preview` adds the static server configured for it.
2. **Expose tools.** SMG adds each server's tools to the model request as function tools, after `allowed_tools` filtering, aliases, and name de-duplication.
3. **Generate.** The model runs, and SMG parses its tool calls.
4. **Execute.** Calls to MCP tools pass the [approval](#approval) check and run on their server under the [per-call timeout](#per-call-timeout). On gRPC workers, calls to the client's own function tools go back to the client.
5. **Loop.** SMG appends the calls and their results to the conversation and calls the model again, until the model answers without tool calls or a [limit](#tool-loop-limits) stops the loop.
6. **Respond.** In the Responses API, `mcp_list_tools` and `mcp_call` items (or hosted-tool items such as `web_search_call`) come before the model's answer in `output`. In the Messages API, executed calls appear as `mcp_tool_use` and `mcp_tool_result` blocks.

---

## Servers and Transports

### Transports

| `protocol` | Servers | Connection |
|------------|---------|------------|
| `stdio` | Static only | SMG starts `command` with `args` as a child process and speaks MCP over its stdin and stdout. The process inherits SMG's environment plus `envs`, and its stderr goes to SMG's stderr. |
| `streamable` | Static and dynamic | Streamable HTTP to `url`. `token` is sent as `Authorization: Bearer <token>`, `headers` are added to every request, and the TCP connect timeout is 10 seconds. |
| `sse` | None | The legacy HTTP+SSE transport was removed in v1.7.0. A server configured with it fails to connect with `the SSE client transport was removed in rmcp 1.7; use 'protocol: streamable' instead`. |

!!! warning "Dynamic URLs that contain `/sse`"
    SMG treats a request's `server_url` (or a Messages API server `url`) that contains `/sse` as a legacy SSE endpoint, so the connection fails. Point requests at the server's Streamable HTTP endpoint. Only `http://` and `https://` URLs are accepted; SMG skips any other scheme.

### Static Servers

Static servers are declared under `servers` in the [configuration file](#configuration-reference).

- **Registration.** SMG registers each server in the background after the gateway starts, with up to 100 connection attempts. The log shows `Successfully connected and registered MCP server: <name>` once a server is ready. The gateway keeps serving while registration runs, and also when it fails; see `required` in the [server fields](#servers-fields).
- **Referencing.** A Responses request uses a static server by setting `server_label` to the server's `name` and leaving out `server_url`. Built-in tool routing only uses static servers.
- **Tool discovery.** SMG lists the server's tools when it connects and lists them again when the server sends a `tools/list_changed` (or resource or prompt list-changed) notification. There is no periodic refresh.
- **Restarts.** When a Streamable HTTP server restarts and reports the old session as expired (HTTP 404), the client opens a new session on the next call. SMG does not currently restart a stdio server whose process exits; restart SMG instead.
- **Server logs.** Log messages that a static server sends are written to SMG's log at the matching level.

### Dynamic Servers

A dynamic server comes from the request: `server_url` on a Responses `mcp` tool, or `url` in a Messages API `mcp_servers` entry.

- **Transport.** Only Streamable HTTP. Stdio is not available for dynamic servers.
- **Pooling.** SMG keeps connections in a pool keyed by the URL plus a hash of the credentials (`authorization` and `headers`). A later request with the same URL and credentials reuses the connection and the tool list fetched when it was opened.
- **Capacity.** The pool holds up to 100 connections. When it is full, the least recently used connection is dropped and its tools are removed. Idle connections are not closed.
- **Credential isolation.** Two different credentials for the same URL create two pooled connections, and calls to that URL then fail closed. See [Ambiguous Pooled Connections](#ambiguous-pooled-connections).
- **Failures.** SMG skips a server it cannot connect to and logs a warning. If no MCP server is left for the request, gRPC workers return `424` with code `connect_mcp_server_failed`, and the Messages API returns `502` with code `mcp_connection_failed`.

### Tool Names

The model sees each tool under the server's tool name, or under the `alias` you configure for it. When two servers in the same request expose the same tool name, SMG renames both to `mcp_<server_label>_<tool>`. Letters are lowercased, any character other than a letter, digit, or `_` becomes `_`, and a numeric suffix is added if the name is still taken. For example, two servers labeled `server-a` and `server-b` that both expose `run_query` become `mcp_server_a_run_query` and `mcp_server_b_run_query`. The `name` on each `mcp_call` item is the name the model used.

---

## Tool Loop Limits

On gRPC workers, the Responses tool loop for regular (non-Harmony) models behaves the same in streaming and non-streaming mode:

- **Budget.** A request can execute at most `max_tool_calls` MCP calls, and never more than 10, the internal safety cap. Without `max_tool_calls`, the budget is 10.
- **Partial batches.** When one model turn asks for more MCP calls than the budget has left, SMG executes the calls that fit in order, keeps their results, and ends the response. Calls beyond the budget are dropped.
- **User cap or safety cap.** Reaching your own `max_tool_calls` (10 or lower) is a normal finish, and the response completes with the results so far. Reaching the internal cap (no `max_tool_calls`, or a value above 10) fails the response with `error.code` `max_tool_calls_exceeded` (a `response.failed` event when streaming).
- **Final answer.** When a turn's calls fit the remaining budget exactly, SMG still calls the model again, so the model can answer after its last permitted call.
- **Interrupted generation.** If a model turn ends with finish reason `length`, `failed`, or `error`, SMG executes none of that turn's calls, even complete ones. Results from earlier turns stay in the response. `length` gives `status: "incomplete"`, and `failed` or `error` give `status: "failed"` with the upstream error, or a `server_error` fallback.
- **Mixed batches.** When a turn calls both MCP tools and the client's own function tools, SMG executes the permitted MCP calls first, then returns the response with the client's `function_call` items for the client to run.
- **No leaked calls.** Calls that SMG executes or drops never appear as `function_call` items, and in streaming mode their argument deltas are not sent as function-call events. They only show up as `mcp_call` or hosted-tool items.
- **Usage.** `usage` adds up input, output, total, cached, and reasoning tokens across every model turn in the loop.

### Other Paths

The other paths also budget MCP calls, but enforce the limit differently:

| Path | Limit | When the limit is reached |
|------|-------|---------------------------|
| gRPC workers, gpt-oss (Harmony) models | `max_tool_calls` MCP calls (at most 10), and at most 10 model turns | The batch that would exceed the budget is not executed. Non-streaming responses end with `status: "failed"` and `error.code` `max_tool_calls_exceeded`. More than 10 turns end the request with an error. |
| OpenAI-compatible provider | `max_tool_calls` MCP calls (at most 10) | Non-streaming responses complete with `incomplete_details.reason` `max_tool_calls`, and calls that did not run appear as failed `mcp_call` items. Streaming responses end with an `error` event. |
| Messages API (Anthropic provider) | At most 10 rounds of tool calls | Non-streaming requests fail with `502` and code `mcp_max_iterations`. Streaming responses end with an `error` event. |

On the OpenAI-compatible provider and in the Messages API, SMG runs every tool call in the loop through MCP, so a call to one of the client's own function tools comes back to the model as a failed tool call. Mix client function tools with MCP tools only on gRPC workers.

---

## Call Timeouts and Failures

### Per-Call Timeout

Every MCP tool call has a 120-second deadline. A call that runs longer fails with an unknown outcome and is never retried, because the server may already have acted on it:

```text
Tool call timed out after 120s on server 'docs' while executing 'search_docs'; the outcome is unknown and the call was not retried
```

The deadline covers the whole call, including any re-issue after a reconnect. The value comes from `pool.call_timeout`, but the gateway does not apply the `pool` section of the configuration file, so the timeout is always 120 seconds (see [Accepted but Not Applied](#accepted-but-not-applied)).

### Interrupted Calls

If the connection to an MCP server drops while a call is in flight, SMG cannot tell whether the server ran it, so it does not send the call again. The call fails with:

```text
Tool call outcome unknown: server 'orders' disconnected while executing 'place_order'; the call was not retried because the tool is not marked idempotent or read-only
```

Only a call to a tool that declares the MCP `idempotentHint` or `readOnlyHint` annotation is eligible to be re-issued on a new connection. SMG does not currently read these annotations from a server's tool list, so every interrupted call fails this way.

### Ambiguous Pooled Connections

Dynamic connections are pooled by URL and credentials. When the same `server_url` is pooled under two different credentials (different `authorization` or `headers` values), SMG cannot tell which connection belongs to the current caller. It refuses to run the call rather than risk using another caller's authenticated session:

```text
Server access denied: ambiguous MCP connection for 'https://mcp.example.com/mcp': multiple credentials or tenants are pooled for this URL
```

Calls to that URL keep failing while more than one of those connections stays in the pool. Send a single credential to each `server_url`.

### What the Model and Client See

A failed call does not fail the request. SMG sends the error back to the model as the tool result (for example `{"error": "Tool call failed: ..."}`), and the model usually explains the failure in its answer. On gRPC workers, the client sees the call as an `mcp_call` item with `status: "failed"`, an empty `output`, and a structured `error`:

```json
{
  "type": "mcp_call",
  "status": "failed",
  "name": "search_docs",
  "server_label": "docs",
  "arguments": "{\"query\":\"rate limits\"}",
  "output": "",
  "error": {
    "type": "mcp_tool_execution_error",
    "content": "Tool call failed: Tool call timed out after 120s on server 'docs' while executing 'search_docs'; the outcome is unknown and the call was not retried"
  }
}
```

- `error` follows OpenAI's `mcp_call.error` schema: `mcp_protocol_error` and `http_error` (each with `code` and `message`), or `mcp_tool_execution_error` (with `content`). SMG emits `mcp_tool_execution_error`.
- A legacy item whose `error` is a plain string, such as one stored by an older SMG, is still accepted as input and converted to `mcp_tool_execution_error`.
- In streaming mode SMG also sends a `response.mcp_call.failed` event. Its `error` field is the plain message, an SMG extension.
- Hosted-tool items such as `web_search_call` always end as `completed`. Failure details are in their content.
- On the OpenAI-compatible provider, non-streaming responses currently report a failed call as a `completed` `mcp_call` with the error text in `output`.

---

## Approval

Every MCP call passes through SMG's approval check before it runs, in one of two modes:

| Mode | Used for | Behavior |
|------|----------|----------|
| Policy-only | Every path, by default | SMG decides without asking the client. The gateway currently runs the built-in default policy, which allows every tool. The `policy` section of the configuration file is not applied. |
| Interactive | `require_approval: "always"` on an `mcp` tool, in a non-streaming Responses request to an OpenAI-compatible provider | When the model calls one of that server's tools (or, if `allowed_tools` lists names, one of those tools), SMG stops the loop and returns the response with an `mcp_approval_request` output item instead of running the call. |

- `require_approval: "never"`, the object form (`{"always": ..., "never": ...}`), streaming requests, gRPC workers, and the Messages API all use policy-only mode.
- SMG does not currently act on an `mcp_approval_response` input item, so a paused call cannot be approved through SMG.
- Pending approvals are keyed by the model's call ID, so a second call to the same tool gets its own approval instead of being rejected as a duplicate.

---

## Built-in Tools and Response Formats

A static server can stand in for an OpenAI hosted tool. Set `builtin_type` and `builtin_tool_name` together on the server. When a Responses request includes the matching hosted tool, for example `{"type": "web_search_preview"}`, SMG adds that server to the request and returns the configured tool's results in the hosted format. If no server is configured for a requested hosted tool, SMG passes the tool through without MCP.

| `builtin_type` | Request tool | Default output item |
|----------------|--------------|---------------------|
| `web_search_preview` | `{"type": "web_search_preview"}` | `web_search_call` |
| `code_interpreter` | `{"type": "code_interpreter"}` | `code_interpreter_call` |
| `file_search` | `{"type": "file_search"}` | `file_search_call` |
| `image_generation` | `{"type": "image_generation"}` | `image_generation_call` |

Configure each `builtin_type` on one server only. For tools with a hosted format, SMG also:

- Merges the options declared on the request's hosted tool (for example `size` and `quality` on `image_generation`) into the call arguments. The request's values win over the model's.
- Adds the request's `user` field as a `user` argument, unless the model already supplied a non-null value.

`response_format` on a tool sets its output item explicitly:

| `response_format` | Output item |
|-------------------|-------------|
| `passthrough` (default) | `mcp_call` with the raw tool output |
| `web_search_call` | `web_search_call` |
| `code_interpreter_call` | `code_interpreter_call` |
| `file_search_call` | `file_search_call` |
| `image_generation_call` | `image_generation_call` |

An explicit `response_format` overrides the default that `builtin_type` gives the `builtin_tool_name` tool. The `mcp_list_tools` item for a server with `builtin_type` set is never shown to clients.

---

## MCP in the Messages API

On an Anthropic provider, the Messages API runs its own MCP tool loop when a request has the `X-SMG-MCP: enabled` header and at least one `mcp_toolset` tool. Without the header, SMG forwards `mcp_servers` and `mcp_toolset` to the provider unchanged.

The loop differs from the Responses loop in a few ways:

- **Servers.** They come from the request's `mcp_servers` list and connect as [dynamic servers](#dynamic-servers) over Streamable HTTP. `mcp_toolset` entries choose which of their tools the model sees.
- **Approval.** Every call runs in policy-only [approval](#approval) mode.
- **Execution.** SMG runs the calls of each round one after another, for at most 10 rounds.
- **Output.** Each executed `tool_use` block is returned as an `mcp_tool_use` block followed by an `mcp_tool_result` block.

See the [Messages API reference](../../reference/api/messages.md) for the request fields, tool filtering, a full example, and the response format.

---

## Configuration Reference

### CLI Option

```bash
smg --worker-urls grpc://localhost:50051 --mcp-config-path /etc/smg/mcp.yaml
```

`--mcp-config-path` points at a YAML file. SMG reads and parses it at startup and refuses to start if the file can't be read or parsed. Unknown keys are ignored, but an invalid value, such as an unknown `protocol`, is a parse error.

### Configuration File

!!! warning "Only `servers` is applied"
    In v1.11.0 the gateway reads only the `servers` list from this file. The `pool`, `proxy`, `inventory`, `warmup`, and `policy` sections are accepted, so a file that sets them still loads, but they are not applied: the gateway's MCP client always runs with its built-in defaults, which are a 120-second call timeout, a 100-connection pool for dynamic servers, no global proxy, and an allow-all approval policy. Set proxies on each server instead.

```yaml
servers:                             # required; use [] for no static servers
  # Remote server over Streamable HTTP
  - name: brave
    protocol: streamable
    url: "https://mcp.example.com/mcp"
    token: "<token>"                 # sent as "Authorization: Bearer <token>"
    headers:
      X-Tenant-ID: "tenant-a"
    required: true
    proxy:                           # optional, this server only
      https: "http://proxy.internal:8080"
      no_proxy: "localhost,127.0.0.1"
    builtin_type: web_search_preview # optional hosted-tool routing
    builtin_tool_name: brave_web_search
    tools:
      brave_web_search:
        alias: web_search            # the model sees "web_search"
        response_format: web_search_call
        arg_mapping:
          renames:
            q: query                 # the model's "q" is sent as "query"
          defaults:
            count: 10                # added when the model leaves it out
          overrides:
            safesearch: strict       # always replaces the model's value

  # Local server over stdio (static servers only)
  - name: filesystem
    protocol: stdio
    command: npx
    args: ["-y", "@modelcontextprotocol/server-filesystem", "/workspace"]
    envs:
      NODE_ENV: production

  # Tools stay callable, but hidden from client-visible output
  - name: memory
    protocol: streamable
    url: "http://127.0.0.1:28080/mcp"
    internal: true
```

### `servers[]` Fields

| Field | Protocols | Default | Description |
|-------|-----------|---------|-------------|
| `name` | All | Required | Server name. Responses requests reference it as `server_label`, so it must start with a letter and contain only letters, digits, `-`, and `_` to be usable there. Names must be unique; a duplicate is skipped. |
| `protocol` | All | Required | `stdio` or `streamable`. `sse` parses but fails to connect. |
| `command` | `stdio` | Required | Executable to start. |
| `args` | `stdio` | `[]` | Arguments for `command`. |
| `envs` | `stdio` | `{}` | Environment variables added to the process's inherited environment. |
| `url` | `streamable` | Required | Streamable HTTP endpoint. |
| `token` | `streamable` | None | Bearer token, sent as `Authorization: Bearer <token>`. |
| `headers` | `streamable` | `{}` | Extra HTTP headers sent with every request. |
| `proxy` | `streamable` | None | Proxy for this server: `http`, `https`, `no_proxy`, and optional `username` and `password`. See [HTTP Proxies](#http-proxies). |
| `required` | All | `false` | SMG keeps serving either way. A required server that still fails after its connection attempts is logged as an error (`Required MCP server '<name>' failed to register`) and its registration job is marked failed. An optional one is logged as a warning. |
| `tools` | All | None | Per-tool settings, keyed by the server's tool name. See the next table. |
| `builtin_type` | All | None | Hosted tool this server handles: `web_search_preview`, `code_interpreter`, `file_search`, or `image_generation`. Set together with `builtin_tool_name`. |
| `builtin_tool_name` | All | None | The server's tool to call for `builtin_type`. |
| `internal` | All | `false` | Hide this server's tool list and calls from client-visible output. See [Internal MCP Servers](../../reference/mcp-internal-servers.md). |

### `tools` Fields

| Field | Description |
|-------|-------------|
| `alias` | Name the model sees instead of the server's tool name. |
| `response_format` | Output item for the tool's results. See [Built-in Tools and Response Formats](#built-in-tools-and-response-formats). |
| `arg_mapping.renames` | Map of argument name from the model to argument name sent to the server. |
| `arg_mapping.defaults` | Values added when the model leaves an argument out. |
| `arg_mapping.overrides` | Values that always replace the model's value. |

SMG applies renames first, then defaults, then overrides. It then converts string values to numbers where the tool's input schema declares the property as `number` or `integer`. A `tools` entry for a tool the server doesn't list at connect time is skipped with a warning.

### Accepted but Not Applied

The gateway accepts these keys but does not apply them. The defaults shown are the values in effect.

| Key | Default | Meaning |
|-----|---------|---------|
| `pool.call_timeout` | `120` | Seconds allowed for one tool call. `0` disables the bound. |
| `pool.max_connections` | `100` | Capacity of the dynamic-server connection pool. |
| `pool.idle_timeout` | `300` | Seconds. Not implemented; idle connections stay open. |
| `proxy` | None | Global proxy (`http`, `https`, `no_proxy`, `username`, `password`). Use per-server `proxy` instead. |
| `inventory.enable_refresh`, `inventory.tool_ttl`, `inventory.refresh_interval`, `inventory.refresh_on_error` | `true`, `300`, `60`, `true` | Tool-cache settings. Not implemented; tools are refreshed only by server notifications. |
| `warmup` | `[]` | List of `url`, `label`, and `token` entries to pre-connect. |
| `policy.default` | `allow` | `allow`, `deny`, or `{deny_with_reason: "..."}`. |
| `policy.servers.<name>.trust_level` | `standard` | `trusted`, `standard`, `untrusted`, or `sandboxed`. |
| `policy.servers.<name>.default` | `allow` | Same values as `policy.default`. |
| `policy.tools."<server>:<tool>"` | None | Same values as `policy.default`. |

A `pool` section that asks for a 60-second call timeout looks like this. The gateway accepts it but still uses 120 seconds:

```yaml
servers: []
pool:
  call_timeout: 60   # seconds per tool call; 0 disables the bound
```

### Keeping Credentials Out of Config Files

SMG parses `mcp.yaml` as plain YAML and does not expand environment variables in it, including in server `token` values. To keep credentials out of the checked-in config, substitute placeholders before the gateway loads the file: render the YAML through `envsubst` from a shell wrapper, use a templating tool (Helm, Kustomize, Jinja), or mount the final config from a secret.

### HTTP Proxies

Set `proxy` on each server that needs one. `http` and `https` are proxy URLs, `no_proxy` is a comma-separated list of hosts to reach directly, and `username` with `password` adds basic authentication. A server without its own `proxy` block uses the standard `HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`, and `NO_PROXY` environment variables (upper- or lowercase). SMG currently ignores the top-level `proxy` section and the `MCP_HTTP_PROXY`, `MCP_HTTPS_PROXY`, and `MCP_NO_PROXY` variables. Proxy settings in `mcp.yaml` apply to MCP traffic only.

---

## Internal Servers

Set `internal: true` on a static server when its tools support the model behind the scenes and should not show up in responses. The model can still call them, but SMG removes the server's `mcp_list_tools` item and its `mcp_call` items from non-streaming Responses output. Live streaming events are not filtered. See [Internal MCP Servers](../../reference/mcp-internal-servers.md) for the exact rules.

---

## Security

- **The model never sees credentials.** It receives tool names, descriptions, input schemas, and results. Server URLs, tokens, and headers stay in SMG.
- **Clients see tool metadata.** `mcp_list_tools` output items list each server's tool names, descriptions, and input schemas. Mark a static server [internal](#internal-servers) to keep them out of non-streaming responses.
- **Static credentials live in the config file.** See [Keeping Credentials Out of Config Files](#keeping-credentials-out-of-config-files).
- **Dynamic credentials come from the request** (`authorization` and `headers`, or `authorization_token` in the Messages API). They are used only for connections pooled under those exact credentials.
- **No exported audit trail.** SMG keeps its approval decisions in an in-memory buffer (the latest 10,000), but does not currently expose that buffer through an API or the logs. Use the [metrics](#metrics) and your MCP servers' own logs to audit tool activity.

---

## Metrics

| Metric | Type | Labels | Recorded |
|--------|------|--------|----------|
| `smg_mcp_tool_calls_total` | Counter | `model`, `tool_name`, `result` (`success` or `error`) | For each MCP call SMG runs |
| `smg_mcp_tool_duration_seconds` | Histogram | `model`, `tool_name` | For each MCP call SMG runs |
| `smg_mcp_tool_iterations_total` | Counter | `model` | For each tool-loop iteration |
| `smg_mcp_servers_active` | Gauge | None | Static servers plus pooled dynamic URLs, updated when a static server finishes registering |

`tool_name` is the name the model used. The `model` and `tool_name` labels each keep up to 1,024 distinct values, after which new values are reported as `other`. See the [Metrics Reference](../../reference/metrics.md) for all gateway metrics.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| Log shows `the SSE client transport was removed in rmcp 1.7` | The server uses `protocol: sse` | Switch to `protocol: streamable` and the server's Streamable HTTP endpoint. |
| Startup fails with `Failed to parse MCP config from <path>` | YAML syntax error, an invalid value, or a missing `servers` key | Fix the file. Use `servers: []` if you have no static servers. |
| `424` with code `connect_mcp_server_failed` | No MCP server in a Responses request could be connected (gRPC workers) | Check `server_url` (`http://` or `https://`, without `/sse`) and `authorization`. |
| `502` with code `mcp_connection_failed` | No server in a Messages request's `mcp_servers` could be connected | Check each `url` and `authorization_token`. |
| `mcp_call` fails with `Tool call timed out after 120s` | The server took longer than the per-call timeout | Make the tool faster or split the work. The timeout can't currently be changed. |
| `mcp_call` fails with `Tool call outcome unknown` | The connection dropped during the call | Check the server. Retry only if the tool is safe to run twice. |
| `mcp_call` fails with `Server access denied: ambiguous MCP connection` | The same `server_url` was used with different credentials | Send a single credential to each URL. |
| Every call to a stdio server fails after its process exits | SMG does not currently restart a stdio server's process | Restart SMG. |
| Request fails with `Unsupported input item type` | On gRPC workers, the request `input` contains `mcp_call`, `mcp_list_tools`, `mcp_approval_request`, or `mcp_approval_response` items | Remove those items from `input`. |
| A `pool`, `policy`, or top-level `proxy` setting has no effect | Only `servers` is applied | See [Accepted but Not Applied](#accepted-but-not-applied). |

---

## What's Next?

<div class="grid" markdown>

<div class="card" markdown>

### :material-rocket-launch: Get Started

Configure a server and call it through `/v1/responses`.

[MCP in Responses API →](../../getting-started/mcp.md)

</div>

<div class="card" markdown>

### :material-puzzle: WASM Plugins

Extend SMG with custom middleware logic.

[WASM Plugins →](wasm-plugins.md)

</div>

<div class="card" markdown>

### :material-sitemap: Architecture

Where MCP fits in the SMG pipeline.

[Architecture Overview →](../architecture/overview.md)

</div>

</div>
