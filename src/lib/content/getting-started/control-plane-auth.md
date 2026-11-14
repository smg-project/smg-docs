---
title: Control Plane Auth
---

# Control Plane Auth

Control plane endpoints are used to manage workers, tokenizers, WASM modules, and other gateway operations. Configure admin authentication with JWT/OIDC and/or control-plane API keys.

<div class="prerequisites" markdown>

#### Before you begin

- Completed the [Getting Started](index.md) guide
- Decide how admins authenticate (JWT, API key, or both)

</div>

---

## Protected Control Plane Endpoints

These routes are guarded by control-plane auth middleware when configured:

- Worker management: `/workers`, `/workers/{worker_id}`
- Tokenizer management: `/v1/tokenizers`, `/v1/tokenizers/{tokenizer_id}`, `/v1/tokenizers/{tokenizer_id}/status`
- Parser admin endpoints: `/parse/function_call`, `/parse/reasoning`
- WASM management: `/wasm`, `/wasm/{module_uuid}`
- Cache flush: `/flush_cache`
- Profiling: `/start_profile`, `/stop_profile`
- Diagnostics of the gateway process: `/inflight`, `/heap_profile`
- Effective configuration: `/config`, mounted only when control-plane auth is configured (`404` without it)
- Tenant rate-limit policy: `/rate-limits/tenants` (`GET` and `PUT`), mounted only when control-plane auth is configured (`404` without it)
- Deprecated load alias: `/get_loads` (the public `/loads` route it aliases needs no credential)
- RL control plane: `/v1/rl/*`, mounted only with `--enable-rl`

