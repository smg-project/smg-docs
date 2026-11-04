---
title: Tenant Rate Limiting
---

# Tenant Rate Limiting

[Rate Limiting](rate-limiting.md) protects **workers** from too much concurrent traffic. Tenant rate limiting protects **budgets**: it caps how many LLM tokens and requests a given tenant can consume per minute, independent of how busy the workers are. The two are orthogonal and can run together — a request can be admitted by the concurrency limiter and still be denied because its tenant is over budget, or vice versa.

Think of it like reserving a hotel room online: the site holds the room — and an estimated price — the moment you book, before you have stayed a single night. When you check out, the front desk settles the bill against what you actually used: extra nights cost more, an early checkout refunds the difference. If you never check in at all, the hotel keeps the deposit: the estimated price stays charged. Tenant rate limiting works the same way: SMG reserves an estimated number of tokens against a tenant's budget *before* it ever contacts a worker, then settles that reservation against the real, backend-reported token count once the response is known.

---

## Why token-based, not just concurrency-based?

The existing [concurrency limiter](rate-limiting.md) answers "how many requests can be in flight at once?" — a good proxy for protecting GPU memory, but a poor proxy for *cost*. A tenant sending one request with a 100,000-token prompt and a tenant sending a hundred one-line chat messages can look identical to a concurrency limiter while being wildly different in actual LLM spend. Tenant rate limiting answers a different question: "how many tokens (and requests) has this tenant actually consumed this minute?" — measured in the unit that maps to real inference cost, not connection count.

---

## Reserve, then settle

The core mechanic is a two-phase commit against a tenant's budget:

1. **Reserve.** Before dispatching to a worker, the gateway estimates the request's input token count — the gRPC router from its own tokenizer, the HTTP routers from the request body itself (see [Estimating without a tokenizer](#estimating-without-a-tokenizer)) — and debits that estimate from the tenant's bucket. If the bucket cannot afford it, the request is denied immediately — no worker or external provider is ever contacted.
2. **Settle.** Once the response is known — the backend's real, reported `prompt_tokens` plus `completion_tokens` — the gateway trues up the reservation: the difference between the estimate and the real total is applied as a signed delta. A response that used *more* than estimated pushes the bucket further down (even temporarily negative, i.e. into debt); a response that used less refunds the difference. A response whose real usage the gateway never learns — on the HTTP routers, every streamed success — keeps the reserved estimate as the final charge instead.

Settling always uses the backend's own reported usage, never the gateway's own estimate — the estimate only exists to gate admission before any real cost has been incurred.

!!! note "Two independent counters, reserved together"
    Each tenant (and optional per-model rule, see below) tracks **tokens per minute** and **requests per minute** as two separate counters. A reservation debits both — the estimated tokens from the token counter, a flat `1` from the request counter — and is denied if *either* counter can't afford it. Only the token counter is trued up at settle time; the request counter's flat debit never changes.

### What happens if a response never completes?

Not every reservation makes it to settlement — a request can fail before dispatch even happens, get preempted, or have its client disconnect mid-stream. Every one of these paths is covered so a reservation never leaks the tenant's budget forever, and never gets resolved twice:

- **A non-2xx final response** (retries exhausted, a request that fails before ever reaching a worker) closes the reservation, keeping the reserved estimate as the final charge — there is no better number to true up against.
- **Preemption or cancellation** before any response exists is caught by the reservation's own RAII cleanup: if nothing else has resolved it by the time its holder is dropped, it self-abandons (keeping the reserved estimate) rather than leaking.
- **A streaming client that disconnects mid-response** is caught the same way — the reservation is attached to the response body's lifetime, so its cleanup fires when the body is dropped, whether that's a clean end-of-stream or a client hang-up.
- **A streaming response that reaches a clean end-of-stream without ever reporting authoritative usage** (on the gRPC router, no `Complete` frame from the backend) settles as "no better number available" rather than truing up to zero — a zero-usage settle would incorrectly refund the entire reservation for a request that plainly did generate output. On the HTTP routers this is every streamed response: the stream is never parsed, and the reservation keeps its estimate when the body ends.

In every case, resolution is idempotent: whichever of settle, close, or abandon reaches a given reservation first wins, and every path after that is a safe no-op.

---

## Reserved once, even across retries

SMG's gRPC pipeline runs its ingress stages once per logical request: preparation (which tokenizes the input), the rate-limit reservation, worker selection, and request building. When a dispatch fails with a retryable error (for example a worker timeout or a `5xx`), a retry selects workers again and re-sends the request that was already built. It never re-tokenizes and never reaches the reserve step again.

The HTTP routers (regular, disaggregated, and external) follow the same contract: the reservation is taken once, before the first dispatch, every retry attempt runs under it, and the request's final answer resolves it.

So the reservation is made **once**, before the first attempt, and the same reservation is settled or closed after the last one: no repeat reservation, no repeat denial check against the backend. A rate-limit denial happens before the retry loop, so it is never retried: retrying immediately against the same exhausted budget would just defeat the wait time the gateway already told the client about.

