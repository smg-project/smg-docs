---
title: Reference
---

# Reference

Reference documentation provides detailed specifications for SMG's APIs, CLI, configuration options, and metrics. Use these pages when you need precise information about specific features.

---

## API Reference

<div class="grid cards" markdown>

-   :material-api:{ .lg .middle } **OpenAI-Compatible API**

    ---

    Complete reference for the OpenAI-compatible endpoints including chat completions, completions, and models.

    [:octicons-arrow-right-24: API Reference](api/openai.md)

-   :material-robot:{ .lg .middle } **Responses API**

    ---

    Agentic `/v1/responses` requests with tool calling and MCP, plus stored responses and conversations.

    [:octicons-arrow-right-24: Responses](api/responses.md)

-   :material-message-text:{ .lg .middle } **Anthropic Messages API**

    ---

    The `/v1/messages` and `/v1/messages/count_tokens` endpoints, with backend support per worker type.

    [:octicons-arrow-right-24: Messages](api/messages.md)

-   :material-shield-account:{ .lg .middle } **Admin API**

    ---

    Request and response details for tokenizer, worker, cache, WASM, and model information endpoints.

    [:octicons-arrow-right-24: Admin](api/admin.md)

-   :material-puzzle:{ .lg .middle } **Extension API**

    ---

    SMG-specific endpoints, from health probes and tokenization to worker management and other control-plane routes, with each route's auth tier.

    [:octicons-arrow-right-24: Extensions](api/extensions.md)

</div>

---

## Configuration Reference

<div class="grid cards" markdown>

-   :material-cog:{ .lg .middle } **Configuration Reference**

    ---

    Complete CLI options, environment variables, and configuration for tuning SMG behavior.

    [:octicons-arrow-right-24: Configuration](configuration.md)

-   :material-speedometer:{ .lg .middle } **Tenant Rate Limiting**

    ---

    Per-tenant token and request budgets: flags, the YAML policy schema, and the `429` response.

    [:octicons-arrow-right-24: Tenant Rate Limiting](tenant-rate-limiting.md)

-   :material-sort-variant:{ .lg .middle } **Priority Scheduler**

    ---

    The priority-aware admission scheduler: the request header, response codes, and configuration flags.

    [:octicons-arrow-right-24: Priority Scheduler](priority-scheduler.md)

-   :material-server-security:{ .lg .middle } **Internal MCP Servers**

    ---

    Static MCP servers whose tools the model uses but clients do not see in responses.

    [:octicons-arrow-right-24: Internal MCP Servers](mcp-internal-servers.md)

</div>

---

## Observability Reference

<div class="grid cards" markdown>

-   :material-chart-line:{ .lg .middle } **Metrics Reference**

    ---

    Complete list of Prometheus metrics exposed by SMG for monitoring and alerting.

    [:octicons-arrow-right-24: Metrics](metrics.md)

</div>

---

## Quick Links

| Reference | Description |
|-----------|-------------|
| [CLI Options](configuration.md) | All command-line flags |
| [Environment Variables](configuration.md#environment-variable-reference) | Configurable environment variables |
| [Chat Completions API](api/openai.md#chat-completions) | `/v1/chat/completions` endpoint |
| [HTTP Metrics](metrics.md#layer-1-http-metrics) | HTTP request metrics |
| [Worker Metrics](metrics.md#layer-3-worker-metrics) | Worker health and performance metrics |
