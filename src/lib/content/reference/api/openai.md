---
title: OpenAI-Compatible API
---

# OpenAI-Compatible API Reference

SMG provides a fully OpenAI-compatible API, allowing you to use existing OpenAI client libraries with your self-hosted inference workers.

---

## Base URL

```
http://localhost:30000/v1
```

---

## Authentication

SMG supports optional API key authentication:

```bash
curl http://localhost:30000/v1/chat/completions \
  -H "Authorization: Bearer your-api-key" \
  -H "Content-Type: application/json" \
  -d '...'
```

Enable authentication with `--api-key`:

```bash
smg launch --worker-urls http://worker:8000 --api-key "your-api-key"
```

For real multi-tenant separation (e.g. per-tenant rate limiting), configure one key per
tenant instead with `--tenant-api-key tenant_id:key` (repeatable). Each key resolves to
its own tenant identity; `--api-key` remains available as a single shared fallback key
whose callers all share one identity.

```bash
smg launch --worker-urls http://worker:8000 \
  --tenant-api-key team-red:red-secret \
  --tenant-api-key team-blue:blue-secret
```

!!! note "Rust binary only"
    `--tenant-api-key` is a flag of the Rust `smg` binary (`cargo install smg` or a source build). The Python launcher behind `pip install smg` and the container images does not accept it.

Callers authenticate the same way as with `--api-key` — a `Bearer` token in the
`Authorization` header — just using their own tenant's key instead of the shared one:

```bash
curl http://localhost:30000/v1/chat/completions \
  -H "Authorization: Bearer red-secret" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "meta-llama/Llama-3.1-8B-Instruct",
    "messages": [{"role": "user", "content": "Hello"}]
  }'
```

This request is attributed to `team-red`, distinct from a request authenticated with
`blue-secret`. A missing key, or one that doesn't match any configured `--api-key` or
`--tenant-api-key` value, is rejected with `401 Unauthorized` and an empty body. Tenant
keys authenticate serving endpoints only — `/v1/chat/completions`, `/v1/completions`,
`/v1/embeddings`, etc. They do not grant access to admin/management routes (`/workers`,
`/flush_cache`, and similar); that surface requires either the shared `--api-key` or
control-plane authentication.