The model a reservation is scoped to is pinned the same way: the router resolves the request's model to its canonical ID once, up front, and both the reservation and every dispatch attempt use that ID, even if the alias mapping changes mid-request. Without that, a request could be reserved against one model's budget and settled against another's.

A Responses request on the gRPC router is the one case where a single client request can contain several of these logical requests: each iteration of its MCP tool loop is a generation of its own, with a reservation of its own (see [Responses and Decisions](#responses-and-decisions)). The reserved-once rule holds per iteration, not per client request. On the HTTP routers, a Responses request takes one reservation, like any other request.

---

## Tenant and per-model policy

A policy is **tenant-global limits**, plus optional **per-model rules** layered on top:

- Every tenant gets a `tokens_per_minute` / `requests_per_minute` pair — either from an explicit entry keyed by that tenant, or from the config's `default_policy` if it has none.
- A tenant can additionally define per-model rules, each matching an exact model ID or a prefix, with their own `tokens_per_minute` / `requests_per_minute`.
- At most **one** model rule applies per request — an exact match wins over a prefix match, and the longest prefix wins among competing prefixes. Rules never stack with each other.
- When a model rule does apply, a reservation debits **both** the tenant-global bucket and the matching rule's bucket, and is only admitted if both can afford it.

Tenant identity uses the same tenant key SMG already resolves elsewhere in the request path (`auth:<id>`, `header:<id>`, `ip:<address>`, or `anonymous`) — there is no separate identity system to configure.

See the [reference page](../../reference/tenant-rate-limiting.md) for the exact YAML shape and validation rules.

---

## `n>1` and streaming are counted correctly

A few accounting details avoid over- or under-charging a tenant:

- **A shared prompt is charged once, not per choice.** When a request asks for `n>1` completions, every choice shares the same input prompt. The reservation counts the prompt once, and settlement takes a single prompt count from the reported usage, not the sum — the *maximum* across choices on the gRPC router's chat endpoint; each prompt counted once across its `n` answer entries on the HTTP routers' `/generate` route. Only completion tokens, which really are distinct per sample, are summed.
- **A streaming response never settles with partial usage.** On the gRPC router, a streaming response settles only once every expected choice has actually finished: a clean end-of-stream partway through an `n>1` request (some choices completed, others didn't) is not treated as full, trustworthy usage — it closes the reservation at the estimate instead of settling with an understated real count. On the HTTP routers, a streamed response is never parsed at all and always keeps its reserved estimate (see [What settles, per router](#what-settles-per-router)).

---

## Failure handling

- **Startup fails closed:** with `--tenant-rate-limit-enabled`, a missing `--tenant-rate-limit-config`, a file that can't be read or parsed, or a policy that fails validation stops the gateway at startup. An operator who turned rate limiting on never gets a gateway that silently runs unlimited.
- **Missing tenant identity fails open:** if a request somehow reaches the reserve stage without a resolved tenant identity (it shouldn't, since the tenant-resolution middleware runs on every serving route), the gateway logs a warning and skips reservation rather than blocking the request on missing context.

---

## Response codes

A denied reservation returns **429** with the gateway's standard JSON error envelope (`X-SMG-Error-Code: tenant_rate_limit_exceeded`). When the wait is finite, the response also carries `Retry-After: <seconds>`. When the request's estimated cost exceeds the tenant's *total* capacity — meaning it could never be admitted no matter how long the client waits — `Retry-After` is omitted rather than sent as an effectively-infinite number.

---

## Current scope

Tenant rate limiting is wired into every router that serves inference traffic. Each reserves once per logical request, before any dispatch, and answers a denial with the same 429 (see [Response codes](#response-codes)) — no worker or external provider ever sees a denied request:

- **The gRPC router** covers the Chat, Generate, Completion, Messages, Decisions, Embeddings, Classify, Audio Transcriptions, and Responses endpoints (Chat and Responses in regular and Harmony mode alike). It serves [ZMQ-connected workers](../../getting-started/zmq-workers.md) too — `--connection-mode zmq` builds the same router and pipeline — and the reservation happens before a worker is even selected, so for every covered endpoint except Decisions, enforcement is identical whichever transport the workers use.
- **The regular HTTP router** covers `/generate`, `/v1/chat/completions`, `/v1/completions`, `/v1/messages`, `/v1/responses`, `/v1/embeddings`, and `/v1/classify`. Its `/v1/messages/count_tokens`, `/v1/decisions`, `/v1/systemone`, and `/v1/rerank` routes, the audio transcription route, and the realtime routes take no reservation.
- **The disaggregated (PD) HTTP router** covers `/generate`, `/v1/chat/completions`, `/v1/completions`, `/v1/messages`, and `/v1/responses`; its `/v1/messages/count_tokens` and `/v1/rerank` routes take no reservation.
- **The external-provider routers** reserve on the gateway side, before the provider router dispatches, on `/v1/chat/completions`, `/v1/responses`, `/v1/messages`, and `/v1/interactions`; their realtime routes pass through. The reservation is keyed by the model's canonical name, so a request under an alias is charged against its model's budget and per-model rule.

### Estimating without a tokenizer

The gRPC router tokenizes input itself, so its estimate is the prepared request's real input token count. The HTTP routers (regular, PD, and external) have no tokenizer: a pre-tokenized request reserves exactly the token ids it carries, and text is estimated at **1.3 tokens per whitespace-separated word, rounded up**.

The estimate reads the request's own content, including input that routing ignores: a chat history's tool-call arguments, a Messages history's tool inputs and tool-result text, a Responses request's instructions and its function and custom tool calls with their outputs, an Interactions request's system instruction, and every prompt of a batch — every sequence of a `/generate` batch and every prompt of a completions batch, on the PD router even when a text-reading policy routed on the first prompt alone. Images, files, and built-in tool items are not counted. An `x-smg-routing-tokens` hint never sets the estimate — a hint says nothing the gateway can hold a tenant to.

Because the estimate is read from the body, enabling the limiter buffers every request on the regular HTTP router's otherwise streamable path (see [Request Streaming](../performance/request-streaming.md)).

### What settles, per router

On the gRPC router, settlement follows what each endpoint's backend actually reports: an embeddings or classify response carries only a prompt token count — there is no completion to charge — so those requests settle against the backend's reported prompt tokens alone. A transcription settles against the backend's reported prompt and completion counts, like a chat response, even though the transcript returned to the client carries no usage object.

On the HTTP routers, the request's final answer — retried or not — resolves the reservation, exactly once:

- **A buffered success** settles the usage its JSON body reports, in the route's own shape: OpenAI's `prompt_tokens` and `completion_tokens` (chat, completions), the Responses `input_tokens` and `output_tokens`, the Messages `usage` with its cache reads and writes added back onto `input_tokens` (Anthropic's `input_tokens` leave the cached prefix out; the Responses shape already includes it), `/generate`'s per-entry `meta_info` counts, and `prompt_tokens` alone for embeddings and classify. On `/v1/interactions`, the prompt is `total_input_tokens` plus `total_tool_use_tokens`, and the generation is `total_output_tokens` plus `total_thought_tokens` — thinking tokens are generated output and charged with it. A body whose usage can't be read whole closes the reservation at the estimate instead.
- **A streamed success** is never parsed: the reservation rides the response body and keeps the reserved estimate when the body ends, or when the client disconnects.
- **Any non-2xx answer** closes the reservation at the estimate.

### Responses and Decisions

On the gRPC router, the Responses endpoint is metered **per iteration**: every turn of an [MCP tool loop](../extensibility/mcp.md) is a generation of its own, taking exactly the reservation a chat request takes — the estimated input tokens, plus a flat `1` from the request counter — and settling against the backend's reported usage for that turn. A loop that runs *N* turns therefore debits *N* requests, and each turn's real token count, from the tenant's budget; a request without MCP tools runs a single iteration and is charged like a chat request. Each iteration is admitted independently, so a loop can be denied partway through once the budget runs out, and an iteration that fails keeps its reserved estimate as the charge, like any other failed request. On the HTTP routers, a `/v1/responses` request takes a single reservation.

Decisions reserves on the gRPC router only, and the worker transport matters there: the gRPC router serves it only on gRPC-connected SGLang workers, so a Decisions request for a model served entirely by ZMQ-connected workers is rejected as unsupported before any budget is reserved. The regular HTTP router's `/v1/decisions` route takes no reservation at all.

The `--tenant-rate-limit-*` flags belong to the Rust `smg` binary. The Python launcher (`smg launch` from pip, and the container image) does not accept them in v1.12.0.

Enforcement is also **per gateway instance**: a tenant's true limit across several independent SMG instances is roughly the configured value multiplied by the instance count. A distributed backend for exact cluster-wide enforcement is a possible future extension behind the same interface, not something this version provides.

There are no dedicated Prometheus metrics for tenant rate-limit decisions. Denials show up only as `429` responses, which the generic `smg_http_responses_total` counter records with `error_code="tenant_rate_limit_exceeded"`; admissions and settlement deltas aren't observable.

---

## What's Next?

<div class="grid" markdown>

<div class="card" markdown>

### :material-file-document-outline: Tenant Rate Limiting Reference

CLI flags, the full YAML schema, and exact response codes.

[Tenant Rate Limiting Reference →](../../reference/tenant-rate-limiting.md)

</div>

<div class="card" markdown>

### :material-tray-full: Rate Limiting

The concurrency-based limiter this feature complements, not replaces.

[Rate Limiting →](rate-limiting.md)

</div>

<div class="card" markdown>

### :material-priority-high: Priority Scheduling

Another opt-in, per-tenant-policy admission layer — this one orders requests by class instead of metering tokens.

[Priority Scheduling →](priority-scheduling.md)

</div>

<div class="card" markdown>

### :material-refresh: Retries

How a failed dispatch is retried — the mechanism tenant rate limiting has to stay correct across.

[Retries →](retries.md)

</div>

</div>
