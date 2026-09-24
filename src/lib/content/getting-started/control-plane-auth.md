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
- Deprecated load alias: `/get_loads` (the public `/loads` route it aliases needs no credential)
- RL control plane: `/v1/rl/*`, mounted only with `--enable-rl`

Control-plane middleware requires the **admin role** on every one of these routes. A missing or invalid credential gets `401`, and a valid credential with the `user` role gets `403`.

Control-plane credentials apply only to these routes. The inference routes, such as `/v1/chat/completions`, check the shared `--api-key` and per-tenant keys instead, and stay open when neither is set. Set `--api-key` as well to protect them.

### Without Control Plane Auth

When no control-plane API keys and no JWT settings are configured, the routes above fall back to the shared `--api-key`:

| Keys configured | Control plane routes |
|-----------------|----------------------|
| `--api-key` (with or without tenant keys) | Need `Authorization: Bearer <api-key>`; per-tenant keys get `401` |
| `--tenant-api-key` only | Every request gets `401` |
| None | Open to anyone |

The same fallback applies when control-plane auth is configured but fails to initialize at startup, for example because OIDC discovery fails. SMG then logs `Failed to initialize control plane auth: <error>. Falling back to simple API key auth.`

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

Format: `id:name:role:key` where role is `admin` or `user`. Repeat the flag to add more keys. A `user` key authenticates, but every control-plane route answers it with `403`.

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

JWT auth needs both `--jwt-issuer` and `--jwt-audience`. With only one of them, it stays off. SMG checks the token's signature (RS256, RS384, RS512, ES256, or ES384, with the JWKS key named by the token's `kid`), issuer, audience, and expiry, and then assigns a role:

- The role comes from the claim named by `--jwt-role-claim` (default `roles`). Only when that claim is missing does SMG read the `role`, `roles`, `groups`, and `group` claims. `--jwt-role-claim` is a flag of the Rust `smg` binary; the Python launcher (`pip install smg`, and the container image) always uses `roles`.
- Without `--jwt-role-mapping`, a claim value of `admin` or `user` (in any case) is used as the role.
- With mappings, only mapped values count. A token with no mapped value gets the `user` role, so control-plane routes answer it with `403`.

Optional explicit JWKS URI:

```bash
--jwt-jwks-uri https://login.example.com/.well-known/jwks.json
```

Without it, SMG reads `<issuer>/.well-known/openid-configuration` at startup to find the JWKS URI. The discovery URL and the JWKS URI must use `https://`; plain `http://` is accepted only for `localhost`, `127.0.0.1`, and `::1`. An `https://` URL is also rejected when its host is a private, loopback, or link-local IP address, or a name ending in `.internal` or `.local`, such as an in-cluster `*.svc.cluster.local` service. A rejected URL or a failed discovery leaves all of control-plane auth off, control-plane API keys included (see [Without Control Plane Auth](#without-control-plane-auth)).

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

With control-plane auth configured, SMG can log every control-plane request it authenticates, denies, or rejects, as an INFO line with the message `control_plane_audit` on the `smg::audit` log target. Whether it does by default depends on how you run SMG:

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
