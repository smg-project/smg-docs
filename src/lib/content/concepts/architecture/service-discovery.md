---
title: Service Discovery
---

# Service Discovery

SMG discovers workers from a provider and keeps its worker registry in step with what the provider reports. The Kubernetes provider, which most of this page describes, watches pods: pods that match a label selector and are Ready become workers; workers whose pods are deleted, stop matching, or turn unready are drained and removed. Outside Kubernetes, [file-based discovery](#file-based-discovery) reads the workers from a JSON manifest on disk, [Slurm discovery](#slurm-discovery) polls a Slurm cluster for the nodes of running worker jobs, and the Consul provider is configured in the [configuration reference](../../reference/configuration.md#file-consul-and-slurm-providers).

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

The reverse holds too: a discovered worker takes its spec and labels from its discovery source, and the worker API does not override them. A `PUT` of it, or a `PATCH` that includes `labels`, is refused with `409` (`WORKER_DISCOVERY_OWNED`): change the worker at its discovery source instead. A `PATCH` of the fields no provider publishes (`priority`, `cost`, `api_key`, `health`) still works, and so does a `DELETE`, though a later pass registers a deleted worker again while its provider still publishes it.

Discovery also marks the workers it owns with labels under the `smg.ai/discovery-` prefix, so the prefix is reserved: no request may set a label under it, on any worker, discovered or not (`400`), because such a label would hand a manual worker to a provider or forge a discovered worker's record. See [Worker Errors](../../reference/api/admin.md#worker-errors).

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
    Selecting any discovery provider — `--service-discovery` or `--discovery-provider` — turns on IGW mode (`--enable-igw`) automatically, in the `smg` binary and in the Python launcher (`smg launch` from pip, and the container image). PD and EPD deployments keep their disaggregated routing mode under IGW.

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

Service discovery turns on IGW mode automatically; the PD (or EPD) routing mode and the per-role policies stay in effect. `/readiness` reports ready only when at least one prefill worker and one decode worker are healthy (and an encode worker in EPD mode). See [PD Disaggregation](../routing/pd-disaggregation.md) for how the legs are paired and dispatched.

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
| `smg.ai/kv-connector` | vLLM prefill and decode | The engine's KV connector, shared by every worker in the pod: `NixlConnector`, `MooncakeConnector`, or a KV-store connector (`MooncakeStoreConnector`, `SharedStorageConnector`, or a name starting with `FlexKV` or `HF3FS`). Any other value is kept but handled as passthrough, with a warning. `MoRIIOConnector` also draws the warning but is dispatched as MoRI-IO and cannot be configured through discovery (see below) |
| `smg.ai/kv-engine-id` | vLLM Mooncake prefill | The engine's `kv_transfer_config.engine_id`. One id for a single-port pod, or a comma-separated list of distinct ids in the order of `smg.ai/worker-ports`. A list of the wrong length, or with an empty or duplicate id, is ignored with a warning |

SGLang and TokenSpeed pods need only their role labels and, on prefill (and encode) pods, the bootstrap-port annotation. vLLM workers served over HTTP need `smg.ai/kv-connector` for a KV handoff, because the vLLM HTTP server does not report its connector. vLLM gRPC workers report their connector and engine id themselves, and the annotations override what they report. To pin a [pairing protocol](../routing/pd-disaggregation.md#explicit-pairing-protocol), set `SMG_PAIRING_PROTOCOL` in the engine container of gRPC workers; there is no annotation for it.

Do not register MoRI-IO legs through discovery. A pod annotated `MoRIIOConnector` draws the passthrough warning but is not passthrough: its workers are dispatched on the MoRI-IO path, which requires the `moriio_mode` worker label. No annotation sets worker labels, so every request routed to such a pair of HTTP workers fails with 503 `moriio_pair_misconfigured`. Register MoRI-IO legs through the [worker API](../../reference/api/admin.md) with their `moriio_*` labels instead; see [MoRI-IO (HTTP)](../routing/pd-disaggregation.md#mori-io-http).

SMG reads these annotations on every reconcile pass. Editing one on a live pod updates its registered workers in place on a later pass, without removing or draining them; the pod does not need to be replaced.

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
3. **Startup probing**: After `--worker-startup-delay`, registration probes the address over HTTP and gRPC in parallel and keeps the protocol that answers (HTTP, if both do). No worker is created until a probe succeeds — a `2xx` from `GET /health`, or a gRPC health RPC in which the engine reports itself healthy. Failed probes are retried every `--worker-startup-check-interval` seconds; when `--worker-startup-timeout-secs` elapses first, the job fails, and a later reconcile pass submits it again.
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
2. **Drain**: `Ready` workers move to `Draining`. They receive no new requests; requests already in flight can continue while the engine remains alive.
3. **Settle**: SMG waits `--drain-settle-secs` (default `5`). A worker's `health.drain_settle_secs` overrides it, and a job that drains several workers waits for the longest window. Workers that were not `Ready` are not drained, and a job with no `Ready` worker skips the wait.
4. **Remove**: The workers leave the worker registry and the routing policies.

A removal job matches every registration at the address: the `http://` or `grpc://` worker and each DP rank registered there (`<address>@<rank>`).

This workflow does not delay Kubernetes signals or shut down the engine for you. Protect in-flight streams with an engine-container `preStop` wait or a tested graceful engine shutdown. Include discovery propagation, the settle window, the longest remaining request, and engine exit time in the pod's termination budget. See the [worker manifest and rollout checks](../../getting-started/service-discovery.md#rollouts-and-draining).

When the pod becomes Ready again, the next pass registers it again. If readiness returns while the removal is still inside its settle window, re-registration can wait for the next periodic pass.

!!! note "Drain settle window"
    `--drain-settle-secs` applies whenever a `Ready` worker is removed: by pod changes seen by discovery or by `DELETE /workers/{worker_id}`. [Worker auto-recovery](../reliability/health-checks.md#worker-auto-recovery) removes only `Failed` workers, so it never waits. Set the flag to `0` to remove `Ready` workers immediately. The flag belongs to the `smg` binary; the Python launcher (`smg launch` from pip, and the container image) does not accept it yet, so those deployments use the 5-second default.

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

## File-Based Discovery

Outside Kubernetes — or wherever another system already knows the fleet — SMG discovers workers from a JSON manifest on disk:

```bash
smg \
  --discovery-provider file \
  --discovery-file /run/smg/workers.json
```

| Parameter | Default | Description |
|-----------|---------|-------------|
| `--discovery-provider` | — | The worker discovery provider: `kubernetes`, `file`, `slurm`, or `consul`. Give it or `--service-discovery`, not both |
| `--discovery-file` | — | Path of the worker manifest (required with `--discovery-provider file`). The file may be created after startup |
| `--discovery-check-interval-secs` | `30` | Seconds between the periodic passes that reread the manifest |

File discovery requires the regular, PD, or EPD routing mode, and turns on IGW mode like every provider.

The manifest is reread in full whenever it changes and on every periodic pass, in case a change went unreported (a manifest behind a symlink into another directory, or on a network filesystem). Every read reconciles the manifest against the registry the same way Kubernetes discovery does: workers are registered through the same startup probing, a registration that failed is submitted again on a later pass, and removed records go through the same drain-then-remove workflow.

### Manifest Format

```json
{
  "version": 1,
  "workers": [
    { "id": "decode-a", "url": "10.0.0.23:8000", "worker_type": "decode" },
    { "id": "prefill-a", "url": "10.0.0.22:8000", "worker_type": "prefill",
      "bootstrap_port": 8998, "kv_connector": "MooncakeConnector" }
  ]
}
```

| Field | Required | Description |
|-------|----------|-------------|
| `version` | Yes | Manifest version; SMG reads version `1` |
| `workers[].url` | Yes | The worker's address. `host:port` (IPv6 in brackets) is probed over HTTP and gRPC, keeping the protocol that answers; a `grpc://` URL registers a gRPC worker, and `ipc://` socket paths are accepted. A DP rank suffix (`@rank`) is rejected |
| `workers[].id` | No | A stable identity for the worker instance, unique within the manifest. Defaults to the URL with any credentials masked |
| `workers[].worker_type` | In PD and EPD routing | `regular`, `prefill`, `decode`, or `encode`; it must be a type the routing mode uses. In regular routing it defaults to `regular` |
| `workers[].bootstrap_port` | No | The worker's bootstrap port, `1`–`65535`, on prefill and encode workers only |
| `workers[].model_id` | No | Overrides the worker's model ID |
| `workers[].kv_connector`, `workers[].kv_role`, `workers[].kv_engine_id` | No | KV metadata for vLLM PD deployments. `kv_connector` and `kv_engine_id` carry what the [pod annotations](#pod-annotations) carry in Kubernetes; `kv_role` is the engine's `kv_transfer_config.kv_role`, which has no pod annotation |

The parse is strict, and a manifest is applied whole or not at all: an unknown or misspelled field, an unknown worker type, an unsupported version, a duplicate `id`, or two records naming the same endpoint rejects the whole file, with a log line naming the record and the reason. `{"version": 1, "workers": []}` is valid and means an intentionally empty fleet.

### Updating the Manifest

SMG watches the manifest's directory and rereads once a change settles. Write a temporary file in the same directory and rename it over the manifest, so no read ever sees a half-written file. A manifest mounted from a Kubernetes ConfigMap or Secret is picked up when Kubernetes swaps its content. When the directory cannot be watched — it does not exist yet, or was removed — SMG warns once, rereads on the periodic pass alone, and retries the watch every second.

A read that fails never removes a worker. An unreadable manifest (absent, or a permission error) and a manifest that was read but rejected both leave the last good worker list in place, with a warning and a count on `smg_discovery_snapshot_errors_total` (`reason="unreadable"` or `reason="invalid"`). An absent manifest is a retryable error, not an empty fleet.

How an edit maps onto a running worker:

- **Editing a record that keeps its `id`** (for example, correcting its bootstrap port) updates its worker in place with the new settings, without removing or draining it.
- **A new `id` at a reused address** signals a new instance there: the old worker is removed and the address is registered again.
- **Removing a record** drains and removes its worker, through the same [removal flow](#removal-flow) as a deleted pod.

---

## Slurm Discovery

On a Slurm cluster, SMG discovers a worker on every node of each running job whose name maps to a worker type:

```bash
smg \
  --discovery-provider slurm \
  --slurm-job-name smg-worker \
  --slurm-port 8000
```

`--slurm-job-name` names jobs whose nodes are regular workers; `--slurm-prefill-job-name`, `--slurm-decode-job-name`, and `--slurm-encode-job-name` map jobs to the disaggregated roles, and `--slurm-bootstrap-port` sets the bootstrap port of the discovered prefill and encode workers. The full flag list, including the `--slurm-user`, `--slurm-account`, and `--slurm-partition` scopes, is in the [configuration reference](../../reference/configuration.md#file-consul-and-slurm-providers).

Slurm discovery requires the regular, PD, or EPD routing mode, and turns on IGW mode like every provider. Each job-name flag is repeatable, a name can map to only one worker type, and the names must fit the mode: regular routing needs a regular job name, PD a prefill or a decode one, and EPD all three of encode, prefill, and decode.

### Polling

Slurm discovery polls — nothing pushes a change to SMG — so a job that starts, ends, or is requeued shows up on a later poll rather than immediately. At startup and then every `--discovery-check-interval-secs` seconds (default `30`), a poll runs three commands:

1. `squeue --json` lists the running jobs with the mapped names, scoped to one user's jobs (`--slurm-user`, defaulting to the user the gateway runs as) and, when set, to `--slurm-account` and `--slurm-partition`.
2. `scontrol show hostnames` expands each job's allocation (such as `gpu[01-04]`) into its node names.
3. One `scontrol --json show nodes` resolves every node named.

The commands run without a shell, each within 30 seconds, so run the gateway somewhere `squeue` and `scontrol` are on `PATH` and can reach the cluster.

Each node of a selected job becomes one worker on `--slurm-port` at the node's `NodeAddr`. Slurm defaults `NodeAddr` to `NodeHostname` and that to the bare node name, so on a cluster whose node names do not resolve from the gateway, set `NodeAddr`.

Discovered workers go through the same reconciler as the other providers: registration probes HTTP and gRPC on the address and keeps the protocol that answers, and a worker whose job is gone is drained and removed through the same [removal flow](#removal-flow) as a deleted pod.

A worker's identity is its job's cluster, job ID, start time, and restart count, plus the node and the worker type. A requeued job keeps its job ID but starts again, so its workers are removed and registered again as new instances at the same addresses.

### One Job per Node

Every discovered worker listens on `--slurm-port`, so two jobs' workers cannot share a node. Submit worker jobs with `--exclusive`, or run them on nodes of their own; Slurm otherwise shares a node between jobs. When two selected jobs do claim one node, the address stays with the job that started first, whose server holds the port since a later one cannot bind it, and the later job's worker there is left out.

### Failed Polls and Skipped Jobs

A poll SMG cannot read is rejected whole and leaves the last good worker list in place, so a broken Slurm never removes a worker. Each rejected poll logs a warning and counts on `smg_discovery_snapshot_errors_total`: `reason="unreadable"` for a command that failed or timed out, `reason="invalid"` for an answer that could not be used — including one from a Slurm release older than 23.11 (see [below](#cluster-scope-and-releases)).

A job that cannot be placed — running with no start time, with an allocation that does not expand, or on a node `scontrol` does not know or that has no usable address — is left out alone (`reason="invalid"`), and the rest of the poll stands. Everything a poll leaves out — such a job, or a worker whose node another job holds (`reason="conflict"`) — is counted on `smg_discovery_skipped_total` on every poll and logged when it first appears.

### Cluster Scope and Releases

Slurm discovery reads Slurm 23.11 and later; `squeue` reports its release, and a poll of an older one is rejected. Discovery stays within the local cluster, the one `squeue` runs in: a federation's remote jobs are not discovered, because `scontrol` resolves node names in the local cluster only, and a remote job's `gpu01` would otherwise take the local `gpu01`'s address.

---

## Mesh Router Discovery

`--router-selector` finds peer SMG gateway pods for an HA mesh. It runs as its own task, independent of worker discovery, but it is configured through the service discovery flags: it starts only when Kubernetes worker discovery is selected and the mesh is enabled (`--enable-mesh`). Under the file, Slurm, or Consul provider, `--router-selector` is ignored and router discovery does not run. Router pods can publish their mesh port in the `sglang.ai/mesh-port` annotation; pods without it are assumed to use this gateway's `--mesh-port`. See [High Availability](high-availability.md).

---

## Monitoring

### Metrics

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `smg_discovery_workers_discovered` | Gauge | `source` | Desired workers seen by the latest reconcile pass: Ready pods × ports for Kubernetes, the manifest's records for file discovery, one per node of each selected job for Slurm |
| `smg_discovery_registrations_total` | Counter | `source`, `result` | `AddWorker` jobs submitted by discovery; `result` is `success` when the job was queued and `failed` when the queue refused it |
| `smg_discovery_deregistrations_total` | Counter | `source`, `reason` | `RemoveWorker` jobs submitted by discovery; `reason` is `reconciled` |
| `smg_discovery_sync_duration_seconds` | Histogram | `source` | Duration of reconcile passes that submitted jobs |
| `smg_discovery_snapshot_errors_total` | Counter | `source`, `reason` | Reads that produced no snapshot and left the last good one in place: for file discovery, a manifest that was `unreadable` or `invalid`; for Slurm, a poll with a failed or timed-out command (`unreadable`) or an answer that could not be used (`invalid`). Not emitted by the Kubernetes provider |
| `smg_discovery_skipped_total` | Counter | `source`, `reason` | Workers left out of an otherwise good snapshot, counted on every read: `invalid` for a record that cannot become a worker, such as a [Slurm job that cannot be placed](#failed-polls-and-skipped-jobs), and `conflict` for an address another record holds. Emitted by the Slurm and Consul providers |

`source` names the provider: `kubernetes`, `file`, `slurm`, or `consul`. The counters record job submissions; whether a registration then succeeded shows up in `GET /workers` and in the worker [health metrics](../reliability/health-checks.md#monitoring).

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
