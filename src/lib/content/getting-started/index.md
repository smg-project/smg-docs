---
title: Getting Started
---

# Getting Started

Shepherd Model Gateway (SMG) routes and manages LLM traffic across workers. This page gives you a fast path to a working gateway, then points you to feature-specific setup guides.

## Install

=== "pip (recommended)"

    ```bash
    pip install smg
    ```

    The wheels on [PyPI](https://pypi.org/project/smg/) target the stable ABI (`abi3`), so each one works on CPython 3.9 and newer:

    | Platform | Wheels |
    |----------|--------|
    | Linux x86_64 | manylinux2014 (glibc 2.17+), musllinux 1.1 |
    | Linux aarch64 | manylinux 2.28 (glibc 2.28+), musllinux 1.1 |
    | macOS | Intel (10.12+), Apple Silicon (11.0+) |
    | Windows | x86_64 |

    The package installs the `smg` command:

    - `smg launch` starts the gateway alone, for workers you run yourself
    - `smg serve` starts engine workers and then the gateway; the engine (vLLM, SGLang, TensorRT-LLM, or TokenSpeed) must be installed in the same environment

    `smg launch` parses its flags in Python (`smg.launch_router`, which is also the gateway Docker image's entrypoint) and runs the Rust gateway in-process. This launcher accepts most of the Rust binary's flags but not all of them: `--priority-scheduler-*`, `--tenant-*`, `--drain-settle-secs`, and `--runtime-worker-threads`, among others, exist only in the binary from `cargo install` or a source build.

    Nightly Linux x86_64 wheels are attached to dated prereleases in [smg-project/artifacts](https://github.com/smg-project/artifacts/releases). Install one with `pip install <wheel URL>`.

=== "Cargo (crates.io)"

    ```bash
    cargo install --locked smg
    ```

    This compiles the gateway from [crates.io](https://crates.io/crates/smg) and installs the `smg` binary (and its `amg` alias) into `~/.cargo/bin`. `--locked` builds with the `Cargo.lock` published with the release. The binary takes the full Rust flag set through `smg launch` (or plain `smg`); `smg serve` is only in the Python package.

    The build needs `protoc`, a C/C++ toolchain, and OpenSSL headers:

    ```bash
    # Debian/Ubuntu
    sudo apt-get install -y build-essential pkg-config libssl-dev protobuf-compiler

    # macOS (Homebrew)
    brew install protobuf openssl@3
    ```

    To compile OpenSSL from source instead of linking the system library, add `--features vendored-openssl` (needs `perl` and `make`).

=== "Docker"

    **Gateway only** (no inference engine), for `linux/amd64` and `linux/arm64`:

    ```bash
    docker pull lightseekorg/smg:latest

    # Same image on GitHub Container Registry
    docker pull ghcr.io/smg-project/smg:latest
    ```

    Release tags are the bare version with no `v` prefix, for example `lightseekorg/smg:1.10.1`, and `latest` points at the newest release. GHCR carries gateway releases from 1.10.1 on; older release tags are on Docker Hub. Nightly builds are on GHCR only: `ghcr.io/smg-project/smg:nightly`, or `nightly-<YYYYMMDD>-<short-sha>` to pin one night. Dated nightly tags are pruned after a short retention window.

    **Gateway + engine** (all-in-one images that route and serve), for `linux/amd64` only:

    ```bash
    docker pull ghcr.io/smg-project/smg:<version>-<engine>-<engine_version>
    ```

    As of v1.11.0, each release builds these engine images from the listed upstream base images:

    | Engine | Tag suffix | Base image | Nightly tag |
    |--------|-----------|------------|-------------|
    | vLLM | `vllm-v0.27.1`, `vllm-v0.26.0`, `vllm-v0.25.0` | `vllm/vllm-openai` | `nightly-vllm` |
    | SGLang | `sglang-v0.5.20` | `lmsysorg/sglang` | `nightly-sglang` |
    | TensorRT-LLM | `trtllm-1.3.0rc24`, `trtllm-1.3.0rc23`, `trtllm-1.3.0rc22` | `nvcr.io/nvidia/tensorrt-llm/release` | `nightly-trtllm` |
    | TokenSpeed | `tokenspeed-tml` | `lightseekorg/tokenspeed:tml` | `nightly-tokenspeed` |

    For example, v1.10.1 published `ghcr.io/smg-project/smg:1.10.1-vllm-v0.27.1` and `ghcr.io/smg-project/smg:1.10.1-sglang-v0.5.18`. Browse every tag on [GHCR](https://github.com/smg-project/smg/pkgs/container/smg) or [Docker Hub](https://hub.docker.com/r/lightseekorg/smg). Engine images for 1.9.0 and earlier were published as `ghcr.io/lightseekorg/smg`.

=== "From Source"

    ```bash
    # Install Rust
    curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
    source "$HOME/.cargo/env"

    # Clone and build (needs the protoc and OpenSSL packages from the Cargo tab)
    git clone https://github.com/smg-project/smg.git
    cd smg
    cargo build --release
    ```

    The gateway binary is at `./target/release/smg`. To build and install the Python package (which adds `smg serve`) from the same checkout:

    ```bash
    pip install maturin
    make python-install
    ```

## Step 1: Start SMG

Choose one of these startup paths.

### Option A: All-in-one with `smg serve`

`smg serve` launches backend worker process(es) and then starts SMG with generated worker URLs.

=== "vLLM"

    ```bash
    smg serve \
      --backend vllm \
      --model meta-llama/Llama-3.1-8B-Instruct \
      --data-parallel-size 2 \
      --host 0.0.0.0 \
      --port 30000
    ```

=== "TensorRT-LLM (gRPC)"

    ```bash
    smg serve \
      --backend trtllm \
      --model meta-llama/Llama-3.1-8B-Instruct \
      --data-parallel-size 2 \
      --host 0.0.0.0 \
      --port 30000
    ```

=== "TokenSpeed (ZMQ)"

    ```bash
    smg serve \
      --backend tokenspeed \
      --connection-mode zmq \
      --model meta-llama/Llama-3.1-8B-Instruct \
      --router-model-path meta-llama/Llama-3.1-8B-Instruct \
      --data-parallel-size 2 \
      --host 0.0.0.0 \
      --port 30000
    ```

    TokenSpeed runs headless, and SMG connects to its engine core over ZMQ. `--router-model-path` gives the gateway the model name and tokenizer, which the engine doesn't report over ZMQ.

=== "SGLang"

    ```bash
    smg serve \
      --backend sglang \
      --model-path meta-llama/Llama-3.1-8B-Instruct \
      --data-parallel-size 2 \
      --connection-mode grpc \
      --host 0.0.0.0 \
      --port 30000
    ```

This starts `--data-parallel-size` worker replicas, waits for each to become healthy, then starts the gateway. With `--connection-mode zmq` there is no wait: the gateway starts at once, and `/readiness` returns 503 until a worker has completed its handshake and the tokenizer has loaded.

To connect vLLM to its engine core directly instead of through gRPC, add `--connection-mode zmq` and `--router-model-path <model>`. See [ZMQ Direct Workers](zmq-workers.md).

| Option | Default | Description |
|--------|---------|-------------|
| `--backend` | `sglang` | Inference backend: `sglang`, `vllm`, `trtllm`, or `tokenspeed`. The `SMG_DEFAULT_BACKEND` environment variable changes the default |
| `--connection-mode` | `grpc` | Worker connection mode: `grpc`, `http`, or `zmq`. TensorRT-LLM supports only `grpc` and TokenSpeed only `zmq`; `zmq` also works with `vllm` |
| `--host` | `127.0.0.1` | Router host |
| `--port` | `8080` | Router port (`30000` with `--backend sglang`; see below) |
| `--data-parallel-size`, `--dp-size` | `1` | Number of worker replicas, each on its own GPU slice |
| `--worker-host` | `127.0.0.1` | Host for worker processes |
| `--worker-base-port` | `31000` | Base port for worker processes |
| `--worker-startup-timeout` | `300` | Seconds to wait for each worker to become healthy |
| `--enable-token-usage-details` | off | In `http` mode, start the engine with cached-token reporting (`--enable-cache-report` for SGLang, `--enable-prompt-tokens-details` for vLLM) |

Gateway options take a `--router-` prefix (for example `--router-policy` or `--router-model-path`); other flags are passed to the engine. When the backend's own CLI also defines `--host` or `--port`, its definition replaces the one above: with `--backend sglang` the router defaults to `127.0.0.1:30000`, and with vLLM in `http` mode to `0.0.0.0:8000`. Pass both flags to be explicit.

!!! note "Single-worker defaults"
    With `--data-parallel-size 1`, `smg serve` disables gateway retries and the circuit breaker and forces the `passthrough` routing policy, overriding any `--router-policy`: with one worker there is nothing to fail over to or balance across.

### Option B: Launch gateway only with `smg launch`

Use this when workers are already running or managed by another platform.

For gRPC workers:

```bash
smg launch \
  --worker-urls grpc://localhost:50051 \
  --model-path meta-llama/Llama-3.1-8B-Instruct \
  --policy round_robin \
  --host 0.0.0.0 \
  --port 30000
```

For HTTP workers:

```bash
smg launch \
  --worker-urls http://localhost:8000 \
  --policy round_robin \
  --host 0.0.0.0 \
  --port 30000
```

## Step 2: Verify Core Endpoints

Health:

```bash
curl http://localhost:30000/health
curl http://localhost:30000/readiness
```

OpenAI-compatible chat completions:

```bash
curl http://localhost:30000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "meta-llama/Llama-3.1-8B-Instruct",
    "messages": [{"role": "user", "content": "Say hello in one sentence."}]
  }'
```

Responses API:

```bash
curl http://localhost:30000/v1/responses \
  -H "Content-Type: application/json" \
  -d '{
    "model": "meta-llama/Llama-3.1-8B-Instruct",
    "input": "Say hello in one sentence."
  }'
```

## Step 3: Choose Your Setup Track

### Core Deployment

- [Multiple Workers](multiple-workers.md)
- [gRPC Workers](grpc-workers.md)
- [ZMQ Direct Workers](zmq-workers.md)
- [PD Disaggregation](pd-disaggregation.md)
- [Service Discovery](service-discovery.md)

### Operations and Security

- [Monitoring](monitoring.md)
- [Logging](logging.md)
- [TLS](tls.md)
- [Control Plane Auth](control-plane-auth.md)
- [Control Plane Operations](control-plane-operations.md)

### Reliability and Data

- [Reliability Controls](reliability-controls.md)
- [Data Connections](data-connections.md)
- [Tokenization and Parsing APIs](tokenization-and-parsing.md)

### Advanced Features

- [Load Balancing](load-balancing.md)
- [KV Events Cache-Aware Routing](kv-events-cache-aware.md)
- [Tokenizer Caching](tokenizer-caching.md)
- [MCP in Responses API](mcp.md)
- [External Providers](external-providers.md)
- [RL Control Plane](rl-control-plane.md)

---

## Worker Startup Recipes (Standalone)

Use these when workers are not started via `smg serve`. Each command starts one worker; register it with `smg launch --worker-urls` using a `grpc://` or `http://` URL, as in [Option B](#option-b-launch-gateway-only-with-smg-launch). The SMG engine images for vLLM, SGLang, TensorRT-LLM, and TokenSpeed already contain the engine and the SMG gRPC servicer. SMG v1.11.0's CI starts workers with these commands (plus test-specific flags) on vLLM 0.27.1, SGLang 0.5.20, TensorRT-LLM 1.3.0rc24, and a pinned TokenSpeed commit.

=== "vLLM"

    gRPC mode needs the SMG servicer: `pip install "vllm[grpc]"` adds `smg-grpc-servicer[vllm]`.

    ```bash
    # gRPC worker (grpc://<host>:50051)
    python -m vllm.entrypoints.grpc_server \
      --model meta-llama/Llama-3.1-8B-Instruct \
      --host 0.0.0.0 \
      --port 50051 \
      --tensor-parallel-size 1

    # HTTP worker (http://<host>:8000)
    python -m vllm.entrypoints.openai.api_server \
      --model meta-llama/Llama-3.1-8B-Instruct \
      --host 0.0.0.0 \
      --port 8000
    ```

=== "SGLang"

    SGLang depends on `smg-grpc-servicer`, so gRPC mode works after `pip install sglang`.

    ```bash
    # gRPC worker (grpc://<host>:50051)
    python -m sglang.launch_server \
      --model-path meta-llama/Llama-3.1-8B-Instruct \
      --host 0.0.0.0 \
      --port 50051 \
      --smg-grpc-mode

    # HTTP worker (http://<host>:8000)
    python -m sglang.launch_server \
      --model-path meta-llama/Llama-3.1-8B-Instruct \
      --host 0.0.0.0 \
      --port 8000
    ```

    `--smg-grpc-mode` needs SGLang 0.5.16 or later; older releases use `--grpc-mode`, which is now a deprecated alias. In this mode SGLang also opens an HTTP sidecar (profiling, plus `/metrics` with `--enable-metrics`) on `--port + 1` (set `--smg-http-sidecar-port` to move it), so leave a gap between the ports of workers on the same host.

=== "TensorRT-LLM"

    gRPC serving is built into TensorRT-LLM 1.3.0rc14 and later.

    ```bash
    # gRPC worker (grpc://<host>:50051)
    python -m tensorrt_llm.commands.serve serve \
      meta-llama/Llama-3.1-8B-Instruct \
      --grpc \
      --host 0.0.0.0 \
      --port 50051 \
      --backend pytorch \
      --tp_size 1
    ```

=== "TokenSpeed"

    Install TokenSpeed from [source](https://github.com/lightseekorg/tokenspeed), then `pip install "smg-grpc-servicer[tokenspeed]"`.

    ```bash
    # gRPC worker (grpc://<host>:50051)
    python -m smg_grpc_servicer.tokenspeed \
      --model meta-llama/Llama-3.1-8B-Instruct \
      --host 0.0.0.0 \
      --port 50051 \
      --grammar-backend xgrammar
    ```

    TokenSpeed's default grammar backend is `none`, which leaves `tool_choice` and `response_format` constraints unenforced. Add `--enable-output-logprobs` if clients request logprobs; without it they come back empty.

=== "MLX"

    Apple Silicon only. Install the servicer with `pip install "smg-grpc-servicer[mlx]"`.

    ```bash
    # gRPC worker (grpc://<host>:50051)
    python -m smg_grpc_servicer.mlx.server \
      --model mlx-community/Qwen3-0.6B-4bit \
      --host 0.0.0.0 \
      --port 50051
    ```

For ZMQ direct workers (`ipc://` URLs), the engine runs headless and connects to the gateway instead; see [ZMQ Workers](zmq-workers.md).

### PD Disaggregation Workers

For prefill-decode disaggregation, start separate prefill and decode workers, each on its own GPUs (on one host, pin them with `CUDA_VISIBLE_DEVICES`):

=== "SGLang PD (gRPC)"

    ```bash
    # Prefill worker
    python -m sglang.launch_server \
      --model-path meta-llama/Llama-3.1-8B-Instruct \
      --host 0.0.0.0 \
      --port 50051 \
      --smg-grpc-mode \
      --disaggregation-mode prefill \
      --disaggregation-bootstrap-port 8998

    # Decode worker
    python -m sglang.launch_server \
      --model-path meta-llama/Llama-3.1-8B-Instruct \
      --host 0.0.0.0 \
      --port 50061 \
      --smg-grpc-mode \
      --disaggregation-mode decode
    ```

    `--smg-grpc-mode` needs SGLang 0.5.16 or later; older releases use `--grpc-mode`, which is now a deprecated alias. In this mode SGLang also opens an HTTP sidecar on `--port + 1`, so the decode worker uses `50061` rather than `50052`.

    Start SMG with the prefill worker's bootstrap port after its URL:

    ```bash
    smg launch \
      --pd-disaggregation \
      --prefill grpc://localhost:50051 8998 \
      --decode grpc://localhost:50061 \
      --model-path meta-llama/Llama-3.1-8B-Instruct \
      --host 0.0.0.0 \
      --port 30000
    ```

=== "SGLang PD (HTTP)"

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

    Start SMG with the prefill worker's bootstrap port after its URL:

    ```bash
    smg launch \
      --pd-disaggregation \
      --prefill http://localhost:8000 8998 \
      --decode http://localhost:8001 \
      --host 0.0.0.0 \
      --port 30000
    ```

=== "vLLM PD (gRPC + NIXL)"

    vLLM uses NIXL for KV cache transfer between prefill and decode workers:

    ```bash
    # Prefill worker
    VLLM_NIXL_SIDE_CHANNEL_PORT=5600 \
    python -m vllm.entrypoints.grpc_server \
      --model meta-llama/Llama-3.1-8B-Instruct \
      --host 0.0.0.0 \
      --port 50051 \
      --kv-transfer-config '{"kv_connector":"NixlConnector","kv_role":"kv_producer"}'

    # Decode worker
    VLLM_NIXL_SIDE_CHANNEL_PORT=5601 \
    python -m vllm.entrypoints.grpc_server \
      --model meta-llama/Llama-3.1-8B-Instruct \
      --host 0.0.0.0 \
      --port 50052 \
      --kv-transfer-config '{"kv_connector":"NixlConnector","kv_role":"kv_consumer"}'
    ```

    Start SMG (no bootstrap ports needed — NIXL handles KV transfer):

    ```bash
    smg launch \
      --pd-disaggregation \
      --prefill grpc://localhost:50051 \
      --decode grpc://localhost:50052 \
      --model-path meta-llama/Llama-3.1-8B-Instruct \
      --host 0.0.0.0 \
      --port 30000
    ```

=== "vLLM PD (HTTP + NIXL)"

    SMG drives vLLM disaggregation over the OpenAI-compatible HTTP server too, with no gRPC servicer:

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

    Start SMG in PD mode without startup workers:

    ```bash
    smg launch --pd-disaggregation --host 0.0.0.0 --port 30000
    ```

    The vLLM HTTP server does not report its KV connector, so register both workers with it. Without `kv_connector`, requests still succeed but decode recomputes every prompt:

    ```bash
    curl -X POST http://localhost:30000/workers \
      -H "Content-Type: application/json" \
      -d '{"url": "http://localhost:8000", "worker_type": "prefill", "kv_connector": "NixlConnector"}'

    curl -X POST http://localhost:30000/workers \
      -H "Content-Type: application/json" \
      -d '{"url": "http://localhost:8001", "worker_type": "decode", "kv_connector": "NixlConnector"}'
    ```

See [PD Disaggregation](pd-disaggregation.md) for the Mooncake backend, TokenSpeed and EPD, Kubernetes discovery, and scaling.

## Send a Request

```bash
curl http://localhost:30000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "meta-llama/Llama-3.1-8B-Instruct",
    "messages": [
      {"role": "user", "content": "What is the capital of France?"}
    ],
    "max_tokens": 50
  }'
```

Expected response:

```json
{
  "id": "chatcmpl-abc123",
  "object": "chat.completion",
  "model": "meta-llama/Llama-3.1-8B-Instruct",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "The capital of France is Paris."
      },
      "finish_reason": "stop"
    }
  ],
  "usage": {
    "prompt_tokens": 14,
    "completion_tokens": 8,
    "total_tokens": 22
  }
}
```

## Verify Health

```bash
# Gateway health
curl http://localhost:30000/health

# Worker status
curl http://localhost:30000/workers
```

## Deploy with Docker

For local deployment, run the gateway image and point it at your worker. Container arguments go to the image's entrypoint, `python3 -m smg.launch_router`, so they are the same flags as `smg launch` from pip:

```bash
docker run -d \
  --name smg \
  -p 30000:30000 \
  -p 29000:29000 \
  --add-host=host.docker.internal:host-gateway \
  lightseekorg/smg:latest \
  --worker-urls http://host.docker.internal:8000 \
  --policy cache_aware
```

The gateway listens on `0.0.0.0:30000` and serves Prometheus metrics on `0.0.0.0:29000` by default. `--add-host` makes `host.docker.internal` resolve to the Docker host on Linux; Docker Desktop provides that name already. The container runs as a non-root user (UID 65532), so files you mount into it must be readable by that user. `ghcr.io/smg-project/smg:latest` is the same image.

Verify:

```bash
docker ps | grep smg
curl http://localhost:30000/health
```

### All-in-one with engine images

Engine images use `smg` as their entrypoint, so pass `serve` to start the worker and the gateway in one container. Set `--host 0.0.0.0`: `smg serve` binds the gateway to `127.0.0.1` by default, which a published port cannot reach. Replace `<version>` with an SMG release (see the Docker tab under [Install](#install) for the engine tags each release builds).

=== "SGLang"

    ```bash
    docker run -d --gpus all \
      --name smg \
      -p 30000:30000 \
      -v /path/to/models:/models \
      ghcr.io/smg-project/smg:<version>-sglang-v0.5.20 \
      serve \
      --backend sglang \
      --model-path /models/meta-llama/Llama-3.1-8B-Instruct \
      --host 0.0.0.0 \
      --port 30000
    ```

=== "vLLM"

    ```bash
    docker run -d --gpus all \
      --name smg \
      -p 30000:30000 \
      -v /path/to/models:/models \
      ghcr.io/smg-project/smg:<version>-vllm-v0.27.1 \
      serve \
      --backend vllm \
      --model /models/meta-llama/Llama-3.1-8B-Instruct \
      --host 0.0.0.0 \
      --port 30000
    ```

Each engine image sets `SMG_DEFAULT_BACKEND` to its engine, so `--backend` is optional. Workers connect over gRPC by default. TensorRT-LLM images take `--backend trtllm` and support only gRPC; TokenSpeed images need `--connection-mode zmq` (see [ZMQ Workers](zmq-workers.md)).

Verify:

```bash
curl http://localhost:30000/health
curl http://localhost:30000/v1/models
```

## Deploy to Kubernetes (Quick Start)

Run SMG in the cluster with the Helm chart, or with your own manifests.

### Helm

The chart is published to GHCR as an OCI artifact, versioned with SMG releases (1.9.0 and later):

```bash
helm install smg oci://ghcr.io/smg-project/charts/smg \
  --version 1.11.0 \
  --namespace inference --create-namespace \
  -f values.yaml
```

A minimal `values.yaml` either lists workers or discovers them:

=== "Static workers"

    ```yaml
    router:
      policy: cache_aware
      workerUrls:
        - http://worker-1.inference.svc:8000
        - http://worker-2.inference.svc:8000
    ```

    For gRPC workers, use `grpc://` URLs and set `router.model` to the model's Hugging Face ID or path so the gateway can load the tokenizer.

=== "Service discovery"

    ```yaml
    router:
      policy: cache_aware
      serviceDiscovery:
        enabled: true
        selector: "app=sglang-worker"
        port: 8000
    ```

    The gateway watches pods with this label in the release namespace. Set `router.serviceDiscovery.namespace` to watch another namespace, or `router.serviceDiscovery.clusterWide: true` to watch all of them.

The chart creates these resources (names assume the release is called `smg`):

| Resource | When |
|----------|------|
| `Deployment` `smg-router` running `docker.io/lightseekorg/smg:<chart appVersion>` | Always; a `StatefulSet` plus a headless `Service` when `router.mesh.enabled` |
| `Service` `smg-router` (`ClusterIP`): port 80 to the gateway's 30000, and 29000 for metrics | Always |
| `ServiceAccount` `smg` | `serviceAccount.create` (default `true`) |
| `Role` and `RoleBinding` allowing `get`, `list`, and `watch` on pods | `router.serviceDiscovery.enabled`, `router.mesh.enabled`, or `rbac.create`; a `ClusterRole` and `ClusterRoleBinding` with `router.serviceDiscovery.clusterWide` |
| `ServiceMonitor` | `router.metrics.serviceMonitor.enabled` (needs the Prometheus Operator CRDs) |
| `Ingress`, `HorizontalPodAutoscaler`, `PodDisruptionBudget`, Grafana dashboard `ConfigMap` | `router.ingress.enabled`, `router.autoscaling.enabled`, `router.podDisruptionBudget.enabled`, `grafana.dashboard.enabled` |
| `Deployment` and `Service` for an engine worker | One per `workers[]` entry |

A few chart behaviors to know:

- The router container runs the gateway image, so it accepts the same flags as `smg launch` from pip. `router.extraArgs` appends flags the values file does not cover.
- The chart's schema limits `router.policy` to `cache_aware`, `round_robin`, `power_of_two`, `manual`, `random`, and `prefix_hash`.
- `global.image.tag` overrides the gateway version, which defaults to the chart's `appVersion`.
- Engine images for `workers[]` resolve to `ghcr.io/<repository>:<tag>`, and the repository defaults to `global.image.repository` (`lightseekorg/smg`). GHCR has no engine release images under that name after 1.9.0, so set `image.repository: smg-project/smg` next to each worker's `image.tag`. The chart's worker examples still pin 1.3.3 images.

!!! warning "History backends in chart 1.11.0"
    With `history.backend` set to `postgres`, `redis`, or `oracle`, chart 1.11.0 passes flags the gateway image rejects (`--postgres-pool-max-size`, `--redis-pool-max-size`, `--oracle-dsn`), and the router exits at startup. Leave `history.backend` at `memory` and pass the history flags through `router.extraArgs` instead, for example `--history-backend postgres --postgres-db-url <url>`.

Verify:

```bash
helm test smg -n inference
kubectl -n inference port-forward svc/smg-router 30000:80 &
curl http://localhost:30000/workers
```

The [chart README](https://github.com/smg-project/smg/blob/main/deploy/helm/smg/README.md), [`values.yaml`](https://github.com/smg-project/smg/blob/main/deploy/helm/smg/values.yaml), and [examples](https://github.com/smg-project/smg/tree/main/deploy/helm/smg/examples) cover engine workers, multiple models, mesh HA, monitoring, and ingress.

### Without Helm

Start SMG with service discovery:

```bash
smg launch \
  --service-discovery \
  --selector app=sglang-worker \
  --service-discovery-namespace inference \
  --service-discovery-port 8000 \
  --policy cache_aware
```

In a pod, pass the same flags as container `args` to the gateway image. SMG needs permission to watch pods in the namespace:

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

Bind the `Role` to the gateway pod's service account with a `RoleBinding`; [Service Discovery](service-discovery.md) has the full manifests. Without `--service-discovery-namespace`, SMG watches pods in all namespaces and needs a `ClusterRole` instead.

Verify:

```bash
kubectl get pods -n inference -l app=sglang-worker
curl http://localhost:30000/workers
```

## Navigate by Category

### Core Setup

- [Multiple Workers](multiple-workers.md) — connect local or external worker endpoints
- [gRPC Workers](grpc-workers.md) — gateway-side tokenization, parsing, and tool handling
- [ZMQ Direct Workers](zmq-workers.md) — same-host engines with no engine API server in the path
- [PD Disaggregation](pd-disaggregation.md) — split prefill and decode paths
- [Service Discovery](service-discovery.md) — Kubernetes pod-based worker registration

### Operations

- [Monitoring](monitoring.md) — Prometheus metrics, tracing, and alerts
- [Logging](logging.md) — structured logs and aggregation patterns
- [TLS](tls.md) — HTTPS gateway configuration
- [Control Plane Auth](control-plane-auth.md) — secure worker/tokenizer/WASM management endpoints

### Reliability and Data

- [Reliability Controls](reliability-controls.md) — concurrency limits, retries, and circuit breakers
- [Data Connections](data-connections.md) — history backend setup for PostgreSQL, Redis, and Oracle
- [Tokenization and Parsing APIs](tokenization-and-parsing.md) — tokenize, detokenize, and parser endpoints

### Advanced Features

- [Load Balancing](load-balancing.md) — policy selection and tuning
- [KV Events Cache-Aware Routing](kv-events-cache-aware.md) — prefix routing driven by engine KV cache events
- [Tokenizer Caching](tokenizer-caching.md) — L0/L1 cache setup for gRPC mode
- [MCP in Responses API](mcp.md) — configure and execute MCP tools through `/v1/responses`
- [External Providers](external-providers.md) — route to OpenAI, Anthropic, Gemini, and xAI backends
- [RL Control Plane](rl-control-plane.md) — pause, refit, and resume inference engines from an RL training loop

## Troubleshooting

??? question "Gateway starts but can't connect to worker"

    **Symptoms:** Gateway logs show connection errors.

    **Solutions:**

    1. Verify the worker is running: `curl http://localhost:8000/health`
    2. Check network connectivity between gateway and worker
    3. If using Docker, ensure proper network configuration (`--network host` or Docker network)

??? question "Request times out"

    **Symptoms:** Requests hang or return 504 errors.

    **Solutions:**

    1. Check worker health: `curl http://localhost:30000/workers`
    2. Increase timeout: `--request-timeout-secs 120`
    3. Check worker logs for errors

??? question "Model not found error"

    **Symptoms:** `model not found` in response.

    **Solutions:**

    1. The `model` field in requests should match the model loaded on the worker
    2. Check available models: `curl http://localhost:30000/v1/models`
