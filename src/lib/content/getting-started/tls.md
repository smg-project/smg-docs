---
title: Configure TLS
---

# Configure TLS

This guide shows how to serve SMG over HTTPS, and how to present a client certificate to workers that require mutual TLS (mTLS).

<div class="prerequisites" markdown>

#### Before you begin

- Completed the [Getting Started](index.md) guide
- TLS certificates (or follow the steps below to generate them)

</div>

---

## Overview

SMG supports TLS configurations for securing communications:

| Configuration | Purpose | Status |
|---------------|---------|--------|
| **Server TLS** | HTTPS for client → gateway communication | Available |
| **Client mTLS** | Mutual TLS for gateway → worker communication | Python launcher only |

!!! info "Client mTLS"
    The client certificate flags (`--client-cert-path`, `--client-key-path`, and `--ca-cert-paths`) exist only in the Python launcher: `smg launch` from pip, and the container image. The Rust `smg` binary has no client certificate flags in v1.11.0. See [Client mTLS to Workers](#client-mtls-to-workers).

---

## Generate Certificates

For testing, generate self-signed certificates:

### Step 1: Create CA

```bash
# Generate CA private key
openssl genrsa -out ca.key 4096

# Generate CA certificate
openssl req -new -x509 -days 365 -key ca.key -out ca.crt \
  -subj "/CN=SMG CA/O=SMG"
```

### Step 2: Create server certificate

```bash
# Generate server private key
openssl genrsa -out server.key 2048

# Generate server CSR
openssl req -new -key server.key -out server.csr \
  -subj "/CN=smg.example.com/O=SMG"

# Sign with CA
openssl x509 -req -days 365 -in server.csr \
  -CA ca.crt -CAkey ca.key -CAcreateserial \
  -out server.crt
```

### Step 3: Create client certificate (for mTLS to workers)

!!! note "Only for workers that verify client certificates"
    Skip this step unless your workers require a client certificate. The gateway presents it through the Python launcher's `--client-cert-path` and `--client-key-path` flags.

```bash
# Generate client private key
openssl genrsa -out client.key 2048

# Generate client CSR
openssl req -new -key client.key -out client.csr \
  -subj "/CN=smg-client/O=SMG"

# Sign with CA
openssl x509 -req -days 365 -in client.csr \
  -CA ca.crt -CAkey ca.key -CAcreateserial \
  -out client.crt
```

---

## Enable Server TLS

Serve the gateway over HTTPS.

### Configuration

```bash
smg \
  --worker-urls http://worker:8000 \
  --tls-cert-path /path/to/server.crt \
  --tls-key-path /path/to/server.key \
  --host 0.0.0.0 \
  --port 443
```

Set both flags or neither: SMG refuses to start with only one of them, or when it can't read one of the files. Both files are PEM. HTTPS covers the main listener only; the Prometheus metrics endpoint (`--prometheus-port`) stays plain HTTP.

### Verification

```bash
curl --cacert ca.crt https://smg.example.com/health
```

---

## Client mTLS to Workers

When your workers serve HTTPS and verify client certificates, give the gateway a client certificate and its key, plus the CA that signed the workers' server certificates:

```bash
smg launch \
  --worker-urls https://worker1:8443 https://worker2:8443 \
  --client-cert-path /path/to/client.crt \
  --client-key-path /path/to/client.key \
  --ca-cert-paths /path/to/ca.crt
```

| Flag | Description |
|------|-------------|
| `--client-cert-path` | Client certificate (PEM) that the gateway presents to workers. Set it together with `--client-key-path`. |
| `--client-key-path` | Private key (PEM) for the client certificate. |
| `--ca-cert-paths` | One or more CA certificates (PEM) that the gateway trusts, in addition to the system's trusted roots, when it verifies worker certificates. Takes several paths after one flag, and the flag can repeat. |

- These flags belong to the Python launcher (`smg launch` from pip, and the container image). The Rust `smg` binary does not accept them.
- SMG reads the files at startup. Setting only one of `--client-cert-path` and `--client-key-path`, or passing a file it can't read, stops startup.
- The certificate and CAs apply to the gateway's HTTP connections: requests to HTTP workers and external providers, and worker health checks. gRPC workers (`grpc://`, `grpcs://`) don't use them.

If you run the Rust binary, terminate mTLS toward the workers outside SMG, for example with a service mesh (such as Istio) or a sidecar proxy.

---

## Full TLS Configuration

With the Rust `smg` binary, TLS covers the gateway's own listener:

```bash
smg \
  --worker-urls http://worker1:8000 http://worker2:8000 \
  --tls-cert-path /etc/certs/server.crt \
  --tls-key-path /etc/certs/server.key \
  --api-key "${API_KEY}" \
  --host 0.0.0.0 \
  --port 443
```

With the Python launcher, one command can serve HTTPS and present a client certificate to HTTPS workers:

```bash
smg launch \
  --worker-urls https://worker1:8443 https://worker2:8443 \
  --tls-cert-path /etc/certs/server.crt \
  --tls-key-path /etc/certs/server.key \
  --client-cert-path /etc/certs/client.crt \
  --client-key-path /etc/certs/client.key \
  --ca-cert-paths /etc/certs/ca.crt \
  --api-key "${API_KEY}" \
  --host 0.0.0.0 \
  --port 443
```

---

## Kubernetes with cert-manager

Use cert-manager for automatic certificate management.

### Step 1: Install cert-manager

```bash
kubectl apply -f https://github.com/cert-manager/cert-manager/releases/download/v1.14.0/cert-manager.yaml
```

### Step 2: Create Certificate

```yaml title="smg-certificate.yaml"
apiVersion: cert-manager.io/v1
kind: Certificate
metadata:
  name: smg-tls
  namespace: inference
spec:
  secretName: smg-tls-secret
  issuerRef:
    name: letsencrypt-prod
    kind: ClusterIssuer
  dnsNames:
    - smg.example.com
```

### Step 3: Mount in deployment

```yaml
spec:
  containers:
    - name: smg
      volumeMounts:
        - name: tls-certs
          mountPath: /etc/certs
          readOnly: true
      args:
        - --tls-cert-path
        - /etc/certs/tls.crt
        - --tls-key-path
        - /etc/certs/tls.key
  volumes:
    - name: tls-certs
      secret:
        secretName: smg-tls-secret
```

---

## Verification

### Test server TLS

```bash
# With CA certificate
curl --cacert ca.crt https://smg.example.com/health

# Check certificate details
openssl s_client -connect smg.example.com:443 -showcerts
```

### Test worker connectivity

```bash
# Check SMG logs for worker connections
kubectl logs -n inference -l app=smg | grep -i worker

# Verify worker connection via control plane API
curl --cacert ca.crt https://smg.example.com/workers
```

---

## Troubleshooting

??? question "Certificate verification failed"

    1. Verify CA certificate matches:
    ```bash
    openssl verify -CAfile ca.crt server.crt
    ```

    2. Check certificate expiration:
    ```bash
    openssl x509 -in server.crt -noout -dates
    ```

    3. Verify hostname matches:
    ```bash
    openssl x509 -in server.crt -noout -text | grep DNS
    ```

??? question "Connection refused"

    1. Check SMG is listening on correct port:
    ```bash
    netstat -tlnp | grep smg
    ```

    2. Verify TLS configuration in logs:
    ```bash
    smg --tls-cert-path ... 2>&1 | grep -i tls
    ```

??? question "Handshake failure"

    1. Check TLS version compatibility
    2. Verify cipher suite support
    3. Ensure certificate chain is complete

---

## What's Next?

- [Monitoring](monitoring.md) — Set up observability and alerts
- [Authentication Concepts](../concepts/security/authentication.md) — Security architecture and controls
