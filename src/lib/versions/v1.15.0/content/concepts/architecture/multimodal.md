---
title: Multimodal Pipeline
---

# Multimodal Pipeline

With gRPC workers, SMG runs the multimodal front end for the engine. It fetches each image or video, preprocesses it the way the model's reference processor does, expands the prompt's placeholder tokens to the right length, and sends the encoder tensors to the worker. Workers that run a media processor of their own can instead receive the media URLs: vLLM gRPC workers, and SGLang gRPC workers running SMG's own pipeline.

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

Workers that advertise a media processor receive media URLs instead: vLLM workers run vLLM's own processor (in process or in a Redis sidecar) or SMG's own pipeline; SGLang workers run SMG's pipeline.

</div>

<div class="card" markdown>

### :material-filmstrip: Reference-Rate Video

Clips are sampled at the model's reference frame rate, decoded with ffmpeg or OpenCV, and preprocessed in parallel.

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
| Messages (`/v1/messages`) | `image` blocks with a `base64` or `url` source, in user messages, directly or inside tool results |
| Responses (`/v1/responses`) | `input_image` content parts that carry an `image_url` |

Media URLs can be `http://`, `https://`, or base64 `data:` URLs.

On the gRPC path, a media request goes through these steps:

1. **Plan and validate.** SMG collects the media parts in prompt order and picks the model's processor from the model name and its `config.json` `model_type`. A modality the model does not take or none of its workers accepts, or more items than the per-request limit, is rejected before anything is fetched. A model with no processor is rejected only when the router must process its media itself; when placement forwards media references instead (see [Where Media Is Processed](#where-media-is-processed)), the request goes on to a worker that processes media itself, whose engine owns the placeholders, their expansion, and the per-prompt limits, and SMG renders and checks no anchors of its own.
2. **Render and check anchors.** The chat template renders one placeholder anchor per item (for example `<|image_pad|>` for Qwen3-VL). After tokenizing, SMG checks that the prompt holds exactly one anchor per item.
3. **Choose where to process.** `--mm-processing` decides between the router and a worker that processes media itself (see [Where Media Is Processed](#where-media-is-processed)).
4. **Fetch and decode.** Items are fetched concurrently. Images are decoded and videos are sampled into frames.
5. **Preprocess and expand.** The model's processor turns each item into encoder tensors and a feature-token count. SMG replaces each anchor with the model's full placeholder run, including any per-frame markers and timestamps.
6. **Select a worker.** Selection keeps image and video requests off workers without a vision encoder; the request is refused if the selected engine does not accept one of its modalities.
7. **Assemble and send.** The tensors are serialized for that engine and sent inline, through shared memory, or over RDMA. With `--multimodal-max-inflight-bytes` set, the request first reserves room for its media.

The processor configuration (`config.json`, `preprocessor_config.json`, and, when present, `processor_config.json` and `video_preprocessor_config.json`) comes from the tokenizer bundle the gRPC worker serves, or from the tokenizer source (a local directory or a Hugging Face download).

---

## Where Media Is Processed

`--mm-processing` decides, per request, whether SMG preprocesses the media or forwards media references — the URLs, with the prompt's anchors unexpanded — to a gRPC worker that processes them itself. Two kinds of worker process references: vLLM workers running the engine's own processor (`inprocess` or `redis`), and workers running SMG's own pipeline (`--mm-processor smg`, on the vLLM and SGLang Rust servicers). Every other worker receives router-preprocessed media.

| Mode | Behavior |
|------|----------|
| `auto` (default) | Forward references when the request is forwardable and every registered worker of the model processes them: either the engine's own processor expands the model's anchors, or every worker runs SMG's own pipeline, which expands any supported family's image and video anchors. Otherwise preprocess on the router. |
| `router` | Always preprocess on the router. |
| `worker` | Forward references. A request with per-item hints (`fps`, `max_long_side_pixel`) is preprocessed on the router instead when SMG can apply the hints itself (it has a processor for the model's family and no part is audio), and refused with 400 `multimodal_hint_unsupported_in_worker_mode` when it cannot. Any other request that cannot be forwarded is refused with 400; when the model has registered workers and none processes media references, SMG returns 503 `no_media_ref_capable_worker`. |

A request is forwardable when every media part is an `image_url`, `video_url`, or `audio_url` without per-item hints (`fps`, `max_long_side_pixel`); inline media count, as `data:` URLs. Audio references go only to vLLM workers running the engine's own processor — SMG's pipeline takes no audio — and a family whose anchors only SMG's pipeline expands goes only to workers running it; selection places each request on the workers that can take it. The flag falls back to `SMG_MM_PROCESSING` (deprecated; env support ends in the next minor release), then `auto`. An unreadable `SMG_MM_PROCESSING` stops the gateway at startup.

In `auto`, SMG consults every registered worker of the model, healthy or not, so a health flap does not move a model between modes. A model served through encode workers stays on the router, unless its encode workers are vLLM push-handoff encoders that process references themselves (see [EPD](#epd-encode-prefill-decode)). Each decision is counted in `smg_mm_processing_total{model,mode,reason}`, with reason `config`, `hint_fallback`, `pipeline_workers`, `plan_not_forwardable`, `model_not_opted_in`, `auto_none` (no registered workers), `auto_uniform`, `auto_pipeline`, `auto_epd`, or `auto_mixed`.

On the worker path:

- The request carries the unexpanded prompt (one anchor per item) and the media URLs in prompt order.
- An inline `data:` URL larger than `SMG_IMAGE_MAX_INPUT_BYTES`, `SMG_VIDEO_MAX_INPUT_BYTES`, or `SMG_AUDIO_MAX_INPUT_BYTES` is refused with `media_ref_too_large`.
- Every URL scheme must be one the selected worker advertises in its `mm_media_ref_schemes` label: `http`, `https`, and `data`, plus `file` when vLLM runs with `--allowed-local-media-path` (never with the `smg` processor).
- Direct ZMQ workers and TokenSpeed EPD encode workers never take references.

!!! warning "Routing sees unexpanded prompts on the worker path"
    When media is forwarded, SMG never expands the placeholders, so routing that weighs prompt length (cache-aware policies, load estimates) counts one token per media item while the worker schedules the full placeholder run.

### Worker-Side Processing on vLLM

A vLLM gRPC worker processes references when its servicer runs a media processor:

```bash
SMG_VLLM_MM_PROCESSOR=inprocess \
  vllm serve Qwen/Qwen3-VL-8B-Instruct --grpc --allowed-media-domains example.com
```

The servicer reads each setting below from its `--mm-*` flag, and otherwise from the matching `SMG_VLLM_MM_*` variable. The `smg-grpc-servicer` package adds the flags to both gRPC launchers' parsers (`vllm serve --grpc` and `python -m vllm.entrypoints.grpc_server`) as a `vllm.general_plugins` plugin, so either form works on the worker's command line.

A value read from a variable logs a deprecation warning, and env support is due to end in the next minor release. `SMG_VLLM_MM_FETCH_TIMEOUT_MS`, `SMG_VLLM_MM_VIDEO_FETCH_TIMEOUT_MS`, and `SMG_VLLM_MM_TURBOJPEG` are the exceptions: they stay supported fallbacks for their flags and never log the warning.

| Servicer flag | Env fallback | Default | Description |
|---------------|--------------|---------|-------------|
| `--mm-processor` | `SMG_VLLM_MM_PROCESSOR` | `off` | `off`, `inprocess` (fetch and process inside the vLLM process), `redis` (hand jobs to a sidecar), or `smg` (SMG's own pipeline, [Rust servicer](../../getting-started/grpc-workers.md#rust-vllm-servicer) only) |
| `--mm-max-inflight` | `SMG_VLLM_MM_MAX_INFLIGHT` | `64` | Multimodal jobs the worker runs at once, whichever processor runs; once as many are waiting, further requests are shed with a retryable error |
| `--mm-max-items` | `SMG_VLLM_MM_MAX_ITEMS` | unset | Overrides the per-modality item limits the worker takes from vLLM's `--limit-mm-per-prompt` |
| `--mm-max-item-bytes` | `SMG_VLLM_MM_MAX_ITEM_BYTES` | 32 MiB | Cap on one inline `data:` payload |
| `--mm-fetch-timeout-ms` | `SMG_VLLM_MM_FETCH_TIMEOUT_MS` | `10000` | Download budget per image or audio URL (`smg` processor only); must be positive |
| `--mm-video-fetch-timeout-ms` | `SMG_VLLM_MM_VIDEO_FETCH_TIMEOUT_MS` | `30000` | Download budget per video URL (`smg` processor only); must be positive |
| `--mm-turbojpeg` | `SMG_VLLM_MM_TURBOJPEG` | `prefer` | What the `smg` processor does when libjpeg-turbo cannot be loaded: `prefer` decodes JPEGs with the pure-Rust fallback, `require` refuses to start |
| `--mm-redis-url` | `SMG_VLLM_MM_REDIS_URL` | `redis://127.0.0.1:6379/0` | Sidecar Redis (`redis` mode) |
| `--mm-sidecar-timeout-ms` | `SMG_VLLM_MM_SIDECAR_TIMEOUT_MS` | `30000` | How long the worker waits for a sidecar result |
| `--mm-sidecar-max-queue` | `SMG_VLLM_MM_SIDECAR_MAX_QUEUE` | `256` | Fail fast once this many jobs are queued for the sidecar |
| `--mm-sidecar-namespace` | `SMG_VLLM_MM_SIDECAR_NAMESPACE` | derived | Overrides the Redis key namespace |

`SMG_VLLM_MM_MAX_VIDEO_FRAMES` (env only; default `0`, which leaves it to vLLM's `--media-io-kwargs`) caps the frames a video is sampled to.

The worker advertises its processor in the `mm_processor`, `mm_processor_source`, and `mm_media_ref_schemes` labels, which appear in `GET /workers`. An engine started with `--language-model-only` never advertises one. With `inprocess` and `redis`, vLLM's own `--allowed-media-domains`, `--allowed-local-media-path`, `--media-io-kwargs`, `--limit-mm-per-prompt`, and `VLLM_*_FETCH_TIMEOUT` govern fetching on the worker; without `--allowed-media-domains` the worker fetches from any host. The `smg` processor fetches with SMG's own connector instead (see [SMG's Pipeline on the Rust Servicer](#smgs-pipeline-on-the-rust-servicer)).

Failures keep their cause. The caller's own mistakes (a bad URL, a disallowed host, an oversized payload) come back as 400, and a saturated worker or a sidecar that is down or overloaded as a retryable 503. A sidecar timeout is not retried, because the worker already spent the whole budget on that input.

Each processor answers a failed fetch — a timeout, a refused connection, an origin 5xx — in its own way: `inprocess` returns a retryable 503; the `redis` sidecar answers with a 400 whose message includes `fetch_failed`; `smg` reports whose fault the fetch is, so the request gets the same 400-or-502 split as on the router path (see [Errors](#errors)).

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

`--mm-processor smg` runs SMG's own media pipeline — the same fetch, decode, preprocess, and placeholder expansion the router runs for `--mm-processing router` — inside the worker, with no Python on the request path. The resulting batches reach the engine as a router-preprocessed request's would. The mode exists only in the [Rust vLLM servicer](../../getting-started/grpc-workers.md#rust-vllm-servicer), the default implementation; the deprecated Python servicer, selected with `--servicer-impl python` / `SMG_VLLM_SERVICER_IMPL=python`, refuses the value at startup.

```bash
vllm serve Qwen/Qwen3-VL-8B-Instruct --grpc --mm-processor smg
```

- The worker serves the model families SMG's pipeline supports (see [Supported Models](#supported-models)); a multimodal model outside them is refused at startup, while a text-only model ignores the setting as it does the other processors. The router still decides per model whether to forward references at all (see [Where Media Is Processed](#where-media-is-processed)): `smg` changes what runs once references arrive, not which models they are forwarded for.
- The pipeline reads the model's `config.json` and preprocessor configs from the model directory, or from the tokenizer directory the launcher resolved. The engine's `mm_processor_kwargs` are applied as overrides of the preprocessor config; a key the config has no field for fails the launch rather than being ignored.
- Workers running the pipeline expand any supported family's image and video anchors, including families vLLM's own processor does not expand, so they can serve forwarded references for models the `inprocess` and `redis` processors cannot.
- The worker advertises `mm_processor=smg` and `mm_media_ref_schemes=http,https,data`, and fetches under the same address policy as the router: a URL that names or resolves to a loopback, link-local, private or otherwise non-public address is refused, unless `SMG_MM_ALLOW_PRIVATE_MEDIA_HOSTS` in the worker's environment admits such origins (the pipeline has no flag for it). `--mm-fetch-timeout-ms` and `--mm-video-fetch-timeout-ms` set its download budgets (10 and 30 seconds by default); each budget covers one URL's fetch, from connecting through the last byte, not decoding or inference. vLLM's `--allowed-media-domains` and `--allowed-local-media-path` do not apply — any host the address policy passes is fetched — and `file` URLs are never accepted.
- `--mm-max-inflight` and `--mm-max-item-bytes` apply as above. `--mm-max-items` overrides the model's built-in per-request limits, while vLLM's `--limit-mm-per-prompt` stays a ceiling: the pipeline refuses a request above the engine's own limits as the engine's server does, even when `--mm-max-items` allows more. For the families whose sampling follows the engine's loader, the pipeline samples video to the engine's own frame budget (`--media-io-kwargs` `video.num_frames`, else its loader's default), and `SMG_VLLM_MM_MAX_VIDEO_FRAMES` caps that budget: a cap below the engine's count makes the pipeline sample fewer frames than the engine's own server would. The Redis sidecar settings are not used.
- An engine that normalizes pixels on device (vLLM's `mm_device_do_normalize`, on by default for the Qwen-VL family) takes raw `uint8` pixels, and the pipeline writes those for it. A model whose processor produces only normalized floats is refused at startup under that setting: start the engine with `--mm-device-do-normalize=false` or use `inprocess`. Otherwise pixels are written in the engine's own dtype.
- The in-flight cap, the saturation refusal, and the PD prefill leg's `media_identity` behave as with the `inprocess` and `redis` processors.

### SMG's Pipeline on the SGLang Servicer

`SMG_SGLANG_MM_PROCESSOR=smg` runs the same pipeline inside the Rust SGLang servicer (the default implementation). The worker advertises `mm_processor=smg` and takes image and video references — mixed in either order — for GLM-5.3-Flash and MiniMax-M3; another model family is refused at startup, and audio and hinted parts are never forwarded to it. This is also the one path that serves video on SGLang: router-preprocessed SGLang media stay images only. The processor cannot be combined with encode disaggregation. `SMG_SGLANG_MM_MAX_INFLIGHT` (default `64`) caps the media requests the worker processes at once, and `SMG_SGLANG_MM_DTYPE` overrides the vision encoder's input width.

In prefill-decode deployments both legs take the references; when both workers advertise the same processor fingerprint, the decode leg reuses the prefill leg's processed identity instead of fetching and processing again, and falls back to its own processing after a five-second wait. The reuse covers single-modality requests: a request mixing images and video is processed on both legs. `SMG_SGLANG_PD_MEDIA_REUSE=0` disables the reuse.

---

## Supported Models

SMG picks a model's processor from the model name and its `config.json` `model_type`. These families have vision support on the router path: SMG can preprocess their media itself. A multimodal model outside them can still be served when placement forwards media references to a vLLM worker running the engine's own processor (`inprocess` or `redis`): the worker expands the engine's own placeholders and enforces the engine's per-prompt limits, as on the engine's own server (see [Where Media Is Processed](#where-media-is-processed)). Where the router would have to process such a model's media itself, the request is refused (see [Errors](#errors)). Most families set no per-request media count of their own (*no limit of its own* below): such a modality is bounded by the deployment's [media-count settings](#media-counts), never above the per-request limit the model's workers advertise for their engine; with neither, the context length is the practical bound. The counts in the table are the limits a family keeps of its own:

| Model family | Images | Video | Anchors vLLM's own processor expands |
|--------------|--------|-------|--------------------------------------|
| Qwen3-VL, Qwen3.5, Qwen3.6, Qwen4-Exp (`qwen4_exp`) | No limit of its own | No limit of its own ¹ | Images and video |
| Qwen2-VL, Qwen2.5-VL | No limit of its own | No limit of its own ¹ | — |
| Qwen3-Omni | No limit of its own | No limit of its own | Images, video, and audio |
| MiniMax-M3 | 200 | 20 clips ¹ | Images and video ² |
| Muse Spark ⁴ | No limit of its own | No limit of its own | — |
| Gemma 4 | No limit of its own | No limit of its own ¹ | Images, video, and audio |
| GLM-5.3-Flash | 64 | 2 clips ¹ | Images |
| Nemotron-H Nano Omni | No limit of its own | No limit of its own | — |
| Kimi-K3 | No limit of its own | — | — ³ |
| Kimi-K2.5 | 10 | — | — |
| DeepSeek-V4.1 | No limit of its own | — | Images |
| Llama 4 | 8 | — | Images |
| Step3-VL, Step5-VL | 8 | — | Images |
| LLaVA 1.5, LLaVA-NeXT | 4 | — | Images |
| Phi-3 and Phi-3.5 vision | 4 | — | — |
| Inkling | No limit of its own (when the checkpoint enables its vision tower) | — | — |

The last column lists the anchors vLLM's own processor (`inprocess`, `redis`) expands on the worker path. Workers running SMG's own pipeline (`--mm-processor smg`) expand any of these families' image and video anchors instead, never audio.

¹ When the checkpoint declares the tokens its video layout needs.

² vLLM currently sizes some MiniMax-M3 images differently from SMG, so answers on the two paths can differ.

³ Kimi-K3 anchors are not expanded by vLLM's own processor, which expands a different anchor than the one SMG renders; Kimi-K3 references are forwarded only to workers running SMG's own pipeline.

⁴ Muse Spark media are served on TokenSpeed workers only: the tensors are packed in a layout the other engines do not read. Each checkpoint's config declares the modalities it takes: images with `image_token_id`, video with `video_token_id`, and audio with the audio tower (an `audio_config` plus `audio_token_id`).

Audio (`audio_url`, `input_audio`) that SMG preprocesses itself is accepted for Qwen3-Omni, Qwen3-ASR, Inkling, Muse Spark, and Nemotron-H Nano Omni (checkpoints with a `sound_config`), on TokenSpeed workers only. On the worker path, audio references are forwarded to vLLM workers running the engine's own processor for the families whose audio anchor it expands (Qwen3-Omni, Qwen3-ASR, Gemma 4); Gemma 4 audio is served only this way, as SMG has no audio processor for it. No family sets an audio-clip count of its own: `SMG_AUDIO_MAX_COUNT` and the engine's advertised limit bound the count. Muse Spark serves audio on checkpoints that ship the audio tower; each clip is resampled to 24 kHz and zero-padded to a whole number of audio tokens (1,920 samples per token), and the clip's placeholder run in the prompt is that token count.

### Engine Support

| gRPC engine | Images | Video | Audio | What SMG sends |
|-------------|--------|-------|-------|----------------|
| vLLM | :material-check: | :material-check: | Worker path only | Preprocessed tensors with per-item content hashes, or media references on the worker path |
| TokenSpeed | :material-check: | :material-check: | :material-check: | Preprocessed tensors, one set per item |
| SGLang | :material-check: | Worker path only | :material-close: | Preprocessed float32 pixel tensors, or media references to workers running SMG's pipeline |
| TensorRT-LLM | :material-check: | :material-close: | :material-close: | The original image bytes |
| MLX | :material-close: | :material-close: | :material-close: | — |

vLLM and TokenSpeed take several modalities in one request, such as images and video together. A modality the selected engine does not take is refused with 400 `multimodal_not_supported`. *Worker path only*: vLLM takes audio, and SGLang takes video, only as media references to a worker that processes them itself (see [Where Media Is Processed](#where-media-is-processed)).

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
| Frame rate | The model's reference rate: 1 fps for MiniMax-M3, the video processor config's `fps` for Muse Spark (default 2), 2 fps for other models | `video_url.fps`, from 0.2 to 5.0 |
| Frame count | Clip duration times the frame rate, kept within 4 to 768 frames. The Qwen2-VL line and Qwen3-Omni keep that window but follow their loader: whole temporal groups of two, placed at a constant step from the first frame. Gemma 4 and Nemotron-H Nano Omni take every frame of a clip with at most 32, else 32 spread evenly. Muse Spark follows the checkpoint's video processor config, and GLM-5.3-Flash its family's own loader rule (see below). | — |
| Frame placement | Spread evenly from the first frame to the last. MiniMax-M3 takes one frame per interval from the start and always keeps the last frame, as its reference does. GLM-5.3-Flash picks frames with its family's loader walk (see below). | — |
| Frame size | Set by the model's processor | `video_url.max_long_side_pixel`: a multiple of 28 from 150 to 3584, applied to the decoded frames |

`fps` and `max_long_side_pixel` are MiniMax-M3 extensions to the OpenAI content part, and SMG applies them for any model. A part with any of these hints is never forwarded to a worker: even under `--mm-processing worker`, a request carrying one is preprocessed on the router when SMG can apply the hints itself, and refused with 400 `multimodal_hint_unsupported_in_worker_mode` when it cannot (SMG has no processor for the model's family, or the request also carries audio).

`image_url.max_long_side_pixel` caps an image's long side. On MiniMax-M3, any positive value is accepted, and an image without the hint gets the default cap of 2016. SMG plans the output size from the source dimensions, keeping the aspect ratio: an image whose long side exceeds the cap is shrunk to the cap; otherwise an image whose short side is below 112 is enlarged until that side reaches 112, even if its long side then exceeds the cap. Both sides are then rounded to the nearest multiple of 28 to fit the patch grid, which can also leave the final long side slightly above the cap. An image whose planned size exceeds 12,845,056 pixels is refused with `media_too_large` before it is decoded.

On other model families, `image_url.max_long_side_pixel` must be a positive multiple of 28 and only shrinks an image whose long side exceeds it. Video frames follow the rule in the table above for every model.

```json
{"type": "video_url", "video_url": {"url": "https://example.com/clip.mp4", "fps": 1.0}}
```

Models whose reference processor lays video out frame by frame get the same layout from SMG: Qwen3-VL, Gemma 4, GLM-5.3-Flash, and MiniMax-M3 prompts carry one block per frame or temporal group, with a timestamp. For MiniMax-M3, SMG also applies the pixel budget per frame and stamps each frame with the reference timestamp.

GLM-5.3-Flash clips follow the family's own loader rule rather than the 4-to-768-frame window: the clip's duration times the sampling rate, truncated to a whole number of frames, up to 2,048. The frames are placed the way the model's reference loader walks a clip — one frame each time the clip's clock reaches the next sampling instant, with the instants spaced at twice the rate, then re-spread evenly when the walk's count misses the target — and each distinct frame is kept once, in temporal groups of two (an odd count repeats the last frame, as the model's processor does). A per-request `fps` hint still overrides the 2 fps rate; a rate the checkpoint's video processor config states does not move it. A clip shorter than one sampling interval (`1 / rate` seconds: half a second at the default rate) gives the rule no frame to pick, so it is refused with 400 `media_fetch_failed` before any frames are decoded.

Muse Spark clips follow the checkpoint's video processor config rather than the 4-to-768-frame window: frames are sampled evenly across the clip at the config's `fps` (default 2), in whole temporal groups of `temporal_patch_size` frames (default 2), up to the config's `num_frames` (default 96; the released checkpoints raise it). A per-request `fps` hint still overrides the sampling rate. The prompt carries one token block per temporal group, stamped with the time of the group's first sampled frame in the config's `frame_timestamp_format` (default `Time: {timestamp:.1f}s`; `Time: {timestamp:.2f}s` on the released checkpoints).

Several clips in one request are preprocessed in parallel, and a request can carry images and video together on the engines that take both.

---

## Limits and Safety

### Media Counts

A few model families keep built-in per-request limits; most declare their media without a count of their own (see [Supported Models](#supported-models)). Either way, SMG never sends more items than the per-request limit the model's workers advertise for their engine. These deployment settings replace a family's own count:

| Setting | Scope | Description |
|---------|-------|-------------|
| `--mm-per-request-image-limit` | Images, every model | Replaces each model's own image limit (at least 1). It tightens, never loosens, the limit the model's workers advertise for their engine, and takes precedence over `SMG_IMAGE_MAX_COUNT`. |
| `SMG_IMAGE_MAX_COUNT`, `SMG_VIDEO_MAX_COUNT`, `SMG_AUDIO_MAX_COUNT` | One modality, every model | Environment overrides that raise or lower the limit; unset, zero, or non-numeric values are ignored. |

Neither setting enables a modality the model does not support. When you raise a limit, raise the engine's own limit to match. On the worker path, the worker also enforces its own item limits.

### Sizes and Timeouts

| Limit | Default | Setting |
|-------|---------|---------|
| Encoded image | 256 MiB | `SMG_IMAGE_MAX_INPUT_BYTES` |
| Encoded video | 256 MiB | `SMG_VIDEO_MAX_INPUT_BYTES` |
| Encoded audio | 256 MiB | `SMG_AUDIO_MAX_INPUT_BYTES` |
| Decoded image | 512 MiB | Fixed; the libjpeg-turbo path checks it before allocating |
| MiniMax-M3 planned image pixels | 12,845,056 | Fixed; the output size planned from the source dimensions is checked before the image is decoded (see [Frame Sampling](#frame-sampling)) |
| Decoded video frames | 1 GiB | `SMG_VIDEO_MAX_DECODED_BYTES` |
| Inkling image patches per request | 32,768 | `in_patch_limit` in the preprocessor config |
| Feature tokens per media item | The engine's advertised budget | Fixed per engine: the smallest `mm_encoder_budget` a model's workers advertise (vLLM's encoder-cache budget); an item over it — a long clip sampled at a high rate — is refused with 400 `multimodal_item_too_large` once its features are counted, instead of being sent to an engine that admits it and never schedules it |
| Media fetch | 10 seconds per image or audio URL, 30 per video URL | `--mm-fetch-timeout-ms`, `--mm-video-fetch-timeout-ms` (`SMG_MM_FETCH_TIMEOUT_MS`, `SMG_MM_VIDEO_FETCH_TIMEOUT_MS`) |

!!! note "Where the gateway fetches media from"
    When SMG preprocesses media, it fetches the `http://` and `https://` URLs a request names under a host policy. A URL that names or resolves to a loopback, link-local, private or otherwise non-public address is refused before anything is connected, so a gateway fetching on a client's behalf cannot be made to reach what only it can reach; `--mm-allow-private-media-hosts` (`SMG_MM_ALLOW_PRIVATE_MEDIA_HOSTS`) admits such origins for a deployment whose media lives inside. `--mm-allowed-media-domains` restricts fetches to the named hosts, every redirect hop included; without it, any host the address policy passes is fetched, so for untrusted clients name the hosts or restrict the gateway's outbound network access. On the worker path, vLLM's `--allowed-media-domains` applies with the `inprocess` and `redis` processors; a worker running `--mm-processor smg` applies the address policy with no allow-list — `SMG_MM_ALLOW_PRIVATE_MEDIA_HOSTS` in its environment admits non-public origins, and any host the policy passes is fetched — so for untrusted clients restrict its outbound access the same way.

### In-Flight Media Budget

`--multimodal-max-inflight-bytes` caps the bytes of preprocessed media SMG holds for engines at once. A request that fits waits up to 2 seconds for room and then gets 429 `multimodal_inflight_budget` with a `Retry-After` header. A request larger than the whole budget gets 413 `multimodal_payload_too_large` right away. A waiting request still holds its media, and the waiting queue is capped at one budget (a request that would overfill it gets 429 right away), so size memory for about twice the value. Unset leaves the budget unbounded; the budget is counted in whole 1024-byte units, so a value under 1024 bytes is refused at startup. The `smg_mm_inflight_bytes` gauge reports what is held, budget or not.

### Errors

Multimodal errors use the standard error body (`{"error": {"type": ..., "code": ..., "message": ...}}`), and the code is also sent in the `X-SMG-Error-Code` header.

| Status | Code | Cause |
|--------|------|-------|
| 400 | `unsupported_content_part` | A content part type the gRPC path does not know |
| 400 | `invalid_multimodal_request` | The model has no multimodal processor, does not take the modality, or the request has too many items; the refusal names the limit and what set it (for example `the request carries 11 image items, above this model's limit of 10 (--mm-per-request-image-limit)`) |
| 400 | `multimodal_prompt_contract_mismatch` | The rendered prompt does not hold exactly one anchor per media item: a chat template that drops media, or a literal anchor token in user text |
| 400 | `media_fetch_failed` | The request's own media failed: a URL that cannot be parsed or resolved, a host that refuses the connection, an origin 4xx, a malformed `data:` URL, a payload that is not the media it claims to be, a clip too short for the model's video sampling (see [Frame Sampling](#frame-sampling)), or an invalid `fps` or `max_long_side_pixel` |
| 400 | `media_too_large` | The media is over a size cap: its encoded bytes, its decoded bytes, a MiniMax-M3 image's planned size over the pixel budget, or the image decoder's own limits (see [Sizes and Timeouts](#sizes-and-timeouts)) |
| 400 | `multimodal_processing_failed` | Preprocessing failed, or a processing failure the media codes do not cover |
| 400 | `multimodal_item_too_large` | One media item's feature tokens exceed the engine's advertised encoder budget (see [Sizes and Timeouts](#sizes-and-timeouts)) |
| 400 | `multimodal_not_supported` | The selected engine does not take the modality; media references cannot reach the worker (ZMQ, TokenSpeed EPD encode workers); audio reached a worker running SMG's pipeline; or the router must process media for a model it has no processor for |
| 400 | `multimodal_worker_processing_unsupported_model` | `--mm-processing worker`, and the engine's own processor cannot expand the model's anchor while the model's media-processing workers do not all run SMG's pipeline; or a request pinned to SMG's pipeline landed on a worker running the engine's processor |
| 400 | `multimodal_hint_unsupported_in_worker_mode` | `--mm-processing worker`, and a part carries an `fps` or `max_long_side_pixel` hint that SMG cannot apply itself (it has no processor for the model's family, or the request also carries audio); a hinted request SMG can serve is preprocessed on the router instead |
| 400 | `media_ref_too_large` | Worker path: an inline `data:` payload is above the byte cap |
| 400 | `media_ref_scheme_not_accepted` | Worker path: the selected worker does not fetch that URL scheme |
| 413 | `multimodal_payload_too_large` | The request's media exceed the whole in-flight budget |
| 429 | `multimodal_inflight_budget` | The in-flight budget stayed full for 2 seconds, or the queue waiting for it was full |
| 500 | `multimodal_processing_failed` | The gateway's own failure: a decoder it cannot run (for example no ffmpeg on the gateway), a scratch file or decoder process that failed on the gateway's side, or a decode or preprocessing task that died |
| 502 | `media_fetch_failed` | The media's host failed for now: the fetch timed out, the origin answered 5xx, or the transfer broke |
| 502 | `multimodal_config_unavailable` | The model hub failed to serve one of the model's processor configuration files while the request's media was resolved: no answer in time, a 5xx or 429, or a broken transfer. The message is generic — the hub's answer stays in the gateway's log — and nothing is kept from the failed attempt, so a retried request resolves the configuration again |
| 503 | `multimodal_wire_changed` | The gateway's own transient state: the model's workers changed between media preparation and worker selection, so the tensors were prepared for an encoder dtype the selected worker does not read; retry the request |
| 503 | `no_media_ref_capable_worker` | `--mm-processing worker`, and no worker of the model advertises a media processor |

While `no_media_ref_capable_worker` holds for a model with a worker that reports a vision encoder, the ready body of [`/readiness`](../reliability/health-checks.md#gateway-probe-endpoints) names the model under `models_without_media_processing`; the status stays ready, and the model leaves the list once a worker of it that processes media references registers. A model served through encode workers is never listed, whatever engine runs them: media references travel the encode-prefill-decode pipeline only when the encode workers process them themselves, so a media-processing worker elsewhere in the pool would change nothing.

The status under `media_fetch_failed` tells whose fault the failure is: a 400 is the request's own mistake to fix; a 502 is the media host's trouble for now, so the client can retry later. SMG itself never retries the request — another attempt would fetch from the same host — and a worker that reports a media failure stays healthy and in rotation. The split applies wherever SMG's own pipeline fetches, on the router or on a worker running `--mm-processor smg`; the `inprocess` and `redis` processors answer as described under [Worker-Side Processing on vLLM](#worker-side-processing-on-vllm).

Prefill-decode deployments add three codes, listed under [Prefill-Decode Disaggregation](#prefill-decode-disaggregation).

---

## Performance

- **Fetch once.** Parts of one request whose payload is identical byte for byte — the same URL, inline `data:` URL, bytes, or file path — with the same settings share one fetch and decode, so an image repeated as several identical data URLs is fetched and decoded once. Duplicates are matched by comparing payloads, looked up by their kind and length and falling back to digests once several distinct payloads share one length.
- **Data URL fast path.** `data:image/...` URLs go straight to the base64 decoder without a full URL parse, which matters for large inline images.
- **Parallel preprocessing.** Modality batches are preprocessed concurrently. Within a batch, processors that preprocess each image independently split the images across cores, video clips run in parallel, and a batch's still-encoded images are decoded in parallel on the preprocessing pool before preprocessing starts — a repeated image only once.
- **Pixel cache.** `--mm-pixel-cache-mb` gives SMG a host-memory LRU cache of preprocessed images, keyed by the image's content hash and a fingerprint of the model and its preprocessing config. It serves single-image requests and, for processors that preprocess each image independently (among them the Qwen-VL families, Gemma 4, and Step3-VL), requests with fewer than 32 images, where each image is cached on its own and a repeated image is preprocessed once. The cache is off by default.
- **JPEG and WebP decoding.** When `libturbojpeg` is installed on the gateway host, JPEGs are decoded with libjpeg-turbo using Pillow's defaults, so the pixels match what vLLM computes. Without it, SMG uses a pure-Rust decoder that can differ by a few levels per pixel; `--mm-turbojpeg require` (`SMG_MM_TURBOJPEG`) refuses to start instead, and the `smg_mm_turbojpeg_available` gauge reports which decoder runs. WebP files likewise decode through libwebp when it is present (`smg_mm_webp_available`).

---

## Tensor Transport

`--multimodal-tensor-transport` chooses how preprocessed tensors reach the worker:

| Mode | Behavior |
|------|----------|
| `inline` (default) | Tensor bytes travel in the gRPC message. |
| `shm` | Tensors of at least `--multimodal-shm-min-bytes` (default 64 KiB) go through `/dev/shm` whenever SMG can write it. You assert that the worker shares it. |
| `auto` | Like `shm`, but only when the worker is verified to share SMG's `/dev/shm`: its `shm_namespace_id` label (`<boot_id>:<st_dev of /dev/shm>`) must equal SMG's own. |
| `rdma` | Pixel tensors for TokenSpeed workers travel over the NIXL RDMA pixel lane. Other tensors, and other engines, stay inline. |

A failed shared-memory write falls back to inline and is counted in `smg_mm_shm_write_failures_total`. The two flags fall back to `SMG_MM_TENSOR_TRANSPORT` and `SMG_MM_SHM_MIN_BYTES` (legacy names `SMG_TOKENSPEED_MM_TENSOR_TRANSPORT` and `SMG_TOKENSPEED_MM_SHM_MIN_BYTES`). The transport is chosen gateway-wide; the worker spec's `multimodal_tensor_transport` and `multimodal_shm_min_bytes` fields override it for the worker that runs the vision encoder — the encode worker in EPD, otherwise the selected or prefill worker (see the [Admin API](../../reference/api/admin.md#worker-spec)). The RDMA lane is also on or off for the whole gateway.

| Engine | `inline` | `shm` and `auto` | `rdma` |
|--------|----------|------------------|--------|
| vLLM (gRPC) | :material-check: | :material-check: | Falls back to inline |
| TokenSpeed (gRPC, including EPD encode workers) | :material-check: | :material-check: | :material-check: (with an `mm-rdma` build) |
| SGLang, TensorRT-LLM (gRPC) | :material-check: | Inline only | Inline only |
| vLLM, TokenSpeed, SGLang (direct ZMQ) | :material-check: | Inline only | Inline only |

### RDMA Pixel Lane

The RDMA lane stages each pixel tensor in a pre-registered host-memory arena and hands the TokenSpeed worker a small descriptor; the worker pulls the pixels with a one-sided RDMA read.

- Build the `smg` binary with the `mm-rdma` Cargo feature (NIXL). A default build compiles an inert exporter, so every tensor stays inline.
- Turn the lane on with `--multimodal-tensor-transport rdma` (or the legacy `--mm-pixel-rdma`), and set `--rdma-listen-ip` to the gateway's RDMA address. Without a listen IP, the lane stays off.
- The arena defaults to 64 slots of 32 MiB (2 GiB). `SMG_RDMA_POOL_SLOTS` and `SMG_RDMA_SLOT_BYTES` resize it (a slot must hold one image's pixels), and `SMG_RDMA_LISTEN_PORT` (default `18515`) sets the listener port.
- A slot the worker never releases is reclaimed after a TTL derived from the worker's longest hold: `SMG_RDMA_LANDING_WAIT_S` (default 120) plus `SMG_RDMA_READ_TIMEOUT_S` (default 60) plus 30 seconds. `--rdma-slot-ttl-s` overrides it only with a value longer than that hold.
- A tensor that cannot be staged falls back to shared memory or inline.

### Encoder Input Width

vLLM workers receive the pixel tensor as float32 unless `SMG_VLLM_ENCODER_INPUT_DTYPE` asks for `bfloat16` or `float16`, or the worker carries a `multimodal_encoder_dtype` label; the vLLM servicer accepts half-precision media. TokenSpeed workers receive the width they advertise, or bfloat16 when they advertise none; `SMG_TOKENSPEED_ENCODER_INPUT_DTYPE` and the per-modality `SMG_TOKENSPEED_IMAGE_ENCODER_INPUT_DTYPE`, `SMG_TOKENSPEED_VIDEO_ENCODER_INPUT_DTYPE`, and `SMG_TOKENSPEED_AUDIO_ENCODER_INPUT_DTYPE` override it. SGLang always receives float32.

---

## Other Paths

### Direct ZMQ Backends

vLLM, TokenSpeed, and SGLang workers reached over the direct ZMQ backend take router-preprocessed media, sent inline. Media references, shared memory, and RDMA are not available on this wire. A vLLM ZMQ worker takes one modality per request: a request mixing images and video needs the gRPC backend.

On TokenSpeed, media whose `image_grid_thw` or `video_grid_thw` tensors feed M-RoPE positions are served only for the Qwen3.5 lineage (Qwen3.5, Qwen3.5-MoE, Qwen4-Exp): SMG computes the lineage's M-RoPE position table itself, as the engine's input processor would, provided the engine's handshake declares the checkpoint's `spatial_merge_size`, `image_token_id`, and `video_token_id`. When a lineage engine's handshake omits any of them, every multimodal request is refused with a 400. Media of other M-RoPE families (such as Qwen3-VL or Qwen3-Omni) are refused with a 400; serve those models over gRPC. Muse Spark's grid tensors only describe the vision patch layout, so its images and video pass.

SGLang workers reached over the direct ZMQ backend take router-preprocessed images, assembled as the gRPC path assembles them; video on SGLang needs gRPC workers running SMG's own pipeline. See [ZMQ Workers](../../getting-started/zmq-workers.md).

### Prefill-Decode Disaggregation

With prefill-decode disaggregation, media are normally processed once, for the prefill leg:

- **Router path.** The prefill worker gets the pixels. On vLLM and TokenSpeed, the decode worker gets each item's identity (content hashes, placeholder ranges, and M-RoPE grid tensors) but not the pixels; on SGLang it gets no media data. The exception is vLLM with `n > 1`: there is no KV handoff, so the decode leg keeps the full payload and encodes the media itself. On vLLM, the decode worker keys its cached KV blocks by the content hashes (through the cache salt when no grid tensors travel), so two different images behind the same text cannot share cached KV blocks.
- **Worker path (vLLM).** The prefill worker processes the references and returns the processed identity with its result. SMG builds the decode leg from it, so the decode worker does not fetch or process the media again. With `n > 1` there is no KV handoff, so the decode leg keeps the references and processes them itself.
- **Worker path (SGLang).** Both legs take the references; workers running SMG's pipeline that advertise a matching processor fingerprint reuse the prefill leg's processed identity on decode (see [SMG's Pipeline on the SGLang Servicer](#smgs-pipeline-on-the-sglang-servicer)).
- **Language-model-only decode workers.** vLLM decode workers started with `--language-model-only` report `supports_vision=false` and get the prefill-expanded prompt plus the content hashes, with no media payload. Three combinations cannot be served and are refused with a non-retryable 400: models that need M-RoPE grids (`pd_decode_language_model_only_mrope`), media references (`pd_decode_language_model_only_media_refs`), and `n > 1` (`pd_decode_language_model_only_n_samples`).

See [PD Disaggregation](../routing/pd-disaggregation.md) for how the legs are paired and dispatched.

### EPD (Encode-Prefill-Decode)

`--epd-disaggregation` (gRPC vLLM or TokenSpeed workers) adds encode workers, listed with `--encode <url> [bootstrap_port]` and picked by `--encode-policy` (default `consistent_hashing`). The encode worker runs the vision tower, so the prefill request carries no pixels.

- **TokenSpeed.** SMG fetches and preprocesses the media and sends each item's pixels to the encode worker — inline, through shared memory, or over the RDMA lane; the encode worker ships the embeddings to the prefill worker over Mooncake. These encode workers never take media references.
- **vLLM.** The encode worker answers first with the prompt's expanded identity and one transfer handle per media item, and the prefill worker consumes a pixel-less prompt that names them, through the engine's encoder-cache connectors. An image-only request whose images reach the encode worker as forwarded media references can fan its images out across several encode workers; any other request — router-preprocessed, or carrying audio, video, or mixed modalities — keeps a single encoder. Encode workers that advertise a media processor and encoder-cache transfer can take forwarded media references (see [Where Media Is Processed](#where-media-is-processed)).

---

## Observability

`--log-mm-timing` (env fallback `SMG_LOG_MM_TIMING`, deprecated) logs `smg_mm_timing` lines at `INFO`: a per-request breakdown (`config_lookup_ms`, `media_fetch_decode_ms`, `preprocess_ms`, `token_expand_ms`, `total_ms`) plus video decode and tensor transport events.

| Metric | Labels | Description |
|--------|--------|-------------|
| `smg_mm_processing_total` | `model`, `mode`, `reason` | Multimodal requests by where their media was processed (`router` or `worker`) and why |
| `smg_mm_tensors_total` | `runtime`, `path` | Tensors sent to vLLM and TokenSpeed workers, by transport path (`inline`, `shm`, `remote`) |
| `smg_mm_tensor_bytes_total` | `runtime`, `path` | Bytes of those tensors |
| `smg_mm_shm_write_failures_total` | `runtime` | Shared-memory writes that failed and fell back to inline |
| `smg_mm_processing_seconds` | `model`, `stage` | Seconds a request spent in a stage (`config_lookup`, `media_fetch_decode`, `preprocess`, `token_expand`, `total`; `pool_queue` observes each blocking task's wait for a pool thread) |
| `smg_mm_media_per_request` | `model`, `modality` | Media items a request carried, counted before any refusal or limit |
| `smg_mm_media_items_total` | `model`, `modality`, `source` | Those items by source (`url`, `data_url`, `inline`) |
| `smg_mm_media_fetch_seconds` | `model`, `modality`, `source`, `outcome` | One media fetch and decode's time, by outcome (`ok`, `too_large`, `client_fault`, `host_fault`) |
| `smg_mm_media_fetch_bytes_total` | `model`, `modality`, `source` | Bytes the fetches brought in |
| `smg_mm_inflight_bytes` | — | Preprocessed media bytes held in flight for engines |
| `smg_mm_pixel_cache_hits_total`, `smg_mm_pixel_cache_misses_total` | `model` | Pixel cache lookups |
| `smg_mm_pixel_cache_evictions_total`, `smg_mm_pixel_cache_bytes`, `smg_mm_pixel_cache_entries` | — | What the pixel cache dropped and holds |
| `smg_mm_turbojpeg_available`, `smg_mm_webp_available` | — | 1 when the native decoder is loaded, 0 on the pure-Rust fallback |
| `smg_admission_queue_rejected_total` | `reason` | Includes `multimodal_inflight` (429) and `multimodal_too_large` (413) refusals |

`GET /workers` shows the labels behind these decisions: `mm_processor`, `mm_processor_source`, `mm_media_ref_schemes`, `mm_item_limits`, `mm_encoder_budget`, `multimodal_encoder_dtype`, `supports_vision`, and `shm_namespace_id`. See the [Metrics Reference](../../reference/metrics.md) for every metric.

---

## Configuration Summary

| Flag | Default | Description |
|------|---------|-------------|
| `--mm-processing` | `auto` | Where media for gRPC workers is processed: `auto`, `router`, or `worker` |
| `--mm-per-request-image-limit` | Model limit | Image limit applied to every model |
| `--multimodal-max-inflight-bytes` | Unbounded | Cap on preprocessed media held in flight (429 when busy, 413 when too large) |
| `--mm-pixel-cache-mb` | `0` (off) | Pixel cache budget in MiB |
| `--mm-turbojpeg` | `prefer` | Without libjpeg-turbo: fall back to the pure-Rust JPEG decoder (`prefer`) or refuse to start (`require`) |
| `--multimodal-tensor-transport` | `inline` | `inline`, `shm`, `auto`, or `rdma` |
| `--multimodal-shm-min-bytes` | `65536` | Smallest tensor sent through shared memory |
| `--mm-pixel-rdma` | Off | Legacy switch for the RDMA lane |
| `--rdma-listen-ip` | Unset | RDMA listener IP; the lane needs it |
| `--rdma-slot-ttl-s` | Derived | RDMA slot TTL override |
| `--log-mm-timing` | Off | Per-request multimodal timing logs |

`--mm-processing`, `--mm-pixel-cache-mb`, `--mm-pixel-rdma`, `--rdma-listen-ip`, `--rdma-slot-ttl-s`, and `--log-mm-timing` replace `SMG_*` variables that still work as deprecated fallbacks until the next minor release; `--mm-turbojpeg` falls back to `SMG_MM_TURBOJPEG`, a supported spelling. With `smg serve`, prefix router flags with `--router-`, for example `--router-mm-processing router`. See [Multimodal Configuration](../../reference/configuration.md#multimodal-configuration) for the full list.

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