Control-plane middleware requires the **admin role** for every method on these routes but the read-only ones: a valid credential with the `user` role may `GET` and `HEAD` them, and gets `403` (`Admin role required for this control plane operation`) on any other method. Two reads are the exception, kept to the `admin` role alone: [`GET /config`](../reference/api/admin.md#effective-configuration), whose view names every worker and store address, and [`GET /rate-limits/tenants`](../reference/api/admin.md#tenant-rate-limit-policy); both answer a `user` credential with the same `403`. A missing or invalid credential gets `401`.

Control-plane credentials apply only to these routes. The inference routes, such as `/v1/chat/completions`, check the shared `--api-key` and per-tenant keys instead, and stay open when neither is set. Set `--api-key` as well to protect them.

### Admin Listener

By default the control-plane routes answer on the gateway's data listener (`--port`), alongside the serving routes. Set `--admin-port` to serve them on an admin listener of their own instead: the whole list above moves there and the data listener answers `404` for them, so the control plane can sit on a private address behind its own network policy while the serving routes stay reachable. `--admin-host` sets the admin listener's bind address (the `--host` value by default; it needs `--admin-port`). The probe routes `/health`, `/liveness`, and `/readiness` answer on both listeners, so the admin listener can be probed on its own; the public `/loads` stays on the data listener.

The split changes where the routes answer, not who may call them: control-plane authentication applies on the admin listener exactly as described on this page. When the gateway serves HTTPS, the admin listener presents the same certificate ([Configure TLS](tls.md#enable-server-tls)). The admin listener also sits outside the data listener's `--max-connections` bound, so it stays reachable while the data plane is saturated. Each listener needs its own port; see the [Listeners reference](../reference/configuration.md#listeners).

### Without Control Plane Auth

When no control-plane API keys and no JWT settings are configured, the routes above fall back to the shared `--api-key`:

| Keys configured | Control plane routes |
|-----------------|----------------------|
| `--api-key` (with or without tenant keys) | Need `Authorization: Bearer <api-key>`; per-tenant keys get `401` |
| `--tenant-api-key` only | Every request gets `401` |
| None | Open to anyone |

`GET /config` and the `/rate-limits/tenants` routes (`GET` and `PUT`) do not fall back: a gateway without control-plane auth does not mount them, and the paths answer `404` whatever other keys are set.

This fallback is for a gateway where control-plane auth is not configured at all. For what happens when configured JWT auth fails to initialize at startup, see [Option B: JWT / OIDC](#option-b-jwt-oidc).

#### Default posture

With no key configured at all, which is what the quick-start commands and the Kubernetes recipe give you, the gateway is open: anything that can reach it can list the backends with their addresses (`GET /workers`, `GET /get_loads`), register or remove backends by URL (`POST /workers`, `PUT`/`PATCH`/`DELETE /workers/{id}`), drop the engines' prefix caches (`POST /flush_cache`), start and stop the engines' profilers (`POST /start_profile`, `POST /stop_profile`), write the gateway's own heap profile to disk (`POST /heap_profile`), manage parsers, WASM modules and tokenizers, and send inference requests. Two read-only routes are public in every configuration and name each backend by its address as well: `GET /loads` (the backends' live loads) and `GET /engine_metrics` (the engines' own metrics). The metrics listener (`--prometheus-port`, `29000` by default, bound to `0.0.0.0`, or to `::` when `--host` is an IPv6 address) has no authentication in any configuration and names each backend by its address in its per-worker series.

SMG keeps this default so a local start stays one command, but it says so: when a plane is open, the first log lines carry one `WARN` record whose message starts with `SECURITY POSTURE: this gateway runs without authentication`, with a field per open surface (`control_plane`, `data_plane`, and `metrics_listener` with its bound address) naming the routes and the flag that closes them. It is silent once both planes are keyed, with one exception: a router whose [high-availability mesh](../concepts/architecture/high-availability.md#mesh-mtls) listener runs without TLS still logs the record, whose `control_plane` field names that listener's address and the `--mesh-tls-ca-cert`, `--mesh-tls-cert`, and `--mesh-tls-key` flags that close it. To close the open surfaces:

| Surface | Flag |
|---------|------|
| Control plane routes (the list above) | `--control-plane-api-keys id:name:admin:<key>`, or `--jwt-issuer` with `--jwt-audience`; the shared `--api-key` also gates them. `--admin-port` moves them to their own [admin listener](#admin-listener) that a network policy can fence |
| Serving routes (`/v1/chat/completions` and the rest) | `--api-key <key>`, or `--tenant-api-key tenant:<key>` per tenant |
| Metrics listener | No credential exists: bind it to a private address with `--prometheus-host` and fence the port with a network policy ([Monitoring](monitoring.md#enable-metrics)) |
| Public read-only routes (`GET /loads`, `GET /engine_metrics`, `/v1/models`, `/get_server_info`, `/get_model_info` and the health routes) | No credential exists: fence the gateway's port with a network policy |

In Kubernetes, a gateway that discovers its workers never needs `POST /workers` from outside, so there is no reason to leave the control plane open there: set control-plane keys, and restrict the gateway's ports with a NetworkPolicy to the clients and the scraper that need them.

---

## Option A: API keys

```bash
smg launch \
  --worker-urls http://worker:8000 \
  --control-plane-api-keys 'admin1:PlatformAdmin:admin:super-secret-key'
```

Use the key in `Authorization` header:

```bash
curl -H "Authorization: Bearer super-secret-key" \
  http://localhost:30000/v1/tokenizers
```

Format: `id:name:role:key` where role is `admin` or `user`. Repeat the flag to add more keys. A `user` key reaches the read-only routes (`GET` and `HEAD`, except the admin-only reads `GET /config` and `GET /rate-limits/tenants`); every other control-plane method answers it with `403`.

SMG refuses to start on a malformed entry, including one with a role other than `admin` or `user`; the error does not echo the entry, since it carries the key.

---

## Option B: JWT / OIDC

```bash
smg launch \
  --worker-urls http://worker:8000 \
  --jwt-issuer https://login.example.com \
  --jwt-audience api://smg-control-plane \
  --jwt-role-mapping 'Gateway.Admin=admin' \
  --jwt-role-mapping 'Gateway.User=user'
```

`--jwt-issuer` and `--jwt-audience` are required together: SMG refuses to start with only one of them. It also refuses to start on a malformed `--jwt-role-mapping` entry (one without `=`, or with a gateway role other than `admin` or `user`).

SMG checks the token's signature (RS256, RS384, RS512, ES256, or ES384, with the JWKS key named by the token's `kid`), issuer, audience, expiry, and not-before (`nbf`). The expiry and not-before checks share a 30-second clock-skew leeway, and a token without an `nbf` claim passes the not-before check. SMG then assigns a role:

- The role comes from the claim named by `--jwt-role-claim` (default `roles`). Only when that claim is missing does SMG read the `role`, `roles`, `groups`, and `group` claims. `--jwt-role-claim` is a flag of the Rust `smg` binary; the Python launcher (`pip install smg`, and the container image) always uses `roles`.
- Without `--jwt-role-mapping`, a claim value of `admin` or `user` (in any case) is used as the role.
- With mappings, only mapped values count. A token with no mapped value gets the `user` role: it may `GET` and `HEAD` control-plane routes (except the admin-only reads `GET /config` and `GET /rate-limits/tenants`), and any other method answers it with `403`.

Optional explicit JWKS URI:

```bash
--jwt-jwks-uri https://login.example.com/.well-known/jwks.json
```

Without it, SMG reads `<issuer>/.well-known/openid-configuration` at startup to find the JWKS URI. The discovery URL and the JWKS URI must use `https://`; plain `http://` is accepted only for `localhost`, `127.0.0.1`, and `::1`. An `https://` URL is also rejected when its host is a private, loopback, or link-local IP address, or a name ending in `.internal` or `.local`, such as an in-cluster `*.svc.cluster.local` service.

When JWT validation cannot be set up at startup — the URL is rejected, or discovery fails — control-plane authentication stays required: SMG logs `Failed to initialize JWT authentication: <error>` and keeps running without JWT validation. Keys from `--control-plane-api-keys` keep working; JWTs and every other token get `401`. SMG does not retry JWT initialization while running, so restart the gateway after correcting the configuration or restoring the identity provider.

JWTs are validated first when configured. A token with three dot-separated parts that fails JWT validation gets `401`; SMG does not fall back to API key validation for it. Any other token is checked against the control-plane API keys.

---

## Option C: JWT + API keys together

```bash
smg launch \
  --worker-urls http://worker:8000 \
  --jwt-issuer https://login.example.com \
  --jwt-audience api://smg-control-plane \
  --control-plane-api-keys 'admin1:PlatformAdmin:admin:super-secret-key'
```

This lets human admins use OIDC while service automation uses API keys.

---

## Audit logging

With control-plane auth configured, SMG can audit control-plane requests as INFO lines on the `smg::audit` log target. Every request it authenticates, denies, or rejects gets an admission line with the message `control_plane_audit`.

An admitted request other than a `GET` or `HEAD` also gets an outcome line, `control_plane_mutation`, once it has run to its answer: it carries the HTTP status of that answer and, when the handler named one, the resource it acted on, such as a worker, tokenizer, or WASM-module ID. The two lines share a `request_id`. See [Audit Log Format](../concepts/security/authentication.md#audit-log-format) for the full field list.

The outcome line does not depend on the client staying connected: an admitted mutation runs to completion and writes its line with the status the handler actually answered, even when nobody reads the answer. A handler that fails without answering (a panic) is answered and recorded as a `500` with `outcome` `failed`. [Graceful shutdown](../concepts/reliability/graceful-shutdown.md) drains an admitted mutation like any other in-flight request, so the line is written before an orderly exit; only a stop that cuts the mutation short — the shutdown grace period running out, or a killed process — leaves the admission line alone.

Whether SMG writes these lines by default depends on how you run it:

=== "Cargo binary"

    The `smg` binary from `cargo install` or a source build logs audit events by default. Turn them off with `--disable-audit-logging`:

    ```bash
    smg launch \
      --worker-urls http://worker:8000 \
      --control-plane-api-keys 'admin1:PlatformAdmin:admin:super-secret-key' \
      --disable-audit-logging
    ```

=== "pip or Docker"

    The Python launcher behind `pip install smg` and the container image leaves audit logging off. Turn it on with `--control-plane-audit-enabled` (this launcher has no `--disable-audit-logging`):

    ```bash
    smg launch \
      --worker-urls http://worker:8000 \
      --control-plane-api-keys 'admin1:PlatformAdmin:admin:super-secret-key' \
      --control-plane-audit-enabled
    ```

See [Python Launcher Differences](../reference/configuration.md#python-launcher-differences) for the other flags that differ between the two.

---

## Next Steps

- [Admin API Reference](../reference/api/admin.md)
- [Configuration Reference](../reference/configuration.md#control-plane-authentication)
