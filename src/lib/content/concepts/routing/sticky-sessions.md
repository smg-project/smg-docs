---
title: Sticky Sessions and Routing Keys
---

# Sticky Sessions and Routing Keys

A multi-turn conversation runs fastest when each turn lands on the worker that served the turn before it, because that worker still holds the conversation's prefix in its KV cache. Sticky sessions give SMG that property: each request carries a **routing key**, taken from the request body's `rid` or from a header, and SMG pins the key to one worker for as long as the key stays in use.

SMG provides stickiness in two forms:

- The **`manual` policy** pins every keyed request in its own key-to-worker map.
- The **routing-key override** (`--routing-key-override`, alias `--sticky-sessions`) adds the same pinning on top of any other policy. Keyed requests stick; requests without a key, and by default the first request for each new key, are still placed by the configured policy, such as `cache_aware`.

---

## Overview

<div class="grid" markdown>

<div class="card" markdown>

### :material-source-branch: Conversation Lineage

The key comes from the body `rid` with turn and retry suffixes stripped, so `conv_t1`, `conv_t2`, and `conv_t2_r1` all pin as `conv`.

</div>

<div class="card" markdown>

### :material-tag-outline: Header Fallback

Without a `rid`, SMG reads the first valid value from an ordered list of headers, `x-smg-routing-key` by default.

</div>

<div class="card" markdown>

### :material-routes: Any Policy

With the override, keyed requests stick on every policy, and by default the configured policy still chooses where a new key starts.

</div>

<div class="card" markdown>

### :material-gauge: Bounded and Observable

Idle pins expire, a key's requests beyond two in flight are placed again instead of going straight to its pinned worker, and inference responses from HTTP workers name the worker that served them.

</div>

</div>

---

## Deriving the Routing Key

### Key Precedence

For each request, SMG uses the first of these that yields a key:

1. **Body `rid`** (override enabled only): the `rid` with its lineage suffixes stripped.
2. **Routing-key headers**: the first header named in `--routing-key-headers` that carries a valid value.
3. **No key**: the configured policy places the request, and nothing is pinned.

A body `rid` wins even when a routing-key header is also present, so a proxy that stamps a unique header value on every request cannot split a conversation. This holds on every policy, including `manual` and `consistent_hashing`.

!!! note "Request IDs are not routing keys"
    `X-Request-ID` and the other request-ID headers identify a request in logs, traces, and backend request IDs, but never route it. Only the body `rid` field and the configured routing-key headers feed sticky routing.

### Body `rid` Lineage

`rid` is an SGLang-style request ID accepted in the body of chat completions, completions, messages, `/generate`, embeddings, classify, and rerank requests (for rerank, the first ID of a list). The Responses API has no `rid`, so Responses requests can pin only through a header. SMG reads `rid` for routing only when `--routing-key-override` is enabled.

SMG removes one trailing retry suffix, then one trailing turn suffix:

```text
<base>[_t<digits>][_r<digits>]  ->  <base>
```

- A suffix is an underscore, a lowercase `t` (turn) or `r` (retry), and one or more ASCII digits, at the very end of the `rid`.
- The retry suffix is removed first, so it has to come last: `conv_t2_r1` becomes `conv`, while `conv_r1_t2` becomes `conv_r1`.
- Each suffix is removed at most once.
- If nothing would be left, the whole `rid` is the key.
- If the resulting key is longer than 128 bytes, SMG ignores the `rid` and falls back to the headers.

| `rid` | Routing key |
|-------|-------------|
| `conv` | `conv` |
| `conv_t2` | `conv` |
| `conv_t2_r1` | `conv` |
| `conv_r1` | `conv` |
| `conv_t1_t2` | `conv_t1` |
| `conv_r1_t2` | `conv_r1` |
| `conv_t`, `conv_tx1`, `conv_T2` | Unchanged |
| `under_scored_id` | `under_scored_id` |
| `_t1` | `_t1` |

Stripping only computes the key; the request keeps its full `rid`. On gRPC workers outside PD mode, SMG sends the `rid` as the engine request ID, so the suffixes let every turn and retry carry a distinct ID while sharing one key.

### Routing-Key Headers

`--routing-key-headers` takes an ordered list of header names; the default is `x-smg-routing-key`. Each name must be a valid HTTP header name, or SMG refuses to start, and names match case-insensitively. For each request, SMG walks the list and takes the first header whose value is:

- non-empty,
- valid UTF-8, and
- at most 128 bytes.

A value that fails these checks is skipped without an error, and SMG tries the next name. With the override enabled, header keys get the same lineage stripping as a `rid`, so a proxy that forwards `conv_t2` in a header pins the same entry as a body `rid` of `conv_t2`.

