---
title: Service Discovery
---

# Service Discovery

SMG can automatically discover workers in Kubernetes by watching pods with label selectors. Workers are registered when their pods become Ready and are drained and removed when pods scale down, terminate, or turn unready — no manual URL management needed.

<div class="prerequisites" markdown>

#### Before you begin

- Completed the [Getting Started](index.md) guide
- A Kubernetes cluster with worker pods deployed
- `kubectl` configured for your cluster

</div>

---

## Basic Setup

Enable service discovery with a label selector that matches your worker pods:

```bash
smg \
  --service-discovery \
  --selector app=sglang-worker \
  --service-discovery-namespace inference \
  --service-discovery-port 8000
```

SMG keeps a local cache of the matching pods, registers a worker for each Ready pod, and removes workers whose pods go away. Every pod change triggers a reconcile pass, and a full pass also runs every 60 seconds, so changes missed while the watch was disconnected still converge.

### Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `--service-discovery` | `false` | Enable Kubernetes service discovery |
| `--selector` | — | Label selector for worker pods (required unless PD or EPD mode is on) |
| `--service-discovery-namespace` | (all namespaces) | Kubernetes namespace to watch |
| `--service-discovery-port` | `80` | Worker port for pods without a `smg.ai/worker-ports` annotation |

Connection mode (HTTP vs gRPC) is probed automatically during worker registration, so no protocol flag is required — the first protocol that responds successfully is used, with HTTP taking priority when both succeed.

Service discovery also turns on IGW mode (`--enable-igw`) automatically.

---

## Multiple Engines per Pod

If a pod runs several engine servers, list their ports in the `smg.ai/worker-ports` annotation. SMG registers one worker per port:

```yaml
metadata:
  labels:
    app: sglang-worker
  annotations:
    smg.ai/worker-ports: "8000,8001,8002,8003"
```

Pods without the annotation get a single worker at `--service-discovery-port`. If the annotation is invalid, SMG logs a warning and falls back to that port.

---

## Label Selectors

### Single Label

```bash
smg --service-discovery --selector app=vllm
```

### Multiple Labels

Pass multiple `key=value` pairs separated by spaces:

```bash
smg --service-discovery --selector app=sglang environment=production
```

Matches pods that carry every listed label.

---

## PD Disaggregation Discovery

For prefill-decode deployments, use separate selectors:

```bash
smg \
  --service-discovery \
  --pd-disaggregation \
  --prefill-selector app=sglang role=prefill \
  --decode-selector app=sglang role=decode \
  --service-discovery-namespace inference
```

Label your pods accordingly:

```yaml
# Prefill worker pod
metadata:
  labels:
    app: sglang
    role: prefill

# Decode worker pod
metadata:
  labels:
    app: sglang
    role: decode
```

---

## RBAC

SMG's informer lists and watches pods and reads no other resources. Apply these resources to your cluster:

```yaml
apiVersion: v1
kind: ServiceAccount
metadata:
  name: smg
  namespace: inference
---
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: smg-discovery
  namespace: inference
rules:
  - apiGroups: [""]
    resources: ["pods"]
    verbs: ["get", "list", "watch"]
---
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

Without `--service-discovery-namespace`, SMG watches all namespaces: use a `ClusterRole` and `ClusterRoleBinding` with the same rule instead.

---

## Deployment Example

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
          readinessProbe:
            httpGet:
              path: /health
              port: 8000
            periodSeconds: 10
```

The readiness probe matters: SMG registers a pod only once its `Ready` condition is `True`, and drains its workers as soon as it turns unready.

---

## Rollouts and Draining

When a pod is deleted, stops matching the selector, or fails its readiness probe, SMG stops routing new requests to its workers (they enter `Draining`; in-flight requests continue), then removes them after `--drain-settle-secs` (default `5`). The pod is registered again when it becomes Ready.

With service discovery on, worker auto-recovery (`--remove-unhealthy-workers`) is also on by default: a worker that keeps failing health checks is removed, and discovery registers it again while its pod is still Ready. See [Health Checks](../concepts/reliability/health-checks.md#worker-auto-recovery).

---

## Verify

```bash
# Check discovered workers, their state, and the pod behind each one
curl -s http://localhost:30000/workers | jq '.workers[] | {url, status, pod: .labels["smg.ai/pod-name"]}'

# Check pod labels match selector
kubectl get pods -n inference -l app=sglang-worker

# Verify RBAC permissions
kubectl auth can-i list pods -n inference --as=system:serviceaccount:inference:smg
kubectl auth can-i watch pods -n inference --as=system:serviceaccount:inference:smg
```

---

## Troubleshooting

| Symptom | Cause | Solution |
|---------|-------|----------|
| No workers discovered | Wrong selector, or pods not Ready | Verify labels match: `kubectl get pods -l <selector>`, and check the `READY` column |
| RBAC error | Missing permissions | Apply Role and RoleBinding above |
| Only one worker per multi-engine pod | `smg.ai/worker-ports` missing or invalid | Check the pod annotation; SMG logs a warning for invalid values |
| Workers not ready | Health check failing | Check worker health endpoint |
| Workers not added or removed | Watch cannot reach the API server | Check Kubernetes API connectivity; discovery converges once the watch reconnects |

---

## Next Steps

- [Service Discovery Concepts](../concepts/architecture/service-discovery.md) — Reconcile loop, worker lifecycle, fleet-scale tuning, monitoring metrics
- [Health Checks](../concepts/reliability/health-checks.md) — Worker states and auto-recovery
- [Load Balancing](load-balancing.md) — Choose a routing policy for discovered workers
- [PD Disaggregation](pd-disaggregation.md) — Full PD setup with SGLang and vLLM