`GET /v1/models` is a public route and answers without a key (see [List Models](#list-models)).

---

## Endpoints

### Chat Completions

Create a chat completion.

```
POST /v1/chat/completions
```

#### Request Body

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `model` | string | Yes | Model identifier. A name configured with `--model-alias` is accepted and resolved to its canonical model |
| `messages` | array | Yes | Array of message objects; must not be empty |
| `max_completion_tokens` | integer | No | Upper bound on generated completion tokens (at least 1) |
| `max_tokens` | integer | No | Deprecated — use `max_completion_tokens`. Still accepted and migrated when `max_completion_tokens` is unset |
| `temperature` | number | No | Sampling temperature (0-2) |
| `top_p` | number | No | Nucleus sampling parameter, greater than 0 and at most 1 |
| `n` | integer | No | Number of completions to generate (1-10) |
| `stream` | boolean | No | Enable streaming responses |
| `stream_options` | object | No | Streaming options; only allowed with `stream: true`. See [Stream Usage](#stream-usage) |
| `stop` | string/array | No | Up to 4 non-empty stop sequences |
| `presence_penalty` | number | No | Presence penalty (-2 to 2) |
| `frequency_penalty` | number | No | Frequency penalty (-2 to 2) |
| `logprobs` | boolean | No | Return log probabilities of the output tokens |
| `top_logprobs` | integer | No | Most likely tokens to return per position (0-20); requires `logprobs: true` |
| `logit_bias` | object | No | Map of token ID to bias |
| `tools` | array | No | Function tools the model may call |
| `tool_choice` | string/object | No | `none`, `auto`, `required`, `{"type": "function", "function": {"name": "..."}}`, or `{"type": "allowed_tools", "mode": "auto", "tools": [...]}` (`mode` is `auto` or `required`). Every value except `none` and `auto` requires tools, and a named function must exist in them |
| `parallel_tool_calls` | boolean | No | Allow parallel function calls |
| `response_format` | object | No | `text`, `json_object`, or `json_schema`. See [Structured Output](#structured-output) |
| `reasoning_effort` | string/number | No | Reasoning effort; a number is converted to its decimal string. See [Thinking and Reasoning Effort](#thinking-and-reasoning-effort) |
| `thinking` | object | No | Typed thinking control (`type`, `effort`, `keep`, `clear_thinking`). See [Thinking and Reasoning Effort](#thinking-and-reasoning-effort) |
| `user` | string | No | End-user identifier |

#### Extension Fields

SMG also accepts these SGLang-style extensions on Chat Completions:

| Field | Type | Description |
|-------|------|-------------|
| `top_k` | integer | Top-k sampling; `-1` disables it, otherwise at least 1 |
| `min_p` | number | Min-p sampling threshold (0-1) |
| `min_tokens` | integer | Minimum tokens to generate; must not exceed `max_completion_tokens` |
| `repetition_penalty` | number | Repetition penalty (0-2) |
| `regex`, `ebnf` | string | Constrain the output. Only one of `regex`, `ebnf`, and a `json_schema` response format may be set, and neither combines with a JSON `response_format` |
| `stop_token_ids` | array | Token IDs that stop generation |
| `ignore_eos`, `no_stop_trim` | boolean | Keep generating past EOS; keep the stop sequence in the output |
| `skip_special_tokens` | boolean | Drop special tokens when detokenizing (default `true`) |
| `continue_final_message` | boolean | Continue the trailing assistant message instead of starting a new one |
| `separate_reasoning`, `stream_reasoning` | boolean | Return reasoning in `reasoning_content` and stream it (both default `true`) |
| `chat_template_kwargs` | object | Extra chat-template variables. On gRPC workers an explicit entry wins over the `reasoning_effort`, `tool_choice`, and `response_format` values SMG passes to the template |
| `lora_path` | string | LoRA adapter to apply |
| `sampling_seed` | integer | Seed for deterministic sampling |
| `return_hidden_states` | boolean | Return the model's hidden states |
| `rid` | string | Request ID forwarded to the backend |

Fields SMG does not model are kept and forwarded unchanged to HTTP workers, and so are
unknown `stream_options` keys such as SGLang's `step_usage_chunks`. The SGLang-native
`/generate` endpoint likewise keeps its nested `sampling_params.custom_params` object.
Typed sampling numbers are forwarded without float widening: `"top_p": 0.95` reaches
HTTP workers, PD legs, and external providers as `0.95`, not `0.949999988079071`.

#### Message Object

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `role` | string | Yes | `system`, `developer`, `user`, `assistant`, `tool`, `function`, or `root` (MiniMax only; see [Provider Profiles](#provider-profiles)) |
| `content` | string/array | Yes | Text, or an array of [content parts](#content-parts). Optional on `system` and `assistant` messages; must not be empty on `user` messages |
| `name` | string | No | Participant name |
| `tool_calls` | array | No | Tool calls made by an `assistant` message |
| `tool_call_id` | string | For `tool` | ID of the tool call this `tool` message answers |
| `reasoning_content` | string | No | Reasoning from an earlier `assistant` turn; `reasoning` is accepted as an alias |
| `tools` | array | No | Kimi only: tools declared on a `system` or `developer` message |

#### Content Parts

| `type` | Payload | Notes |
|--------|---------|-------|
| `text` | `text` | |
| `image_url` | `image_url.url`, optional `detail` | MiniMax-M3 also takes `image_url.max_long_side_pixel` |
| `video_url` | `video_url.url` | MiniMax-M3 also takes `video_url.fps` and `video_url.max_long_side_pixel` |
| `input_audio` | `input_audio.data` (base64), `input_audio.format` | |
| `audio_url` | `audio_url.url` | |
| Anything else | Forwarded as sent | HTTP workers only |

A content part whose `type` SMG does not model, or a known type with a malformed payload,
is forwarded unchanged to HTTP workers. gRPC workers reject it with `400`
`unsupported_content_part`, and the gpt-oss (Harmony) path with `400`
`harmony_build_failed`. Which workers accept which media is covered in
[Multimodal](../../concepts/architecture/multimodal.md).

#### Thinking and Reasoning Effort

Two fields control reasoning. `reasoning_effort` is OpenAI's effort level. `thinking` is
a typed object shared by the Kimi, MiniMax, z.ai, and DeepSeek dialects:

| Field | Values | Meaning |
|-------|--------|---------|
| `type` | `enabled`, `disabled`, `adaptive` | Thinking toggle. `adaptive` states no preference; any other value is rejected |
| `effort` | string | Effort level; takes precedence over `reasoning_effort` |
| `keep` | string | Kimi `keep` mode; accepted and ignored by SMG's renderers |
| `clear_thinking` | boolean | z.ai: whether earlier turns' reasoning is dropped from the prompt |

The effective effort is `thinking.effort` when present, otherwise `reasoning_effort`.
Leaving `thinking` out changes nothing: the chat template's own default applies (Kimi K3,
for example, thinks by default), subject to `reasoning_effort`. The exception is the
DeepSeek V4 profile, which turns thinking on unless the request turns it off (see
[Defaults and Rewrites](#defaults-and-rewrites)). A `thinking` object without `type`
expresses no toggle, but its `effort` still counts.

On gRPC workers SMG renders the chat template itself. The template receives the effective
effort as its `reasoning_effort` variable, and SMG decides whether the model starts in
reasoning mode from the first of these that applies:

1. an explicit thinking toggle in `chat_template_kwargs`;
2. for templates with their own effort levels, the effort (`chat_template_kwargs.reasoning_effort`, else the effective effort). A template that toggles thinking through its own `reasoning_effort` on/off words (Hy4-style, `high`/`no_think`) is read by those words alone: its off word (`no_think`) turns thinking off and every other value turns it on (these templates default to thinking on), so `none` and `minimal` do not turn thinking off there. For renderers with native effort levels but no off words (DeepSeek V4, V4.1, Kimi-K3), `none` and `minimal` turn thinking off, a native level turns it on, and any other value falls through to the next steps;
3. `thinking.type` (`enabled` on, `disabled` off);
4. the effective effort, where `none` and `minimal` turn thinking off.

If none applies, the template default stands. The template and the reasoning parser use
the same decision. On gpt-oss (Harmony) models SMG reads `reasoning_effort` and clamps it to
the three levels Harmony knows: `none` and `minimal` become `low`, `xhigh` and `max`
become `high`, and an unknown value becomes `medium`. HTTP workers receive both fields as
sent unless a [provider profile](#provider-profiles) rewrites them, and provider profiles
restrict the accepted values per model family.

#### Structured Output

`response_format` accepts:

- `{"type": "text"}`
- `{"type": "json_object"}`
- `{"type": "json_schema", "json_schema": {"name": "...", "schema": {...}, "strict": true}}`

For `json_schema`, `name` must not be empty and `schema` must be a JSON object (`{}` is
allowed). A string, array, boolean, number, or `null` schema is rejected with `400` before
the request reaches any worker, on every backend, unless the HTTP router streams the request
to the worker unparsed (see [Request Streaming](../../concepts/performance/request-streaming.md)).

#### Example Request

```bash
curl http://localhost:30000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "meta-llama/Llama-3.1-8B-Instruct",
    "messages": [
      {"role": "system", "content": "You are a helpful assistant."},
      {"role": "user", "content": "What is the capital of France?"}
    ],
    "max_tokens": 100,
    "temperature": 0.7
  }'
```

#### Response

```json
{
  "id": "chatcmpl-abc123",
  "object": "chat.completion",
  "created": 1705312345,
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
    "prompt_tokens": 25,
    "completion_tokens": 8,
    "total_tokens": 33
  }
}
```

#### Usage

| Field | Description |
|-------|-------------|
| `prompt_tokens`, `completion_tokens`, `total_tokens` | Token counts. With `n` > 1 the shared prompt is counted once and the choices' completions are summed |
| `prompt_tokens_details.cached_tokens` | Prompt tokens served from the worker's prefix cache |
| `completion_tokens_details.reasoning_tokens` | Reasoning tokens, when the count is non-zero. On gRPC workers SGLang reports them, and SMG counts them itself for gpt-oss (Harmony) models |
| `completion_tokens_details.accepted_prediction_tokens` | Speculative decoding: draft tokens the target model accepted |
| `completion_tokens_details.rejected_prediction_tokens` | Speculative decoding: draft tokens rejected (drafted minus accepted) |

On gRPC workers SMG builds `usage` from the engine's counters; HTTP workers return the
engine's own usage object. The speculative-decoding fields appear only when the engine
reports proposed draft tokens. SMG reads them from SGLang, TokenSpeed, vLLM (started with
`--per-request-spec-decode-metrics`), and TensorRT-LLM (once its servicer fills the
field); MLX reports none.

#### Streaming Response

With `"stream": true`, responses are sent as Server-Sent Events:

```bash
curl http://localhost:30000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "meta-llama/Llama-3.1-8B-Instruct",
    "messages": [{"role": "user", "content": "Hello"}],
    "stream": true,
    "stream_options": {"include_usage": true}
  }'
```

Response (abbreviated):

```
data: {"id":"chatcmpl-abc123","object":"chat.completion.chunk","created":1705312345,"model":"meta-llama/Llama-3.1-8B-Instruct","choices":[{"index":0,"delta":{"role":"assistant"},"finish_reason":null}]}

data: {"id":"chatcmpl-abc123","object":"chat.completion.chunk","created":1705312345,"model":"meta-llama/Llama-3.1-8B-Instruct","choices":[{"index":0,"delta":{"content":"Hello!"},"finish_reason":null}]}

data: {"id":"chatcmpl-abc123","object":"chat.completion.chunk","created":1705312345,"model":"meta-llama/Llama-3.1-8B-Instruct","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}

data: {"id":"chatcmpl-abc123","object":"chat.completion.chunk","created":1705312345,"model":"meta-llama/Llama-3.1-8B-Instruct","choices":[],"usage":{"prompt_tokens":9,"completion_tokens":2,"total_tokens":11}}

data: [DONE]
```

An error before the first chunk is an ordinary JSON error response with its own status;
when a worker rejects a streaming request, SMG keeps the worker's content type
(`application/json`, not `text/event-stream`) so SSE clients still see the error body.
If generation fails after a gRPC stream has started, SMG sends
`data: {"error": {"message": "...", "type": "internal_error"}}` followed by `data: [DONE]`.

#### Stream Usage

| `stream_options` field | Default | Effect |
|------------------------|---------|--------|
| `include_usage` | `false` (Kimi: `true`) | Send the whole request's usage in a final chunk with an empty `choices` array |
| `continuous_usage_stats` | `false` | gRPC workers, except for gpt-oss (Harmony) models: with `include_usage: true`, every chunk also carries a running `usage` snapshot (the prompt plus the tokens generated so far) |

On HTTP workers `stream_options`, including keys SMG does not model, is forwarded to the
engine, which produces the usage chunks. The Kimi profile turns `include_usage` on for
streaming requests that leave it unset, on every backend; an explicit `false` is kept.

The DeepSeek V4 profile uses DeepSeek's stream format on gRPC workers instead: aggregate
usage rides on the final finish chunk whether or not `include_usage` is set, and no
usage-only chunk follows. With `include_usage: true`, earlier chunks carry
`"usage": null`. `continuous_usage_stats` does not apply, and if the backend stream ends
before every choice completes, the finish chunk carries no aggregate usage.

---

### Completions

Create a text completion (legacy API).

```
POST /v1/completions
```

#### Request Body

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `model` | string | Yes | Model identifier |
| `prompt` | string/array | Yes | Text prompt, or a non-empty array of prompts |
| `max_tokens` | integer | No | Maximum tokens to generate |
| `temperature` | number | No | Sampling temperature (0-2) |
| `top_p` | number | No | Nucleus sampling parameter, greater than 0 and at most 1 |
| `n` | integer | No | Completions per prompt (1-128) |
| `best_of` | integer | No | Candidates to generate server-side (0-20); must be greater than `n` and cannot be combined with `stream` |
| `logprobs` | integer | No | Log probabilities of the most likely tokens (0-5) |
| `stream` | boolean | No | Enable streaming |
| `stream_options` | object | No | `include_usage`; only allowed with `stream: true` |
| `stop` | string/array | No | Up to 4 non-empty stop sequences |
| `echo` | boolean | No | Echo prompt in response |
| `suffix` | string | No | Text that comes after the completion |
| `presence_penalty`, `frequency_penalty` | number | No | Penalties (-2 to 2) |
| `seed` | integer | No | Seed for best-effort deterministic sampling |
| `user` | string | No | End-user identifier |

Completions takes the same [extension fields](#extension-fields) as Chat Completions except
the chat-only ones (`continue_final_message`, `separate_reasoning`, `stream_reasoning`,
`chat_template_kwargs`), plus a `json_schema` string constraint. On gRPC workers each
prompt in an array is its own engine request.

#### Example Request

```bash
curl http://localhost:30000/v1/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "meta-llama/Llama-3.1-8B-Instruct",
    "prompt": "The quick brown fox",
    "max_tokens": 50
  }'
```

#### Response

```json
{
  "id": "cmpl-abc123",
  "object": "text_completion",
  "created": 1705312345,
  "model": "meta-llama/Llama-3.1-8B-Instruct",
  "choices": [
    {
      "text": " jumps over the lazy dog.",
      "index": 0,
      "finish_reason": "stop"
    }
  ],
  "usage": {
    "prompt_tokens": 4,
    "completion_tokens": 7,
    "total_tokens": 11
  }
}
```

---

### List Models

List available models.

```
GET /v1/models
```

#### Example Request

```bash
curl http://localhost:30000/v1/models
```

#### Response

```json
{
  "object": "list",
  "data": [
    {
      "id": "meta-llama/Llama-3.1-8B-Instruct",
      "object": "model",
      "created": 0,
      "owned_by": "self_hosted"
    }
  ]
}
```

The list covers the models served by self-hosted workers, each once under its canonical
ID. Names configured with `--model-alias` are accepted in requests but are not listed.
`created` is always `0`. `owned_by` is `self_hosted` for locally hosted workers, or the
provider name (for example `openai`, `anthropic`, `xai`, `gemini`) for upstream providers.
When no self-hosted worker serves any model, the endpoint returns `503` with the
plain-text body `No models available`.

The endpoint needs no key. If the caller does send a bearer token (or an `x-api-key`
header) that is not one of the gateway's own keys, SMG treats it as the caller's own
provider key: it asks the registered external providers' `/v1/models` with that token and
returns the first non-empty list, falling back to the self-hosted list. A gateway key
(`--api-key` or `--tenant-api-key`) is never forwarded to a provider.

---

### Audio Transcriptions

Transcribe an audio file (batch).

```
POST /v1/audio/transcriptions
```

The request is `multipart/form-data`:

| Field | Required | Description |
|-------|----------|-------------|
| `file` | Yes | The audio file; must not be empty |
| `model` | Yes | Model identifier |
| `language` | No | Language of the audio |
| `prompt` | No | Text to guide the transcript |
| `response_format` | No | Output format (default `json`) |
| `temperature` | No | Sampling temperature (0.0-1.0) |
| `timestamp_granularities[]` | No | `word` or `segment`; repeat the field for both |
| `stream` | No | `true`/`false` (or `1`/`0`) |

A malformed form, a missing or empty `file`, a missing `model`, an out-of-range
`temperature`, or an unparseable `stream` gets a plain-text `400` before routing. Other
form fields are ignored.

```bash
curl http://localhost:30000/v1/audio/transcriptions \
  -F file=@audio.wav \
  -F model=Qwen/Qwen3-ASR-1.7B
```

What serves the request depends on the worker:

| Worker | Behavior |
|--------|----------|
| HTTP | The form is forwarded to the worker's `/v1/audio/transcriptions`; the worker's model and options apply |
| gRPC (regular mode) | SMG runs transcription as its own pipeline endpoint: it turns the audio into a chat turn, renders it with the model's chat template, and returns the transcript. Supported family: Qwen3-ASR |
| PD, EPD, and external providers | Not implemented (`501`) |

On gRPC, a model belongs to the Qwen3-ASR family when its ID, or a serving worker's model
ID or model-identity label, contains `qwen3-asr` or `qwen3_asr` (ignoring case). Audio
input over gRPC is currently accepted by TokenSpeed workers only; other gRPC runtimes
answer `400` `multimodal_not_supported`. The gRPC endpoint is whole-file only:

| Request | gRPC result |
|---------|-------------|
| A model outside the supported families | `400` `audio_transcription_model_not_supported` |
| `stream: true` | `400` `streaming_transcription_not_supported` |
| Any `timestamp_granularities` | `400` `transcription_timestamps_not_supported` |
| `response_format` of `srt`, `vtt`, or `verbose_json` | `400` `unsupported_transcription_response_format` |
| A `language` outside the model's list (ISO code or English name, such as `en` or `English`) | `400` `unsupported_transcription_language` |
| A `prompt` over 4096 bytes | `400` `asr_prompt_too_long` |

`response_format: json` returns `{"text": "..."}` and `text` returns the plain transcript.
Sampling is greedy unless `temperature` is set, and a given `language` pins the transcript
language.

---

### Realtime API

SMG proxies the OpenAI Realtime API to a realtime-capable worker. Both the OpenAI router
(to an upstream provider) and the HTTP router (to a **local** worker labeled
[`realtime: "true"`](../../getting-started/multiple-workers.md#realtime-capable-workers))
support it. SMG relays frames verbatim, so the worker must speak the OpenAI Realtime
protocol — for local workers, for example vLLM serving an ASR model with the realtime task.

| Endpoint | Transport | Purpose |
|----------|-----------|---------|
| `GET /v1/realtime` | WebSocket | Bidirectional realtime session (e.g. live streaming transcription) |
| `POST /v1/realtime/calls` | WebRTC (SDP) | Browser/WebRTC realtime session |
| `POST /v1/realtime/sessions` | HTTP | Create a realtime session |
| `POST /v1/realtime/client_secrets` | HTTP | Mint an ephemeral client secret |
| `POST /v1/realtime/transcription_sessions` | HTTP | Create a realtime transcription session |

`GET /v1/realtime` requires the `model` query parameter and answers `400` without it.
`POST /v1/realtime/calls` takes the model from the `model` query parameter for an
`application/sdp` body, or from the session JSON in a `multipart/form-data` body.

#### WebSocket example

```python
# pip install websockets
import asyncio, websockets

async def main():
    url = "ws://localhost:30000/v1/realtime?model=Qwen/Qwen3-ASR-1.7B"
    headers = {"Authorization": "Bearer your-api-key"}
    async with websockets.connect(url, additional_headers=headers) as ws:
        # Send realtime events (session.update, input_audio_buffer.append, ...)
        # and receive transcription/response events from the worker.
        ...

asyncio.run(main())
```

---

## Provider Profiles

Some model families come with a vendor contract that differs from the OpenAI baseline.
SMG picks a provider profile from the request's `model` and applies that profile's
defaults and validation to Chat Completions requests before routing, on every backend.

### Profile Selection

A profile matches when any `/`-separated segment of the model ID starts with one of its
markers, ignoring case, so `moonshotai/Kimi-K2-Instruct` and `/models/kimi-k3` both select
Kimi.

| Profile | Selected by |
|---------|-------------|
| Kimi | `kimi*`, `moonshot*`; the K3 rules apply to `kimi-k3*` and `kimi_k3*` |
| MiniMax | `minimax*`, `abab*` |
| z.ai (GLM) | `glm*`, `zai*`, `z-ai*`; the GLM-5.3 rules apply to `glm-5.3*`, `glm5.3*`, `glm_5.3*`, `glm-5-3*`, `glm5-3*` |
| DeepSeek V4 | A segment equal to `deepseek-flash`, `deepseek-v4-flash`, `deepseek-v4-pro`, or `deepseek-v4.1-flash` |
| OpenAI baseline | Everything else, including older DeepSeek models and `THUDM/chatglm3-6b` |

Selection reads the model name as sent, before `--model-alias` resolution: an alias that
doesn't match a marker itself gets the OpenAI baseline. Message-level extension fields
that belong to another profile (such as Kimi's message `tools` sent to a non-Kimi model)
are dropped before dispatch, with one warning logged per request.

Profile defaults and validation apply to `/v1/chat/completions` requests that pass
through SMG's request validation. They are not applied to `/v1/responses`, and a request
the HTTP router streams to the worker unparsed skips them (see
[Request Streaming](../../concepts/performance/request-streaming.md)).

### Validation Rules

A violation is rejected with `400`. The response has `error.type` set to
`invalid_request_error` and `error.code` set to `400` (as for every validation failure),
and `error.message` carries the rule's message:

```json
{
  "error": {
    "message": "__all__: invalid thinking.type: only enabled or disabled is allowed for this model",
    "type": "invalid_request_error",
    "code": 400
  }
}
```

The rule code in the last column names the rule in SMG's source and tests; only
`context_length_exceeded` is returned as `error.code`.

| Profile | Rejected | Rule code |
|---------|----------|-----------|
| All except MiniMax | A `root` message | `invalid_role` |
| Kimi | `tools` on a `user` or `assistant` message | `tools_role_restricted` |
| Kimi | `tools` on a `system` or `developer` message that is not a list of tools | `tools_malformed` |
| Kimi | A non-empty message `tools` list together with non-empty content on that message | `tools_content_conflict` |
| Kimi | A message tool whose `type` is not `function` | `tool_type_unsupported` |
| Kimi | A message tool name not matching `[A-Za-z_][A-Za-z0-9_]*`, or longer than 256 characters | `tool_name_invalid` |
| Kimi | A message tool name already declared in `tools` or on another message | `tool_name_duplicate` |
| Kimi K3 | `temperature` other than 0, 0.6, or 1; `top_p` other than 0.95; a non-zero `presence_penalty` or `frequency_penalty`; `n` other than 1 | `temperature_not_allowed`, `top_p_not_allowed`, `presence_penalty_not_allowed`, `frequency_penalty_not_allowed`, `n_not_allowed` |
| Kimi K3, z.ai, DeepSeek V4 | `thinking.type: adaptive` | `thinking_type_not_supported` |
| Kimi K3 | `thinking.effort` other than `low`, `high`, or `max` | `thinking_effort_invalid` |
| MiniMax | A tool-call ID used twice in the history | `tool_call_id_duplicate` |
| MiniMax | A `tool` message whose `tool_call_id` answers no pending call | `tool_call_id_mismatch` |
| MiniMax | Historical tool-call `arguments` that are non-empty but not a JSON object | `tool_call_arguments_invalid_json` |
| MiniMax | A tool call with no `tool` message answering it | `tool_call_unanswered` |
| z.ai | `thinking.effort` or `reasoning_effort` outside `none`, `minimal`, `low`, `medium`, `high`, `xhigh`, `max` (GLM-5.3: outside `low`, `high`, `max`) | `reasoning_effort_not_allowed` |
| z.ai GLM-5.3 | `thinking.type: disabled` | `thinking_disabled_not_supported` |
| z.ai | A tool whose `type` is not `function` | `tool_type_not_supported` |
| z.ai | A `file_url` content part (SMG does not fetch files for the model) | `content_part_not_supported` |
| z.ai (gRPC) | `max_completion_tokens` (or `max_tokens`) larger than the context window the selected worker advertises | `context_length_exceeded` |
| DeepSeek V4 | `reasoning_effort` or `thinking.effort` outside `none`, `minimal`, `low`, `medium`, `high`, `xhigh`, `max`; V4.1 also takes an integer budget from 1 to 100 | `reasoning_effort_not_allowed` |
| DeepSeek V4 | `chat_template_kwargs.thinking` or `enable_thinking` that is neither a boolean nor `null` | `invalid_thinking_override` |
| DeepSeek V4 | `chat_template_kwargs.thinking` and `enable_thinking` that disagree | `conflicting_thinking_overrides` |
| DeepSeek V4 | A forced `tool_choice` (`required`, a named function, or `allowed_tools` with mode `required`) while thinking is on | `tool_choice_not_supported` |

z.ai and DeepSeek V4 check both effort spellings, even the one that `thinking.effort`
shadows.

### Defaults and Rewrites

| Profile | Behavior |
|---------|----------|
| Kimi | Streaming requests get `stream_options.include_usage: true` unless set. Tools declared on `system` and `developer` messages join the request's `tools` for `tool_choice`, the tool-choice constraint, and tool-call parsing; a `system` message that only declares tools may omit `content` |
| Kimi K3 | Omitted fields get the contract values: `temperature` 1.0, `top_p` 0.95, `presence_penalty` 0, `frequency_penalty` 0, `n` 1 |
| MiniMax | `root` messages move, in order, to the front as `system` messages. On gRPC workers, responses are scanned for tool calls even when the request declares no tools |
| z.ai | `thinking.clear_thinking` becomes the `clear_thinking` chat-template variable unless `chat_template_kwargs` sets it. `tool_stream` is accepted and dropped (SMG streams tool-call deltas anyway). GLM-5.x, GLM-4.7, and GLM-4.6 get `temperature` 1.0 and `top_p` 0.95 when omitted |
| DeepSeek V4 | Efforts `minimal` → `low` and `medium` → `high`; `xhigh` → `high` on V4 (V4.1 keeps `xhigh`). Thinking is on unless turned off (`thinking.type: disabled`, effort `none`, or a template override), and the result is written to `chat_template_kwargs.thinking` when that is unset. Streams use DeepSeek's usage format (see [Stream Usage](#stream-usage)) |

---

## Error Responses

### Error Format

Errors that SMG generates use this envelope and repeat `code` in the `X-SMG-Error-Code`
response header:

```json
{
  "error": {
    "type": "Bad Request",
    "code": "context_length_exceeded",
    "message": "This model's maximum context length is 2048 tokens. However, your request has 2310 input tokens. Please reduce the length of the input.",
    "param": null
  }
}
```

`type` is the HTTP status's reason phrase (`Bad Request`, `Not Found`,
`Too Many Requests`, `Service Unavailable`, ...). Request parsing and validation failures
use a different shape and carry no `X-SMG-Error-Code` header:

| Failure | Body |
|---------|------|
| Body is not valid JSON, does not match the request schema, or lacks `Content-Type: application/json` | `{"error": {"message": "...", "type": "invalid_request_error", "code": "json_parse_error"}}` |
| A field range, a cross-field rule, or a [provider-profile](#provider-profiles) rule fails | `{"error": {"message": "...", "type": "invalid_request_error", "code": 400}}` |

A `401` from API-key authentication has an empty body. Errors from HTTP workers are
relayed with the worker's status and body. SMG never forwards a worker's
`X-SMG-Error-Code` header, so that header always means SMG itself produced the error.

### Error Codes

| Status | `code` | Returned when |
|--------|--------|---------------|
| 400 | `400` | Request validation failed: a field range, a cross-field rule (`stream_options` without `stream`, `top_logprobs` without `logprobs`, a `tool_choice` naming a missing tool, ...), a non-object `json_schema`, or a [provider-profile](#provider-profiles) rule |
| 400 | `json_parse_error` | The body could not be parsed into the request type |
| 400 | `context_length_exceeded` | gRPC: the longest prompt is longer than the context window the selected worker advertises (the tighter leg governs in PD, and each prompt of a batched completion counts on its own). Workers that advertise no window are not checked |
| 400 | `unsupported_content_part` | gRPC: a content part SMG cannot render |
| 400 | `audio_output_not_supported`, `unsupported_output_modality` | gRPC: `return_audio: true` or a non-`text` entry in `modalities`; gRPC workers produce text only |
| 400 | `multimodal_not_supported` | gRPC: the selected engine does not accept one of the request's input modalities |
| Engine-mapped | `start_generation_failed`, `prefill_worker_failed_to_start`, `decode_worker_failed_to_start` | gRPC: the engine did not start generation. The status follows the engine's gRPC status, for example invalid argument → `400` (not retried and not counted against the circuit breaker), resource exhausted → `429`, unavailable → `503`, deadline exceeded → `504`, internal → `500` |
| 401 | — | Missing or unknown API key (empty body) |
| 404 | `model_not_found` | No worker serves the model |
| 408 | `request_body_stalled` | HTTP router: a streamed request body stalled for `--stream-body-stall-timeout-secs` |
| 413 | `request_body_too_large` | HTTP router: a streamed request body exceeded `--max-payload-size` |
| 429 | `admission_queue_full`, `scheduler_queue_full`, `tenant_rate_limit_exceeded` | Admission control or rate limiting; see [Rate Limiting](#rate-limiting) |
| 500 | `internal_error` and stage-specific codes | A failure inside SMG; the code names the failing step |
| 502 | `upstream_response_too_large` | A worker's non-streaming response exceeded `--max-payload-size` |
| 503 | `no_available_workers` | Workers serve the model, but every one is unhealthy or has an open circuit breaker, or the routing policy selects none of them (HTTP and gRPC paths) |
| 503 | `worker_overload_protection_shed`, `admission_queue_timeout`, `scheduler_queue_timeout`, `scheduler_preempted` | Overload and admission sheds; see [Rate Limiting](#rate-limiting) |

On gRPC workers, an engine finish reason of `error` (outside the OpenAI set) becomes an
error response or stream error; clients never receive `"finish_reason": "error"`.

---

## Client Libraries

### Python (OpenAI SDK)

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:30000/v1",
    api_key="your-api-key"  # or "not-needed" if auth disabled
)

response = client.chat.completions.create(
    model="meta-llama/Llama-3.1-8B-Instruct",
    messages=[
        {"role": "user", "content": "Hello!"}
    ]
)

print(response.choices[0].message.content)
```

### JavaScript/TypeScript

```typescript
import OpenAI from 'openai';

const client = new OpenAI({
  baseURL: 'http://localhost:30000/v1',
  apiKey: 'your-api-key'
});

const response = await client.chat.completions.create({
  model: 'meta-llama/Llama-3.1-8B-Instruct',
  messages: [
    { role: 'user', content: 'Hello!' }
  ]
});

console.log(response.choices[0].message.content);
```

### cURL

```bash
curl http://localhost:30000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer your-api-key" \
  -d '{
    "model": "meta-llama/Llama-3.1-8B-Instruct",
    "messages": [{"role": "user", "content": "Hello!"}]
  }'
```

---

## Request Headers

Header names are case-insensitive. SMG reads the following request headers:

| Header | Purpose | Documented in |
|--------|---------|---------------|
| `Content-Type` | `application/json`, or `multipart/form-data` for `/v1/audio/transcriptions` | — |
| `Authorization` | `Bearer <key>`; required when `--api-key` or `--tenant-api-key` is set | [Authentication](../../concepts/security/authentication.md) |
| `x-api-key`, `x-goog-api-key` | The caller's own key for Anthropic (`x-api-key`) and Gemini (`x-goog-api-key`) provider workers. `/v1/responses` and `/v1/interactions` prefer it over `Authorization`, and `/v1/messages` forwards `x-api-key` as sent; `/v1/chat/completions` uses only `Authorization` | [External Providers](../../getting-started/external-providers.md) |
| `X-Request-ID` | Request ID for log correlation. SMG checks `x-request-id`, `x-correlation-id`, `x-trace-id`, and `request-id` in that order, and generates an ID when none is present. `--request-id-headers` replaces the list | [Logging](../../getting-started/logging.md) |
| `traceparent`, `tracestate` | W3C trace context. With `--enable-trace`, SMG continues the caller's trace and propagates it to workers | [Monitoring](../../getting-started/monitoring.md) |
| `X-SMG-Routing-Key` | Routing key for sticky sessions and the key-based policies (`manual`, `consistent_hashing`, `prefix_hash`). It is the default name in `--routing-key-headers` | [Sticky Sessions and Routing Keys](../../concepts/routing/sticky-sessions.md) |
| `X-SMG-Routing-Tokens` | Routing hint carrying the prompt's leading token IDs | [Routing Hint Headers](#routing-hint-headers) |
| `X-SMG-Target-Worker` | 0-based index of the worker to use among the model's available workers. Honored only by `consistent_hashing`; an index with no available worker fails with `503` | [Load Balancing](../../concepts/routing/load-balancing.md) |
| `X-SMG-Priority` | Priority class: `system`, `interactive`, `default`, or `bulk`. Read only when the priority scheduler is enabled | [Priority Scheduler](../priority-scheduler.md) |
| `X-SMG-Tenant-ID` | Tenant identity asserted by a trusted upstream, resolved as tenant key `header:<value>`. Read only with `--trust-tenant-header`, and only when API-key auth has not already identified the caller. `--tenant-header-name` renames it | [Tenant Rate Limiting](../tenant-rate-limiting.md) |
| `X-SMG-MCP` | `enabled` makes SMG run the MCP tool loop itself for `/v1/messages` requests with MCP toolsets sent to an Anthropic provider, instead of forwarding them as-is | [Messages API](messages.md) |
| Names set with `--storage-context-headers` | Copied into the storage hook request context | [Configuration Reference](../configuration.md) |

When SMG proxies a request to an HTTP worker, it forwards only these client headers: `Authorization`, `anthropic-version`, `anthropic-beta`, `X-Request-ID`, `X-Correlation-ID`, headers starting with `X-Request-ID-`, `traceparent`, `tracestate`, and `X-SMG-Routing-Key`.

### Routing Hint Headers

A client, or a proxy in front of SMG that has already tokenized the prompt, can pass the routing signal in a header so SMG does not need the request body to choose a worker. Hints affect only worker placement. A malformed or over-limit hint is ignored and the request routes as if the header were absent; it never causes an error.

| Header | Value | Limits | Effect |
|--------|-------|--------|--------|
| `X-SMG-Routing-Tokens` | The prompt's leading token IDs as comma-separated decimal integers from `0` to `4294967295`, with no spaces, signs, or empty items | At most 512 IDs and 4096 bytes | Replaces the token IDs and text SMG would take from the body when choosing a worker. `cache_aware` matches the hinted prefix against its token tree (its placement index with `--cache-index hash`) and records it there; `prefix_hash` hashes it. Read on the regular HTTP path only (not in PD mode or for gRPC workers), and never forwarded to workers |
| `X-SMG-Routing-Key` | An opaque key | Non-empty UTF-8, at most 128 bytes | Without `--routing-key-override`, `consistent_hashing` and `prefix_hash` hash the key instead of their other inputs, and `manual` pins it. With the override, every policy except `consistent_hashing` pins the key |

```bash
# X-SMG-Routing-Tokens carries the prompt's first token IDs
curl http://localhost:30000/v1/completions \
  -H "Content-Type: application/json" \
  -H "X-SMG-Routing-Tokens: 128000,791,4062,14198,39935" \
  -d @request.json
```

With a valid `X-SMG-Routing-Tokens` hint, the text-routing policies (`cache_aware`, `bucket`) no longer need the body to place the request, so a large body can stream to the worker instead of being buffered; see [Request Streaming](../../concepts/performance/request-streaming.md). When `--routing-key-override` is enabled, SMG always buffers the body to read its `rid`, and hints do not change that.

### Response Headers

| Header | When SMG sets it | Documented in |
|--------|------------------|---------------|
| `x-request-id` | On API responses: the request ID SMG used, whether received or generated | [Logging](../../getting-started/logging.md) |
| `x-smg-routed-worker-id` | On inference responses from HTTP workers: the URL of the worker that served the request, with an `@<rank>` suffix for data-parallel workers. For a prefill/decode pair it names the decode worker | [Sticky Sessions and Routing Keys](../../concepts/routing/sticky-sessions.md#routed-worker-header) |
| `X-SMG-Error-Code` | On errors the gateway generates in its standard error envelope, with the same value as `error.code`; request-validation `400` errors do not carry it. SMG drops this header from worker responses, so it always marks a gateway decision | — |
| `X-SMG-Preempted` | `true` on the `503` returned to a request preempted by the priority scheduler | [Priority Scheduler](../priority-scheduler.md) |
| `Retry-After` | On `429` and `503` responses that ask the client to back off: concurrency limits, worker overload, the priority scheduler, and tenant rate limits | [Rate Limiting](../../concepts/reliability/rate-limiting.md) |

---

## Rate Limiting

SMG does not send `X-RateLimit-*` headers. A request shed by admission control, rate
limiting, or overload protection gets `429` or `503` with the standard
[error envelope](#error-format), the `X-SMG-Error-Code` header, and usually a
`Retry-After` header in seconds:

| `code` | Status | `Retry-After` | Cause |
|--------|--------|---------------|-------|
| `admission_queue_full` | 429 | `2` | `--max-concurrent-requests` is reached and the admission queue is full or disabled |
| `admission_queue_timeout` | 503 | `2` | The request waited in the admission queue longer than `--queue-timeout-secs` |
| `scheduler_queue_full` | 429 | `2` | Priority scheduler: the request's class queue is full |
| `scheduler_queue_timeout` | 503 | `2` | Priority scheduler: the request waited longer than its class's queue timeout |
| `scheduler_preempted` | 503 | `1` | Priority scheduler: preempted by higher-priority traffic before its first token; also sets `X-SMG-Preempted: true` |
| `tenant_rate_limit_exceeded` | 429 | Seconds until the tenant's budget can admit the request; omitted when it never can | Tenant rate limiting |
| `worker_overload_protection_shed` | 503 | `--load-monitor-interval` (default `10`) | Every eligible worker is over its overload threshold (or the selected one crossed it just before dispatch), or a PD decode worker could not admit the request within `--pd-admission-wait-secs` |

Wait at least `Retry-After` seconds before retrying. For configuration, see
[Rate Limiting](../../concepts/reliability/rate-limiting.md),
[Tenant Rate Limiting](../tenant-rate-limiting.md),
[Priority Scheduling](../../concepts/reliability/priority-scheduling.md), and
[Overload Protection](../../concepts/reliability/overload-protection.md).
