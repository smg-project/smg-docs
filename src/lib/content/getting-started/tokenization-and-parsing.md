---
title: Tokenization and Parsing APIs
---

# Tokenization and Parsing APIs

SMG exposes utility endpoints for tokenization, detokenization, function-call parsing, and reasoning separation. They use the gateway's own tokenizers and the same legacy parser registries as the [gRPC pipeline](../concepts/architecture/grpc-pipeline.md). You can use them to see exactly how SMG tokenizes a prompt or splits a model's raw output.

To get the same token IDs in Python without a running gateway — and to render a chat conversation into the exact prompt SMG feeds the model — the `smg` package also ships the gateway's tokenizer as a library; see [Python Library](#python-library).

<div class="prerequisites" markdown>

#### Before you begin

- Completed the [Getting Started](index.md) guide
- For `/v1/tokenize` and `/v1/detokenize`, at least one loaded tokenizer: from `--model-path` or `--tokenizer-path` at startup, from a registered gRPC or ZMQ worker, or added with `POST /v1/tokenizers`

</div>

| Method | Path | Purpose | Auth |
|--------|------|---------|------|
| `POST` | `/v1/tokenize` | Convert text to token IDs | Serving API key |
| `POST` | `/v1/detokenize` | Convert token IDs to text | Serving API key |
| `POST` | `/parse/function_call` | Extract tool calls from model output | Control plane |
| `POST` | `/parse/reasoning` | Separate reasoning from the final answer | Control plane |

---

## Tokenize

`POST /v1/tokenize`

| Field | Type | Description |
|-------|------|-------------|
| `model` | string | Name of a loaded tokenizer (required) |
| `prompt` | string or array of strings | Text to tokenize (required) |

`model` is the name a tokenizer was loaded under: a worker's model ID, the `--model-path` or `--tokenizer-path` value the gateway started with, or the `name` given to `POST /v1/tokenizers`. A tokenizer ID also works, and so does a [model alias](../reference/configuration.md#model-aliases), which selects the canonical model's tokenizer, as on the serving routes. An empty string or `"unknown"` picks the loaded tokenizer whose name sorts first. The endpoint doesn't add special tokens such as BOS.

Single input:

```bash
curl http://localhost:30000/v1/tokenize \
  -H "Content-Type: application/json" \
  -d '{
    "model": "Qwen/Qwen3-4B-Instruct-2507",
    "prompt": "Hello world"
  }'
```

```json
{"tokens": [9707, 1879], "count": 2, "char_count": 11}
```

Batch input:

```bash
curl http://localhost:30000/v1/tokenize \
  -H "Content-Type: application/json" \
  -d '{
    "model": "Qwen/Qwen3-4B-Instruct-2507",
    "prompt": ["Hello", "World"]
  }'
```

```json
{"tokens": [[9707], [10134]], "count": [1, 1], "char_count": [5, 5]}
```

| Response field | Description |
|----------------|-------------|
| `tokens` | Token IDs; one array per input for a batch |
| `count` | Number of tokens; an array for a batch |
| `char_count` | Number of characters in the input; an array for a batch |

---

## Detokenize

`POST /v1/detokenize`

| Field | Type | Description |
|-------|------|-------------|
| `model` | string | Name of a loaded tokenizer (required) |
| `tokens` | array of IDs, or array of arrays for a batch | Token IDs to decode (required) |
| `skip_special_tokens` | boolean | Drop special tokens from the output. Default `true` |

```bash
curl http://localhost:30000/v1/detokenize \
  -H "Content-Type: application/json" \
  -d '{
    "model": "Qwen/Qwen3-4B-Instruct-2507",
    "tokens": [9707, 1879],
    "skip_special_tokens": true
  }'
```

```json
{"text": "Hello world"}
```

A batch request returns `text` as an array of strings. For tiktoken-based tokenizers such as Kimi's, `skip_special_tokens` drops only the tokens marked `"special": true` in `tokenizer_config.json`. Control markers without that flag stay in the text.

### Errors

