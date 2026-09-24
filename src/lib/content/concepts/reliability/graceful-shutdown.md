---
title: Graceful Shutdown
---

# Graceful Shutdown

Graceful shutdown allows in-flight requests to complete before the gateway terminates, preventing request failures during deployments and restarts.

---

## Overview

<div class="grid" markdown>

<div class="card" markdown>

### :material-power: Clean Termination

Allow existing requests to finish rather than abruptly closing connections.

</div>

<div class="card" markdown>

### :material-rocket-launch: Zero-Downtime Deployments

Deploy updates without causing client-visible errors.

</div>

<div class="card" markdown>

### :material-timer: Configurable Grace Period

Control how long to wait for in-flight requests.

</div>

<div class="card" markdown>

### :material-traffic-light: Readiness Signaling

`/readiness` reports `503` from the start of shutdown, so load balancers stop routing to the gateway before it stops accepting connections.

</div>

</div>

---

## Why Graceful Shutdown?

Without graceful shutdown:

- **Abrupt termination**: Active requests are immediately disconnected
- **Client errors**: In-flight requests return connection errors
- **Data loss**: Streaming responses may be truncated
- **Deployment failures**: Rolling updates cause visible errors

With graceful shutdown:

- **Request completion**: Active requests finish normally
- **No client errors**: Users don't see deployment-related failures
- **Clean streaming**: Streaming responses complete before shutdown
- **Smooth deployments**: Zero-downtime rolling updates

---

## How It Works

### Shutdown Sequence

