---
title: Service Discovery
---

# Service Discovery

SMG automatically discovers and registers workers in Kubernetes environments, eliminating manual worker URL management and enabling dynamic scaling.

---

## Overview

<div class="grid" markdown>

<div class="card" markdown>

### :material-kubernetes: Native Kubernetes

Watch pods matching label selectors with automatic registration and removal.

</div>

<div class="card" markdown>

### :material-sync: Dynamic Scaling

Workers are automatically added and removed as pods scale up or down.

</div>

<div class="card" markdown>

### :material-filter: Label Selectors

Target specific workers using Kubernetes label selectors.

</div>

<div class="card" markdown>

### :material-swap-horizontal: PD Support

Separate discovery for prefill and decode workers in disaggregated deployments.

</div>

</div>

---

## How It Works

<div class="architecture-diagram" markdown>

![Service Discovery Architecture](../../assets/images/service-discovery.svg)

</div>

### Discovery Flow

1. **Watch Pods**: SMG creates a Kubernetes watcher for pods matching the configured label selector
2. **Filter Events**: Only pods matching the selector (regular or PD mode) are processed
3. **Handle Events**: Pod creation triggers `AddWorker` job, deletion triggers `RemoveWorker` job
4. **Register Workers**: Workers are added to the registry with health checks starting immediately
5. **Track State**: A HashSet tracks discovered pods to prevent duplicate registrations

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
| `--service-discovery` | `false` | Enable Kubernetes service discovery |
| `--selector` | - | Label selector for worker pods (required) |
| `--service-discovery-namespace` | (all namespaces) | Kubernetes namespace to watch |
| `--service-discovery-port` | `80` | Port to use for worker connections |

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

For prefill-decode disaggregated deployments, use a separate selector for each worker role. SMG gives each pod the role of the first selector it matches (encode, then prefill, then decode) and ignores pods that match none.

### Configuration

```bash
smg launch \
  --service-discovery \
  --pd-disaggregation \
  --prefill-selector app=vllm role=prefill \
  --decode-selector app=vllm role=decode \
  --service-discovery-namespace inference \
  --service-discovery-port 8000
```

Service discovery turns on IGW mode automatically; the PD (or EPD) routing mode and the per-role policies stay in effect. `/readiness` reports ready once at least one prefill worker and one decode worker are healthy. See [PD Disaggregation](../routing/pd-disaggregation.md) for how the legs are paired and dispatched.

### Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `--prefill-selector` | — | Label selector for prefill pods |
| `--decode-selector` | — | Label selector for decode pods |
| `--encode-selector` | — | Label selector for encode pods (EPD mode) |
| `--kv-connector-annotation` | `smg.ai/kv-connector` | Pod annotation that names the vLLM KV connector |
| `--kv-engine-id-annotation` | `smg.ai/kv-engine-id` | Pod annotation that lists the vLLM KV engine ids |

PD mode needs at least one of `--prefill-selector` and `--decode-selector`. EPD mode (`--epd-disaggregation`) needs all three role selectors. Annotation names must not be empty or padded with whitespace.

### Pod Annotations

| Annotation | Pods | Value |
|------------|------|-------|
| `sglang.ai/bootstrap-port` | Prefill, encode | The worker's bootstrap port: SGLang and TokenSpeed `--disaggregation-bootstrap-port`, or vLLM Mooncake `VLLM_MOONCAKE_BOOTSTRAP_PORT`. A single value applies to every worker port of the pod; a comma-separated list needs one port per worker port, or it is ignored |
| `smg.ai/kv-connector` | vLLM prefill and decode | `NixlConnector` or `MooncakeConnector`, shared by every worker in the pod. Any other value is kept but handled as passthrough, with a warning |
| `smg.ai/kv-engine-id` | vLLM Mooncake prefill | The engine's `kv_transfer_config.engine_id`. One id for a single-port pod, or a comma-separated list of distinct ids in the order of `smg.ai/worker-ports`. A list of the wrong length, or with an empty or duplicate id, is ignored with a warning |

