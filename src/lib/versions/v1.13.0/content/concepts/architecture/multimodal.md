---
title: Multimodal Pipeline
---

# Multimodal Pipeline

With gRPC workers, SMG runs the multimodal front end for the engine. It fetches each image or video, preprocesses it the way the model's reference processor does, expands the prompt's placeholder tokens to the right length, and sends the encoder tensors to the worker. vLLM gRPC workers can instead receive the media URLs and process them on the engine.

!!! note "HTTP workers"
    With HTTP workers, SMG forwards content parts as they are, and the engine's own OpenAI-compatible server fetches and processes the media. This page covers gRPC workers and, where noted, the direct ZMQ backends.

---

## Overview

<div class="grid" markdown>

<div class="card" markdown>

### :material-image-multiple: Router-Side Preprocessing

The default. SMG fetches, decodes, and preprocesses media in Rust, following each model's reference processor, and sends ready-to-encode tensors.

</div>

<div class="card" markdown>

### :material-server-network: Worker-Side Processing

vLLM gRPC workers that advertise a media processor receive media URLs instead and run vLLM's own processor (in process or in a Redis sidecar) or, on the Rust servicer, SMG's own pipeline.

</div>

<div class="card" markdown>

### :material-filmstrip: Reference-Rate Video

Clips are sampled the way the model's reference processor does — at its reference frame rate, or as its engine's default loader picks frames — decoded with ffmpeg or OpenCV, and preprocessed in parallel.

</div>

<div class="card" markdown>

### :material-swap-horizontal: Transport Options

Tensors travel inline in the gRPC message, through same-host shared memory, or over an RDMA pixel lane to TokenSpeed workers.

</div>

</div>

---

## Request Flow

SMG accepts media on these APIs:

| API | Media input |
|-----|-------------|
| Chat Completions (`/v1/chat/completions`) | `image_url`, `video_url`, `audio_url`, and `input_audio` content parts in any non-assistant message |
| Messages (`/v1/messages`) | `image` blocks with a `base64` or `url` source, in user messages and inside tool results |
| Responses (`/v1/responses`) | `input_image` content parts that carry an `image_url` |

Media URLs can be `http://`, `https://`, or base64 `data:` URLs.

On the gRPC path, a media request goes through these steps:

1. **Plan and validate.** SMG collects the media parts in prompt order and picks the model's processor from the model name and its `config.json` `model_type`. A modality the model does not take, or more items than the effective per-request limit (see [Media Counts](#media-counts)), is rejected before anything is fetched. A model with no processor is rejected only where SMG would preprocess the media itself; on the worker path its media is forwarded for the worker's engine to process (see [Where Media Is Processed](#where-media-is-processed)).
2. **Render and check anchors.** The chat template renders one placeholder anchor per item (for example `<|image_pad|>` for Qwen3-VL). After tokenizing, SMG checks that the prompt holds exactly one anchor per item.
3. **Choose where to process.** `--mm-processing` decides between the router and a vLLM worker (see [Where Media Is Processed](#where-media-is-processed)).
4. **Fetch and decode.** Items are fetched concurrently. Images are decoded and videos are sampled into frames.
5. **Preprocess and expand.** The model's processor turns each item into encoder tensors and a feature-token count. SMG replaces each anchor with the model's full placeholder run, including any per-frame markers and timestamps.
6. **Select a worker.** The request is refused if the selected engine does not accept one of its modalities.
7. **Assemble and send.** The tensors are serialized for that engine and sent inline, through shared memory, or over RDMA. With `--multimodal-max-inflight-bytes` set, the request first reserves room for its media.

The processor configuration (`config.json`, `preprocessor_config.json`, and, when present, `processor_config.json` and `video_preprocessor_config.json`) comes from the tokenizer bundle the gRPC worker serves, or from the tokenizer source (a local directory or a Hugging Face download).

---

## Where Media Is Processed

`--mm-processing` decides, per request, whether SMG preprocesses the media or forwards media references to a vLLM gRPC worker that processes them itself. Only vLLM gRPC workers process references; every other engine receives router-preprocessed media.

| Mode | Behavior |
|------|----------|
| `auto` (default) | Forward references when the request is forwardable, the model supports worker-side expansion, and every registered worker of the model is a vLLM gRPC worker that advertises a media processor. Otherwise preprocess on the router. |
| `router` | Always preprocess on the router. |
| `worker` | Forward references. A request whose parts carry per-item hints is preprocessed on the router instead when SMG's own pipeline can serve it, and refused with 400 when it cannot; any other request that cannot be forwarded is refused with 400. When no worker of the model advertises a media processor, SMG returns 503 `no_media_ref_capable_worker`. |

A request is forwardable when every media part is an `image_url` or `video_url` without per-item hints (`fps`, `max_long_side_pixel`), or audio of a model whose audio anchor a worker expands itself (Gemma 4, or a model SMG has no processor for) — an `audio_url`, or an `input_audio` part, which SMG forwards as a base64 `data:` audio URL. Embeddings have no reference form. The flag falls back to `SMG_MM_PROCESSING` (deprecated; env support ends in the next minor release), then `auto`. An unreadable `SMG_MM_PROCESSING` stops the gateway at startup.

In `auto`, SMG consults every registered worker of the model, healthy or not, so a health flap does not move a model between modes. Each decision is counted in `smg_mm_processing_total{model,mode,reason}`, with reason `config`, `hint_fallback` (a hinted request preprocessed on the router in `worker` mode), `plan_not_forwardable`, `model_not_opted_in`, `auto_none` (no registered workers), `auto_uniform`, or `auto_mixed`.

On the worker path:

- The request carries the unexpanded prompt (one anchor per item) and the media URLs in prompt order. For a model SMG has no processor for, the prompt's anchors are not checked: the worker's engine owns the placeholders, their expansion, and the per-prompt limits, as on its own server.
- An inline `data:` URL larger than `SMG_IMAGE_MAX_INPUT_BYTES`, `SMG_VIDEO_MAX_INPUT_BYTES`, or `SMG_AUDIO_MAX_INPUT_BYTES` is refused with `media_ref_too_large`.
- Every URL scheme must be one the selected worker advertises in its `mm_media_ref_schemes` label: `http`, `https`, and `data`, plus `file` when vLLM runs with `--allowed-local-media-path` (never with the `smg` processor).
- Direct ZMQ workers and EPD encode workers never take references.

!!! warning "Routing sees unexpanded prompts on the worker path"
    When media is forwarded, SMG never expands the placeholders, so routing that weighs prompt length (cache-aware policies, load estimates) counts one token per media item while the worker schedules the full placeholder run.

### Worker-Side Processing on vLLM

A vLLM gRPC worker processes references when its servicer runs a media processor:

```bash
SMG_VLLM_MM_PROCESSOR=inprocess \
  vllm serve Qwen/Qwen3-VL-8B-Instruct --grpc --allowed-media-domains example.com
```

The servicer reads each setting below from its `--mm-*` flag, which both gRPC launchers (`vllm serve --grpc` and `python -m vllm.entrypoints.grpc_server`) accept, and otherwise from the matching `SMG_VLLM_MM_*` variable. A variable standing in for an available flag logs a deprecation warning; env support is due to end in the next minor release.

| Servicer flag | Env fallback | Default | Description |
|---------------|--------------|---------|-------------|
| `--mm-processor` | `SMG_VLLM_MM_PROCESSOR` | `off` | `off`, `inprocess` (fetch and process inside the vLLM process), `redis` (hand jobs to a sidecar), or `smg` (SMG's own pipeline, [Rust servicer](../../getting-started/grpc-workers.md#rust-vllm-servicer) only) |
| `--mm-max-inflight` | `SMG_VLLM_MM_MAX_INFLIGHT` | `64` | Multimodal jobs the worker runs at once, whichever processor runs; once as many are waiting, further requests are shed with a retryable error |
| `--mm-max-items` | `SMG_VLLM_MM_MAX_ITEMS` | unset | Overrides the per-modality item limits the worker takes from vLLM's `--limit-mm-per-prompt` |
| `--mm-max-item-bytes` | `SMG_VLLM_MM_MAX_ITEM_BYTES` | 32 MiB | Cap on one inline `data:` payload |
| `--mm-redis-url` | `SMG_VLLM_MM_REDIS_URL` | `redis://127.0.0.1:6379/0` | Sidecar Redis (`redis` mode) |
| `--mm-sidecar-timeout-ms` | `SMG_VLLM_MM_SIDECAR_TIMEOUT_MS` | `30000` | How long the worker waits for a sidecar result |
| `--mm-sidecar-max-queue` | `SMG_VLLM_MM_SIDECAR_MAX_QUEUE` | `256` | Fail fast once this many jobs are queued for the sidecar |
| `--mm-sidecar-namespace` | `SMG_VLLM_MM_SIDECAR_NAMESPACE` | derived | Overrides the Redis key namespace |

`SMG_VLLM_MM_MAX_VIDEO_FRAMES` (env only; default `0`, which leaves it to vLLM's `--media-io-kwargs`) caps the frames a video is sampled to.

The worker advertises its processor in the `mm_processor`, `mm_processor_source`, and `mm_media_ref_schemes` labels, which appear in `GET /workers`. An engine started with `--language-model-only` never advertises one. With `inprocess` and `redis`, vLLM's own `--allowed-media-domains`, `--allowed-local-media-path`, `--media-io-kwargs`, `--limit-mm-per-prompt`, and `VLLM_*_FETCH_TIMEOUT` govern fetching on the worker; without `--allowed-media-domains` the worker fetches from any host. The `smg` processor fetches with SMG's own connector instead (see [SMG's Pipeline on the Rust Servicer](#smgs-pipeline-on-the-rust-servicer)).

Failures keep their cause. The caller's own mistakes (a bad URL, a disallowed host, an oversized payload) come back as 400. Transient failures (a fetch timeout, a refused connection, an origin 5xx, a saturated worker, a sidecar that is down or overloaded) come back as retryable 503s. A sidecar timeout is not retried, because the worker already spent the whole budget on that input.

### Redis Media-Processing Sidecar

The sidecar moves fetching and processing out of the vLLM process. It is a GPU-free process that runs next to the worker with a private Redis: it pops jobs, fetches with vLLM's `MediaConnector`, runs vLLM's multimodal processor over the unexpanded prompt, and pushes full tensors back.

```bash
pip install "smg-grpc-servicer[vllm,vllm-redis]"

python -m smg_grpc_servicer.vllm.mm_sidecar --model Qwen/Qwen3-VL-8B-Instruct \
  --redis-url redis://127.0.0.1:6379/0 --allowed-media-domains example.com

SMG_VLLM_MM_PROCESSOR=redis SMG_VLLM_MM_REDIS_URL=redis://127.0.0.1:6379/0 \
  vllm serve Qwen/Qwen3-VL-8B-Instruct --grpc
```

- The sidecar takes `--redis-url` (falls back to `SMG_VLLM_MM_REDIS_URL`, then localhost), `--namespace` (falls back to `SMG_VLLM_MM_SIDECAR_NAMESPACE`), `--concurrency` (default `2`), and vLLM's engine flags. It has no timeout flag: the worker's `--mm-sidecar-timeout-ms` travels with each job as its deadline.
- The worker and the sidecar must agree on the model, vLLM version, dtype, video backend, media and processor kwargs, and `--limit-mm-per-prompt` (pass it to both; the sidecar's limit is the one that applies). The worker advertises `mm_processor=redis` only while a sidecar with a matching fingerprint keeps its `hello` key alive (refreshed every 5 seconds, 15-second TTL). SMG reads the label when it registers the worker, so start the sidecar first: a worker registered while no sidecar was up keeps the model on the router path in `auto` until that worker is registered again.
- Jobs and results travel over Redis lists under `smg:mm:v1:{namespace}`, and results expire after 120 seconds. An encoded result at or above `SMG_VLLM_MM_MAX_RESULT_BYTES` (default 512 MiB, lowered to Redis's `proto-max-bulk-len` when that is smaller) is not pushed; the request fails with a 400 whose message includes `media_too_large`.

### SMG's Pipeline on the Rust Servicer

`--mm-processor smg` runs SMG's own media pipeline — the same fetch, decode, preprocess, and placeholder expansion the router runs for `--mm-processing router` — inside the worker, with no Python on the request path. The resulting batches reach the engine as a router-preprocessed request's would. The mode exists only in the [Rust vLLM servicer](../../getting-started/grpc-workers.md#rust-vllm-servicer); the Python servicer refuses the value at startup with an error pointing to `--servicer-impl rust` / `SMG_VLLM_SERVICER_IMPL=rust`.

```bash
SMG_VLLM_SERVICER_IMPL=rust SMG_VLLM_MM_PROCESSOR=smg \
  vllm serve Qwen/Qwen3-VL-8B-Instruct --grpc
```

- The worker serves the model families SMG's pipeline supports (see [Supported Models](#supported-models)); a multimodal model outside them is refused at startup, while a text-only model ignores the setting as it does the other processors. The router still decides per model whether to forward references at all (see [Where Media Is Processed](#where-media-is-processed)): `smg` changes what runs once references arrive, not which models they are forwarded for.
- The pipeline reads the model's `config.json` and preprocessor configs from the model directory, or from the tokenizer directory the launcher resolved. The engine's `mm_processor_kwargs` are applied as overrides of the preprocessor config; a key the config has no field for fails the launch rather than being ignored.
- The worker advertises `mm_processor=smg` and `mm_media_ref_schemes=http,https,data`. vLLM's `--allowed-media-domains` and `--allowed-local-media-path` do not apply, and `file` URLs are never accepted. The pipeline fetches under SMG's own address policy: a URL on a private, loopback or link-local address is refused unless the worker's environment sets `SMG_MM_ALLOW_PRIVATE_MEDIA_HOSTS`, any other host is allowed, and a fetch gets 10 seconds (30 seconds for a video).
- `--mm-max-inflight` and `--mm-max-item-bytes` apply as above. `--mm-max-items` replaces the model's built-in per-request limits but never rises above vLLM's `--limit-mm-per-prompt`, which the pipeline enforces as the engine's own server would. Video is sampled to the engine's own frame budget (`--media-io-kwargs` `video.num_frames`, capped by `SMG_VLLM_MM_MAX_VIDEO_FRAMES`) for the families that sample the way the engine's loader does; an engine video rule the pipeline cannot follow (`--media-io-kwargs video.fps`, a non-default `VLLM_VIDEO_LOADER_BACKEND`) fails the launch for those families. The Redis sidecar settings are not used.
- An engine that normalizes pixels on device (vLLM's `mm_device_do_normalize`, on by default for the Qwen-VL family) takes raw `uint8` pixels, and the pipeline writes those for it. A model whose processor produces only normalized floats is refused at startup under that setting: start the engine with `--mm-device-do-normalize=false` or use `inprocess`. Otherwise pixels are written in the engine's own dtype.
- The in-flight cap, the saturation refusal, and the PD prefill leg's `media_identity` behave as with the Python backends.

---

## Supported Models

SMG picks a model's processor from the model name and its `config.json` `model_type`. These families have vision support, with their built-in per-request limits:

| Model family | Images | Video | Worker-side expansion on vLLM |
|--------------|--------|-------|-------------------------------|
| Qwen3-VL, Qwen3.5, Qwen3.6, Qwen4-Exp (`qwen4_exp`) | 10 | 1 clip ¹ | Images and video |
| Qwen2-VL, Qwen2.5-VL | 10 | — | — |
| Qwen3-Omni | 10 | 1 clip | — |
| MiniMax-M3 | 200 | 20 clips ¹ | Images and video ² |
| Gemma 4 | 128 | 8 clips ¹ | Images, video, and audio ⁴ |
| GLM-5.3-Flash | 64 | 2 clips ¹ | Images |
| Nemotron-H Omni ⁵ | 16 | 2 clips | — |
| Kimi-K3 | 10 | — | — ³ |
| Kimi-K2.5 | 10 | — | — |
| DeepSeek-V4.1 | 128 | — | — |
| Llama 4 | 8 | — | Images |
| LLaVA 1.5, LLaVA-NeXT | 4 | — | — |
| Phi-3 and Phi-3.5 vision | 4 | — | — |
| Inkling | 10 (when the checkpoint enables its vision tower) | — | — |

¹ When the checkpoint declares the tokens its video layout needs.

² vLLM sizes some MiniMax-M3 images differently from SMG, so answers on the two paths can differ.

³ Kimi-K3 media stay on the router path: vLLM's Kimi-K3 processor expands a different anchor than the one SMG renders.

⁴ Gemma 4 audio is worker-path only: checkpoints that ship the audio tower (an `audio_config` plus `audio_token_id`) take `audio_url` references on a vLLM worker whose own processor fetches, expands, and encodes them. SMG itself does not preprocess Gemma 4 audio, and the worker enforces its own per-request count.

⁵ Nemotron-H Omni media serve on TokenSpeed gRPC workers only: the tensors are packed in a layout the other engines do not read. Audio takes 4 clips per request on checkpoints that ship the audio tower.

Audio (`audio_url`, `input_audio`) is accepted for Qwen3-Omni, Qwen3-ASR, Inkling, and Nemotron-H Omni, on TokenSpeed workers only, and for Gemma 4 on the worker path (⁴ above).

### Engine Support

| gRPC engine | Images | Video | Audio | What SMG sends |
|-------------|--------|-------|-------|----------------|
| vLLM | :material-check: | :material-check: | :material-close: | Preprocessed tensors with per-item content hashes, or media references on the worker path |
| TokenSpeed | :material-check: | :material-check: | :material-check: | Preprocessed tensors, one set per item |
| SGLang | :material-check: | :material-close: | :material-close: | Preprocessed float32 pixel tensors plus the original image bytes |
| TensorRT-LLM | :material-check: | :material-close: | :material-close: | The original image bytes |
| MLX | :material-close: | :material-close: | :material-close: | — |

vLLM and TokenSpeed take several modalities in one request, such as images and video together. A modality the selected engine does not take is refused with 400 `multimodal_not_supported`.

---

## Video

### Decoding

SMG decodes video on the gateway. The default build runs `ffprobe` and `ffmpeg`, which must be on the gateway's `PATH`; without them a video request fails with `ffmpeg executable not found`. Builds with the `opencv-video` feature decode with OpenCV first and fall back to ffmpeg. The SMG container image is built with `opencv-video` and ships ffmpeg; the PyPI wheels are built without `opencv-video`.

| Setting | Default | Description |
|---------|---------|-------------|
| `SMG_VIDEO_DECODE_BACKEND` | `auto` | `auto`, `opencv` (needs `opencv-video`), or `ffmpeg` |
| `SMG_VIDEO_PROCESS_TIMEOUT_SECS` | `30` | Time limit for each `ffprobe` or `ffmpeg` run and each OpenCV decode |
| `SMG_VIDEO_MAX_DECODED_BYTES` | 1 GiB | Cap on a clip's decoded RGB frames |

Concurrent decodes share one CPU budget: each ffmpeg or OpenCV decode gets fewer threads as more run at once.

### Frame Sampling

| | Default | Per-request override |
|---|---------|----------------------|
| Frame rate | The model's reference rate: 1 fps for MiniMax-M3, 2 fps for other rate-sampled models. Gemma 4, GLM-5.3-Flash, and Nemotron-H Omni do not sample by rate (see frame count). | `video_url.fps`, from 0.2 to 5.0 |
| Frame count | Clip duration times the frame rate, kept within 4 to 768 frames. Gemma 4, GLM-5.3-Flash, and Nemotron-H Omni instead take every frame of a clip with at most 32, else 32 spread evenly, as their engines' default loaders sample. | — |
| Frame placement | Spread evenly from the first frame to the last; the Qwen3-VL family picks the nearest frame of an even spread, as its reference does. MiniMax-M3 takes one frame per interval from the start and always keeps the last frame, as its reference does. | — |
| Frame size | Set by the model's processor | `video_url.max_long_side_pixel`: a multiple of 28 from 150 to 3584, applied to the decoded frames |

`fps` and `max_long_side_pixel` are MiniMax-M3 extensions to the OpenAI content part, and SMG applies them for any model. `image_url.max_long_side_pixel` (a positive multiple of 28) caps an image's long side the same way. A part with any of these hints is never forwarded to a worker: under `--mm-processing worker`, a hinted request is preprocessed on the router when SMG's pipeline can serve it.

```json
{"type": "video_url", "video_url": {"url": "https://example.com/clip.mp4", "fps": 1.0}}
```

Models whose reference processor lays video out frame by frame get the same layout from SMG: Qwen3-VL, GLM-5.3-Flash, MiniMax-M3, Gemma 4, and Nemotron-H Omni prompts carry one block per temporal frame or frame group, stamped with its time. For MiniMax-M3, SMG also applies the pixel budget per frame.

Several clips in one request are preprocessed in parallel, and on vLLM gRPC workers a request can carry images and video together; over the direct ZMQ backend, vLLM takes one modality per request (see [Direct ZMQ Backends](#direct-zmq-backends)).

---

## Limits and Safety

### Media Counts

Each model has built-in per-request limits (see [Supported Models](#supported-models)). To change them:

| Setting | Scope | Description |
|---------|-------|-------------|
| `--mm-per-request-image-limit` | Images, every model | Replaces each model's image limit (at least 1). Takes precedence over `SMG_IMAGE_MAX_COUNT`. |
| `SMG_IMAGE_MAX_COUNT`, `SMG_VIDEO_MAX_COUNT`, `SMG_AUDIO_MAX_COUNT` | One modality, every model | Environment overrides that raise or lower the limit; unset, zero, or non-numeric values are ignored. |

Neither setting enables a modality the model does not support, and neither rises above the smallest per-request limit the model's workers advertise for their engine (the `mm_item_limits` label, from vLLM's `--limit-mm-per-prompt` or SGLang's `--limit-mm-data-per-request`): the engine sizes its encoder budget by its own limit, so SMG refuses what the engine's server would refuse, before any fetch and whichever worker is later picked. The refusal names the limit and what set it. On the worker path, the vLLM worker also enforces its own item limits.

### Sizes and Timeouts

| Limit | Default | Setting |
|-------|---------|---------|
| Encoded image | 256 MiB | `SMG_IMAGE_MAX_INPUT_BYTES` |
| Encoded video | 256 MiB | `SMG_VIDEO_MAX_INPUT_BYTES` |
| Encoded audio | 256 MiB | `SMG_AUDIO_MAX_INPUT_BYTES` |
| Decoded image | 512 MiB | Fixed; the libjpeg-turbo path checks it before allocating |
| Decoded video frames | 1 GiB | `SMG_VIDEO_MAX_DECODED_BYTES` |
| Inkling image patches per request | 32,768 | `in_patch_limit` in the preprocessor config |
| Media fetch | 10 seconds per URL; 30 seconds for a video | Fixed |

!!! warning "Media fetch policy"
    When SMG preprocesses media, a URL that names or resolves to a loopback, link-local, private or otherwise non-public address is refused before anything is connected; `--mm-allow-private-media-hosts` (or `SMG_MM_ALLOW_PRIVATE_MEDIA_HOSTS`) opts a deployment with internal media origins back in. `--mm-allowed-media-domains` restricts fetches to named hosts, enforced on every redirect hop; without it, SMG fetches from any host the address policy passes. On the worker path, vLLM's `--allowed-media-domains` applies with the `inprocess` and `redis` processors (without it, the worker fetches from any host the router forwards); a worker running `--mm-processor smg` takes no domain allowlist but applies the same address policy as the router, reading `SMG_MM_ALLOW_PRIVATE_MEDIA_HOSTS` from its own environment.

### In-Flight Media Budget

`--multimodal-max-inflight-bytes` caps the bytes of preprocessed media SMG holds for engines at once. A request that fits waits up to 2 seconds for room and then gets 429 `multimodal_inflight_budget` with a `Retry-After` header. A request larger than the whole budget gets 413 `multimodal_payload_too_large` right away. A waiting request still holds its media, and the waiting queue is capped at one budget (a request that would overfill it gets 429 right away), so size memory for about twice the value. Unset leaves the budget unbounded. The budget is counted in whole KiB, rounded down: a set value under 1024 bytes (zero included) is refused at startup rather than read as unset.

### Errors

Multimodal errors use the standard error body (`{"error": {"type": ..., "code": ..., "message": ...}}`), and the code is also sent in the `X-SMG-Error-Code` header.

| Status | Code | Cause |
|--------|------|-------|
| 400 | `unsupported_content_part` | A content part type the gRPC path does not know |
| 400 | `invalid_multimodal_request` | The model does not take the modality, or the request has too many items (for example `model spec qwen3_vl supports at most 10 image inputs; got 11`); also a model with no multimodal processor, where SMG would preprocess the media itself |
| 400 | `multimodal_prompt_contract_mismatch` | The rendered prompt does not hold exactly one anchor per media item: a chat template that drops media, or a literal anchor token in user text |
| 400 | `multimodal_processing_failed` | Fetching, decoding, or preprocessing failed: an HTTP error or timeout, a media origin the fetch policy refuses, an oversized payload, a malformed data URL, an invalid `fps` or `max_long_side_pixel`, or no ffmpeg on the gateway |
| 400 | `multimodal_not_supported` | The selected engine does not take the modality; media references cannot reach the worker (a part with no reference form such as embeddings, an EPD encode leg, a selected leg without a media processor); or the model has no processor and its media could not go to a worker that processes media |
| 400 | `multimodal_worker_processing_unsupported_model` | `--mm-processing worker`, and vLLM cannot expand the model's anchor |
| 400 | `multimodal_hint_unsupported_in_worker_mode` | `--mm-processing worker`, and a part carries `fps` or `max_long_side_pixel` that SMG cannot apply itself (no processor for the model, or a modality only the worker processes) |
| 400 | `media_ref_too_large` | Worker path: an inline `data:` payload is above the byte cap |
| 400 | `media_ref_scheme_not_accepted` | Worker path: the selected worker does not fetch that URL scheme |
| 413 | `multimodal_payload_too_large` | The request's media exceed the whole in-flight budget |
| 429 | `multimodal_inflight_budget` | The in-flight budget stayed full for 2 seconds, or the queue waiting for it was full |
| 503 | `no_media_ref_capable_worker` | `--mm-processing worker`, and no worker of the model advertises a media processor |

Prefill-decode deployments add three codes, listed under [Prefill-Decode Disaggregation](#prefill-decode-disaggregation).

---

## Performance

- **Fetch once.** Parts of one request whose payload is identical byte for byte — the same URL, inline `data:` URL, bytes, or file path — with the same settings share one fetch and decode, so an image repeated as several identical data URLs is fetched and decoded once. Duplicates are matched by comparing payloads, looked up by their kind and length and falling back to digests once several distinct payloads share one length.
- **Data URL fast path.** `data:image/...` URLs go straight to the base64 decoder without a full URL parse, which matters for large inline images.
- **Parallel preprocessing.** Modality batches are preprocessed concurrently. Within a batch, processors built on the Qwen-VL pipeline (Qwen2-VL through Qwen3-VL, Qwen3-Omni, MiniMax-M3) split the images across cores, and video clips run in parallel. A batch's still-encoded images are decoded in parallel on the preprocessing pool before preprocessing starts, a repeated image only once.
- **Pixel cache.** `--mm-pixel-cache-mb` gives SMG a host-memory LRU cache of preprocessed images, keyed by the image's content hash and a fingerprint of the model and its preprocessing config. It serves single-image requests and, for processors that preprocess each image on its own (the Qwen-VL families, Gemma 4, Nemotron-H Omni), requests with fewer than 32 images, where each image is cached on its own and a repeated image is preprocessed once. The cache is off by default.
- **Image decoding.** When `libturbojpeg` is installed on the gateway host, JPEGs are decoded with libjpeg-turbo using Pillow's defaults, so the pixels match what vLLM computes. Without it, SMG uses a pure-Rust decoder that can differ by a few levels per pixel. WebP files decode through libwebp when it is present. A transparent image decodes under the destination engine's own transparency rule; for the vLLM family the alpha channel is composited over white.

---

## Tensor Transport

`--multimodal-tensor-transport` chooses how preprocessed tensors reach the worker:

| Mode | Behavior |
|------|----------|
| `inline` (default) | Tensor bytes travel in the gRPC message. |
| `shm` | Tensors of at least `--multimodal-shm-min-bytes` (default 64 KiB) go through `/dev/shm` whenever SMG can write it. You assert that the worker shares it. |
| `auto` | Like `shm`, but only when the worker is verified to share SMG's `/dev/shm`: its `shm_namespace_id` label (`<boot_id>:<st_dev of /dev/shm>`) must equal SMG's own. |
| `rdma` | Pixel tensors for TokenSpeed workers travel over the NIXL RDMA pixel lane. Other tensors, and other engines, stay inline. |

A failed shared-memory write falls back to inline and is counted in `smg_mm_shm_write_failures_total`. The two flags fall back to `SMG_MM_TENSOR_TRANSPORT` and `SMG_MM_SHM_MIN_BYTES` (legacy names `SMG_TOKENSPEED_MM_TENSOR_TRANSPORT` and `SMG_TOKENSPEED_MM_SHM_MIN_BYTES`). The transport is chosen gateway-wide. The worker spec's `multimodal_tensor_transport` and `multimodal_shm_min_bytes` fields override both per worker, consulted for the worker that runs the vision encoder (the encode worker in EPD, otherwise the single or prefill worker) — for example, `shm` for one co-located worker and `inline` for a remote one (see the [Admin API](../../reference/api/admin.md#worker-spec)). The RDMA lane is on or off for the whole gateway.

| Engine | `inline` | `shm` and `auto` | `rdma` |
|--------|----------|------------------|--------|
| vLLM (gRPC) | :material-check: | :material-check: | Falls back to inline |
| TokenSpeed (gRPC, including EPD encode workers) | :material-check: | :material-check: | :material-check: (with an `mm-rdma` build) |
| SGLang, TensorRT-LLM (gRPC) | :material-check: | Inline only | Inline only |
| vLLM, TokenSpeed (direct ZMQ) | :material-check: | Inline only | Inline only |

SGLang workers reached over the direct ZMQ backend take no media on any transport mode (see [Direct ZMQ Backends](#direct-zmq-backends)).

### RDMA Pixel Lane

The RDMA lane stages each pixel tensor in a pre-registered host-memory arena and hands the TokenSpeed worker a small descriptor; the worker pulls the pixels with a one-sided RDMA read.

- Build the `smg` binary with the `mm-rdma` Cargo feature (NIXL). A default build compiles an inert exporter, so every tensor stays inline.
- Turn the lane on with `--multimodal-tensor-transport rdma` (or the legacy `--mm-pixel-rdma`), and set `--rdma-listen-ip` to the gateway's RDMA address. Without a listen IP, the lane stays off.
- The arena defaults to 64 slots of 32 MiB (2 GiB). `SMG_RDMA_POOL_SLOTS` and `SMG_RDMA_SLOT_BYTES` resize it (a slot must hold one image's pixels), and `SMG_RDMA_LISTEN_PORT` (default `18515`) sets the listener port.
- A slot the worker never releases is reclaimed after a TTL derived from the worker's longest hold: `SMG_RDMA_LANDING_WAIT_S` (default 120) plus `SMG_RDMA_READ_TIMEOUT_S` (default 60) plus 30 seconds. `--rdma-slot-ttl-s` overrides it only with a value longer than that hold.
- A tensor that cannot be staged falls back to shared memory or inline.

### Encoder Input Width

vLLM workers receive the pixel tensor as float32 unless `SMG_VLLM_ENCODER_INPUT_DTYPE` asks for `bfloat16` or `float16`, or the worker carries a `multimodal_encoder_dtype` label; the environment override wins. An engine that normalizes pixels on device receives the image's raw `uint8` bytes instead, whatever the dtype settings say. TokenSpeed workers receive the width they advertise, or bfloat16 when they advertise none; `SMG_TOKENSPEED_ENCODER_INPUT_DTYPE` and the per-modality `SMG_TOKENSPEED_IMAGE_ENCODER_INPUT_DTYPE`, `SMG_TOKENSPEED_VIDEO_ENCODER_INPUT_DTYPE`, and `SMG_TOKENSPEED_AUDIO_ENCODER_INPUT_DTYPE` override it. SGLang always receives float32.

---

## Other Paths

### Direct ZMQ Backends

vLLM and TokenSpeed workers reached over the direct ZMQ backend take router-preprocessed media, sent inline. Media references, shared memory, and RDMA are not available on this wire. vLLM over ZMQ takes one modality per request: a request that mixes modalities, such as images and video together, is refused with a 400 and needs a gRPC worker. TokenSpeed over ZMQ refuses media whose tensors include `image_grid_thw` or `video_grid_thw` (the M-RoPE families, such as Qwen-VL); serve those models over gRPC.

SGLang workers reached over the direct ZMQ backend take no media: a request with images — the one modality SGLang takes over gRPC — is refused with a 400 (`multimodal inputs are not supported over the SGLang ZMQ backend yet`). Serve vision SGLang models over gRPC. See [ZMQ Workers](../../getting-started/zmq-workers.md).

### Prefill-Decode Disaggregation

With prefill-decode disaggregation, media are normally processed once, for the prefill leg:

- **Router path.** The prefill worker gets the pixels. On vLLM and TokenSpeed, the decode worker gets each item's identity (content hashes, placeholder ranges, and M-RoPE grid tensors) but not the pixels; on SGLang it gets no media data. The exception is vLLM with `n > 1`: there is no KV handoff, so the decode leg keeps the full payload and encodes the media itself. On vLLM, the decode worker keys its cached KV blocks by the content hashes (through the cache salt when no grid tensors travel), so two different images behind the same text cannot share cached KV blocks.
- **Worker path (vLLM).** The prefill worker processes the references and returns the processed identity with its result. SMG builds the decode leg from it, so the decode worker does not fetch or process the media again. With `n > 1` there is no KV handoff, so the decode leg keeps the references and processes them itself.
- **Language-model-only decode workers.** vLLM decode workers started with `--language-model-only` report `supports_vision=false` and get the prefill-expanded prompt plus the content hashes, with no media payload. Three combinations cannot be served and are refused with a non-retryable 400: models that need M-RoPE grids (`pd_decode_language_model_only_mrope`), media references (`pd_decode_language_model_only_media_refs`), and `n > 1` (`pd_decode_language_model_only_n_samples`).

See [PD Disaggregation](../routing/pd-disaggregation.md) for how the legs are paired and dispatched.

### EPD (Encode-Prefill-Decode)

`--epd-disaggregation` (gRPC TokenSpeed workers only) adds encode workers, listed with `--encode <url> [bootstrap_port]`. SMG still fetches and preprocesses the media, then sends each item's pixels to an encode worker picked by `--encode-policy` (default `consistent_hashing`). The encode worker runs the vision tower and ships the embeddings to the prefill worker over Mooncake, so the prefill request carries no pixels. Pixels reach encode workers inline, through shared memory, or over the RDMA lane.

---

## Observability

`--log-mm-timing` (env fallback `SMG_LOG_MM_TIMING`, deprecated) logs `smg_mm_timing` lines at `INFO`: a per-request breakdown (`media_fetch_decode_ms`, `preprocess_ms`, `token_expand_ms`, `total_ms`) plus video decode and tensor transport events.

| Metric | Labels | Description |
|--------|--------|-------------|
| `smg_mm_processing_total` | `model`, `mode`, `reason` | Multimodal requests by where their media was processed (`router` or `worker`) and why |
| `smg_mm_tensors_total` | `runtime`, `path` | Tensors sent to vLLM and TokenSpeed workers, by transport path (`inline`, `shm`, `remote`) |
| `smg_mm_tensor_bytes_total` | `runtime`, `path` | Bytes of those tensors |
| `smg_mm_shm_write_failures_total` | `runtime` | Shared-memory writes that failed and fell back to inline |
| `smg_admission_queue_rejected_total` | `reason` | Includes `multimodal_inflight` (429) and `multimodal_too_large` (413) refusals |

The pixel cache reports `smg_mm_pixel_cache_hits_total` and `smg_mm_pixel_cache_misses_total` (by `model`), `smg_mm_pixel_cache_evictions_total`, and the `smg_mm_pixel_cache_bytes` and `smg_mm_pixel_cache_entries` gauges.

`GET /workers` shows the labels behind these decisions: `mm_processor`, `mm_processor_source`, `mm_media_ref_schemes`, `mm_item_limits`, `multimodal_encoder_dtype`, `supports_vision`, and `shm_namespace_id`. See the [Metrics Reference](../../reference/metrics.md) for every metric.

---

## Configuration Summary

| Flag | Default | Description |
|------|---------|-------------|
| `--mm-processing` | `auto` | Where media for vLLM gRPC workers is processed: `auto`, `router`, or `worker` |
| `--mm-per-request-image-limit` | Model limit | Image limit applied to every model |
| `--multimodal-max-inflight-bytes` | Unbounded | Cap on preprocessed media held in flight (429 when busy, 413 when too large) |
| `--mm-pixel-cache-mb` | `0` (off) | Pixel cache budget in MiB |
| `--mm-allowed-media-domains` | Unset (any host) | Hosts the router's own pipeline may fetch media from, enforced on every redirect hop |
| `--mm-allow-private-media-hosts` | Off | Allow media URLs on private, loopback, or link-local addresses (`SMG_MM_ALLOW_PRIVATE_MEDIA_HOSTS` sets it too, and is how a worker's own pipeline is told) |
| `--multimodal-tensor-transport` | `inline` | `inline`, `shm`, `auto`, or `rdma` |
| `--multimodal-shm-min-bytes` | `65536` | Smallest tensor sent through shared memory |
| `--mm-pixel-rdma` | Off | Legacy switch for the RDMA lane |
| `--rdma-listen-ip` | Unset | RDMA listener IP; the lane needs it |
| `--rdma-slot-ttl-s` | Derived | RDMA slot TTL override |
| `--log-mm-timing` | Off | Per-request multimodal timing logs |

`--mm-processing`, `--mm-pixel-cache-mb`, `--mm-pixel-rdma`, `--rdma-listen-ip`, `--rdma-slot-ttl-s`, and `--log-mm-timing` replace `SMG_*` variables that still work as deprecated fallbacks until the next minor release. With `smg serve`, prefix router flags with `--router-`, for example `--router-mm-processing router`. See [Multimodal Configuration](../../reference/configuration.md#multimodal-configuration) for the full list.

---

## Example

Start a vLLM gRPC worker with a vision model and point SMG at it:

```bash
python -m vllm.entrypoints.grpc_server \
  --model Qwen/Qwen3-VL-8B-Instruct \
  --host 0.0.0.0 \
  --port 50051

smg launch \
  --worker-urls grpc://localhost:50051 \
  --model-path Qwen/Qwen3-VL-8B-Instruct \
  --port 30000
```

Send an image with a chat completion:

```bash
curl http://localhost:30000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "Qwen/Qwen3-VL-8B-Instruct",
    "messages": [{
      "role": "user",
      "content": [
        {"type": "image_url", "image_url": {"url": "https://qianwen-res.oss-cn-beijing.aliyuncs.com/Qwen-VL/assets/demo.jpeg"}},
        {"type": "text", "text": "Describe this image in one sentence."}
      ]
    }],
    "max_tokens": 64
  }'
```

`usage.prompt_tokens` includes the expanded image placeholder run. To send a local file, pass it as a base64 data URL such as `data:image/jpeg;base64,...`.

---

## What's Next?

<div class="grid" markdown>

<div class="card" markdown>

### :material-pipe: gRPC Pipeline

See the stages a gRPC request goes through, from chat templates to tool parsing.

[gRPC Pipeline →](grpc-pipeline.md)

</div>

<div class="card" markdown>

### :material-call-split: PD Disaggregation

Split prefill and decode across worker pools.

[PD Disaggregation →](../routing/pd-disaggregation.md)

</div>

<div class="card" markdown>

### :material-cog: Configuration Reference

Every multimodal flag, environment variable, and default.

[Multimodal Configuration →](../../reference/configuration.md#multimodal-configuration)

</div>

</div>
