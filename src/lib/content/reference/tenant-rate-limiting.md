---
title: Tenant Rate Limiting Reference
---

# Tenant Rate Limiting Reference

Precise contract for per-tenant token/request rate limiting: every configuration knob, the YAML policy schema, and the exact response shape. For how it works conceptually — the reserve/settle model, retry handling, `n>1` accounting — see [Tenant Rate Limiting](../concepts/reliability/tenant-rate-limiting.md).

Tenant rate limiting is **disabled by default** and, when enabled, only enforced on the **gRPC router's** Chat, Generate, Completion, and Messages endpoints (Harmony-mode chat included). It does not apply to the Responses endpoint, embeddings, classify, audio transcriptions, or the HTTP/external-provider routers.

---

## Enabling it

```bash
smg \
  --worker-urls grpc://w1:9000 grpc://w2:9000 \
  --tenant-rate-limit-enabled \
  --tenant-rate-limit-config /etc/smg/tenant-rate-limit.yaml
```

### CLI flags

| Flag | Default | Description |
|------|---------|--------------|
| `--tenant-rate-limit-enabled` | `false` | Master switch. When unset, no rate limiter is constructed and every request skips reservation entirely. |
| `--tenant-rate-limit-config` | unset | Path to the tenant-rate-limit YAML. Required when `--tenant-rate-limit-enabled` is set. |

!!! warning "Fail-safe startup"
    If the config path is missing, unreadable, or fails validation, the gateway logs the failure at `ERROR` and starts **without** a rate limiter rather than aborting — a broken policy file must never take the data plane down. Every request then behaves exactly as it did before this feature existed.

---

## YAML configuration

```yaml
default_policy:
  tokens_per_minute: 100000
  requests_per_minute: 600

tenants:
  - tenant_key: "auth:team-red"
    tokens_per_minute: 500000
    requests_per_minute: 3000
    model_rules:
      - rule_id: gpt4-cap
        matcher:
          type: exact
          value: gpt-4
        tokens_per_minute: 50000
        requests_per_minute: 300
      - rule_id: legacy-models
        matcher:
          type: prefix
          value: "legacy-"
        tokens_per_minute: 10000
        requests_per_minute: 100

  - tenant_key: "anonymous"
    tokens_per_minute: 5000
    requests_per_minute: 60
```

A tenant not listed under `tenants` uses `default_policy`. Tenant keys are the same canonical keys SMG resolves elsewhere in the request path — `auth:<id>`, `header:<id>`, `ip:<address>`, or `anonymous` — there's no separate tenant-identity system for this feature.

### `default_policy` / `tenants[]` fields

| Field | Type | Meaning |
|-------|------|---------|
| `tenant_key` | string | Canonical tenant key. Must be **absent** on `default_policy` and **present** on every entry under `tenants`. |
| `tokens_per_minute` | integer, `> 0` | Token-bucket capacity *and* full-minute refill rate for this scope. The bucket starts full, so the first request(s) can burst up to this value immediately. |
| `requests_per_minute` | integer, `> 0` | Same shape as `tokens_per_minute`, but for request count. Each admitted reservation debits exactly `1`, regardless of how many tokens it used. |
| `model_rules` | list, optional | Per-model overrides layered on top of this scope's own limits. See below. |

### `model_rules[]` fields

| Field | Type | Meaning |
|-------|------|---------|
| `rule_id` | string, `[A-Za-z0-9._-]+` | Stable identifier, unique within the tenant (or `default_policy`). |
| `matcher.type` | `exact` \| `prefix` | How `matcher.value` is compared against the request's model ID. |
| `matcher.value` | string | The model ID (`exact`) or model ID prefix (`prefix`) this rule applies to. No surrounding whitespace. |
| `tokens_per_minute` | integer, `> 0` | Independent token bucket for this rule. |
| `requests_per_minute` | integer, `> 0` | Independent request bucket for this rule. |