```bash
# Read an upstream proxy's header first, and keep the SMG header as a fallback
smg launch --worker-urls http://w1:8000 http://w2:8000 \
  --routing-key-override \
  --routing-key-headers x-routing-key x-smg-routing-key
```

!!! warning "`manual` and `consistent_hashing` read headers their own way"
    These two policies handle routing keys themselves, even with the override enabled. `manual` reads only `X-SMG-Routing-Key`, requires a non-empty ASCII value but applies no 128-byte cap, and ignores `--routing-key-headers`. `consistent_hashing` tries the `--routing-key-headers` list, then `X-SMG-Routing-Key`. Neither strips lineage suffixes from header values. A body `rid` is still stripped and still wins on both.

---

## Configuration

Enable the override on top of any policy, here `cache_aware`:

```bash
smg launch --worker-urls http://w1:8000 http://w2:8000 http://w3:8000 \
  --policy cache_aware \
  --routing-key-override
```

Then give each turn of a conversation a `rid` that shares a base:

```bash
curl http://localhost:30000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "meta-llama/Llama-3.1-8B-Instruct",
    "rid": "chat-7f3a_t1",
    "messages": [{"role": "user", "content": "Plan a three-day trip to Kyoto."}]
  }'
```

The next turn sends `"rid": "chat-7f3a_t2"`, and a retry of that turn sends `"rid": "chat-7f3a_t2_r1"`. All three pin under the key `chat-7f3a`. A client that cannot set `rid` sends `-H "x-smg-routing-key: chat-7f3a"` instead.

| Flag | Default | Description |
|------|---------|-------------|
| `--routing-key-override` | Off | Pin keyed requests on any policy. Alias: `--sticky-sessions` |
| `--routing-key-headers` | `x-smg-routing-key` | Ordered header names to read the key from; the first valid value wins |
| `--assignment-mode` | `delegate` for the override, `random` for `--policy manual` | How a new key picks its worker: `random`, `min_load`, `min_group`, or `delegate` |
| `--max-idle-secs` | `14400` | Seconds a pin can go unused before it is evicted. Alias: `--sticky-key-idle-secs` |
| `--eviction-interval` | `120` | Seconds between eviction sweeps. Also sets the cache-aware tree eviction interval |