Errors use the body `{"error": {"message": "...", "type": "..."}}`:

| Status | `type` | Cause |
|--------|--------|-------|
| 400 | `tokenizer_not_found` | No tokenizer is loaded under that name (the message lists the loaded names), or none is loaded at all |
| 500 | `tokenization_error` / `detokenization_error` | The tokenizer failed on the input |

---

## Python Library

`smg.tokenizer.Tokenizer` encodes, decodes and renders chat prompts in Python with the tokenizer SMG serves with: the same Rust code loads the checkpoint, and chats render through the gateway's own request path, so a training or evaluation pipeline gets the token IDs the gateway feeds the model, without running a gateway. It ships in the `smg` package:

```bash
pip install smg
```

```python
from smg.tokenizer import Tokenizer

# What the gateway loads, and the model ID it serves it as.
tokenizer = Tokenizer("/models/Qwen3-8B", model="Qwen/Qwen3-8B")
ids = tokenizer.encode("Hello world")               # list[int]
text = tokenizer.decode(ids)
batch = tokenizer.encode_batch(["Hello", "World"])  # parallel, on a Rust thread pool

# The token IDs SMG would feed the model for this conversation.
messages = [{"role": "user", "content": "Hi"}]
prompt_ids = tokenizer.apply_chat_template(messages, add_generation_prompt=True)
```

### Loading

`Tokenizer(source, *, chat_template=None, model=None)` — or `Tokenizer.from_pretrained(...)`, which is the same — accepts a path or a model name as `source`:

| `source` | Behavior |
|---|---|
| Checkpoint directory or tokenizer file | Loaded in place |
| Hugging Face model ID | Downloaded as the gateway downloads it: `HF_TOKEN`, `HF_HOME` and `HF_ENDPOINT` are honored, and the revision is always `main`. To pin a revision or run offline, download with `huggingface_hub.snapshot_download` and pass the directory |
| OpenAI model name (`gpt-4o`) | tiktoken's encoding, which ships with the package; nothing is downloaded |

A name that can only be a path — an absolute path, one starting with `.` or `~`, or one with more than two `/`-separated segments — raises `FileNotFoundError` when it doesn't exist, instead of being read as a model name.