1. **Shutdown signal received** — SIGTERM or SIGINT (Ctrl+C) starts graceful shutdown. There is no HTTP endpoint that triggers it.
2. **Readiness flips to draining** — `/readiness` starts returning `503` with reason `"draining"`, on the main port and on the [dedicated probe port](#dedicated-probe-port), while `/health` and `/liveness` stay `200`. Load balancers de-list the gateway without restarting it.
3. **Settle window** — the listener keeps accepting connections for half the grace period, capped at 5 seconds, so requests still routed to the gateway while load balancers and EndpointSlices catch up are served instead of refused.
4. **Stop accepting new connections** — `axum_server`'s handle stops the TCP accept loop; new connections are refused at the socket level rather than receiving a `503` response.
5. **Drain in-flight requests** — existing requests, including streaming responses, continue while SMG waits for them for the rest of the grace period. When that runs out, SMG shuts down with any remaining requests still in flight.
6. **Clean exit** — once all requests complete (or the grace period expires), background components are cleaned up (worker and mesh discovery tasks, the MCP orchestrator) and the process exits.

The settle window is carved out of the grace period, so the whole sequence stays within `--shutdown-grace-period-secs`. At the default of 180 seconds, the settle window is 5 seconds and in-flight requests get up to 175 seconds.

---

## Configuration

```bash
smg \
  --worker-urls http://w1:8000 http://w2:8000 \
  --shutdown-grace-period-secs 180
```

### Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `--shutdown-grace-period-secs` | `180` (3 min) | Total shutdown budget: the settle window (half of it, at most 5 seconds) plus the wait for in-flight requests |

---

## Recommended Configurations

<div class="grid" markdown>

<div class="card" markdown>

### :material-lightning-bolt: Fast Shutdown

Quick termination for development.

```bash
smg --shutdown-grace-period-secs 10
```

**Use when**: Development, testing, quick restarts

</div>

<div class="card" markdown>

### :material-server-network: Production Standard

Balanced grace period for typical workloads.

```bash
smg --shutdown-grace-period-secs 180
```

**Use when**: Standard production deployments

</div>

<div class="card" markdown>

### :material-cog: Batch Processing

Long grace period for long-running requests.

```bash
smg --shutdown-grace-period-secs 600
```

**Use when**: Batch inference, long-running generations

</div>

<div class="card" markdown>

### :material-clock-fast: Critical Low-Latency

Minimal grace for latency-sensitive systems.

```bash
smg --shutdown-grace-period-secs 30
```

**Use when**: Very short requests, rapid scaling

</div>

</div>

---

## Triggering Shutdown

### Via Signal

```bash
# Find the SMG process
pgrep -f smg

# Send SIGTERM for graceful shutdown
kill -TERM <pid>

# Or SIGINT (Ctrl+C in terminal)
kill -INT <pid>
```

### Kubernetes Integration

Kubernetes sends SIGTERM by default when terminating pods. Configure `terminationGracePeriodSeconds` to match or exceed your SMG grace period:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: smg
spec:
  template:
    spec:
      terminationGracePeriodSeconds: 210  # SMG grace + buffer
      containers:
        - name: smg
          args:
            - --shutdown-grace-period-secs=180
```

!!! warning "Kubernetes timeout"
    Kubernetes will force-kill the pod after `terminationGracePeriodSeconds`. Set this **higher** than `--shutdown-grace-period-secs` to ensure SMG has time to complete its graceful shutdown.

---

## Sizing the Grace Period

Consider these factors when setting the grace period:

| Factor | Impact on Grace Period |
|--------|------------------------|
| **Average request duration** | Grace period should exceed typical request time |
| **Longest expected request** | Batch jobs may need longer grace periods |
| **Streaming responses** | Long streams need extended grace periods |
| **Deployment frequency** | Frequent deployments may need shorter periods |
| **Scaling responsiveness** | Autoscaling may need faster termination |

### Calculation Guidelines

```
grace_period = max(
    avg_request_duration * 3,
    p99_request_duration * 1.5,
    max_streaming_duration
)
```

**Example**: If your average request is 30s, p99 is 60s, and max streaming is 120s:

```
grace_period = max(90, 90, 120) = 120 seconds
```

---

## Integration with Load Balancers

For zero-downtime deployments, coordinate with your load balancer:

### Pre-Stop Hook (Kubernetes)

SMG already keeps accepting connections for up to 5 seconds after the signal while `/readiness` reports `503`. If your load balancer needs longer to stop sending traffic, add a pre-stop hook that delays the signal:

```yaml
spec:
  containers:
    - name: smg
      lifecycle:
        preStop:
          exec:
            command: ["/bin/sh", "-c", "sleep 5"]
```

The sleep allows the load balancer to stop sending new traffic before SMG begins its graceful shutdown. Kubernetes starts the `terminationGracePeriodSeconds` countdown before it runs the hook, so set `terminationGracePeriodSeconds` higher than the sleep plus `--shutdown-grace-period-secs`.

### Health Check Coordination

As soon as SMG receives the shutdown signal and begins draining, `/readiness` flips to `503 Service Unavailable` with reason `"draining"`, while `/health` and `/liveness` keep returning `200 OK` throughout the drain. Kubernetes therefore removes the pod from Service endpoints (stopping new connections) without restarting it:

```bash
curl http://gateway:30000/health
# Returns 200 OK both during normal operation and throughout the drain

curl http://gateway:30000/readiness
# 200 while serving; 503 {"status":"not ready","reason":"draining"} once shutdown begins
```

`/readiness` also returns `503` independent of the shutdown signal when SMG cannot serve: when no healthy workers remain (in prefill/decode mode, when either side has no healthy worker), or while a healthy gRPC or ZMQ worker's tokenizer is not registered yet. See [Gateway Probe Endpoints](health-checks.md#gateway-probe-endpoints) for every reason. The readiness decision is maintained event-driven from worker registry state and served from cached memory, so probes stay O(1) regardless of fleet size.

### Dedicated Probe Port

Under heavy load the main listener's probe routes share the request runtime, so probe responses can lag behind request traffic. Pass the `--health-check-port` flag (Python: `health_check_port`) to additionally serve `/liveness`, `/readiness`, and `/health` on a dedicated plain-HTTP port, handled by a small isolated runtime on its own OS thread — probe latency then stays flat even when the request runtime is saturated, and the port keeps answering through the entire drain window:

```yaml
spec:
  containers:
    - name: smg
      args: ["--health-check-port", "30001"]
      livenessProbe:
        httpGet: { path: /liveness, port: 30001 }
      readinessProbe:
        httpGet: { path: /readiness, port: 30001 }
```

When `--health-check-port` is unset no extra listener is started. The probe routes always remain available on the main port as well.

---

## Worker Draining

Graceful shutdown drains the gateway itself. Removing a worker from a running gateway is a separate drain, controlled by `--drain-settle-secs`:

| | Gateway shutdown | Worker removal |
|---|------------------|----------------|
| **Trigger** | SIGTERM or SIGINT to SMG | [Service discovery](../architecture/service-discovery.md) sees the pod terminate or turn unready, [worker auto-recovery](health-checks.md#worker-auto-recovery), or `DELETE /workers/{worker_id}` |
| **Effect** | `/readiness` reports `503`; the listener stops accepting after the settle window | The worker moves to `Draining` and receives no new requests |
| **Wait** | Until in-flight requests finish, up to `--shutdown-grace-period-secs` | A fixed `--drain-settle-secs` (default `5`, per worker `health.drain_settle_secs`), then the worker is removed |

The worker settle window is a fixed delay: SMG does not wait for the worker's in-flight request count to reach zero. Only workers that were `Ready` are drained; `Pending`, `NotReady`, and `Failed` workers are not, and a removal that finds no `Ready` worker skips the wait. Set `--drain-settle-secs 0` to skip draining. The flag belongs to the `smg` binary; the Python launcher does not accept it yet.

---

## Monitoring

### Shutdown Events

Watch logs for shutdown-related messages:

```text
# Signal received
INFO Received Ctrl+C, starting graceful shutdown
# or
INFO Received terminate signal, starting graceful shutdown

# Readiness flips to 503 "draining"
INFO Beginning graceful shutdown: readiness draining in_flight=5

# Settle window before the accept loop stops
INFO Keeping listener open during load-balancer propagation window settle_secs=5

# Drain completes within the grace period
INFO All in-flight requests drained

# Or the grace period expires with requests still running
WARN Drain timed out, forcing shutdown with requests still in-flight remaining=2 timeout_secs=175

# Component teardown
INFO HTTP server stopped. Starting component cleanup...
INFO Cleanup complete. Process exiting.
```

### Metrics During Shutdown

| Metric | Observation |
|--------|-------------|
| `smg_worker_requests_active` | Should decrease towards 0 |
| `smg_http_requests_total` | New requests should stop |

---

## Tuning Guidelines

| Symptom | Potential Adjustment |
|---------|---------------------|
| Requests failing during deployment | Increase `--shutdown-grace-period-secs` |
| Slow scaling down | Decrease `--shutdown-grace-period-secs` |
| Kubernetes force-killing pods | Increase `terminationGracePeriodSeconds` |
| Streaming responses truncated | Match grace period to max stream duration |
| Connections refused right after the signal | Add a `preStop` sleep so load balancers stop routing first, and add the sleep to `terminationGracePeriodSeconds` |

---

## What's Next?

<div class="grid" markdown>

<div class="card" markdown>

### :material-electric-switch: Circuit Breakers

Isolate failing workers to prevent cascade failures.

[Circuit Breakers →](circuit-breakers.md)

</div>

<div class="card" markdown>

### :material-heart-pulse: Health Checks

Proactive worker monitoring and failure detection.

[Health Checks →](health-checks.md)

</div>

<div class="card" markdown>

### :material-traffic-light: Rate Limiting

Protect workers from overload with token bucket rate limiting.

[Rate Limiting →](rate-limiting.md)

</div>

</div>
