---
title: PD Disaggregation
---

# PD Disaggregation

Prefill-Decode (PD) disaggregation separates the two phases of LLM inference — prompt processing (prefill) and token generation (decode) — onto specialized workers. This optimizes Time to First Token (TTFT) and throughput independently.

<div class="prerequisites" markdown>

#### Before you begin

- Completed the [Getting Started](index.md) guide
- At least one prefill worker and one decode worker, each on its own GPUs
- For vLLM PD: workers started with a `--kv-transfer-config` for NIXL or Mooncake

</div>

---

## Why Disaggregate?

| Phase | Compute Pattern | Bottleneck |
|-------|-----------------|------------|
| **Prefill** | Compute-bound, parallel | GPU compute |
| **Decode** | Memory-bound, sequential | Memory bandwidth |

Running both on the same worker creates contention — prefill batches wait for decode slots, and decode batches stay small due to memory pressure. Dedicating workers to each phase removes this conflict.

---

## Choose a Setup

| Engine | HTTP workers | gRPC workers | Section |
|--------|--------------|--------------|---------|
| SGLang | :material-check: | :material-check: | [SGLang PD](#sglang-pd) |
| vLLM | :material-check: | :material-check: | [vLLM PD over gRPC](#vllm-pd-over-grpc), [vLLM PD over HTTP](#vllm-pd-over-http) |
| TokenSpeed | :material-close: | :material-check: | [TokenSpeed PD and EPD](#tokenspeed-pd-and-epd-grpc) |

With HTTP workers, SMG forwards the request body to both legs and adds the handoff fields. With gRPC workers, SMG tokenizes the prompt and parses the output itself (see [gRPC Workers](grpc-workers.md)).

---

## SGLang PD

SMG sends the request to the prefill and decode workers at the same time. The two workers find each other through the prefill worker's bootstrap server, then move the KV cache with their transfer backend.

### Start SGLang Workers

=== "HTTP"

    ```bash
    # Prefill worker
    python -m sglang.launch_server \
      --model-path meta-llama/Llama-3.1-8B-Instruct \
      --host 0.0.0.0 \
      --port 8000 \
      --disaggregation-mode prefill \
      --disaggregation-bootstrap-port 8998

    # Decode worker
    python -m sglang.launch_server \
      --model-path meta-llama/Llama-3.1-8B-Instruct \
      --host 0.0.0.0 \
      --port 8001 \
      --disaggregation-mode decode
    ```

=== "gRPC"

    ```bash
    # Prefill worker
    python -m sglang.launch_server \
      --model-path meta-llama/Llama-3.1-8B-Instruct \
      --host 0.0.0.0 \
      --port 50051 \
      --grpc-mode \
      --disaggregation-mode prefill \
      --disaggregation-bootstrap-port 8998

    # Decode worker
    python -m sglang.launch_server \
      --model-path meta-llama/Llama-3.1-8B-Instruct \
      --host 0.0.0.0 \
      --port 50061 \
      --grpc-mode \
      --disaggregation-mode decode
    ```

Both workers must use the same KV transfer backend (`--disaggregation-transfer-backend`, for example `mooncake` or `nixl`).

In gRPC mode SGLang also opens an HTTP sidecar on `--port + 1` (move it with `--smg-http-sidecar-port`), so leave a gap between the ports of workers on the same host.

### Start SMG

Pass each prefill worker's bootstrap port after its URL. It must match the worker's `--disaggregation-bootstrap-port`:

```bash
smg launch \
  --pd-disaggregation \
  --prefill http://prefill:8000 8998 \
  --decode http://decode:8001 \
  --host 0.0.0.0 \
  --port 30000
```

For gRPC workers, use `grpc://` URLs and add `--model-path meta-llama/Llama-3.1-8B-Instruct` so SMG can load the tokenizer.

### Multiple Workers

```bash
smg launch \
  --pd-disaggregation \
  --prefill http://prefill1:8000 8998 \
  --prefill http://prefill2:8000 8998 \
  --decode http://decode1:8001 \
  --decode http://decode2:8001 \
  --prefill-policy cache_aware \
  --decode-policy power_of_two
```

!!! warning "Parallel sampling over HTTP"
    The HTTP PD router does not split an `n>1` request into one rendezvous per sample, so SGLang `n>1` completions can stall over HTTP. Serve `n>1` traffic through gRPC workers, where SMG sends each sample as its own prefill/decode dispatch with its own bootstrap room.

---

## vLLM PD over gRPC

SMG sends to prefill first, capped at one output token, then sends the original request to decode, relaying KV-transfer metadata between the two legs:

- **NIXL**: SMG tags the prefill request with `do_remote_decode=true`, harvests the `kv_transfer_params` the prefill engine returns (engine id, request id, block ids, side-channel address, TP size), and forwards them verbatim with the decode request so decode pulls the KV cache over NIXL.
- **Mooncake**: the connector is push-based and returns nothing, so SMG mints a shared `transfer_id`, tags the prefill request with it, and synthesizes the decode params (`remote_engine_id` discovered from the worker, `remote_bootstrap_addr` from the worker's bootstrap host and port). With an older servicer that doesn't report `kv_engine_id`, SMG falls back to legacy host/port injection.

The servicer reports each worker's connector, so gRPC workers need no extra registration. A `MultiConnector` that wraps exactly one NIXL or Mooncake connector is reported as that connector.

### Start vLLM Workers with NIXL

```bash
# Prefill worker
VLLM_NIXL_SIDE_CHANNEL_PORT=5600 \
python -m vllm.entrypoints.grpc_server \
  --model /path/to/model \
  --port 50051 \
  --kv-transfer-config '{"kv_connector":"NixlConnector","kv_role":"kv_producer"}'

# Decode worker
VLLM_NIXL_SIDE_CHANNEL_PORT=5601 \
python -m vllm.entrypoints.grpc_server \
  --model /path/to/model \
  --port 50052 \
  --kv-transfer-config '{"kv_connector":"NixlConnector","kv_role":"kv_consumer"}'
```

`VLLM_NIXL_SIDE_CHANNEL_PORT` must be unique per worker on the same host (with
data parallelism each rank uses `port + dp_rank`). When prefill and decode run
on different machines, also set `VLLM_NIXL_SIDE_CHANNEL_HOST` to an address
reachable from the decode worker — prefill embeds this host/port in the
handoff params that decode uses to fetch the KV cache.

To verify KV transfer is active, send a request and look for `Transfer plan:`
in the decode worker log (vLLM >= 0.20). If the router logs
`prefill returned no kv_transfer_params`, upgrade the servicer
(smg-grpc-servicer >= 0.5.4, smg-grpc-proto >= 0.4.9) or check the
`--kv-transfer-config` on the workers.

### Start SMG

vLLM gRPC workers use `grpc://` URLs:

```bash
smg launch \
  --pd-disaggregation \
  --prefill grpc://prefill:50051 \
  --decode grpc://decode:50052 \
  --model-path /path/to/model \
  --host 0.0.0.0 \
  --port 30000
```

On the gRPC path SMG tokenizes the prompt itself, so it must be able to load the model's tokenizer: from the path each worker reports, or from `--model-path` (a Hugging Face ID or a local path).

### Alternative: Mooncake Backend

Mooncake supports TCP transport (no RDMA required). Each prefill worker needs a unique bootstrap port:

```bash
# Prefill worker
VLLM_MOONCAKE_BOOTSTRAP_PORT=8998 \
python -m vllm.entrypoints.grpc_server \
  --model /path/to/model \
  --port 50051 \
  --kv-transfer-config '{"kv_connector":"MooncakeConnector","kv_role":"kv_producer","engine_id":"prefill-0"}'

# Decode worker
python -m vllm.entrypoints.grpc_server \
  --model /path/to/model \
  --port 50052 \
  --kv-transfer-config '{"kv_connector":"MooncakeConnector","kv_role":"kv_consumer"}'
```

Set an explicit `engine_id` on each prefill worker in production. SMG reads
the id at registration and reads it again when a failed or not-ready prefill
worker recovers. Without a pinned id, vLLM generates a new one per process, so
a prefill restart that no health check notices leaves SMG minting handoffs for
the old id.

With vLLM data parallelism (`data_parallel_size > 1`), run SMG with
`--dp-aware`: SMG pins each request to a DP rank and mints the decode params
with the matching `{engine_id}_dp{rank}` engine-core id. Without `--dp-aware`,
decode params are not minted for DP>1 prefill workers, and SMG falls back to legacy host/port
injection (decode recomputes the prompt locally). External-LB DP
(`--data-parallel-external-lb`, one pod per rank) is unsupported for Mooncake
minting: every pod's engine core is `{engine_id}_dp0`, so register pods as
plain workers and pin a distinct `engine_id` per pod.

```bash
smg launch \
  --pd-disaggregation \
  --prefill grpc://prefill:50051 8998 \
  --decode grpc://decode:50052 \
  --model-path /path/to/model
```

### Helper Script

The smg repository ships a script that launches a prefill/decode pair with either backend. Run it from an smg checkout:

```bash
# NIXL (default)
./scripts/launch-pd-workers.sh vllm /path/to/model

# Mooncake
KV_BACKEND=mooncake ./scripts/launch-pd-workers.sh vllm /path/to/model
```

---

## vLLM PD over HTTP

SMG runs the same sequential flow against vLLM's OpenAI-compatible HTTP server, with no gRPC servicer in the stack. It sends the prefill leg as a one-token, non-streaming request, captures the `kv_transfer_params` from the prefill response, and relays them to the decode leg.

### Start vLLM Workers

```bash
# Prefill worker
VLLM_NIXL_SIDE_CHANNEL_PORT=5600 \
vllm serve meta-llama/Llama-3.1-8B-Instruct \
  --host 0.0.0.0 \
  --port 8000 \
  --kv-transfer-config '{"kv_connector":"NixlConnector","kv_role":"kv_producer"}'

# Decode worker
VLLM_NIXL_SIDE_CHANNEL_PORT=5601 \
vllm serve meta-llama/Llama-3.1-8B-Instruct \
  --host 0.0.0.0 \
  --port 8001 \
  --kv-transfer-config '{"kv_connector":"NixlConnector","kv_role":"kv_consumer"}'
```

The NIXL side-channel notes in the gRPC section apply here too.

### Start SMG and Register the Workers

The vLLM HTTP server does not report its KV connector, so start SMG in PD mode without startup workers:

```bash
smg launch --pd-disaggregation --host 0.0.0.0 --port 30000
```

Then register each worker with its connector:

```bash
curl -X POST http://localhost:30000/workers \
  -H "Content-Type: application/json" \
  -d '{"url": "http://prefill:8000", "worker_type": "prefill", "kv_connector": "NixlConnector"}'

curl -X POST http://localhost:30000/workers \
  -H "Content-Type: application/json" \
  -d '{"url": "http://decode:8001", "worker_type": "decode", "kv_connector": "NixlConnector"}'
```

!!! warning "Register the connector"
    Workers passed as `--prefill http://...` and `--decode http://...` carry no connector. SMG then runs them in passthrough mode: requests still succeed, but decode recomputes every prompt and `smg_pd_kv_connector_mode_total{mode="passthrough"}` grows.

For Mooncake, start the workers with the Mooncake `--kv-transfer-config` and `VLLM_MOONCAKE_BOOTSTRAP_PORT` from the gRPC section, using `vllm serve` in place of the gRPC entrypoint. Then register the prefill worker with its engine id and bootstrap port as well:

```bash
curl -X POST http://localhost:30000/workers \
  -H "Content-Type: application/json" \
  -d '{
    "url": "http://prefill:8000",
    "worker_type": "prefill",
    "kv_connector": "MooncakeConnector",
    "kv_engine_id": "prefill-0",
    "bootstrap_port": 8998
  }'
```

`kv_engine_id` must match the `engine_id` in the prefill worker's `--kv-transfer-config`, and `bootstrap_port` its `VLLM_MOONCAKE_BOOTSTRAP_PORT`. Without an engine id, SMG cannot mint the handoff and decode recomputes the prompt. On Kubernetes, the `smg.ai/kv-connector` and `smg.ai/kv-engine-id` pod annotations set the same fields (see [Service Discovery](service-discovery.md#pd-disaggregation-discovery)).

---

## TokenSpeed PD and EPD (gRPC)

TokenSpeed serves PD over gRPC and moves the KV cache with Mooncake. For multimodal models it also supports EPD (encode-prefill-decode): encode workers run the vision tower and ship the embeddings to prefill over Mooncake. `$MODEL` below is the model to serve; for EPD, a vision-language model.

```bash
# Prefill worker
python -m smg_grpc_servicer.tokenspeed \
  --model "$MODEL" \
  --host 0.0.0.0 \
  --port 50061 \
  --disaggregation-mode prefill \
  --disaggregation-bootstrap-port 8998 \
  --disaggregation-transfer-backend mooncake

# Decode worker
python -m smg_grpc_servicer.tokenspeed \
  --model "$MODEL" \
  --host 0.0.0.0 \
  --port 50062 \
  --disaggregation-mode decode \
  --disaggregation-transfer-backend mooncake
```

Start SMG in PD mode:

```bash
smg launch \
  --pd-disaggregation \
  --prefill grpc://prefill:50061 8998 \
  --decode grpc://decode:50062 \
  --model-path "$MODEL"
```

For EPD, add encode workers and switch SMG to `--epd-disaggregation`:

```bash
# Encode worker (vision tower)
python -m smg_grpc_servicer.tokenspeed \
  --model "$MODEL" \
  --host 0.0.0.0 \
  --port 50060 \
  --disaggregation-mode encode \
  --disaggregation-bootstrap-port 8995 \
  --disaggregation-transfer-backend mooncake
```

```bash
smg launch \
  --epd-disaggregation \
  --encode grpc://encode:50060 8995 \
  --prefill grpc://prefill:50061 8998 \
  --decode grpc://decode:50062 \
  --model-path "$MODEL"
```

The number after each encode and prefill URL is that worker's `--disaggregation-bootstrap-port`. `--encode-policy` (`random`, `round_robin`, or the default `consistent_hashing`) assigns media items to encode workers; the default sends a repeated image to the same encode worker. EPD works only with gRPC TokenSpeed workers, and text-only requests skip the encode leg.

---

## Kubernetes

With service discovery, give each role its own label selector:

```bash
smg launch \
  --service-discovery \
  --pd-disaggregation \
  --prefill-selector app=sglang role=prefill \
  --decode-selector app=sglang role=decode \
  --service-discovery-namespace inference \
  --service-discovery-port 8000
```

Prefill pods advertise their bootstrap port with the `sglang.ai/bootstrap-port` annotation, and vLLM pods declare their KV connector with `smg.ai/kv-connector`. See [Service Discovery](service-discovery.md#pd-disaggregation-discovery) for the pod labels and annotations.

---

## Check Pairing

SMG pairs a prefill worker only with decode workers that share its KV transfer protocol: the same runtime, the same transport (NIXL or Mooncake), and a matching KV cache layout. Each prefill and decode worker shows its pairing key in `/workers`:

```bash
curl -s http://localhost:30000/workers | jq '.workers[] | {url, worker_type, pd_pairing}'
```

When no prefill shares a key with any decode, requests fail with 503 `no_compatible_pd_pair`. The Rust `smg` binary's `--pd-pairing-mode` flag (`off`, `lenient`, or `strict`; default `lenient`) sets how strictly the legs are compared. See [Prefill/Decode Pairing](../concepts/routing/pd-disaggregation.md#prefilldecode-pairing).

---

## Verify

```bash
# Check workers and their roles
curl -s http://localhost:30000/workers | jq '.workers[] | {url, worker_type, is_healthy}'

# Ready once at least one prefill and one decode worker are healthy
curl -i http://localhost:30000/readiness

# Send a request
curl http://localhost:30000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "meta-llama/Llama-3.1-8B-Instruct",
    "messages": [{"role": "user", "content": "Hello!"}]
  }'
```

PD mode serves Chat Completions, Completions, the Anthropic Messages API, and the Responses API on both transports. `/v1/messages/count_tokens` works with HTTP workers and goes to a single prefill worker.

---

## vLLM vs SGLang PD at a Glance

| | vLLM PD | SGLang PD |
|---|---------|-----------|
| **Worker transport** | HTTP or gRPC | HTTP or gRPC |
| **Dispatch** | Prefill first, then decode | Both workers receive request simultaneously |
| **KV Transfer** | NIXL or Mooncake (`--kv-transfer-config`) | Mooncake or NIXL (`--disaggregation-transfer-backend`), through the prefill's bootstrap server |
| **Handoff data** | `kv_transfer_params`, relayed or minted by SMG | `bootstrap_host`, `bootstrap_port`, `bootstrap_room`, injected by SMG |
| **SMG flags** | `--prefill <url>` (NIXL) or `--prefill <url> <bootstrap_port>` (Mooncake) | `--prefill <url> <bootstrap_port>` |
| **HTTP workers** | Register with `kv_connector` for a KV handoff | No extra registration |

TokenSpeed PD works like SGLang PD (both legs at once, bootstrap port on the prefill URL), over gRPC with Mooncake only.

---

## Next Steps

For per-phase routing policies, pairing, admission control, multimodal handling, monitoring, and troubleshooting, see the full [PD Disaggregation Concepts](../concepts/routing/pd-disaggregation.md) page.