The two keyword arguments configure [chat rendering](#chat-rendering). `chat_template=` is a template file to render with instead of the checkpoint's own, as the gateway's `--chat-template`; a file that doesn't exist raises `ValueError`. `model=` is the model ID chat requests render under; see [The Model ID and the Provider Profile](#the-model-id-and-the-provider-profile) for its default and what it selects.

To reproduce a gateway's token IDs, load what that gateway loads: its `--tokenizer-path` if set, otherwise its `--model-path`. `config.json` and `generation_config.json` beside the tokenizer files supply the renderer and the stop IDs (`eos_token_ids` below), and `chat_template=` should be the same file the gateway's `--chat-template` gets, when it gets one. Builds must match too: `Tokenizer.smg_build` names the smg release and commit the library was built from, and the gateway prints its own with `--version-verbose`.

### Encode and Decode

| Method | Returns |
|---|---|
| `encode(text, *, add_special_tokens=True)` | `list[int]` |
| `encode_batch(texts, *, add_special_tokens=True)` | `list[list[int]]`, one per text |
| `decode(token_ids, *, skip_special_tokens=False)` | `str` |
| `decode_batch(sequences, *, skip_special_tokens=False)` | `list[str]`, one per sequence |

- `add_special_tokens=True` adds what the tokenizer's post-processor adds — a BOS for Llama-style checkpoints, nothing for most byte-level ones — as `transformers` does by default. SMG encodes `/v1/completions`, `/generate` and `/v1/tokenize` text with `add_special_tokens=False` and embedding input with `True`, so pass `False` to match the prompt IDs of a generation request (and the output of [`/v1/tokenize`](#tokenize) above). A chat prompt's special tokens come from its template; see [Chat Rendering](#chat-rendering) below.
- Special-token text in the input, such as `<|im_end|>`, encodes to the special token.
- `decode` takes a list or tuple of IDs, a numpy array, or a torch tensor (`decode_batch` also takes a 2-D one) and does not apply `transformers`' `clean_up_tokenization_spaces`.
- `skip_special_tokens` defaults to `False` — special tokens stay in the decoded text — while [`/v1/detokenize`](#detokenize) above defaults it to `true`.
- A legacy-format Llama-class `tokenizer.json` (one with a `Prepend` normalizer) is encoded as `transformers`' `LlamaTokenizer` class builds it — the pipeline the engines load — which can differ from `transformers` 4.x in leading-space pieces.

### Chat Rendering

`render_chat` turns a `/v1/chat/completions` request body into the prompt SMG would feed the model, through the gateway's own request path: the request is deserialized, normalized and validated as the HTTP layer does, and refused where the gRPC router refuses it; the provider profile's rewrites apply, message content is shaped for the template, tool-call `arguments` strings are parsed into objects for templates that serialize them, and `tool_choice`, `reasoning_effort` and `chat_template_kwargs` reach the template as the gateway hands them. The prompt is then encoded as the gateway's tokenize step encodes it. Which Jinja template or native renderer a checkpoint uses is described under [Chat Templates and Native Renderers](../concepts/architecture/grpc-pipeline.md#chat-templates-and-native-renderers).

```python
tokenizer = Tokenizer("/models/Qwen3-8B", model="Qwen/Qwen3-8B")

# The request body a client would send — logged bodies replay as they are.
weather = {"type": "object", "properties": {"city": {"type": "string"}}}
rendered = tokenizer.render_chat({
    "messages": [{"role": "user", "content": "What's the weather in Paris?"}],
    "tools": [{"type": "function", "function": {"name": "get_weather", "parameters": weather}}],
    "reasoning_effort": "low",
})
rendered["token_ids"]  # the IDs SMG feeds the model
rendered["text"]       # the prompt text
```

| Method | Returns |
|---|---|
| `render_chat(request)` | `RenderedChat` |
| `render_chat_batch(requests)` | `list[RenderedChat]`, rendered in parallel |
| `apply_chat_template(conversation, ...)` | `list[int]`, or `str` with `tokenize=False`; a list of conversations returns a list, rendered in parallel |

#### render_chat

`request` is the body a client would send to `/v1/chat/completions`, as a mapping or as its JSON text (`str` or `bytes`), so a logged request body replays as it is. A mapping may hold pydantic objects, such as the OpenAI SDK's messages (serialized by their set fields), and numpy values.

The gateway's request fields apply: `messages`, and optionally `tools`, `tool_choice`, `add_generation_prompt` (`True` when absent, as in the gateway), `continue_final_message`, `chat_template_kwargs`, `reasoning_effort`, `thinking` and the other fields the gateway reads. A tool call in the history needs its `id` and `type`, as the gateway requires. A request SMG would refuse with 400 raises `ValueError` with the gateway's reason; a batch failure names the item, as in `requests[1]: ...`.

The result is a `RenderedChat` dict:

| Field | Meaning |
|---|---|
| `text` | The prompt text |
| `token_ids` | The token IDs SMG feeds the model |
| `from_text` | Whether `token_ids` is the flat encode of `text` (`encode(text, add_special_tokens=False)`). `False` for a prompt encoded segment by segment, whose IDs no encode of the text reproduces: always on Kimi-K3; on DeepSeek V3.2 / V4 / V4.1 and on Mistral checkpoints that ship `tekken.json`, when request text spells a control token. Use `token_ids`, never re-encode `text` |
| `generation_prompt` | Whether the renderer wrote its generation header after the conversation |
| `continued_final_message` | Whether the renderer kept the final message open, as `continue_final_message` asks |
| `warnings` | The rewrites SMG made to the request while rendering it, which serving only logs: tool-call `arguments` that are not a JSON object rendered as `{}`, message extensions of another provider's profile dropped, deprecated fields migrated |

`render_chat` returns the rendering as SMG makes it, which can differ from what the request asks: a renderer with an encoder of its own writes the generation header after a final user or tool message whatever `add_generation_prompt` says (`generation_prompt` reports what it did), and SMG continues only a final assistant message (`continued_final_message` reports whether it did). A final assistant message the renderer cannot continue itself is continued the way SMG continues it: its text appended after the generation header (`generation_prompt` true, `continued_final_message` false).

#### apply_chat_template

`apply_chat_template(conversation, tools=None, *, add_generation_prompt=False, continue_final_message=False, tokenize=True, tool_choice=None, reasoning_effort=None, **template_kwargs)` takes `transformers`' shape, builds the chat request, and renders it the same way. It returns the token IDs, or the prompt text with `tokenize=False`. Every call renders under `Tokenizer.model`, so the tokenizer needs one (see below).

- `tools=None` declares no tools and `tools=[]` an empty list, which templates (and the gateway) tell apart. `tool_choice` and `reasoning_effort` are request fields, which the gateway also hands the template. Keyword arguments beyond the named ones reach the template the way `chat_template_kwargs` entries do (`enable_thinking=False`).
- A `transformers` argument SMG has no equivalent for raises `TypeError` with the reason, unless it is given its no-op value (`None` or `False`), which is ignored: `chat_template`, `documents`, `padding`, `truncation`, `max_length`, `return_tensors`, `return_dict`, `return_assistant_tokens_mask`, `tokenizer_kwargs`, `sanitize_control_tokens`. So does a keyword naming a variable the render fills itself — `messages`, `strftime_now`, or a special token the checkpoint defines — and a `continue_final_message` naming a field other than `"content"` (`True` and `"content"` both continue the final message's content).
- A rendering that does not do what `add_generation_prompt` or `continue_final_message` asks raises `ValueError`: a checkpoint whose own encoder writes the generation header after a final user or tool message, or a final message that is not the assistant's to continue. `render_chat` returns such a rendering as SMG makes it.
- The rewrites `render_chat` lists in `warnings` are issued as `smg.tokenizer.RenderWarning` (`warnings.simplefilter("error", RenderWarning)` turns them into errors).

#### The Model ID and the Provider Profile

A request renders under `Tokenizer.model`, as the router renders a request under the served model's ID: a request that names no model takes it, and one that names another model — a client's alias — is rewritten to it. The ID selects the [provider profile](../reference/api/openai.md#provider-profiles) whose request rewrites apply.

| Member | Value |
|---|---|
| `model` | The model ID chat requests render under. Defaults to the Hub ID of a Hub source or of a Hugging Face cache snapshot (`.../models--{org}--{name}/snapshots/...`), and to the name itself for an OpenAI model name; `None` for any other path |
| `provider_profile` | The profile `model` selects: `"deepseek"`, `"kimi"`, `"minimax"`, `"zai"` (GLM), `"openai"` or `"generic"`; `None` without a model ID |
| `chat_template` | The absolute path of the chat template file given at load, if any |

For a plain local checkpoint path, pass `model=`: the ID SMG serves the checkpoint as, or a vendor-named alias of it — the gateway's `--model-alias` registry is not read. Without one, a request renders under the model it names itself, which selects a profile by that name alone; a logged body carries the client's ID, perhaps an alias, so pass `model=` when replaying logs. A request that names no model then raises `ValueError`, and so does every `apply_chat_template` call, which builds a request that names none.

#### Not Reproduced

- **Media content** is refused: SMG expands each image, video or audio item into the model's own tokens when it serves the request, which a tokenizer alone cannot reproduce, so the library renders text-only conversations.
- **gpt-oss models** are refused: SMG renders them with Harmony, not the chat template.
- **Dates from `strftime_now`**: the template writes the local time at the render, as in serving. Setting `SOURCE_DATE_EPOCH` (whole seconds since the Unix epoch) in the environment the process starts with pins it to that instant — still in local time, so also set `TZ` for the same text on every host (Windows uses the system time zone regardless). The variable is read once, when the process loads its first chat template.

### Vocabulary and Special Tokens

| Member | Value |
|---|---|
| `vocab_size` | The base vocabulary without added tokens, as `transformers` reports `vocab_size` |
| `len(tokenizer)` | The vocabulary with added tokens |
| `bos_token`, `eos_token`, `pad_token`, `unk_token`, and their `*_id` counterparts | The checkpoint's special tokens; `None` when it has none |
| `special_tokens` | All roles (`bos_token`, `eos_token`, `unk_token`, `sep_token`, `pad_token`, `cls_token`, `mask_token`) plus `additional_special_tokens`: every added token marked special, sorted by ID |
| `eos_token_ids` | The IDs SMG stops generation at: `eos_token_id` from `config.json` and `generation_config.json` merged with the tokenizer's own EOS, deduplicated and sorted |
| `token_to_id(token)`, `id_to_token(id)` | Vocabulary lookups in the vocabulary's own spelling (`Ġthe` for byte-level vocabularies); `None` on a miss. On the built-in encoding an OpenAI model name loads, `token_to_id` is always `None`; `id_to_token` answers from the encoding itself — `None` only for an ID the encoding doesn't have or whose bytes aren't UTF-8 text |
| `source` | The absolute path of the loaded checkpoint or Hub snapshot, or the OpenAI model name |
| `fingerprint` | A digest of everything the tokenizer's output follows from; tokenizers with one fingerprint render and encode alike. See [The Fingerprint](#the-fingerprint) |
| `Tokenizer.smg_build` | The smg release and commit the library was built from |

### Threads and Worker Processes

An encode of less than 16 KiB of text, a decode of fewer than 4096 IDs, or a chat whose request and rendered prompt are both under 16 KiB runs on the calling thread with the GIL held, like a single encode in Hugging Face's `tokenizers`; longer calls release it. The batch methods (including `render_chat_batch` and a list of conversations in `apply_chat_template`), long encodes (a chat's long prompt among them), and loading run on the library's own thread pool, one per process:

- `SMG_TOKENIZER_THREADS` sets the pool size; it must be a positive integer.
- Without it, the pool has one thread when `TOKENIZERS_PARALLELISM` is off (read as Hugging Face's `tokenizers` reads it), and otherwise `RAYON_NUM_THREADS` threads, or one per CPU when that is unset too.

To encode many texts, prefer one `encode_batch` call over a Python thread per text. Each worker of a multi-process pipeline — a torch `DataLoader` worker, `datasets.map(num_proc=...)` — builds its own pool on first use, including a process forked after the parent used the tokenizer, so size `SMG_TOKENIZER_THREADS` for the workers that run beside each other.

Forking is safe while other threads are mid-call: `os.fork()` waits until every tokenizer call running in another thread has finished, because a child forked in the middle of one would inherit the library's internal locks held, with no thread left to release them, and hang at its own next call. The fork does not wait for a Hub download, which holds none of those locks. Only forks made through `os.fork()` — `multiprocessing`'s `fork` start method among them — wait; a fork made directly from C does not.

### Pickling

A `Tokenizer` pickles, so a worker process — in a `multiprocessing` pool, the `spawn` start method included, or in `datasets.map(num_proc=...)` — can take one as an argument. The pickle holds no tokenizer data, only what identifies it: `source`, `chat_template`, `model`, the Hub snapshot the source is in (if any), `Tokenizer.smg_build` and [`fingerprint`](#the-fingerprint). `copy.copy` and `copy.deepcopy` return the tokenizer itself, which is immutable.

Unpickling loads the tokenizer again from the pickled files — on the same host, from the same paths. Where the pickled path is missing (another host) and the source was a Hub model ID or a path in the Hugging Face cache, the snapshot is downloaded again by its Hub model ID (revision `main`, as at load) and the tokenizer loads from the download; a file source loads from its own path inside the fresh snapshot. A pickled plain path that is not there raises `FileNotFoundError`, and an OpenAI model name reloads from the encoding that ships with the package, with no files.

Unpickling refuses, with `RuntimeError`, a reload that would not behave like the pickled tokenizer:

- A pickle made by another smg build, whose token IDs may differ; the message names both builds.
- Files whose fingerprint changed: edited in place since the pickle, or a Hub snapshot only partly downloaded into this host's cache.
- A fresh download where the Hub's `main` moved on since the pickle: its files differ, or the pickled file is no longer in it.
- A DeepSeek V4.1 tokenizer loaded with `encoding/encoding.py` unpickles only where that file is too: the Hub download does not fetch it, so on another host make the pickled snapshot available yourself.

#### The Fingerprint

`Tokenizer.fingerprint` digests everything the tokenizer's output follows from: the smg build, `model`, the chat template file, a file source itself, and the checkpoint's tokenizer and config files — `tokenizer.json`, `tokenizer_config.json`, `special_tokens_map.json`, `vocab.json`, `merges.txt`, SentencePiece `.model` files, `.tiktoken` files, `tekken.json`, `.jinja` templates, `chat_template.json`, `config.json`, `generation_config.json` and DeepSeek V4.1's `encoding/encoding.py`. Two tokenizers with one fingerprint render and encode alike; the checkpoint's weights and other files don't enter it. The files are read and hashed once, at load, on the calling thread.

Because the fingerprint is in the pickle, a cache keyed on the pickled bytes — `datasets.map`'s — recomputes after a chat template or tokenizer file changes, instead of reusing results the old files produced.

### Library Errors

| Exception | Cause |
|---|---|
| `ValueError` | The tokenizer rejected the input, or a chat request SMG would refuse with 400 — raised with the gateway's reason; a batch failure names the item, as in `texts[3]: ...` or `requests[1]: ...`. `apply_chat_template` also raises it for a rendering that does not honor `add_generation_prompt` or `continue_final_message`, and loading raises it for a `chat_template=` file that doesn't exist |
| `RuntimeError` | The checkpoint didn't load, the thread pool couldn't start, or the tokenizer failed internally. Unpickling raises it for a pickle from another smg build, for files whose fingerprint changed, and for a Hub re-download without the pickled file; see [Pickling](#pickling) |
| `FileNotFoundError` | A `source` path that isn't there, at load or at unpickle |
| `TypeError` | A single string passed to `encode_batch` or a single request to `render_chat_batch`, which take sequences; a chat request that is not a mapping or its JSON text; an `apply_chat_template` argument SMG has no equivalent for, or a keyword naming a template variable the render fills itself |

---

## Parse Function Calls

`POST /parse/function_call`

Runs one tool-call parser over complete model output, the same way the gateway parses a non-streaming response under `--parser legacy`.

The serving pipeline reads with [Symphony tables](../concepts/architecture/grpc-pipeline.md#symphony-parsing) by default, but the `--parser` flag and the `X-SMG-Parser` header change only that pipeline. This endpoint always runs the legacy parser named in `tool_call_parser`, never a Symphony table; a name that only a table answers to is rejected as unknown.

| Field | Type | Description |
|-------|------|-------------|
| `text` | string | Raw model output (required) |
| `tool_call_parser` | string | Registered parser name (required) |
| `tools` | array | Tool definitions in OpenAI format (required; may be empty). Schema-aware parsers use them to convert argument types |

`tool_call_parser` accepts the 24 registered names: `passthrough`, `json`, `mistral`, `qwen`, `qwen_xml`, `qwen_coder`, `nemotron`, `pythonic`, `llama`, `deepseek`, `deepseek31`, `deepseek32`, `deepseek_v4`, `deepseek_v41`, `glm45_moe`, `glm47_moe`, `step3`, `sarashina`, `kimik2`, `kimi_k3`, `inkling`, `minimax_m2`, `minimax_m3`, `cohere`. The [gRPC Pipeline](../concepts/architecture/grpc-pipeline.md#tool-call-parsers) reference shows each parser's format.

If control-plane auth is configured, include an admin bearer token.

```bash
curl http://localhost:30000/parse/function_call \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer ${ADMIN_TOKEN}" \
  -d '{
    "text": "{\"name\":\"get_weather\",\"arguments\":{\"city\":\"SF\"}}",
    "tool_call_parser": "json",
    "tools": [
      {
        "type": "function",
        "function": {
          "name": "get_weather",
          "description": "Get weather",
          "parameters": {
            "type": "object",
            "properties": {
              "city": {"type": "string"}
            },
            "required": ["city"]
          }
        }
      }
    ]
  }'
```

```json
{
  "remaining_text": "",
  "tool_calls": [
    {"function": {"name": "get_weather", "arguments": "{\"city\":\"SF\"}"}}
  ],
  "success": true
}
```

`remaining_text` is the output with the tool calls taken out, and each `arguments` value is a JSON string. When the text holds no tool call, `tool_calls` is empty and `remaining_text` is the whole text. An unknown parser name returns 400 with `{"error": "Unknown tool parser: <name>", "success": false}`, and a parser failure returns 400 with a `Failed to parse function calls` message.

---

## Parse Reasoning

`POST /parse/reasoning`

Runs one reasoning parser over complete model output. Like `/parse/function_call`, it always runs the legacy parser named in `reasoning_parser`, never a Symphony table; the `--parser` flag (`symphony` by default) and the `X-SMG-Parser` header change only the serving pipeline.

| Field | Type | Description |
|-------|------|-------------|
| `text` | string | Raw model output (required) |
| `reasoning_parser` | string | Registered parser name (required) |

`reasoning_parser` accepts the 19 registered names: `base`, `passthrough`, `deepseek_r1`, `deepseek_v31`, `deepseek_v4`, `deepseek_v41`, `qwen3`, `qwen3_thinking`, `glm45`, `step3`, `kimi`, `kimi_k25`, `kimi_thinking`, `kimi_k3`, `minimax`, `minimax_m3`, `cohere_cmd`, `nano_v3`, `inkling`.

If control-plane auth is configured, include an admin bearer token.

```bash
curl http://localhost:30000/parse/reasoning \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer ${ADMIN_TOKEN}" \
  -d '{
    "text": "<think>internal reasoning</think>Final answer",
    "reasoning_parser": "deepseek_r1"
  }'
```

```json
{"normal_text": "Final answer", "reasoning_text": "internal reasoning", "success": true}
```

Each request gets a fresh parser in its default state, with no prompt behind it. Parsers that start in reasoning (`deepseek_r1`, `qwen3_thinking`, `kimi_thinking`, `step3`, `minimax`) treat text before the end marker as reasoning even without a start marker. The others need the start marker, such as `<think>`, in `text`. The exception is `kimi_k3`: raw Kimi-K3 output never carries `<|open|>think<|sep|>` (the prompt itself opens the think channel), so the parser splits `text` at `<|close|>think<|sep|>` alone — reasoning before the close, the answer after it. The serving pipeline differs: there, a request with thinking on starts the parser in reasoning mode. An unknown parser name returns 400 with `{"error": "Unknown reasoning parser: <name>", "success": false}`.

---

## Auth Notes

- `/v1/tokenize` and `/v1/detokenize` are serving routes. When `--api-key` or `--tenant-api-key` is set, send one of those keys as a bearer token. The same admission control as inference requests applies (concurrency limit or priority scheduler).
- `/parse/function_call` and `/parse/reasoning` are control-plane routes. With [control-plane auth](control-plane-auth.md) configured (`--control-plane-api-keys`, or `--jwt-issuer` with `--jwt-audience`), they need a bearer credential with the admin role. Without it, they accept only the shared `--api-key`, never a tenant key. If only `--tenant-api-key` is set, they reject every request with 401, and with no keys configured at all they're open.

---

## Next Steps

- [gRPC Pipeline](../concepts/architecture/grpc-pipeline.md) — Parser formats, auto-detection, and per-model parser overrides
- [Gateway Extensions API](../reference/api/extensions.md) — Route groups for tokenizer management and parser utilities
- [Admin API Reference](../reference/api/admin.md) — Add, list, and remove tokenizers with `/v1/tokenizers`