SGLang and TokenSpeed pods need only their role labels and, on prefill (and encode) pods, the bootstrap-port annotation. vLLM workers served over HTTP need `smg.ai/kv-connector` for a KV handoff, because the vLLM HTTP server does not report its connector. vLLM gRPC workers report their connector and engine id themselves, and the annotations override what they report. To pin a [pairing protocol](../routing/pd-disaggregation.md#explicit-pairing-protocol), set `SMG_PAIRING_PROTOCOL` in the engine container of gRPC workers; there is no annotation for it.

SMG reads the annotations when it registers a pod. After changing them, replace the pod so that SMG registers it again under its new UID.

### Worker Labels

Label and annotate your pods. This example runs vLLM over HTTP with Mooncake:

```yaml
# Prefill worker
apiVersion: v1
kind: Pod
metadata:
  name: vllm-prefill-0
  namespace: inference
  labels:
    app: vllm
    role: prefill
  annotations:
    sglang.ai/bootstrap-port: "8998"
    smg.ai/kv-connector: MooncakeConnector
    smg.ai/kv-engine-id: prefill-0
spec:
  containers:
    - name: vllm
      image: vllm/vllm-openai:latest
      command: ["vllm", "serve", "meta-llama/Llama-3.1-8B-Instruct"]
      args:
        - --port=8000
        - '--kv-transfer-config={"kv_connector":"MooncakeConnector","kv_role":"kv_producer","engine_id":"prefill-0"}'
      env:
        - name: VLLM_MOONCAKE_BOOTSTRAP_PORT
          value: "8998"

---
# Decode worker
apiVersion: v1
kind: Pod
metadata:
  name: vllm-decode-0
  namespace: inference
  labels:
    app: vllm
    role: decode
  annotations:
    smg.ai/kv-connector: MooncakeConnector
spec:
  containers:
    - name: vllm
      image: vllm/vllm-openai:latest
      command: ["vllm", "serve", "meta-llama/Llama-3.1-8B-Instruct"]
      args:
        - --port=8000
        - '--kv-transfer-config={"kv_connector":"MooncakeConnector","kv_role":"kv_consumer"}'
```

---

## Required RBAC

SMG needs permissions to watch pods in the target namespace.

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

To discover workers across multiple namespaces, use a ClusterRole:

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
          image: ghcr.io/lightseekorg/smg:latest
          args:
            - --service-discovery
            - --selector=app=sglang-worker
            - --service-discovery-namespace=inference
            - --service-discovery-port=8000
            - --policy=cache_aware
          ports:
            - containerPort: 8000
              name: http
```

!!! tip "Engine images"
    For all-in-one deployments where each pod runs both gateway and engine, use an engine image tag (e.g., `ghcr.io/lightseekorg/smg:{smg_version}-{engine}-{engine_version}`). See [Getting Started](../../getting-started/index.md#install) for available tags.

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
```

---

## Worker Lifecycle

### Registration Flow

1. **Pod Created**: Kubernetes creates a new worker pod
2. **Watch Event**: SMG receives the pod creation event
3. **Capability Query**: SMG queries the worker's `/model_info` endpoint (falling back to the deprecated `/get_model_info` if the new path returns 404)
4. **Registration**: Worker is added to the registry
5. **Health Check**: Background health checks begin

### Removal Flow

1. **Pod Terminating**: Kubernetes begins pod termination
2. **Watch Event**: SMG receives the pod deletion event
3. **Drain**: SMG stops sending new requests to the worker
4. **Removal**: Worker is removed from the registry

### Worker States

| State | Description | Receives Traffic |
|-------|-------------|------------------|
| **Pending** | Just registered, not yet proven healthy locally | No |
| **Ready** | Locally verified and passing health checks | Yes |
| **NotReady** | Previously `Ready`, now failing readiness checks; not removed unless configured | No |
| **Failed** | Sustained liveness failure; removed when `--remove-unhealthy-workers` is set | No |

---

## Monitoring

### Metrics

| Metric | Description |
|--------|-------------|
| `smg_discovery_workers_discovered` | Workers known via discovery |
| `smg_discovery_registrations_total` | Worker registration events |
| `smg_discovery_deregistrations_total` | Worker deregistration events |
| `smg_discovery_sync_duration_seconds` | Duration of each periodic reconciliation cycle |

### Logs

```bash
# Enable discovery debug logging
RUST_LOG=smg::discovery=debug smg --service-discovery ...
```

Example log output:

```
[INFO] Watching pods in namespace 'inference' with selector 'app=sglang-worker'
[INFO] Discovered new pod: sglang-worker-0 (10.0.0.5:8000)
[INFO] Registered worker: http://10.0.0.5:8000
[INFO] Discovered new pod: sglang-worker-1 (10.0.0.6:8000)
[INFO] Registered worker: http://10.0.0.6:8000
```

---

## Troubleshooting

| Symptom | Cause | Solution |
|---------|-------|----------|
| No workers discovered | Wrong selector | Verify labels match selector |
| RBAC error | Missing permissions | Apply Role and RoleBinding |
| Workers not ready | Health check failing | Check worker health endpoint |
| Stale workers | Watch disconnected | Check Kubernetes API connectivity |

### Verify Discovery

```bash
# Check discovered workers via admin API
curl http://smg:30000/workers | jq

# Check pod labels match selector
kubectl get pods -n inference -l app=sglang-worker

# Verify RBAC
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

Configure health monitoring for workers.

[Health Checks →](../reliability/health-checks.md)

</div>

</div>
