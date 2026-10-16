---
title: Traffic Splits and Canaries
---

# Traffic Splits and Canaries

Rolling out a new engine build, a new quantization, or a new model version is safest a few percent of the traffic at a time. A traffic split gives SMG that primitive: workers are labelled into **groups**, each group carries a **weight**, and every request first picks a group by weight and then lets the configured routing policy choose a worker inside it. Weights are worker labels, so a ramp is an edit through the admin API rather than a restart, and a weight of `0` drains a group while it stays registered.

The split is off by default; `--traffic-split` turns it on.

---

## Overview

<div class="grid" markdown>

<div class="card" markdown>

### :material-tag-outline: Groups by Label

A worker's `traffic_group` label names its group. Workers without one form the group `default`, so a canary can join a fleet that was never labelled.

</div>

<div class="card" markdown>

### :material-scale-balance: Weights by Label

A worker's `traffic_weight` label is its group's weight; a group without one weighs `100`. Two stable workers at `90` and one canary at `10` send a tenth of the traffic to the canary, whatever the worker counts.

</div>

<div class="card" markdown>

### :material-routes: Policy Inside the Group

Once the group is picked, the configured policy (`cache_aware`, `least_load`, `round_robin`, ...) and the sticky-session override run inside it, unchanged.

</div>

<div class="card" markdown>

### :material-source-branch: Sessions Stay Put

A request with a routing key lands in the same group every time, on every gateway replica. A ramp between two groups only moves sessions into the group being raised.

</div>

</div>

---

## How a Request Is Placed

1. The candidates are gathered as usual: the workers serving the model, on the request's transport, that are available (healthy, circuit breaker closed, not overloaded).
2. The available candidates are grouped by their `traffic_group` label. With one group, the split does nothing and the policy runs over the whole pool.
3. With two or more groups, each group's weight is the largest `traffic_weight` among its available workers (`100` when none carries one). A group whose workers are all down drops out of the draw; a group at weight `0` takes nothing.
4. The request picks a group: by its routing key when it has one (the key's fixed share of the total weight, hashed), at random otherwise.
5. The configured policy selects a worker inside the group. The sticky-session override, when on, pins inside the group too.

An explicit `X-SMG-Target-Worker` header indexes the whole pool and bypasses the split, as it bypasses the routing key. The `bucket` prefill policy places by request size over boundaries spanning its whole fleet and is not split; `--traffic-split` is refused with it at start-up.

### Weights and Shares

A group's share is its weight over the sum of the weights of the groups that have an available worker:

| Groups | Shares |
|--------|--------|
| `stable` at `90`, `canary` at `10` | 90 % / 10 % |
| Unlabelled fleet (`default`, `100`), `canary` at `5` | 95.2 % / 4.8 % |
| `stable` at `90`, `canary` at `0` | 100 % / 0 % (drained) |
| `blue` at `100`, `green` at `100` | 50 % / 50 % |

The shares do not depend on how many workers each group has: one canary worker at weight `10` against twenty stable workers at weight `90` still takes a tenth of the requests. A keyed session's group follows the same shares over many keys, and raising one group of a two-group split never moves a session out of it.

---

## Configuration

Start the gateway with the flag:

```bash
smg --policy cache_aware --traffic-split \
  --worker-urls http://stable-1:8000 http://stable-2:8000
```

Label the workers, at registration or afterwards. At registration:

```bash
curl -X POST http://localhost:30000/workers \
  -H "Content-Type: application/json" \
  -d '{
    "url": "http://canary-1:8000",
    "labels": {"traffic_group": "canary", "traffic_weight": "10"}
  }'
```

To ramp or drain, change the weight with a `PATCH`; the listed keys are merged into the worker's labels:

```bash
# Ramp the canary towards a quarter of the traffic.
curl -X PATCH http://localhost:30000/workers/<worker_id> \
  -H "Content-Type: application/json" \
  -d '{"labels": {"traffic_weight": "30"}}'

# Drain it: it stays registered and takes nothing.
curl -X PATCH http://localhost:30000/workers/<worker_id> \
  -H "Content-Type: application/json" \
  -d '{"labels": {"traffic_weight": "0"}}'
```

A `traffic_weight` must be an integer from `0` to `1000000`, and a `traffic_group` 1 to 63 characters of ASCII letters, digits, `_`, `-`, or `.`; anything else is refused with `400 Bad Request`, at registration and on `PATCH`. Workers that arrive through service discovery carry the same labels and are held to the same rules. When the workers of a group disagree on the weight (a ramp in progress, one `PATCH` at a time), the highest value counts.

| Option | Default | Description |
|--------|---------|-------------|
| `--traffic-split` | `false` | Run the weighted split across worker groups before the routing policy. |

The Python launcher takes the same flag (`--traffic-split`, `RouterArgs.traffic_split`).

---

## Observability

`smg_traffic_split_selections_total{model, group}` counts the selections the split confined to each group, so the arms of a canary can be read side by side. It is only incremented when the split applies, that is when two or more groups are among a request's candidates. The request, error, and latency families carry no group label; compare the arms by worker there.

```promql
# Share of a model's split selections that reached the canary over five minutes
sum(rate(smg_traffic_split_selections_total{model="my-model", group="canary"}[5m]))
/
sum(rate(smg_traffic_split_selections_total{model="my-model"}[5m]))
```

---

## Troubleshooting

??? question "The canary takes no traffic"
    Check these causes in order:

    - **The flag is off.** Without `--traffic-split` the labels are stored and returned but never read.
    - **Its weight is `0`.** Read the worker's labels with `GET /workers/{worker_id}`.
    - **Its workers are unavailable.** A group whose workers are all unhealthy, behind an open circuit breaker, or vetoed by overload protection drops out of the draw. Check `smg_worker_health` and the circuit breaker metrics.
    - **It serves another model.** The split runs among the candidates of the requested model.

??? question "The shares drift from the weights"
    The draw runs among the groups with an available worker, so a group whose workers are partly down keeps its full weight while any of them is up, and a group whose workers are all down takes nothing. Over short windows the shares of random (keyless) traffic also vary as any sample does; read them over minutes.

??? question "A session changes group"
    Its requests carry no routing key (see [Sticky Sessions](sticky-sessions.md) for the key sources), or the weights of a split with three or more groups changed. Only a two-group ramp keeps every session in place.

---

## What's Next?

<div class="grid" markdown>

<div class="card" markdown>

### :material-scale-balance: Load Balancing

The policies that run inside the chosen group.

[Load Balancing →](load-balancing.md)

</div>

<div class="card" markdown>

### :material-source-branch: Sticky Sessions and Routing Keys

Where the routing key that keeps a session in one group comes from.

[Sticky Sessions →](sticky-sessions.md)

</div>

<div class="card" markdown>

### :material-api: Admin API

Registering workers with labels and changing them with `PATCH`.

[Admin API →](../../reference/api/admin.md#update-worker-partial)

</div>

<div class="card" markdown>

### :material-chart-box: Metrics Reference

The traffic split counter and the worker families around it.

[Metrics Reference →](../../reference/metrics.md#traffic-split-metrics)

</div>

</div>
