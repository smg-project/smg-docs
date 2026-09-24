---
title: Authentication
---

# Authentication

SMG supports multiple authentication methods for securing access to inference APIs and the control plane, including JWT/OIDC integration, API keys, and role-based access control.

---

## Overview

<div class="grid" markdown>

<div class="card" markdown>

### :material-key-chain: Multiple Auth Methods

Support for JWT/OIDC, API keys, and worker authentication to fit your deployment model.

</div>

<div class="card" markdown>

### :material-shield-account: Role-Based Access

Control plane APIs require the admin role. Data plane APIs use API keys, not roles.

</div>

<div class="card" markdown>

### :material-office-building: Enterprise SSO

Integrate with Keycloak, Auth0, Azure AD, Okta, and other OIDC providers.

</div>

<div class="card" markdown>

### :material-clipboard-text: Audit Logging

Track all control plane operations for security monitoring and compliance.

</div>

</div>

---

## Authentication Methods

| Method | Use Case | Configuration |
|--------|----------|---------------|
| **Control plane JWT/OIDC** | Enterprise SSO integration with identity providers (admin routes) | `--jwt-issuer`, `--jwt-audience` |
| **Control plane API keys** | Service accounts and programmatic access (admin routes) | `--control-plane-api-keys` |
| **Data plane API key** | Shared bearer token gating data plane routes; also the default API key of startup and discovered workers | `--api-key` |
| **Data plane tenant keys** | Per-tenant bearer tokens for data plane routes, each resolving to its own tenant identity | `--tenant-api-key` |

### When to Use Each Method

- **Control plane JWT/OIDC**: Use for enterprise deployments with existing identity providers (Keycloak, Auth0, Azure AD, Okta). Provides centralized user management and SSO for control plane operations.
- **Control plane API keys**: Use for service-to-service automation against admin endpoints (CI/CD pipelines, tooling). Simpler to set up but requires manual key management.
- **Data plane API key**: Use when you want a single shared secret that clients present on data plane routes (chat, completions, responses, and so on), and that workers started with the same key accept.
- **Data plane tenant keys**: Use when each team or application needs its own data plane key, so that [tenant rate limits](../reliability/tenant-rate-limiting.md) and priority scheduling can tell callers apart. Each `--tenant-api-key tenant_id:key` resolves to the tenant `auth:<tenant_id>`. Tenant keys never unlock control plane routes, and the flag belongs to the Rust `smg` binary (the Python launcher does not accept it).

JWTs and control plane API keys are checked only on control plane routes. Data plane routes accept only `--api-key` and `--tenant-api-key` credentials, and are open when neither is set.

---

## JWT/OIDC Authentication

JWT (JSON Web Token) authentication allows integration with OIDC-compliant identity providers for enterprise single sign-on on the control plane.

### Configuration Options

| Option | Environment Variable | Description |
|--------|---------------------|-------------|
| `--jwt-issuer` | `JWT_ISSUER` | OIDC issuer URL (required for JWT auth) |
| `--jwt-audience` | `JWT_AUDIENCE` | Expected audience claim (required for JWT auth) |
| `--jwt-jwks-uri` | `JWT_JWKS_URI` | Explicit JWKS URI (auto-discovered if not set) |
| `--jwt-role-claim` | - | Claim name containing roles (default: `roles`) |
| `--jwt-role-mapping` | - | Map IDP roles to gateway roles |

