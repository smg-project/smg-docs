---
title: Tenant Rate Limiting
---

# Tenant Rate Limiting

[Rate Limiting](rate-limiting.md) protects **workers** from too much concurrent traffic. Tenant rate limiting protects **budgets**: it caps how many LLM tokens and requests a given tenant can consume per minute, independent of how busy the workers are. The two are orthogonal and can run together — a request can be admitted by the concurrency limiter and still be denied because its tenant is over budget, or vice versa.

Think of it like reserving a hotel room online: the site holds the room — and an estimated price — the moment you book, before you have stayed a single night. When you check out, the front desk settles the bill against what you actually used: extra nights cost more, an early checkout refunds the difference. If you never check in at all, the hold quietly expires without ever being charged. Tenant rate limiting works the same way: SMG reserves an estimated number of tokens against a tenant's budget *before* it ever contacts a worker, then settles that reservation against the real, backend-reported token count once the response is known.

---

## Why token-based, not just concurrency-based?

The existing [concurrency limiter](rate-limiting.md) answers "how many requests can be in flight at once?" — a good proxy for protecting GPU memory, but a poor proxy for *cost*. A tenant sending one request with a 100,000-token prompt and a tenant sending a hundred one-line chat messages can look identical to a concurrency limiter while being wildly different in actual LLM spend. Tenant rate limiting answers a different question: "how many tokens (and requests) has this tenant actually consumed this minute?" — measured in the unit that maps to real inference cost, not connection count.

---

## Reserve, then settle

The core mechanic is a two-phase commit against a tenant's budget:

1. **Reserve.** Before dispatching to a worker, the gateway estimates the request's input token count (from its own tokenizer) and debits that estimate from the tenant's bucket. If the bucket cannot afford it, the request is denied immediately — no worker is ever contacted.
2. **Settle.** Once the response is known — the backend's real, reported `prompt_tokens` plus `completion_tokens` — the gateway trues up the reservation: the difference between the estimate and the real total is applied as a signed delta. A response that used *more* than estimated pushes the bucket further down (even temporarily negative, i.e. into debt); a response that used less refunds the difference.

Settling always uses the backend's own reported usage, never the gateway's own estimate — the estimate only exists to gate admission before any real cost has been incurred.

!!! note "Two independent counters, reserved together"
    Each tenant (and optional per-model rule, see below) tracks **tokens per minute** and **requests per minute** as two separate counters. A reservation debits both — the estimated tokens from the token counter, a flat `1` from the request counter — and is denied if *either* counter can't afford it. Only the token counter is trued up at settle time; the request counter's flat debit never changes.

### What happens if a response never completes?

Not every reservation makes it to settlement — a request can fail before dispatch even happens, get preempted, or have its client disconnect mid-stream. Every one of these paths is covered so a reservation never leaks the tenant's budget forever, and never gets resolved twice:

- **A non-2xx final response** (retries exhausted, a request that fails before ever reaching a worker) closes the reservation, keeping the reserved estimate as the final charge — there is no better number to true up against.
- **Preemption or cancellation** before any response exists is caught by the reservation's own RAII cleanup: if nothing else has resolved it by the time its holder is dropped, it self-abandons (keeping the reserved estimate) rather than leaking.
- **A streaming client that disconnects mid-response** is caught the same way — the reservation is attached to the response body's lifetime, so its cleanup fires when the body is dropped, whether that's a clean end-of-stream or a client hang-up.
- **A streaming response that reaches a clean end-of-stream without ever reporting authoritative usage** (no `Complete` frame from the backend) settles as "no better number available" rather than truing up to zero — a zero-usage settle would incorrectly refund the entire reservation for a request that plainly did generate output.

In every case, resolution is idempotent: whichever of settle, close, or abandon reaches a given reservation first wins, and every path after that is a safe no-op.

---

## Reserved once, even across retries

SMG's gRPC pipeline retries a failed dispatch (a worker timeout, a `5xx`) by rerunning the *entire* pipeline for that attempt — including tokenization and worker selection. Naively, that would mean re-reserving tokens on every retry attempt for what is, from the tenant's point of view, a single logical request.

Instead, the reservation is made **once**, by whichever attempt reaches the reserve step first, and cached for the lifetime of that logical request. Every later retry attempt sees the cached outcome and skips straight through — no repeat reservation, no repeat denial check against the backend. A rate-limit denial is also never itself treated as retryable: retrying immediately against the same exhausted budget would just defeat the wait time the gateway already told the client about.

The model a reservation is scoped to is pinned the same way, for the same reason: retries dispatch against the exact canonical model the first attempt resolved, even if the underlying alias mapping changes mid-retry. Without that, a request could be reserved against one model's budget and settled against another's.

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

A few accounting details that were specifically fixed to avoid over- or under-charging a tenant:

- **A shared prompt is charged once, not per choice.** When a request asks for `n>1` completions, every choice shares the same input prompt. The reservation — and the settled usage — uses the *maximum* reported prompt (and cached-token) count across choices, not the sum; only completion tokens, which really are distinct per choice, are summed.
- **A streaming response settles only once every expected choice has actually finished.** A clean end-of-stream partway through an `n>1` request (some choices completed, others didn't) is not treated as full, trustworthy usage — it closes the reservation at the estimate instead of settling with an understated real count.

---

## Fail-open by design

Two situations are deliberately handled by *not* enforcing the limit, rather than by blocking traffic:

- **Startup:** an unparsable or invalid rate-limit YAML is logged at `ERROR` and the gateway starts anyway, without a rate limiter — a broken config file must never take the data plane down.
- **Missing tenant identity:** if a request somehow reaches the reserve stage without a resolved tenant identity (it shouldn't, once tenant-resolution middleware is wired), the gateway logs a warning and skips reservation rather than blocking the request on missing context.

---

## Response codes

A denied reservation returns **429** with the gateway's standard JSON error envelope (`X-SMG-Error-Code: tenant_rate_limit_exceeded`). When the wait is finite, the response also carries `Retry-After: <seconds>`. When the request's estimated cost exceeds the tenant's *total* capacity — meaning it could never be admitted no matter how long the client waits — `Retry-After` is omitted rather than sent as an effectively-infinite number.

---

## Current scope

Tenant rate limiting is wired into SMG's **gRPC router only**, covering the Chat, Generate, Completion, and Messages endpoints (Harmony-mode chat is covered too — it shares the same entry point as regular chat). It is **not yet wired into**: the Responses endpoint, embeddings, classify, audio transcriptions, or any of the HTTP-passthrough / external-provider routers.

Enforcement is also **per gateway instance**: a tenant's true limit across several independent SMG instances is roughly the configured value multiplied by the instance count. A distributed backend for exact cluster-wide enforcement is a possible future extension behind the same interface, not something this version provides.

There are no Prometheus metrics for tenant rate-limit decisions yet — admissions, denials, and settlement deltas aren't currently observable beyond request-level logging and the `429` responses themselves.

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

How the gRPC pipeline retries a failed dispatch — the mechanism tenant rate limiting has to stay correct across.

[Retries →](retries.md)

</div>

</div>