**At most one model rule applies per request.** An exact match wins over any prefix match; the longest matching prefix wins among competing prefixes. Rules never stack with each other — only with the tenant-global limits. When a rule applies, a reservation must be affordable in **both** the tenant-global scope and the rule's scope, or it's denied.

### Validation

Checked once, at load, before the gateway ever serves traffic on this config:

- `default_policy` must **not** set `tenant_key`; every entry under `tenants` **must**.
- Tenant keys must be non-empty, have no surrounding whitespace, be unique across `tenants`, and be a canonical serving-path tenant key (`auth:`, `header:`, `ip:`-prefixed, or exactly `anonymous`) — a bare ID copy-pasted without its prefix is rejected rather than silently never matching.
- `tokens_per_minute` and `requests_per_minute` must be `> 0`, on every scope (`default_policy`, each tenant, each model rule).
- `rule_id` must match `[A-Za-z0-9._-]+`, and be unique within its tenant (or `default_policy`).
- `matcher.value` must be non-empty and have no surrounding whitespace.
- No two rules within the same tenant may share the same `exact` value, or the same `prefix` value (an `exact` and a `prefix` rule *may* share the same literal string — they're different match kinds).
- Unknown YAML fields anywhere in the document are rejected rather than silently ignored (so a typo like `tenant:` instead of `tenants:` fails loudly instead of compiling to an empty override list).

Any validation failure is the same as an unparsable file: logged at `ERROR`, gateway starts without a rate limiter.

---

## Response codes

| Status | Condition | `X-SMG-Error-Code` | Extra headers |
|--------|-----------|---------------------|----------------|
| **429** Too Many Requests | Reservation denied — the tenant (or matching model rule) doesn't have enough budget right now | `tenant_rate_limit_exceeded` | `Retry-After: <seconds>` — present only for a finite wait |

The response body is the gateway's standard JSON error envelope:

```json
{
  "error": {
    "type": "Too Many Requests",
    "code": "tenant_rate_limit_exceeded",
    "message": "Tenant rate limit exceeded for this request",
    "param": null
  }
}
```

!!! tip "When `Retry-After` is absent"
    If the request's estimated token cost exceeds the scope's *total* capacity — meaning no amount of waiting would ever admit it — the gateway omits `Retry-After` instead of sending an effectively-infinite wait time. Every other denial (the budget is just temporarily exhausted) carries a real `Retry-After` value.

---

## How admission is computed

Each affected scope (tenant-global, and the matching model rule if any) is a continuous-refill bucket with two independent counters:

- **Capacity** = the configured `tokens_per_minute` / `requests_per_minute` value itself. The bucket starts full.
- **Refill rate** = capacity ÷ 60, applied continuously (not in discrete per-minute resets).
- **Reserve** debits the estimated input-token count from the token counter and a flat `1` from the request counter, only if *both* counters can currently afford it. A request is denied if *either* can't.
- **Settle** applies a signed delta — `(real input tokens + real completion tokens) − estimated tokens` — to the token counter only. A response that used more than estimated can push the counter temporarily negative (debt); the request counter is never trued up, since it was always exactly `1`.

---

## Observability

There are currently **no Prometheus metrics** for tenant rate-limit decisions — admissions, denials, and settlement deltas are not exposed as counters or histograms today. Denials are visible only via the `429` responses themselves and standard request logging.

---

## See also

<div class="grid" markdown>

<div class="card" markdown>

### :material-swap-horizontal: Tenant Rate Limiting Concept

The reserve/settle model, retry handling, and `n>1` accounting.

[Tenant Rate Limiting →](../concepts/reliability/tenant-rate-limiting.md)

</div>

<div class="card" markdown>

### :material-tray-full: Rate Limiting Reference

The concurrency-based limiter this feature complements.

[Rate Limiting →](../concepts/reliability/rate-limiting.md)

</div>

<div class="card" markdown>

### :material-cog: Configuration Reference

All other gateway CLI flags and configuration options.

[Configuration →](configuration.md)

</div>

</div>