The environment variables and `--jwt-role-claim` belong to the Rust `smg` binary. The Python launcher (`smg launch` from pip, and the container image) takes only the flags and always uses the default `roles` claim (with the fallbacks in [Supported Claims](#supported-claims)). See [Python Launcher Differences](../../reference/configuration.md#python-launcher-differences).

### Basic Setup

Enable JWT authentication by providing the issuer and audience:

```bash
smg \
  --worker-urls http://worker:8000 \
  --jwt-issuer "https://auth.example.com/realms/myrealm" \
  --jwt-audience "smg-gateway"
```

### JWKS Discovery

By default, SMG discovers the JWKS (JSON Web Key Set) endpoint automatically via OIDC discovery (`/.well-known/openid-configuration`). You can override this with an explicit JWKS URI:

```bash
smg \
  --worker-urls http://worker:8000 \
  --jwt-issuer "https://auth.example.com" \
  --jwt-audience "smg-gateway" \
  --jwt-jwks-uri "https://auth.example.com/.well-known/jwks.json"
```

Without `--jwt-jwks-uri`, SMG fetches the discovery document at startup. It fetches the key set on first use, caches it for an hour, and fetches it again when a token names a key it doesn't have. The discovery URL and the JWKS URI must use HTTPS (plain HTTP is allowed only for `localhost`, `127.0.0.1`, and `::1`), must not point at a private, loopback, link-local, or other internal IP address literal, and must not use a host name ending in `.internal` or `.local`. SMG does not follow redirects on these requests. Tokens must carry a `kid` header that names a key in the set.

!!! warning "A failed JWT setup disables control plane authentication"
    If SMG cannot set up JWT validation at startup (for example, OIDC discovery fails or a URL is rejected), it logs `Failed to initialize control plane auth` and starts without control plane authentication, including any `--control-plane-api-keys`. The admin routes then fall back to the `--api-key` check, and are open when no gateway key is configured at all.

### Role Mapping

Map identity provider roles to SMG gateway roles:

```bash
smg \
  --worker-urls http://worker:8000 \
  --jwt-issuer "https://auth.example.com" \
  --jwt-audience "smg-gateway" \
  --jwt-role-mapping "Gateway.Admin=admin" \
  --jwt-role-mapping "Gateway.User=user"
```

**Role Mapping Format**: `idp_role=gateway_role`

| Gateway Role | Permissions |
|--------------|-------------|
| `admin` | Full access to all control plane APIs (workers, WASM modules, tokenizers) |
| `user` | No control plane access: control plane routes answer `403` |

### Supported Claims

SMG reads role values from the configured `--jwt-role-claim` (default: `roles`). Only when the token has no such claim does it fall back to the `role`, `roles`, `groups`, and `group` claims, collecting the values of all of them in that order. Each claim may be a string or an array of strings.

- Without `--jwt-role-mapping`, the first value equal to `admin` or `user` (ignoring case) sets the role.
- With `--jwt-role-mapping`, the first value that has a mapping sets the role; unmapped values are ignored.

If no role is found, the user defaults to the `user` role.

### Supported Algorithms

SMG supports the following JWT signing algorithms:

- **RSA**: RS256, RS384, RS512
- **ECDSA**: ES256, ES384

---

## Identity Provider Setup

### Keycloak

<div class="grid" markdown>

<div class="card" markdown>

**1. Create a Client**

- Navigate to Clients > Create
- Client ID: `smg-gateway`
- Client Protocol: `openid-connect`
- Access Type: `confidential` or `public`

</div>

<div class="card" markdown>

**2. Configure Mappers**

- Add a mapper of type "User Realm Role"
- Token Claim Name: `roles`
- Add to ID token: Yes
- Add to access token: Yes

</div>

</div>

```bash
smg \
  --worker-urls http://worker:8000 \
  --jwt-issuer "https://keycloak.example.com/realms/myrealm" \
  --jwt-audience "smg-gateway" \
  --jwt-role-mapping "admin=admin" \
  --jwt-role-mapping "user=user"
```

### Auth0

<div class="grid" markdown>

<div class="card" markdown>

**1. Create an API**

- Navigate to Applications > APIs > Create API
- Name: `SMG Gateway`
- Identifier: `https://smg.example.com/api`

</div>

<div class="card" markdown>

**2. Add Roles Action**

```javascript
exports.onExecutePostLogin = async (event, api) => {
  const namespace = 'https://smg.example.com';
  if (event.authorization) {
    api.accessToken.setCustomClaim(
      `${namespace}/roles`,
      event.authorization.roles
    );
  }
};
```

</div>

</div>

```bash
smg \
  --worker-urls http://worker:8000 \
  --jwt-issuer "https://your-tenant.auth0.com/" \
  --jwt-audience "https://smg.example.com/api" \
  --jwt-role-claim "https://smg.example.com/roles" \
  --jwt-role-mapping "smg-admin=admin" \
  --jwt-role-mapping "smg-user=user"
```

### Azure AD / Entra ID

<div class="grid" markdown>

<div class="card" markdown>

**1. Register an Application**

- Navigate to Azure Portal > App registrations
- Name: `SMG Gateway`
- Configure app roles: `Gateway.Admin`, `Gateway.User`

</div>

<div class="card" markdown>

**2. Expose an API**

- Navigate to Expose an API
- Set Application ID URI: `api://smg-gateway`

</div>

</div>

```bash
smg \
  --worker-urls http://worker:8000 \
  --jwt-issuer "https://login.microsoftonline.com/{tenant-id}/v2.0" \
  --jwt-audience "api://smg-gateway" \
  --jwt-role-mapping "Gateway.Admin=admin" \
  --jwt-role-mapping "Gateway.User=user"
```

### Okta

```bash
smg \
  --worker-urls http://worker:8000 \
  --jwt-issuer "https://your-org.okta.com/oauth2/default" \
  --jwt-audience "api://smg" \
  --jwt-role-mapping "smg_admins=admin" \
  --jwt-role-mapping "smg_users=user"
```

---

## API Key Authentication

API keys provide a simpler authentication method for service accounts and programmatic access.

### Control Plane API Keys

Configure API keys for control plane access:

```bash
smg \
  --worker-urls http://worker:8000 \
  --control-plane-api-keys "key1:Service Account:admin:sk-your-secret-key-here"
```

**Format**: `id:name:role:key`

| Component | Description |
|-----------|-------------|
| `id` | Unique identifier for the key |
| `name` | Human-readable name/description |
| `role` | Gateway role (`admin` or `user`) |
| `key` | The secret API key value |

### Multiple API Keys

```bash
smg \
  --worker-urls http://worker:8000 \
  --control-plane-api-keys "admin1:Admin Service:admin:sk-admin-key-12345" \
  --control-plane-api-keys "user1:Read Only Service:user:sk-readonly-key-67890"
```

A `user` key authenticates, but every control plane route answers it with `403` (see [Role-Based Access Control](#role-based-access-control)).

### Environment Variable Configuration

For security, pass API keys via environment variable:

```bash
export CONTROL_PLANE_API_KEYS="admin1:Admin Service:admin:sk-admin-key-12345"
smg --worker-urls http://worker:8000
```

The variable holds one key, and only the Rust `smg` binary reads it; the Python launcher (`smg launch` from pip, and the container image) takes keys only from `--control-plane-api-keys`.

### Using API Keys

Clients authenticate by including the API key in the Authorization header:

```bash
curl -H "Authorization: Bearer sk-admin-key-12345" \
  https://smg.example.com/workers
```

### Security Features

- **Hashed Storage**: Keys are SHA-256 hashed immediately; plaintext keys are never stored in memory
- **Constant-Time Comparison**: Key verification uses constant-time comparison to prevent timing attacks
- **Role-Based Access**: Each key is assigned a specific role limiting its permissions

---

## Data Plane API Key (`--api-key`)

The `--api-key` option configures a single bearer token that does two things:

```bash
smg \
  --worker-urls http://worker:8000 \
  --api-key "shared-secret-key"
```

1. **Gates incoming data plane requests.** The gateway requires every client
   request to data plane routes (`/v1/chat/completions`, `/v1/completions`,
   `/v1/responses`, `/v1/embeddings`, `/v1/rerank`, `/v1/messages`,
   `/v1/realtime/*`, etc.) to present `Authorization: Bearer <api-key>`
   (or a `--tenant-api-key` key). Requests without a valid token receive
   `401 Unauthorized`. Public routes such as `/health` and `/v1/models`
   stay open.
2. **Becomes the API key of startup and discovered workers.** Workers from
   `--worker-urls` (and from `--prefill` and `--decode`) and workers that
   Kubernetes service discovery registers get the `--api-key` value as
   their worker API key. A worker added through `POST /workers` uses only
   the `api_key` in its own spec; SMG logs a warning when one arrives
   without a key while `--api-key` is set.

What reaches a worker depends on the path:

| Path | What the worker receives |
|------|--------------------------|
| HTTP regular router | The client's `Authorization` header, as sent. The worker's API key is sent (as `Authorization: Bearer <key>`) only when the client sent no `Authorization` header. With `--api-key` set, every accepted request carries one, so the worker sees the client's token: the shared key, or a tenant key when the client used one. |
| HTTP PD router | The client's allowlisted headers, `Authorization` included, on both the prefill and the decode request. No worker API key is added. |
| gRPC and ZMQ workers | No API key and no `Authorization` metadata. |
| SMG's own calls to HTTP workers (health checks, metadata discovery, load polling) | The worker's API key, as `Authorization: Bearer <key>`. |
| External providers | See [External Providers](../../getting-started/external-providers.md#api-key-handling). |

Service discovery registers each pod as a bare `host:port`, so SMG reaches it over plain HTTP (or plaintext gRPC), and a worker API key crosses the network unencrypted.

This is useful when:

- Clients and workers share a common token
- Workers require authentication (e.g., deployed with API key protection)
- Using DP-aware scheduling that requires authenticated worker queries
- Workers are behind an authentication proxy

Control plane routes (`/workers`, `/wasm`, `/v1/tokenizers`, etc.) use
their own middleware stack. When `--control-plane-api-keys` or
`--jwt-*` are configured they take over as the admin auth backend
(with role-based access control and audit logging); when neither is
set, admin routes fall back to the same `--api-key` bearer check that
guards the data plane (tenant keys are never accepted there, and with
only `--tenant-api-key` set, admin routes reject every request with
`401`). If you run with **only** `--api-key`, the same
shared secret therefore gates both the data plane and the control
plane — which is rarely what you want in production. See
[Control Plane Auth](../../getting-started/control-plane-auth.md) for
configuring JWT/OIDC or dedicated control-plane keys.

---

## Role-Based Access Control

SMG implements role-based access control (RBAC) with two primary roles:

### Admin Role

Full access to all control plane APIs:

- Worker management (`/workers`, `/workers/{id}`)
- WASM module management (`/wasm/*`)
- Tokenizer configuration
- System administration

### User Role

No control plane access. Every control plane route answers a `user` credential with `403 Admin role required for control plane access`.

Roles don't apply to data plane routes (chat completions, completions, embeddings, and the rest): those accept only `--api-key` and `--tenant-api-key` credentials, never JWTs or control plane API keys. `/v1/models` is public.

### Role Assignment

Roles are assigned through:

1. **JWT Claims**: Via `--jwt-role-mapping` configuration
2. **API Key Configuration**: Via the role component in `--control-plane-api-keys`

If no role can be determined, the user defaults to `user` role for safety.

---

## Audit Logging

SMG provides audit logging for control plane operations to support security monitoring and compliance.

### Configuration

With control plane authentication configured (`--jwt-issuer` with `--jwt-audience`, or `--control-plane-api-keys`), the Rust `smg` binary logs audit events **by default**. To disable:

```bash
smg \
  --worker-urls http://worker:8000 \
  --jwt-issuer "https://auth.example.com" \
  --jwt-audience "smg-gateway" \
  --disable-audit-logging
```

The Python launcher (`smg launch` from pip, and the container image) works the other way around: audit logging is off unless you pass `--control-plane-audit-enabled`, and it has no `--disable-audit-logging` flag. Neither launcher audits admin requests checked by the `--api-key` fallback.

### Audit Log Format

Each audit event is an `INFO` log record with target `smg::audit` and message `control_plane_audit`. SMG records every request that reaches the control plane authentication check: successes, `403` role denials, and `401` authentication failures. The layout of the record follows your log format (`--log-json` for JSON).

### Audit Event Fields

| Field | Description |
|-------|-------------|
| `timestamp` | RFC 3339 timestamp of the event |
| `principal` | JWT subject (or its `email`, then `preferred_username`, when `sub` is missing), the API key's `id`, or `unauthenticated` when authentication failed |
| `auth_method` | Authentication method (`jwt`, `api_key`, or `none` when authentication failed) |
| `role` | Role of the principal (`admin`, `user`) |
| `method` | HTTP method (GET, POST, DELETE, etc.) |
| `path` | Request path |
| `resource` | Not filled in v1.11.0 |
| `outcome` | Result (`success`, `denied`) |
| `request_id` | Correlation ID for request tracing |
| `details` | Why the request was denied or failed authentication |

### Viewing Audit Logs

```bash
# Filter for audit logs
RUST_LOG=smg::audit=info smg ...

# Or view in combined logs
kubectl logs -n inference -l app=smg | grep "control_plane_audit"
```

---

## Production Configuration

A production setup combining JWT and API key authentication:

```bash
smg \
  --worker-urls http://worker1:8000 http://worker2:8000 \
  --host 0.0.0.0 \
  --port 443 \
  --tls-cert-path /etc/certs/server.crt \
  --tls-key-path /etc/certs/server.key \
  --jwt-issuer "https://auth.example.com/realms/production" \
  --jwt-audience "smg-gateway" \
  --jwt-role-mapping "Gateway.Admin=admin" \
  --jwt-role-mapping "Gateway.User=user" \
  --control-plane-api-keys "ci-cd:CI/CD Pipeline:admin:${CI_CD_API_KEY}" \
  --api-key "${DATA_PLANE_API_KEY}"
```

JWT and the control plane key protect the control plane routes. `--api-key` protects the data plane routes, which are open without it.

---

## Kubernetes Deployment

### Secret for API Keys

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: smg-auth
  namespace: inference
type: Opaque
stringData:
  CONTROL_PLANE_API_KEYS: "admin1:Admin:admin:sk-secret-key"
```

### ConfigMap for JWT Configuration

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: smg-config
  namespace: inference
data:
  JWT_ISSUER: "https://auth.example.com/realms/production"
  JWT_AUDIENCE: "smg-gateway"
```

### Deployment

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: smg
  namespace: inference
spec:
  template:
    spec:
      containers:
        - name: smg
          image: lightseekorg/smg:latest
          envFrom:
            - configMapRef:
                name: smg-config
            - secretRef:
                name: smg-auth
          args:
            - --service-discovery
            - --selector
            - app=sglang-worker
            - --jwt-issuer
            - $(JWT_ISSUER)
            - --jwt-audience
            - $(JWT_AUDIENCE)
            - --control-plane-api-keys
            - $(CONTROL_PLANE_API_KEYS)
            - --jwt-role-mapping
            - "Gateway.Admin=admin"
            - --jwt-role-mapping
            - "Gateway.User=user"
            - --control-plane-audit-enabled
```

The container image runs the Python launcher, which does not read `JWT_ISSUER`, `JWT_AUDIENCE`, or `CONTROL_PLANE_API_KEYS` itself, so the `args` pass them as flags; Kubernetes expands each `$(VAR)` reference from the container's environment. `--control-plane-audit-enabled` turns on audit logging, which the Python launcher leaves off by default.

---

## Troubleshooting

### JWT Validation Failures

**Symptom**: `Invalid JWT` or `Token validation failed` errors

**Solutions**:

1. Verify issuer URL matches exactly (including trailing slash):
   ```bash
   curl https://auth.example.com/.well-known/openid-configuration
   ```

2. Verify audience claim matches your configuration:
   ```bash
   echo "YOUR_JWT" | cut -d. -f2 | base64 -d | jq .
   ```

3. Check clock synchronization (JWT validation uses time-based claims and allows 30 seconds of clock skew)

4. Verify JWKS endpoint is accessible from the SMG pod

### API Key Not Working

**Symptom**: `Invalid authentication token` errors

**Solutions**:

1. Verify key format is correct: `id:name:role:key`
2. Check for special characters that may need escaping
3. Ensure the Authorization header format is correct: `Bearer <key>`

### Role Mapping Issues

**Symptom**: Users getting wrong permissions

**Solutions**:

1. Check which claim contains roles in your JWT
2. Verify role mapping syntax: `idp_role=gateway_role`
3. Check if role claim name needs to be specified with `--jwt-role-claim`

---

## Security Best Practices

<div class="grid" markdown>

<div class="card" markdown>

### :material-shield-lock: Use HTTPS

Always enable TLS for the gateway in production.

</div>

<div class="card" markdown>

### :material-key-change: Rotate Keys

Regularly rotate API keys and use short-lived JWT tokens.

</div>

<div class="card" markdown>

### :material-account-lock: Least Privilege

Assign `user` role by default, `admin` only when needed.

</div>

<div class="card" markdown>

### :material-clipboard-text: Enable Auditing

Keep audit logs for security monitoring and compliance.

</div>

</div>

---

## What's Next?

<div class="grid" markdown>

<div class="card" markdown>

### :material-traffic-light: Rate Limiting

Protect against overload and abuse.

[Rate Limiting →](../reliability/rate-limiting.md)

</div>

<div class="card" markdown>

### :material-shield-check: High Availability

Deploy SMG in a highly available configuration.

[High Availability →](../architecture/high-availability.md)

</div>

<div class="card" markdown>

### :material-chart-box: Metrics Reference

Count `401` and `403` responses by path with `smg_http_responses_total`.

[Metrics Reference →](../../reference/metrics.md)

</div>

</div>