`--assignment-mode`, `--max-idle-secs`, and `--eviction-interval` apply to both the `manual` policy and the override. See the [Configuration Reference](../../reference/configuration.md#manual-policy-and-sticky-sessions) for every routing option.

### Manual Policy or Override?

| Aspect | `--policy manual` | `--routing-key-override` |
|--------|-------------------|--------------------------|
| First worker for a new key | `--assignment-mode` (default `random`) | The configured policy (`delegate`, the default) |
| Requests without a key | Placed by `--assignment-mode`, not pinned | Placed by the configured policy, unchanged |
| Key sources | `X-SMG-Routing-Key`; the body `rid` first only when the override is also enabled | Body `rid`, then `--routing-key-headers` |
| Lineage stripping of header keys | No | Yes |
| Pins scoped by model | No (by PD leg only) | Yes (by model and PD leg) |
| Per-key in-flight threshold | No | Yes |

Choose the override to keep another policy, usually `cache_aware`, in charge of where conversations start. Choose `manual` when placement should depend only on the assignment mode.

---

## How the Override Composes with Each Policy

| Configured policy | With `--routing-key-override`, a keyed request is | Without the override, a routing key is |
|-------------------|---------------------------------------------------|----------------------------------------|
| `cache_aware`, `round_robin`, `random`, `power_of_two`, `least_load`, `bucket`, `passthrough` | Pinned in the override's sticky map; under `delegate`, the policy chooses a new key's first worker | Ignored |
| `prefix_hash` | Pinned in the override's sticky map; under `delegate`, the policy chooses a new key's first worker | Hashed onto the ring in place of the prompt's token IDs or text, which `prefix_hash` otherwise hashes |
| `manual` | Pinned in the manual policy's own map, with the body `rid` taking precedence | Pinned from `X-SMG-Routing-Key` |
| `consistent_hashing` | Hashed onto the ring, with the body `rid` taking precedence; `X-SMG-Target-Worker` still wins | Hashed onto the ring |

Requests without a key go to the configured policy unchanged. Under `delegate` assignment, the override's default, the first request of a conversation is placed exactly as it would be without the override (by prefix match under `cache_aware`, for example), and later turns follow it. Pinning keeps the worker's cached prefix reachable on every turn; it does not make the engine keep that cache any longer.

In PD mode, the prefill and decode legs are pinned independently.

---

## Assignment Modes and Eviction

### Assignment Modes

`--assignment-mode` decides where a key goes the first time SMG sees it, and where it moves when none of its remembered workers is available.

| Mode | A new key goes to |
|------|-------------------|
| `random` | A random available worker |
| `min_load` | The available worker with the fewest in-flight requests |
| `min_group` | The available worker with the fewest active routing keys (distinct keys with requests in flight on it) |
| `delegate` | The worker the configured policy selects. `--policy manual` has nothing to delegate to, so there it behaves like `min_load` |

`min_load` and `min_group` break ties at random, and each gateway replica counts only the requests it dispatched. The default is `random` for `--policy manual` and `delegate` for the override. One flag sets both, so leave `--assignment-mode` unset to keep delegation under the override.

### Reuse and Failover

Each key remembers up to two workers: a primary and one failover candidate. A request goes to the first remembered worker that is **available**: healthy, with its circuit breaker not open, and not vetoed by [overload protection](../reliability/overload-protection.md). Adding workers never moves a key.

When no remembered worker is available, SMG places the request again and records the result:

- Under the override in `delegate` mode, the configured policy picks the worker, which becomes the new primary; the previous primary is kept as the failover candidate.
- In the other modes, and in every mode under `--policy manual`, the assignment mode picks the worker and adds it behind the existing candidate, dropping the older one when two are already remembered. The earlier worker stays first, so the key returns to it as soon as it is available again.

### In-Flight Threshold

Under the override, when a key already has **2** requests in flight on its pinned worker, SMG places the next concurrent request for that key again: through the configured policy in `delegate` mode, otherwise through the assignment mode. The request goes wherever that placement lands. The threshold triggers a new placement; it is not a hard limit. The placement can pick the pinned worker again, for example when `cache_aware` finds the conversation's prefix there, so a key can have more than 2 requests in flight on one worker. The pin moves only if the placement picks a different worker that has fewer than 2 of the key's requests in flight, so fleet-wide pressure cannot walk a conversation away from the worker that holds its prefix. Other traffic on the worker never triggers a new placement, and each gateway replica counts its own requests. The `manual` policy has no such threshold.

### Idle Eviction

Every `--eviction-interval` seconds (default `120`), SMG removes pins that have not been used for `--max-idle-secs` (default `14400`, four hours). Each request that reuses a pin refreshes it. A key whose pin was evicted is placed like a new key on its next request.

The map has no size limit: it holds one entry per key used within the idle window (per model and PD leg under the override), and each entry remembers at most two workers. Setting `--max-idle-secs` to `0` disables eviction, and the map then grows without bound. `--eviction-interval 0` also disables it, but SMG refuses to start with that value when the policy is `cache_aware`, the default.

---

## Scoping

- **By model (override):** a key used with several models keeps an independent pin for each, so a conversation that switches models cannot evict its own pins. The `manual` policy's map is not scoped by model.
- **By PD leg:** prefill and decode pins are independent, under both `manual` and the override.
- **By gateway replica:** pins live in each replica's memory. They are not shared through [mesh HA](../architecture/high-availability.md), and a restart clears them. With several replicas, send each conversation to the same replica upstream, or use `consistent_hashing`, which computes the same placement on every replica that sees the same workers as available.
- **Self-hosted workers only:** requests to external providers and to the Realtime API are placed by SMG's least-load selector and are never pinned.

---

## Request Body Streaming

SMG can stream a large request body straight to an HTTP worker instead of buffering it, when nothing in the gateway needs to read the body; see [Request Streaming](../performance/request-streaming.md). Sticky routing changes that decision:

- **Override enabled:** SMG buffers every request body so it can read the `rid`, even when the request carries routing hints. These requests are counted in `smg_router_request_body_path_total{path="buffered", reason="routing_key_override"}`.
- **Override disabled:** a valid `x-smg-routing-tokens` header lets `cache_aware` and `bucket` place the request without its body, so a large body can stream. A routing-key header does not lift that requirement.

The hint header format is in the [Request Headers reference](../../reference/api/openai.md#routing-hint-headers).

---

## Observability

### Routed Worker Header

Inference responses from HTTP workers carry `x-smg-routed-worker-id`: the URL of the worker that served the request, as registered with the gateway, including the `@<rank>` suffix for data-parallel workers. For a prefill/decode pair it names the decode worker. Compare it across turns to confirm that a conversation sticks. Responses from gRPC workers do not carry it.

```bash
curl -s -D - -o /dev/null http://localhost:30000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "x-smg-routing-key: user-1234" \
  -d '{"model": "meta-llama/Llama-3.1-8B-Instruct", "messages": [{"role": "user", "content": "Hi"}]}' \
  | grep -i x-smg-routed-worker-id
# x-smg-routed-worker-id: http://w2:8000
```

### Metrics

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `smg_manual_policy_branch_total` | Counter | `branch` | Sticky decisions by outcome: `occupied_hit` (pin reused), `occupied_miss` (remembered workers unavailable, key placed again), `vacant` (new key), `cap_respill` (in-flight threshold reached, request placed again), `no_routing_id` (`manual` request without a key), `no_healthy_workers` |
| `smg_manual_policy_cache_entries` | Gauge | — | Entries in the sticky map, updated on each sticky decision |
| `smg_routing_key_source_total` | Counter | `source` | Where the key came from for each keyed request the override routes: `rid` or `header` |
| `smg_worker_routing_keys_active` | Gauge | `worker` | Distinct routing keys with requests in flight on each worker; the value `min_group` balances |
| `smg_consistent_hashing_policy_branch_total` | Counter | `branch` | `consistent_hashing` outcomes: `target_worker_hit`, `target_worker_miss`, `routing_key_hit`, `random_fallback`, `no_healthy_workers` |
| `smg_router_request_body_path_total` | Counter | `path`, `reason` | `reason="routing_key_override"` counts bodies buffered because the override is enabled |

The `manual` policy and the override both report through the `smg_manual_policy_*` metrics. The full list is in the [Metrics Reference](../../reference/metrics.md).

### Debug Logs

At `--log-level debug`, every decision the override makes logs one `Sticky routing decision` line with the key source (`rid` or `header`), the key (prefixed internally with the model and PD leg), the branch, the chosen worker, and the model ID. An eviction sweep that removes pins logs `ManualPolicy TTL eviction` at `info`.

---

## Troubleshooting

??? question "Turns of one conversation land on different workers"
    Check these causes in order:

    - **The override is off.** Without `--routing-key-override`, SMG ignores the body `rid`, and every policy except `manual`, `consistent_hashing`, and `prefix_hash` ignores routing-key headers.
    - **The `rid` does not match the suffix grammar.** `conv-t2`, `conv_turn2`, and `conv_T2` are each a key of their own. A high `vacant` rate in `smg_manual_policy_branch_total` under steady traffic points here.
    - **The key is invalid.** A key longer than 128 bytes, or an empty or non-UTF-8 header value, is ignored. `manual` instead ignores an `X-SMG-Routing-Key` value that is empty or not ASCII.
    - **The header name is not configured.** The override reads only the names in `--routing-key-headers`; `manual` reads only `X-SMG-Routing-Key`.
    - **The pinned worker became unavailable.** Look for `occupied_miss`, then check worker health, circuit breakers, and overload vetoes.
    - **A key has more than two requests in flight.** The extra requests are placed again; look for `cap_respill`.
    - **The pin expired.** A key idle for longer than `--max-idle-secs` starts over.
    - **Several gateway replicas.** Each replica pins keys independently.

??? question "Load is uneven across workers"
    A pinned key stays on its worker while that worker is available; only new keys and keys placed again see current load. Choose `min_load` or `min_group`, or keep `delegate` with a load-aware policy such as `cache_aware`.

??? question "Large request bodies are always buffered"
    With the override enabled, SMG buffers every body to read the `rid`; check `smg_router_request_body_path_total{reason="routing_key_override"}`. To stream large bodies, disable the override and route with `x-smg-routing-tokens` instead.

??? question "The sticky map keeps growing"
    Each distinct key (per model and PD leg under the override) keeps an entry until it has been idle for `--max-idle-secs`. Watch `smg_manual_policy_cache_entries`, make sure neither `--eviction-interval` nor `--max-idle-secs` is `0`, and lower `--max-idle-secs` if conversations are short-lived.

---

## What's Next?

<div class="grid" markdown>

<div class="card" markdown>

### :material-scale-balance: Load Balancing

Every routing policy, including `manual` and `consistent_hashing`.

[Load Balancing →](load-balancing.md)

</div>

<div class="card" markdown>

### :material-cached: Cache-Aware Routing

The policy that usually places a conversation's first turn.

[Cache-Aware Routing →](cache-aware.md)

</div>

<div class="card" markdown>

### :material-transfer-right: Request Streaming

When SMG buffers a request body and when it streams it.

[Request Streaming →](../performance/request-streaming.md)

</div>

<div class="card" markdown>

### :material-format-list-bulleted: Request Headers

Every header SMG reads or adds, including the routing hints.

[Request Headers →](../../reference/api/openai.md#request-headers)

</div>

<div class="card" markdown>

### :material-cog: Configuration Reference

All routing policy options.

[Configuration Reference →](../../reference/configuration.md#manual-policy-and-sticky-sessions)

</div>

<div class="card" markdown>

### :material-chart-box: Metrics Reference

Sticky routing, consistent hashing, and body-path metrics.

[Metrics Reference →](../../reference/metrics.md)

</div>

</div>
