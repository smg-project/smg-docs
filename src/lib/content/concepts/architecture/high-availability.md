---
title: High Availability
---

# High Availability

Run several SMG routers as one cluster. With `--enable-mesh`, routers form a peer-to-peer mesh: they track each other's membership with a SWIM-style gossip protocol, replicate the workers each router registers, and share cache-aware routing updates, so every router routes with the cluster's workers and cache affinity.

The mesh does not steer client traffic. Put the routers behind a load balancer or a Kubernetes Service; that is what moves clients to the remaining routers when one fails.

---

## Overview

<div class="grid" markdown>

<div class="card" markdown>

### :material-shield-check: Fault Tolerance

When a router fails, the others keep serving with the workers and cache-aware routing state it already shared. Your load balancer sends its clients to them.

</div>

<div class="card" markdown>

### :material-arrow-expand-all: Scalability

Add routers without restarting the others. A new router joins through a running peer or Kubernetes router discovery and picks up the cluster's workers.

</div>

<div class="card" markdown>

### :material-sync: State Synchronization

Workers registered on any router and cache-aware routing tree updates reach every peer. Rate limits, sticky sessions, and other policy state stay local to each router.

</div>

<div class="card" markdown>

### :material-rocket-launch: Rolling Updates

Replace routers one at a time: each drains in-flight requests on shutdown. With Kubernetes router discovery, a replaced pod rejoins as soon as it is Ready.

</div>

</div>

---

## Mesh Architecture

```text
                      clients
                         │
         load balancer or Kubernetes Service
        ┌────────────────┼────────────────┐
        ▼                ▼                ▼
  ┌──────────┐     ┌──────────┐     ┌──────────┐
  │ router A │◀───▶│ router B │◀───▶│ router C │   mesh: gRPC on --mesh-port
  └────┬─────┘     └────┬─────┘     └────┬─────┘
       └────────────────┼────────────────┘
                        ▼
                     workers
```

Every router is equal: there is no leader and no quorum. Each pair of routers (A and C included) keeps one sync stream over the mesh port, and state converges without coordination.

<div class="grid" markdown>

<div class="card" markdown>

### :material-connection: Gossip Membership

SWIM-style membership and failure detection.

- One probe per second to a random peer
- Indirect probes through up to three peers
- Status changes broadcast to live peers

</div>

<div class="card" markdown>

### :material-swap-horizontal: Sync Streams

One bidirectional gRPC stream per pair of routers.

- Opened by the router whose name sorts first
- Each side sends pending updates every second
- Closed after 60 seconds without traffic

</div>

<div class="card" markdown>

### :material-database-sync: CRDT Worker State

Conflict-free replicated keys for worker state.

- No locks or coordination
- Resent until each peer acknowledges
- Relayed through intermediate peers

</div>

<div class="card" markdown>

### :material-share-variant: Cache-Tree Updates

Best-effort broadcasts for cache-aware routing.

- Batched per model every gossip round
- Unknown prefixes trigger a repair from a peer
- Dropped updates are not resent

</div>

</div>

---

## Configuration

### Quick Start

Start the first router, then point the next one at it with `--mesh-peer-urls`:

```bash
# Router 1 (bootstrap)
smg launch --enable-mesh --mesh-advertise-host 10.0.0.1 --mesh-port 39527 \
    --worker-urls http://10.0.1.1:8000 http://10.0.1.2:8000

# Router 2, joining router 1
smg launch --enable-mesh --mesh-advertise-host 10.0.0.2 --mesh-port 39527 \
    --mesh-peer-urls 10.0.0.1:39527
```

Router 2 has no workers of its own; it imports router 1's workers through the mesh.

### Command Line Options

