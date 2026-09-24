---
title: Service Discovery
---

# Service Discovery

SMG discovers workers from Kubernetes pods and keeps its worker registry in step with the cluster. Pods that match a label selector and are Ready become workers; workers whose pods are deleted, stop matching, or turn unready are drained and removed.

---

## Overview

<div class="grid" markdown>

<div class="card" markdown>

### :material-kubernetes: Level-Triggered Reconcile

An informer keeps a local cache of pods. Every pass compares the whole cache with the registry, so a missed watch event is corrected on the next pass.

</div>

<div class="card" markdown>

### :material-sync: Readiness-Aware Scaling

Workers are added when their pods become Ready and drained when pods scale down, terminate, or turn unready.

</div>

<div class="card" markdown>

### :material-lan: Multi-Port Pods

A pod that runs several engine servers lists their ports in one annotation, and each port becomes its own worker.

</div>

<div class="card" markdown>

### :material-swap-horizontal: PD Support

Separate discovery for prefill and decode workers in disaggregated deployments.

</div>

</div>

---

## How It Works

### Informer and Reconcile Loop

SMG runs a Kubernetes informer for pods. An initial LIST fills a local cache and a WATCH keeps it current. If the watch drops, the informer reconnects (with backoff after errors) and resumes from the last version it saw; it lists again only when the API server no longer has that version (HTTP 410 Gone). SMG passes the label selector to the API server, so the cache holds only candidate pods.

A reconcile pass turns the cache into the set of desired workers, compares it with the workers that discovery registered earlier, and submits `AddWorker` and `RemoveWorker` jobs to the control-plane job queue for the difference.

| Trigger | Behavior |
|---------|----------|
| Watch event | Any change to a cached pod starts a pass. Bursts, such as a rollout or a re-list, are coalesced: SMG waits 1 second, then runs one pass. |
| Periodic resync | A pass runs every 60 seconds. It reads the local cache only and makes no API calls. |

Because every pass compares complete state instead of replaying individual events, discovery converges even when events are missed:

- A pod deleted while the watch was disconnected drops out of the cache once the watch recovers, from the replayed delete event or a fresh list, and its workers are removed on the next pass.
- A registration that failed is submitted again on a later pass, because the worker is still missing from the registry.
- A worker registered for a pod that no longer exists, for example a registration that finished after its pod was deleted, is removed on the next pass.

While an add or remove job for an address is pending or running, later passes skip that address; completed and failed jobs do not block, so failures are retried.

### Which Pods Become Workers

A pod contributes workers when all of the following hold:

- Its labels match `--selector` (or a role selector in PD mode).
- It is not terminating (no `deletionTimestamp`).
- It has a pod IP.
- Its phase is `Running` and its `Ready` condition is `True`.

SMG routes to pod IPs directly, so it honors the pod's aggregate `Ready` condition, including readiness gates, the same way a Service endpoint does. Each matching pod contributes one worker per port (see [Multi-Port Pods](#multi-port-pods)). A worker's address is `<pod-ip>:<port>`, with IPv6 addresses in brackets. Registration probes HTTP and gRPC on that address and keeps the protocol that answers, preferring HTTP when both do.

### Ownership

Every worker that discovery registers carries two labels, `smg.ai/pod-name` and `smg.ai/pod-uid`. The reconciler only adds and removes workers that carry `smg.ai/pod-uid` and were registered by this gateway, so:

- Workers added with `--worker-urls` or through the [worker API](../../reference/api/admin.md) are never touched by discovery.
- Workers synchronized from mesh peers are left to the gateway that owns them.
- When a pod is replaced by one with a new UID at the same address (for example, a restart that keeps its IP), the old worker is removed and the new one registered.

---

## Configuration

### Basic Setup

```bash
smg \
  --service-discovery \
  --selector app=sglang-worker \
  --service-discovery-namespace inference \
  --service-discovery-port 8000
```

### Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `--service-discovery` | `false` | Enable Kubernetes service discovery (also turns on IGW mode, see below) |
| `--selector` | - | Label selector for worker pods, as `key=value` pairs (required unless PD or EPD mode is on) |
| `--service-discovery-namespace` | (all namespaces) | Kubernetes namespace to watch |
| `--service-discovery-port` | `80` | Worker port for pods without a `smg.ai/worker-ports` annotation |
| `--model-id-from` | - | Override each worker's model ID from pod metadata: `namespace`, `label:<key>`, or `annotation:<key>` |
| `--model-alias` | - | Extra client-facing model name, `alias=canonical`, repeatable; applies to discovered workers too ([details](../../reference/configuration.md#service-discovery-kubernetes)) |

!!! note "IGW mode"
    `--service-discovery` turns on IGW mode (`--enable-igw`) automatically, in the `smg` binary and in the Python launcher (`smg launch` from pip, and the container image). PD and EPD deployments keep their disaggregated routing mode under IGW.

### Fleet Scale

Discovery submits its jobs to the gateway's control-plane job queue, which it shares with tokenizer, MCP, and WASM jobs.

| Parameter | Default | Description |
|-----------|---------|-------------|
| `--job-queue-capacity` | `1000` | Maximum pending control-plane jobs. A reconcile pass waits while the queue is full, so set this at least as high as the number of discovered workers. |
| `--job-queue-concurrency` | `200` | Maximum control-plane jobs dispatched concurrently |

The cost of registering one worker does not grow with the fleet, and a job's in-flight status is kept until the job finishes, so a long registration wave on a new gateway replica is not submitted twice.

### Worker Startup

| Parameter | Default | Description |
|-----------|---------|-------------|
| `--worker-startup-delay` | `0` | Seconds to wait after a worker is submitted before its first startup probe |
| `--worker-startup-check-interval` | `30` | Seconds between startup probes while registration waits for the engine to answer |
| `--worker-startup-timeout-secs` | `1800` | How long registration waits for the engine before the job fails |

Discovery only submits workers for Ready pods, so give engine pods a readiness probe that passes once the model is loaded. If a pod reports Ready earlier, registration keeps probing at `--worker-startup-check-interval` until the engine answers or `--worker-startup-timeout-secs` elapses. A failed registration is submitted again on a later reconcile pass.

---

## Multi-Port Pods

A pod that runs several engine servers, for example one per GPU, lists their ports in the `smg.ai/worker-ports` annotation as a comma-separated list. Each port becomes an independent worker with its own health checks, circuit breaker, and load tracking.

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: sglang-multi-0
  labels:
    app: sglang-worker
  annotations:
    smg.ai/worker-ports: "8000,8001,8002,8003"
```

| `smg.ai/worker-ports` | Workers registered for the pod |
|-----------------------|--------------------------------|
| Absent | One, at `--service-discovery-port` |
| `"8000,8001,8002,8003"` | One per listed port, in order; duplicate ports are ignored |
| Invalid (an entry that is not a port from 1 to 65535) | One, at `--service-discovery-port`, with a warning that names the pod |

All workers of a pod follow the pod: they are registered when it becomes Ready and removed when it is deleted or turns unready. For per-port bootstrap ports and KV metadata in PD deployments, see [PD Disaggregation Discovery](#pd-disaggregation-discovery).

---

## Label Selectors

SMG uses Kubernetes label selectors to identify worker pods.

### Simple Selector

Match pods with a single label:

```bash
smg --service-discovery --selector app=vllm
```

Matches pods with label `app=vllm`.

### Multiple Labels

Match pods that carry several labels by passing multiple `key=value` pairs:

```bash
smg --service-discovery --selector app=sglang environment=production
```

Matches pods with both `app=sglang` AND `environment=production`.

---

## PD Disaggregation Discovery

For prefill-decode disaggregated deployments, use separate selectors for each worker type.

### Configuration

```bash
smg \
  --service-discovery \
  --pd-disaggregation \
  --prefill-selector app=sglang role=prefill \
  --decode-selector app=sglang role=decode \
  --service-discovery-namespace inference
```

### Parameters

| Parameter | Description |
|-----------|-------------|
| `--prefill-selector` | Label selector for prefill workers |
| `--decode-selector` | Label selector for decode workers |

### Worker Labels

Label your pods appropriately:

```yaml
# Prefill worker
apiVersion: v1
kind: Pod
metadata:
  name: sglang-prefill-0
  labels:
    app: sglang
    role: prefill
spec:
  containers:
    - name: sglang
      image: lmsysorg/sglang:latest
      args: ["--dp-size", "1", "--prefill-only"]

---
# Decode worker
apiVersion: v1
kind: Pod
metadata:
  name: sglang-decode-0
  labels:
    app: sglang
    role: decode
spec:
  containers:
    - name: sglang
      image: lmsysorg/sglang:latest
      args: ["--dp-size", "1", "--decode-only"]
```

---

## Required RBAC

SMG's informer lists and watches pods and reads no other Kubernetes resources. Grant `get`, `list`, and `watch` on `pods` in the watched namespace, the same rule the Helm chart creates. Mesh router discovery (`--router-selector`) watches pods too and needs no extra permissions.

### Role

```yaml
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: smg-discovery
  namespace: inference
rules:
  - apiGroups: [""]
    resources: ["pods"]
    verbs: ["get", "list", "watch"]
```

### RoleBinding

```yaml
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: smg-discovery
  namespace: inference
subjects:
  - kind: ServiceAccount
    name: smg
    namespace: inference
roleRef:
  kind: Role
  name: smg-discovery
  apiGroup: rbac.authorization.k8s.io
```

### ServiceAccount

```yaml
apiVersion: v1
kind: ServiceAccount
metadata:
  name: smg
  namespace: inference
```

### Cross-Namespace Discovery

Without `--service-discovery-namespace`, SMG watches pods in all namespaces. Grant the same rule cluster-wide with a ClusterRole:

```yaml
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: smg-discovery
rules:
  - apiGroups: [""]
    resources: ["pods"]
    verbs: ["get", "list", "watch"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: smg-discovery
subjects:
  - kind: ServiceAccount
    name: smg
    namespace: inference
roleRef:
  kind: ClusterRole
  name: smg-discovery
  apiGroup: rbac.authorization.k8s.io
```

---

## Complete Deployment Example

### SMG Deployment

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: smg
  namespace: inference
spec:
  replicas: 1
  selector:
    matchLabels:
      app: smg
  template:
    metadata:
      labels:
        app: smg
    spec:
      serviceAccountName: smg
      containers:
        - name: smg
          image: ghcr.io/smg-project/smg:latest
          args:
            - --service-discovery
            - --selector=app=sglang-worker
            - --service-discovery-namespace=inference
            - --service-discovery-port=8000
            - --policy=cache_aware
          ports:
            - containerPort: 30000
              name: http
```

!!! tip "Engine images"
    `ghcr.io/smg-project/smg:latest` is the gateway-only image; pin a release tag in production. For all-in-one deployments where each pod runs both gateway and engine, use an engine image tag of the form `ghcr.io/smg-project/smg:{smg_version}-{engine}-{engine_version}` (for example, `ghcr.io/smg-project/smg:1.11.0-vllm-v0.27.1`). See [Getting Started](../../getting-started/index.md#install) for available tags.

### Worker StatefulSet

```yaml
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: sglang-worker
  namespace: inference
spec:
  serviceName: sglang-worker
  replicas: 3
  selector:
    matchLabels:
      app: sglang-worker
  template:
    metadata:
      labels:
        app: sglang-worker
    spec:
      containers:
        - name: sglang
          image: lmsysorg/sglang:latest
          args:
            - --model-path=meta-llama/Llama-3.1-8B-Instruct
            - --port=8000
          ports:
            - containerPort: 8000
          # SMG registers the pod only once it is Ready
          readinessProbe:
            httpGet:
              path: /health
              port: 8000
            periodSeconds: 10
```

---

## Worker Lifecycle

### Registration Flow

1. **Pod becomes Ready**: The pod matches the selector, is `Running`, and its `Ready` condition is `True`.
2. **Reconcile pass**: SMG finds no registered worker for the pod's address and submits an `AddWorker` job.
3. **Startup probing**: After `--worker-startup-delay`, registration detects whether the worker speaks HTTP or gRPC, retrying every `--worker-startup-check-interval` seconds until `--worker-startup-timeout-secs`.
4. **Capability query**: SMG reads the engine's metadata, for example SGLang's `/model_info` endpoint (falling back to the deprecated `/get_model_info` if the new path returns 404).
5. **Registration**: The worker joins the registry with its `smg.ai/pod-name` and `smg.ai/pod-uid` labels, becomes `Ready`, and background health checks start immediately.

### Removal Flow

SMG removes a pod's workers when the pod:

- Starts terminating (its `deletionTimestamp` is set), so draining begins at the start of the pod's termination grace period rather than the end
- Turns unready (its `Ready` condition becomes `False` or `Unknown`)
- No longer matches the selector
- Is replaced by a pod with a different UID at the same address

Each removal runs as a workflow:

1. **RemoveWorker job**: The reconcile pass submits one job per address, pinned to each registration's revision. A worker that was replaced in the meantime is skipped and re-evaluated on the next pass.
2. **Drain**: `Ready` workers move to `Draining`. They receive no new requests; requests already in flight continue.
3. **Settle**: SMG waits `--drain-settle-secs` (default `5`). A worker's `health.drain_settle_secs` overrides it, and a job that drains several workers waits for the longest window. Workers that were not `Ready` are not drained, and a job with no `Ready` worker skips the wait.
4. **Remove**: The workers leave the worker registry and the routing policies.

A removal job matches every registration at the address: the `http://` or `grpc://` worker and each DP rank registered there (`<address>@<rank>`).

When the pod becomes Ready again, the next pass registers it again. If readiness returns while the removal is still inside its settle window, re-registration can wait for the next periodic pass.

!!! note "Drain settle window"
    `--drain-settle-secs` applies to every removal: pod changes seen by discovery, [worker auto-recovery](../reliability/health-checks.md#worker-auto-recovery), and `DELETE /workers/{worker_id}`. Set it to `0` to remove workers without draining. The flag belongs to the `smg` binary; the Python launcher (`smg launch` from pip, and the container image) does not accept it yet, so those deployments use the 5-second default.

### Worker States

| State | Description | Receives Traffic |
|-------|-------------|------------------|
| **Pending** | Just registered, not yet verified | No |
| **Ready** | Verified and passing health checks | Yes |
| **NotReady** | Previously `Ready`, now failing health checks; still probed | No |
| **Failed** | Sustained probe failure (about 12 minutes at the defaults); removed when auto-recovery is on, otherwise still probed | No |
| **Draining** | Being removed; kept for the settle window so in-flight requests can finish | No new requests |

See [Health Checks](../reliability/health-checks.md#state-transitions) for the thresholds behind each transition.

### Recovery

With `--service-discovery`, worker auto-recovery (`--remove-unhealthy-workers`, alias `--worker-auto-recovery`) is on by default. A worker that reaches `Failed` is removed, and while its pod is still Ready the next reconcile pass registers it again; registration then waits for the engine to answer. To keep failed workers registered and let them rejoin in place instead, pass `--remove-unhealthy-workers=false` (Python launcher: `--no-remove-unhealthy-workers`). See [Worker Auto-Recovery](../reliability/health-checks.md#worker-auto-recovery).

---

## Mesh Router Discovery

`--router-selector` finds peer SMG gateway pods for an HA mesh. It runs as its own task, independent of worker discovery, but it is configured through the service discovery flags: it starts only when `--service-discovery` is set and the mesh is enabled (`--enable-mesh`). Router pods can publish their mesh port in the `sglang.ai/mesh-port` annotation; pods without it are assumed to use this gateway's `--mesh-port`. See [High Availability](high-availability.md).

---

## Monitoring

### Metrics

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `smg_discovery_workers_discovered` | Gauge | `source` | Desired workers (Ready pods × ports) seen by the latest reconcile pass |
| `smg_discovery_registrations_total` | Counter | `source`, `result` | `AddWorker` jobs submitted by discovery; `result` is `success` when the job was queued and `failed` when the queue refused it |
| `smg_discovery_deregistrations_total` | Counter | `source`, `reason` | `RemoveWorker` jobs submitted by discovery; `reason` is `reconciled` |
| `smg_discovery_sync_duration_seconds` | Histogram | `source` | Duration of reconcile passes that submitted jobs |

`source` is `kubernetes`. The counters record job submissions; whether a registration then succeeded shows up in `GET /workers` and in the worker [health metrics](../reliability/health-checks.md#monitoring).

!!! note "Changed in v1.10.0"
    The deregistration reason `pod_deleted` and the registration result `duplicate` are gone; every deregistration reports `reason="reconciled"`. `smg_discovery_workers_discovered` counts workers (pods × ports) instead of pods. Update dashboards that filter on the old values.

### Logs

```bash
# Enable discovery debug logging
RUST_LOG=info,smg::service_discovery=debug smg launch --service-discovery ...
```

Example log output:

```text
INFO Starting K8s service discovery | selector: 'app=sglang-worker'
INFO Starting K8s worker watcher | selector: 'app=sglang-worker'
INFO K8s worker store synced, reconciling on change and every 60s
INFO Reconciling workers: 2 to add, 0 to remove (2 desired)
INFO Registering worker 10.0.0.5:8000 (Regular) for pod sglang-worker-0
INFO Registering worker 10.0.0.6:8000 (Regular) for pod sglang-worker-1
```

When a pod is deleted or turns unready:

```text
INFO Reconciling workers: 0 to add, 1 to remove (1 desired)
INFO Removing worker 10.0.0.6:8000 (1 registration(s), pod <pod-uid>): pod unready, gone, terminating, or replaced
INFO Draining 1 worker(s) for 5s before removal
```

---

## Troubleshooting

| Symptom | Cause | Solution |
|---------|-------|----------|
| No workers discovered | Selector does not match, or pods are not Ready | Check `kubectl get pods -l <selector>` and the pods' `READY` column |
| `Failed to start service discovery`, then `Continuing without service discovery` | No in-cluster or kubeconfig credentials | Run SMG with a ServiceAccount or a valid kubeconfig |
| `K8s worker watcher error (auto-retrying with backoff)` | RBAC denies the request, or the API server is unreachable | Apply the Role and RoleBinding and check API connectivity; the informer retries with backoff, and discovery converges once the watch recovers |
| One worker per pod instead of several | `smg.ai/worker-ports` is missing or invalid | Check the pod's annotations and the gateway log for `invalid smg.ai/worker-ports annotation` |
| Workers drop out during a rollout before their pods are gone | Expected: terminating and unready pods are drained | Tune the pods' readiness probes and `--drain-settle-secs` |
| Workers registered but not receiving traffic | Health checks failing | Check the worker health endpoint and [Health Checks](../reliability/health-checks.md) |
| Reconcile passes stall on a large fleet | Control-plane job queue is full | Raise `--job-queue-capacity` |

### Verify Discovery

```bash
# Reach the gateway from your machine
kubectl -n inference port-forward deployment/smg 30000:30000 &

# List discovered workers with their state and owning pod
curl -s http://localhost:30000/workers | jq '.workers[] | {url, status, pod: .labels["smg.ai/pod-name"]}'

# Check pod labels match selector
kubectl get pods -n inference -l app=sglang-worker

# Verify RBAC
kubectl auth can-i list pods -n inference --as=system:serviceaccount:inference:smg
kubectl auth can-i watch pods -n inference --as=system:serviceaccount:inference:smg
```

---

## What's Next?

<div class="grid" markdown>

<div class="card" markdown>

### :material-swap-horizontal: PD Disaggregation

Learn about prefill-decode separation.

[PD Disaggregation →](../routing/pd-disaggregation.md)

</div>

<div class="card" markdown>

### :material-scale-balance: Load Balancing

Configure routing policies for discovered workers.

[Load Balancing →](../routing/load-balancing.md)

</div>

<div class="card" markdown>

### :material-heart-pulse: Health Checks

Configure health monitoring and worker auto-recovery.

[Health Checks →](../reliability/health-checks.md)

</div>

</div>
