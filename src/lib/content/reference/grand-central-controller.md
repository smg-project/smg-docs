---
title: Grand Central Controller
---

# Grand Central Controller Reference

`smg-grand-central` is the standalone Grand Central controller: a second deployable binary, separate from the gateway, that runs the [Grand Central policy engine](../concepts/routing/load-balancing.md#grand-central) as a service of its own. It keeps the worker registry, monitors the workers' load, runs a policy document, and serves placement decisions, their leases, and worker membership to gateways over gRPC.

The controller is for gateways you host: register workers with `RegisterWorker` on its control listener, then ask for placements with `Session` and `Decide` on its gateway listener, through the generated gRPC client or the crate's reference client. The stock SMG gateway does not ask a standalone controller for its decisions — its `--grand-central on` flag runs the same engine embedded in the gateway's own process, documented under [Grand Central](../concepts/routing/load-balancing.md#grand-central).

---

## Deploying the Controller

**With the Helm chart.** `grandCentral.enabled` deploys the controller beside the router as a Deployment and Service of its own, with the admin listener as the chart's scrape target. The policy document comes from `grandCentral.spec`, rendered as a ConfigMap and read with `--spec-file`, or from a spec store on a claim named by `grandCentral.specStoreClaim` (`--spec-store`); `grandCentral.election.enabled` adds leader election among the replicas over a Kubernetes `Lease`. [Deploy to Kubernetes](../getting-started/index.md#helm) covers the chart's resources and the `grandCentral.*` values. Each release from 1.16.0 publishes a controller image beside the gateway's, under the gateway's tag with `-grand-central` appended.

**From a checkout.** The binary builds under the `controller` cargo feature, with `protoc` installed. Save a policy document as `policy.yaml`, then run from the checkout root:

```sh
cargo build --release --locked -p smg-grand-central --features controller
target/release/smg-grand-central --controller-id controller-0 --spec-file policy.yaml \
  --gateway-listen 127.0.0.1:30100 --control-listen 127.0.0.1:30101 \
  --replication-listen 127.0.0.1:30102 --admin-listen 127.0.0.1:30103 \
  --identity-as-told gateway,control
```

The published image also builds the `kubernetes` feature, which adds the Kubernetes `Lease` election backend (`--lease-name`).

---

## Listeners

The controller serves four listeners, each bound on all addresses by default. Their ports sit in one block, clear of the gateway's data port (30000) and metrics port (29000).

| Listener | Default address | Protocol | Serves |
|----------|-----------------|----------|--------|
| Gateway | `0.0.0.0:30100` | gRPC | The gateways' calls: `Session`, `Decide`, `WatchWorkers` |
| Control | `0.0.0.0:30101` | gRPC | The operators' unary calls: worker registration and the policy spec calls |
| Replication | `0.0.0.0:30102` | gRPC | `Replicate`: a standby's follow and restore |
| Admin | `0.0.0.0:30103` | HTTP | The probes, `/version` and `/metrics` ([below](#admin-endpoints)) |

Every gRPC listener also serves the standard gRPC health service and the `Leader` call, which reports the known leader, its address and its term, and takes no identity.

---

## Command-Line Flags

| Flags | Default and effect |
|-------|--------------------|
| `--help`, `--version` | Print help or the version and exit. |
| `--controller-id` | Omitted, it reads `HOSTNAME`; with neither, startup is refused. This controller's identity: the holder of the leases it takes and the name on its log lines; under Kubernetes, the pod name. Letters, digits, `.`, `_` and `-`. |
| `--spec-file` | The policy document as a watched YAML file (JSON under a `.json` name), at most 128 KiB. It is read and checked at startup: a file that cannot be read, or a document that does not run, refuses the start. While the replica leads, a changed file applies as the next generation — or under the number its `generation` line writes — and a rejected document leaves the running generation in place. A following replica runs the leader's document instead and applies its own file's document only once it leads. Give exactly one of `--spec-file` and `--spec-store`. |
| `--spec-store` | The policy document as an existing directory the controller manages through `ApplyPolicySpec`: the newest generation on file applies at startup, the last eight stay on file for `RollbackPolicySpec`, and a store with nothing on file starts, reports ready and sheds every decision until the first apply. One process holds the directory under a lock; a start that finds the lock held waits up to the grace period for its holder to stop. A store runs under a process-local lease only: it is refused beside `--lease-file` or `--lease-name`. |
| `--gateway-listen`, `--control-listen`, `--replication-listen`, `--admin-listen` | `0.0.0.0:30100` through `0.0.0.0:30103`. Where the four listeners bind. |
| `--identity-as-told` | Empty. The listeners, by name (`gateway`, `control`, `replication`, comma-separated), that take a caller's stated `x-smg-principal` metadata value as its identity, unverified. See [Identity and TLS](#identity-and-tls). |
| `--tls-cert`, `--tls-key` | Unset. The PEM server certificate chain and private key of every gRPC listener, given together. The admin listener stays plain HTTP. |
| `--gateway-client-ca`, `--control-client-ca`, `--replication-client-ca` | Unset. A PEM client CA per scope: mutual TLS on that scope's listener. Each requires server TLS and conflicts with `--identity-as-told` on the same listener. |
| `--lease-file` | Unset. Leader election over a lease file, for replicas on one host; the file's directory must exist. Each renewal is a durable write: on storage whose sync is slow, run slower timings (for example `--lease-duration-ms 15000 --lease-renew-deadline-ms 10000 --lease-renew-period-ms 2000`). With no lease flag the controller runs alone and leads itself. |
| `--lease-name`, `--lease-namespace` | Unset. Leader election over a Kubernetes `Lease` of this name (needs the `kubernetes` feature, which the published image builds). The namespace defaults to the pod's own. Conflicts with `--lease-file`; the lease duration must be whole seconds. |
| `--advertise-address` | Unset. This replica's dialable address — a host, which takes the gateway listener's port, or `host:port` — written into the lease and answered by `Leader`. Required for a shared lease when the gateway listener binds a wildcard address. |
| `--lease-duration-ms`, `--lease-renew-deadline-ms`, `--lease-renew-period-ms` | `3000`, `2000`, `1000`. How long a lease stands unrenewed before a follower takes it; how long a leader serves on after its last renewal before it fences itself; how often it renews. The duration must exceed the deadline, which must exceed the positive period. |
| `--load-monitor-interval` | `10` seconds, positive. Seconds between two load polls of an engine whose stream is not serving load records, and the freshness bound a pushed record must meet to stand in for the poll. The gateway's flag of the same name. |
| `--worker-overload-waiting-requests`, `--worker-overload-token-usage` | `8` and `0.8`, the gateway's flags of the same names. The overload thresholds of an engine's worker whose registration states none: queued requests (from 1 to 2^63 - 1) and KV usage (above 0, at most 1). A registration's own threshold stands over each, and a worker of the `external` runtime takes no default. Start every replica with the same two values: each judges by its own while it leads. |
| `--shutdown-settle-secs`, `--shutdown-grace-period-secs` | `5` and `30` seconds. The settle window keeps the listeners accepting after readiness fails, so probes see the change first; the grace period bounds the whole shutdown, settle included, and the listeners close with their streams when it runs out. Grace is positive, and settle is at most half of it. |
| `--decide-shards` | One per CPU available to the process, eight at most. Deciding threads, 1 to 256, each behind a queue of 128 requests. A model's decisions all run on one shard. |
| `--runtime-worker-threads` | Unset: the async runtime's default (`available_parallelism()`, which honors the cgroup CPU quota). 1 to 4096 when set; a count above four per available CPU starts with a warning. |
| `--log-level`, `--log-json` | `info` and text output. The level (`debug`, `info`, `warn` or `error`) applies to the controller's own crates; `RUST_LOG`, when set, replaces the filter whole. Text logs color on a terminal: `NO_COLOR` turns color off, and `FORCE_COLOR` or `CLICOLOR_FORCE` forces it on. |

### What Startup Refuses

Startup checks the whole configuration and refuses to start, naming the first fault it finds:

- A missing or empty `--controller-id` (nothing in `HOSTNAME` to read), or one with characters outside letters, digits, `.`, `_` and `-`.
- Neither or both of `--spec-file` and `--spec-store`; a spec file that cannot be read; a document that does not run.
- Two listeners on one address, the admin listener counted — a wildcard listener shares its port with every listener it covers.
- `--tls-cert` without `--tls-key` or the reverse; a client CA without server TLS; `--identity-as-told` naming an unknown listener, or one whose scope has a client CA.
- Lease timings out of order; a shared lease behind a wildcard gateway listener without `--advertise-address`; a malformed or wildcard advertise address; a Kubernetes lease duration that is not whole seconds; `--spec-store` beside a shared lease.
- A zero `--shutdown-grace-period-secs`; a settle window over half the grace; a zero `--load-monitor-interval`; an overload threshold out of its bounds.

---

## Identity and TLS

Every call except `Leader` names its caller. The controller maps no certificate to a principal: a client CA verifies the caller's certificate at the transport and grants no identity, so calls that need a principal are served only on a listener named in `--identity-as-told`. Such a listener takes the caller's `x-smg-principal` metadata value as its identity with nothing verified — whoever reaches it acts as any principal of its scopes — so name a listener only on a loopback or behind a mesh or network policy that admits its callers alone. A stated principal is a visible ASCII token of at most 253 bytes, without commas.

A call off the listener's scope is refused `PERMISSION_DENIED`, and the scope is checked before the identity: a call that needs a principal and has none is refused `UNAUTHENTICATED`.

---

## gRPC Calls by Scope

| Scope | Call | In brief |
|-------|------|----------|
| Any gRPC listener | `Leader` | The known leader, its address and term. One lease covers all models. Takes no identity. |
| Gateway | `Session` | One session per gateway: `Hello` names the caller and a protocol range containing 1 or 2, `Welcome` answers identity and limits, and the stream carries dispatch, completion, release and veto reports. A new session replaces the gateway's earlier one. |
| Gateway | `Decide` | The decision stream: single requests, a pair decided at its prefill, a decode call that names the pair's lease, and encode requests that carry their items. Answers carry up to four ordered targets and a lease for the caller to dispatch and report; a decision that finds no worker is a shed, answered with its reason and the stage that stopped it, and a call the controller cannot serve answers `Busy` with a reason. |
| Gateway | `WatchWorkers` | A snapshot of worker membership and status followed by the changes, for all models or a named set, resumable from a term and sequence. A position outside the retained history can require a new snapshot. |
| Control | `RegisterWorker`, `PatchWorker`, `DeregisterWorker` | Add, replace or upsert a worker by URL; patch labels and overload thresholds; mark draining or remove, guarded by worker ID and revision. |
| Control | `GetSnapshot` | The leader's redacted worker and spec slices, named explicitly in the request. |
| Control | `Explain` | A sampled decision, looked up by gateway and request ID on the leader. The document's `explain: { sample: N }` turns sampling on. |
| Control | `ApplyPolicySpec`, `GetPolicySpec`, `RollbackPolicySpec` | Dry-run, read, apply and roll back policy documents. Applies and rollbacks write only under a spec store; under a spec file they answer `read_only`. |
| Control | `StepDown`, `WatchRegime` | Admitted, then answered `UNIMPLEMENTED`. |
| Replication | `Replicate` | A standby follows the leader's term, replays retained events or restores a snapshot. A replica that does not lead dials the leader's advertised host at the same replication port every replica listens on, in cleartext, and only when `--identity-as-told` names `replication`. |

The policy documents the controller runs are the same documents the embedded engine takes, with one difference: the standalone service refuses a document that names the `bucket` scorer. [Grand Central](../concepts/routing/load-balancing.md#grand-central) covers the document itself.

---

## Worker Discovery

A controller built with the `discovery` cargo feature can find its workers itself instead of waiting for `RegisterWorker` calls: the replica that leads registers each worker a pass finds and removes each one its source lists no more. The feature is off by default, and the published controller image does not build it.

Such a build takes the gateway's worker-discovery flags under the same names and defaults: `--discovery-provider` selects `kubernetes`, `file`, `slurm` or `consul`, and unset, the controller runs no provider. See [Service Discovery](configuration.md#service-discovery-kubernetes) for the shared flags. Three differences from the gateway: `--router-selector` and `--model-alias` stay with the gateway; the controller keeps no worker API key, so `--api-key` and `--api-key-file` are refused at startup; and a found worker is dialed with no client identity and no private CA, so a worker served over TLS under a private CA, or one that requires mutual TLS, is never registered.

---

## Admin Endpoints

The admin listener speaks plain HTTP. The probes keep the gateway's paths and bodies, so one probe set and one scrape configuration serve both processes.

| Path | Answer |
|------|--------|
| `/liveness`, `/health` | `200 OK`, even while draining: the process is healthy, just not taking new work. |
| `/readiness` | `200` with `{"status": "ready", "role": ...}` for the leader and a ready follower, the role `leader` or `follower`; `503` with `{"status": "not ready", "reason": ...}` otherwise. Readiness drops at the start of a shutdown, before the listeners close. |
| `/version` | JSON naming the binary, its version, and the protobuf package its listeners speak (`smg.policy.v1`). |
| `/metrics` | The controller's Prometheus families. |

### Metrics

`/metrics` serves the controller's own series: the `smg_grand_central_*` families — its state and start time, replication, load polling and scraping, the decision stream's counters, membership classification, overload flips and contained lifecycle panics — the two spec families its spec source adds, `smg_policy_spec_generation` and `smg_policy_spec_apply_total`, and the engine's fault counter `smg_policy_faults_total`.

The `smg_grand_central_*` series and the two spec families are the controller's alone: no gateway publishes them. `smg_policy_faults_total` is not the controller's alone — a gateway running the embedded Grand Central engine publishes the identical family on its own `/metrics` from the first scrape. The gateway's families are in the [Metrics Reference](metrics.md).