| Flag | Default | Description |
|------|---------|-------------|
| `--enable-mesh` | `false` | Start the mesh listener and join the cluster |
| `--mesh-server-name` | `Mesh_` plus 4 random alphanumeric characters | Node name, unique within the cluster. Must be non-empty and must not contain `:`. With router discovery, set it to the pod name |
| `--mesh-host` | `0.0.0.0` | Bind address for the mesh listener (an IP address) |
| `--mesh-advertise-host` | `--mesh-host` | IP address that peers dial. Required when `--mesh-host` is an unspecified address such as `0.0.0.0` |
| `--mesh-port` | `39527` | Mesh port, used for both binding and advertising. `0` is rejected |
| `--mesh-peer-urls` | (none) | Bootstrap peer as `IP:port`. Only the first entry is used |
| `--router-selector` | (none) | Label selector for [Kubernetes router discovery](#kubernetes-router-discovery). Takes effect only with `--service-discovery` |

!!! note "Mesh addresses must be IP addresses"
    `--mesh-host`, `--mesh-advertise-host`, and `--mesh-peer-urls` are parsed as socket addresses and never resolved through DNS. A hostname such as `smg-0.smg-mesh:39527` fails at startup: the Python CLI reports `Invalid mesh peer URL`, the Rust binary `Invalid value for field 'mesh_peer_urls'`. With the default `--mesh-host 0.0.0.0`, the router also refuses to start until `--mesh-advertise-host` names a routable IP, so peers never try to dial an unspecified address. On Kubernetes, use [router discovery](#kubernetes-router-discovery) instead of peer URLs.

!!! warning "The mesh port is not authenticated"
    Mesh traffic is plaintext gRPC and peers are not authenticated: v1.11 has no option to enable TLS on the mesh port. Anything that can reach the mesh port can join the cluster and publish workers to every router, so expose the port only to other routers (for example with a firewall rule or a Kubernetes NetworkPolicy).

### Python Entrypoint

The Python CLI (`smg launch`) and the Docker image's entrypoint (`python3 -m smg.launch_router`) accept the same mesh flags with the same defaults and address checks, so container `args` can pass them directly:

```bash
smg launch --enable-mesh --mesh-host 0.0.0.0 --mesh-advertise-host 10.0.0.11 --mesh-port 39527
```

`smg serve` takes router flags with a `--router-` prefix, for example `--router-enable-mesh` and `--router-mesh-advertise-host`.

### Basic Configuration

<div class="grid" markdown>

<div class="card" markdown>

**Node 1** (Bootstrap)

```bash
smg launch --enable-mesh \
    --mesh-server-name node1 \
    --mesh-host 0.0.0.0 \
    --mesh-advertise-host 10.0.0.11 \
    --mesh-port 39527 \
    --worker-urls http://10.0.1.1:8000 http://10.0.1.2:8000
```

</div>

<div class="card" markdown>

**Node 2** (Join)

```bash
smg launch --enable-mesh \
    --mesh-server-name node2 \
    --mesh-host 0.0.0.0 \
    --mesh-advertise-host 10.0.0.12 \
    --mesh-port 39527 \
    --mesh-peer-urls 10.0.0.11:39527 \
    --worker-urls http://10.0.1.1:8000 http://10.0.1.2:8000
```

</div>

<div class="card" markdown>

**Node 3** (Join)

```bash
smg launch --enable-mesh \
    --mesh-server-name node3 \
    --mesh-host 0.0.0.0 \
    --mesh-advertise-host 10.0.0.13 \
    --mesh-port 39527 \
    --mesh-peer-urls 10.0.0.11:39527 \
    --worker-urls http://10.0.1.1:8000 http://10.0.1.2:8000
```

</div>

</div>

!!! tip "Start the bootstrap router first"
    A router dials its `--mesh-peer-urls` peer once, in its first gossip round, and does not retry it. Start node1 before the others; after that first contact, routers learn about each other through gossip. Additional values on `--mesh-peer-urls` are ignored.

---

## Gossip Protocol

### Membership and Failure Detection

1. **Probe**: Every second, each router pings one random peer that is not `DOWN` or `LEAVING`. The ping carries the sender's membership table, and the receiver adopts any entry with a higher version.
2. **Indirect probe**: If the ping fails, the router asks up to three other `ALIVE` peers to ping the target (ping-req).
3. **Suspect, then down**: If no probe reaches the target, it moves from `ALIVE` to `SUSPECTED`, or from `SUSPECTED` to `DOWN`, and the new status is broadcast to every `ALIVE` peer.
4. **Sync stream**: After a successful probe of an `ALIVE` peer, the router whose name sorts first opens the pair's sync stream if none is open.

### Node Status States

| Status | Description |
|--------|-------------|
| `ALIVE` | Reachable. Every router starts in this state |
| `SUSPECTED` | A probe failed: neither the direct ping nor any indirect ping reached the router. Still probed. Router discovery also sets it for a known pod that is not Ready, unless that router is already `DOWN` |
| `DOWN` | A probe failed again while `SUSPECTED`, or the router's pod was deleted or is terminating. Peers stop probing it |
| `LEAVING` | The node announced a graceful leave. Peers stop probing it. The v1.11 gateway does not announce it during shutdown |
| `INIT` | Defined in the protocol but not used |

!!! warning "A `DOWN` router is not probed again"
    Gossip cannot clear a `DOWN` mark: a router that restarts under the same name announces itself with a lower version than the `DOWN` entry, and its peers no longer probe it. This applies after a crash or restart, and after a network partition long enough for routers to mark each other `DOWN`. Router discovery re-admits a router whenever its watch reports the pod as Ready, as it does when a restarted pod becomes Ready. With static peers, restart the isolated router under a new `--mesh-server-name` (omitting the flag picks a random one) and point `--mesh-peer-urls` at a running router.

### Failure Detection Timing

| Phase | Value |
|-------|-------|
| Gossip round (probe, send, and batching cadence) | 1 second |
| Indirect probe fan-out | Up to 3 `ALIVE` peers |
| Probe connect / request timeout | 5 seconds / 10 seconds |
| Redial backoff after a failed probe of the same peer | 1 second, doubling up to 60 seconds |
| Sync stream idle timeout | 60 seconds |

These values are fixed; v1.11 has no flags to tune them.

---

## State Synchronization

### Synchronized State Types

<div class="grid" markdown>

<div class="card" markdown>

### :material-server: Worker Registry

Workers each router registers, shared with every peer.

- URL, model, health, and load
- Worker spec, without its API key
- Host-local ZMQ workers excluded

</div>

<div class="card" markdown>

### :material-tree: Routing Trees

Cache-aware routing state shared across routers.

- HTTP string trees and gRPC token trees
- Tree inserts, sent as per-model deltas
- Repair from a peer for unknown prefixes

</div>

<div class="card" markdown>

### :material-account-group: Membership

The cluster's node table.

- Node names, addresses, and status
- Carried on every gossip ping

</div>

<div class="card" markdown>

### :material-lan-disconnect: Stays Local

Each router keeps its own:

- Rate limits and admission queues
- Sticky-session (`manual` policy) assignments
- Health checks and circuit-breaker state
- Routing policy and its settings

</div>

</div>

Because each router enforces [rate limits](../reliability/rate-limiting.md) on its own, the cluster-wide limit is roughly the per-router limit times the number of routers. [Sticky-session](../routing/sticky-sessions.md) assignments are also per router, so the same routing key can map to different workers on different routers.

### Worker Registry Sync

Each router publishes the workers it registered itself, whether from `--worker-urls`, the worker API, or service discovery, whenever one is added, changes status, or is removed. Peers import them into their own registries:

- **Imports are health-checked locally.** A peer rebuilds the worker from its published spec and probes it itself. The owner's health flag sets the import's initial state and afterwards only nudges it: healthy promotes a pending or not-ready import to ready, and unhealthy demotes a ready one.
- **Workers are matched by URL.** A router that registered a URL itself keeps its own worker and ignores peers' copies. Give a worker the same URL on every router, and never a loopback address: a peer that imports `http://127.0.0.1:8000` routes to its own host.
- **Only the owner removes a worker.** Removing a worker on the router that registered it removes it everywhere. Deleting an imported worker through another router's API lasts at most until that router's next reconcile pass (every 30 seconds) imports it again.
- **A departed router's workers stay.** If a router leaves for good, its peers keep its workers registered and keep health-checking them.
- **API keys stay local.** The published spec leaves out the worker's `api_key`.

!!! note "ZMQ workers stay on their host"
    Workers reached over ZMQ (`ipc://` URLs) are never published, and peers ignore them if an older router publishes one. An `ipc://` endpoint is a socket on the router's own machine, so no other router could reach it. Each router keeps and routes to its own ZMQ workers; see [ZMQ Workers](../../getting-started/zmq-workers.md).

### Cache-Aware State Sync

The `cache_aware` policy keeps an approximate prefix tree per model: a string tree for HTTP requests and a token tree for gRPC requests. With the mesh on, every routing decision made through these trees is shared:

1. **Publish**: After choosing a worker, the router records it in its own tree and queues a delta: a hash of the request's prefix path plus the worker URL. Once per gossip round, each model's queued deltas go to every connected peer as one batch.
2. **Apply**: A peer that already knows that prefix path adds the worker to it in its own tree.
3. **Repair**: A peer that does not know the path, such as a router that just started, asks a random `ALIVE` peer for its whole tree for that model and tree type and replays it. The tree arrives in pages of up to about 2 MiB; a repair that makes no progress for 5 seconds is retried, preferably with another peer, up to 3 times. Unknown prefixes for the same model and tree type that arrive meanwhile are folded into the repair in flight.

The default policy and per-model cache-aware policies take part, with no flag beyond `--enable-mesh`. In v1.11 the prefill, decode, and encode policies of disaggregated mode do not publish their inserts: the router creates them after the mesh attaches its sync adapter.

What consistency to expect:

- **Approximate.** Deltas and repairs move the trees toward each other, but they are not guaranteed to match: a dropped delta is not resent, and a repair starts only when a later delta names a path the peer does not know. Each router still evicts its own tree on its own schedule (`--eviction-interval`, `--max-tree-size`); evictions are not shared.
- **Best effort.** A delta batch that a peer's stream cannot accept is dropped, not resent. The peer catches up through repair the next time it sees a prefix it does not know.
- **Survives a router failure.** Routing decisions that a failed router already shared stay in its peers' trees, so traffic that moves to them keeps that cache affinity.

Not synchronized: event-driven cache-aware routing (KV events), which each router builds from the workers' KV event streams, and the hash placement index (`--cache-index hash`).

### How State Is Replicated

| Key prefix | Channel | Delivery | Carries |
|------------|---------|----------|---------|
| `worker:` | CRDT | Last-writer-wins by Lamport timestamp, then replica. Resent until each peer acknowledges; relayed through peers | One key per worker, written only by the router that owns it |
| `rl:` | CRDT | Epoch-max-wins | Rate-limit counter shards. Registered, but nothing writes to it in v1.11 |
| `td:` | Stream, broadcast | Best effort, once per gossip round, to directly connected peers | Cache-tree deltas, one entry per model per round |
| `tree:req:`, `tree:page:` | Stream, targeted | Retried after 5 seconds without progress, up to 3 times | Cache-tree repair requests and pages |

Worker-state merges order updates with Lamport clocks rather than wall-clock time, so clock skew between routers does not decide which update wins.

---

## Deployment Patterns

<div class="grid" markdown>

<div class="card" markdown>

### :material-server-network: Static Peers

For VMs and bare metal. Give each router a unique name, its own IP as `--mesh-advertise-host`, and a running router in `--mesh-peer-urls`.

- Start the bootstrap router first
- Restart an isolated router under a new name

[Basic Configuration →](#basic-configuration)

</div>

<div class="card" markdown>

### :material-kubernetes: Kubernetes Router Discovery

Routers find each other by pod label, with no bootstrap peer and no start order.

- Pods join when Ready and are marked `DOWN` when deleted
- Restarted pods rejoin under their pod name

[Kubernetes Deployment →](#kubernetes-deployment)

</div>

</div>

### Cluster Size

Any number of routers works. There is no leader election or quorum, so odd counts are not required, and each router keeps serving with the state it has. Every pair of routers keeps one sync stream, so N routers hold N×(N−1)/2 streams and each router sends its updates to N−1 peers.

---

## Kubernetes Deployment

### StatefulSet Configuration

```yaml
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: smg
  namespace: inference
spec:
  serviceName: smg-mesh
  replicas: 3
  selector:
    matchLabels:
      app: smg
  template:
    metadata:
      labels:
        app: smg
      annotations:
        sglang.ai/mesh-port: "39527"
    spec:
      serviceAccountName: smg
      containers:
      - name: smg
        image: ghcr.io/smg-project/smg:latest
        args:
        - --service-discovery
        - --service-discovery-namespace=inference
        - --selector=app=sglang-worker
        - --service-discovery-port=8000
        - --router-selector=app=smg
        - --enable-mesh
        - --mesh-server-name=$(POD_NAME)
        - --mesh-host=0.0.0.0
        - --mesh-advertise-host=$(POD_IP)
        - --mesh-port=39527
        env:
        - name: POD_NAME
          valueFrom:
            fieldRef:
              fieldPath: metadata.name
        - name: POD_IP
          valueFrom:
            fieldRef:
              fieldPath: status.podIP
        ports:
        - containerPort: 30000
          name: http
        - containerPort: 39527
          name: mesh
```

Each router discovers the `sglang-worker` pods itself and finds its peers through the `app=smg` label. The stable pod names of a StatefulSet double as mesh names, and the `smg` service account needs the same Pod permissions as [worker discovery](service-discovery.md).

!!! tip "Engine images"
    For all-in-one deployments where each pod runs both gateway and engine, use an engine image tag (for example `ghcr.io/smg-project/smg:1.11.0-vllm-v0.27.1`, following `{smg_version}-{engine}-{engine_version}`). See [Getting Started](../../getting-started/index.md#install) for available tags. Workers are shared by URL, so a co-located engine reaches other routers only through an address other pods can dial: ZMQ (`ipc://`) workers stay with their router, and a loopback URL names each router's own host.

### Headless Service

```yaml
apiVersion: v1
kind: Service
metadata:
  name: smg-mesh
  namespace: inference
spec:
  clusterIP: None
  selector:
    app: smg
  ports:
  - port: 39527
    name: mesh
```

This is the StatefulSet's governing Service. Router discovery dials Pod IPs, so the mesh does not use these DNS names; expose the HTTP port to clients through a regular Service or load balancer.

### Kubernetes Router Discovery

Router discovery watches Pods that match `--router-selector` and writes them straight into the mesh membership table, replacing `--mesh-peer-urls`. It runs as its own task alongside worker discovery.

```bash
smg launch --enable-mesh \
    --mesh-server-name "$POD_NAME" \
    --mesh-advertise-host "$POD_IP" \
    --service-discovery \
    --service-discovery-namespace inference \
    --selector app=sglang-worker \
    --router-selector app=smg
```

| Setting | Requirement |
|---------|-------------|
| `--enable-mesh` | Required. Without it the router logs `Router selector configured but mesh is not enabled` and skips router discovery |
| `--service-discovery` | Required. In v1.11 router discovery is configured through service discovery, so `--router-selector` alone does nothing. It also starts worker discovery; set `--selector` for your worker pods |
| `--router-selector` | Required. Space-separated `key=value` labels, all of which a Pod must carry. Without a selector, router discovery does not start |
| `--service-discovery-namespace` | Namespace watched for router Pods, shared with worker discovery. Unset watches all namespaces |
| `--mesh-server-name` | Must equal the pod name, because discovery registers each peer under its pod name |
| `sglang.ai/mesh-port` annotation | Mesh port to dial for that Pod. If it is missing or invalid (invalid values log a warning), the discovering router dials its own `--mesh-port` |
| RBAC | `get`, `list`, and `watch` on Pods, as for [worker discovery](service-discovery.md) |

How Pods map to node status:

- **Running and Ready**: `ALIVE` at the Pod IP and mesh port
- **Not Ready** (already known and not `DOWN`): `SUSPECTED`
- **Deleted or terminating**: `DOWN`

!!! tip "Label selectors"
    Keep `--router-selector` disjoint from `--selector`: a router Pod that matched the worker selector would also be registered as a worker.

---

## Monitoring

!!! note "No HTTP API for mesh state"
    Earlier releases served mesh state under `/ha/*`. Those routes were removed, and v1.11 has no HTTP endpoint for mesh membership or sync state. Use the metrics below, the router logs, and each router's `GET /workers` list.

### Mesh Metrics

Mesh metrics are served with the other gateway metrics on the Prometheus port (default `29000`).

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `router_mesh_peer_connections` | Gauge | `peer` | `1` while the sync stream from `peer` into this router is open, `0` after it closes |
| `router_mesh_peer_reconnects_total` | Counter | `peer` | Sync streams from `peer` into this router that ended |
| `router_mesh_sync_round_duration_seconds` | Histogram | `peer` | Time to queue one round of updates on the stream to `peer` |

Each sync stream is opened by the router whose name sorts first and counted by the router that accepts it, so a router reports `router_mesh_peer_connections` only for peers whose names sort before its own, and `router_mesh_sync_round_duration_seconds` only for peers whose names sort after it. Scrape every router and sum: a fully connected mesh of N routers has N×(N−1)/2 streams. Accepting a stream also sets an empty-`peer` series to `1` that normally stays at `1`, so filter with `peer!=""`. After you remove a router for good, its series stays at `0` on the routers that accepted its streams until they restart.

### Alerting Rules

```yaml
groups:
- name: smg-mesh
  rules:
  # A fully connected mesh of N routers has N*(N-1)/2 sync streams.
  # 3 routers -> 3 streams. Scrape every router.
  - alert: SMGMeshStreamsMissing
    expr: (count(router_mesh_peer_connections{peer!=""} == 1) or vector(0)) < 3
    for: 2m
    labels:
      severity: warning
    annotations:
      summary: "SMG mesh has fewer sync streams than expected"

  - alert: SMGMeshStreamDown
    expr: router_mesh_peer_connections{peer!=""} == 0
    for: 2m
    labels:
      severity: warning
    annotations:
      summary: "Mesh sync stream from router {{ $labels.peer }} is down"
```

---

## Best Practices

<div class="grid" markdown>

<div class="card" markdown>

### :material-account-multiple-check: No Quorum Needed

Any router count works. There is no leader or quorum, so odd counts are not required.

</div>

<div class="card" markdown>

### :material-earth: Availability Zones

Spread routers across zones so that a zone outage leaves routers that hold the shared workers and cache state.

</div>

<div class="card" markdown>

### :material-lock: Protect the Mesh Port

Expose the mesh port only to other routers. It carries plaintext, unauthenticated gRPC.

</div>

<div class="card" markdown>

### :material-monitor: Monitoring

Alert when `count(router_mesh_peer_connections{peer!=""} == 1)` drops below N×(N−1)/2 for N routers.

</div>

</div>

---

## Troubleshooting

### Common Issues

| Symptom | Cause | Solution |
|---------|-------|----------|
| Startup fails with `Invalid mesh peer URL` (Python CLI) or `Invalid value for field 'mesh_peer_urls'` (Rust binary) | A peer address is a hostname | Pass `IP:port`; on Kubernetes, use router discovery |
| Startup fails with `mesh advertise address cannot be unspecified` | `--mesh-host` is `0.0.0.0` and `--mesh-advertise-host` is unset | Set `--mesh-advertise-host` to the node's IP |
| Startup fails with `mesh port cannot be 0` | `--mesh-port 0` | Use a fixed port |
| A joining router logs `No peer address available to connect` every round | Its bootstrap peer was unreachable in the first gossip round and is not retried | Start the bootstrap router, then restart this one |
| A router stays out of the mesh after a restart or partition | Peers marked it `DOWN` and no longer probe it | Restart it under a new name, or use router discovery; see [Node Status States](#node-status-states) |
| Routers appear under both their pod name and another name in the `Status:` log | `--mesh-server-name` differs from the pod name under router discovery | Set `--mesh-server-name=$(POD_NAME)` |
| Log shows `Router selector configured but mesh is not enabled` | `--router-selector` without `--enable-mesh` | Add `--enable-mesh` |
| No `Router node discovery enabled` log line | `--router-selector` without `--service-discovery` | Add `--service-discovery` |
| A router Pod never joins | Pod not Running and Ready, labels do not match, or RBAC is missing | Check readiness, labels, and the Role |
| ZMQ workers are missing on peers | `ipc://` workers are host-local | Expected: each router keeps its own ZMQ workers |

### Debug Logging

```bash
RUST_LOG=warn,smg=info,smg_mesh=debug,smg::mesh=debug,smg::mesh_discovery=debug \
    smg launch --enable-mesh ...
```

Gossip, sync streams, and CRDT merges log under `smg_mesh`; the worker and cache-tree sync adapters under `smg::mesh`; router discovery under `smg::mesh_discovery`. `RUST_LOG` replaces the filter built from `--log-level`, so keep a base level in it.

### Verify Cluster Health

```bash
# Membership: every gossip round logs this router's node table at INFO
kubectl -n inference logs smg-0 | grep 'Status:' | tail -n 1

# Worker sync: every router lists the workers its peers registered
curl -s http://<router-ip>:30000/workers | jq -r '.workers[] | "\(.url) healthy=\(.is_healthy)"'
```

```promql
# Sync streams across all routers: N*(N-1)/2 when fully connected
count(router_mesh_peer_connections{peer!=""} == 1)
```

---

## What's Next?

<div class="grid" markdown>

<div class="card" markdown>

### :material-kubernetes: Service Discovery

Discover workers in Kubernetes and set up the Role that router discovery shares.

[Service Discovery →](service-discovery.md)

</div>

<div class="card" markdown>

### :material-lightning-bolt: Cache-Aware Routing

How the prefix trees that the mesh shares drive routing.

[Cache-Aware Routing →](../routing/cache-aware.md)

</div>

<div class="card" markdown>

### :material-power: Graceful Shutdown

Allow in-flight requests to complete during shutdown.

[Graceful Shutdown →](../reliability/graceful-shutdown.md)

</div>

<div class="card" markdown>

### :material-chart-box: Metrics Reference

Gateway metrics to scrape alongside the mesh metrics.

[Metrics Reference →](../../reference/metrics.md)

</div>

</div>
