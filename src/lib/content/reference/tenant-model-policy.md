---
title: Tenant Model Policy Reference
---

# Tenant Model Policy Reference

The exact contract of the per-tenant model allow-list: the flag, the YAML schema, what a refused request receives, and what `/v1/models` shows. For the identities it keys on, see [Authentication](../concepts/security/authentication.md#per-tenant-model-allow-list).

The policy is **disabled by default**. With it on, every serving route that names a model checks the model against the caller's list before the request reaches a router, and the model inventory routes show the caller its allowed subset.

---

## Enabling it

```bash
smg \
  --worker-urls grpc://w1:9000 grpc://w2:9000 \
  --tenant-api-key team-red:red-secret \
  --tenant-api-key team-blue:blue-secret \
  --tenant-model-policy-config /etc/smg/tenant-model-policy.yaml
```

### CLI flag

| Flag | Default | Description |
|------|---------|--------------|
| `--tenant-model-policy-config` | unset | Path to the tenant model policy YAML. Unset, every tenant may name every model and `/v1/models` lists the whole inventory. |

The flag belongs to the Rust `smg` binary and to the Python launcher (`smg launch --tenant-model-policy-config`).

!!! warning "Fail-closed startup"
    A file that is missing, unreadable, unparsable (an unknown key included) or that fails validation stops the gateway at startup with an error naming `tenant_model_policy_config`. A tenant key that can never match a real request is one of those errors (see below), so a typo cannot silently leave a tenant on the default policy.

---

## YAML configuration

```yaml
default_policy:
  models: ["*"]

tenants:
  - tenant_key: "auth:team-red"
    models:
      - "org/model-a"
      - "org/small-*"
  - tenant_key: "header:team-blue"
    models: []
```

### Schema

| Field | Required | Description |
|-------|----------|-------------|
| `default_policy.models` | yes | The list of every tenant without an entry in `tenants`: the shared `--api-key`, callers known by their address only, anonymous callers, and any tenant key not listed. `["*"]` keeps today's behaviour for them. |
| `tenants[].tenant_key` | yes | The canonical tenant key: `auth:<tenant_id>` for a `--tenant-api-key tenant_id:key`, `header:<id>` for the value of the trusted tenant header, `ip:<address>` or `anonymous`. |
| `tenants[].models` | yes | The tenant's allow-list. An empty list admits no model. |

Unknown keys anywhere in the file are rejected.

### Patterns

Each entry of `models` is one of:

| Pattern | Matches |
|---------|---------|
| `org/model-a` | exactly that model id |
| `org/small-*` | every model id starting with `org/small-` |
| `*` | every model |

A `*` anywhere but at the end, an empty entry, surrounding whitespace and a duplicate entry are configuration errors. A model listed by the id it is served under admits requests that name one of its [aliases](configuration.md#model-aliases) as well; an alias in the list admits that alias as written.

### Reachable tenant keys

Every `tenant_key` must be one a request can actually resolve to:

- `header:<id>` needs `--trust-tenant-header`.
- `auth:<tenant_id>` needs a `--tenant-api-key` whose tenant id is `<tenant_id>`. The shared `--api-key` resolves to `auth:<sha256 of the key, hex>`; list it under that id if it needs a list of its own, otherwise it falls under `default_policy`.
- `ip:<address>` and `anonymous` are always reachable.

Any other key is a startup error.

---

## What a refused request receives

A request whose model is outside the caller's list is answered as if nobody served that model: the same `404` and the same body an unknown model gets, in the caller's envelope.

```http
HTTP/1.1 404 Not Found
Content-Type: application/json
X-SMG-Error-Code: model_not_found

{"error":{"message":"No worker available for model 'org/model-b'","type":"invalid_request_error","param":null,"code":"model_not_found"}}
```

A Messages client (`anthropic-version` header) receives the Messages envelope (`"type":"error"`, `"error":{"type":"not_found_error",...}`), as it does for an unknown model on `/v1/messages`.

It is not a `403`: a distinct answer would tell a tenant which models exist that it may not use, which is what the list hides, and the public APIs answer a model a key cannot access the same way. The gateway's own log names the tenant and the model of a refusal (at most a couple of lines per tenant per second).

### Routes checked

Chat completions, text completions, `/generate`, responses, interactions, messages and `count_tokens`, embeddings, rerank (both routes), classify, decisions, systemone, audio transcriptions, the realtime session routes and the realtime WebSocket and WebRTC routes (the model of their query string). Routes that address stored state by id (`GET /v1/responses/{id}`, conversations) name no model and are not checked.

---

## `/v1/models` and `/v1/models/{id}`

Both routes stay public (no credential required), and under the policy they answer for the caller they can identify:

1. A gateway credential (`Authorization: Bearer <key>` or `x-api-key`): the tenant of that key.
2. Otherwise the identity the serving routes would resolve: the trusted tenant header when `--trust-tenant-header` is on, else the caller's address, else anonymous. These fall under `default_policy` unless listed.

`GET /v1/models` lists the cards the caller's list admits (a card qualifies by its id or by any of its aliases). An allowed subset with nothing in it is an empty list (`200`), not the `503 no_models_available` of an inventory with no model at all. `GET /v1/models/{id}` answers a model outside the list with the `404` above.

Bring-your-own-key callers (a token that is not one of the gateway's keys) are listed the upstream inventory as before, filtered by the default policy.
