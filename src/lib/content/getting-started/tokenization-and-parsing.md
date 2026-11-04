---
title: Tokenization and Parsing APIs
---

# Tokenization and Parsing APIs

SMG exposes utility endpoints for tokenization, detokenization, function-call parsing, and reasoning separation. They use the gateway's own tokenizers and the same legacy parser registries as the [gRPC pipeline](../concepts/architecture/grpc-pipeline.md). You can use them to see exactly how SMG tokenizes a prompt or splits a model's raw output.

To get the same token ids in Python without a running gateway, the `smg` package also ships the gateway's tokenizer as a library; see [Python Library](#python-library).

<div class="prerequisites" markdown>

#### Before you begin

- Completed the [Getting Started](index.md) guide
- For `/v1/tokenize` and `/v1/detokenize`, at least one loaded tokenizer: from `--model-path` or `--tokenizer-path` at startup, from a registered gRPC worker, or added with `POST /v1/tokenizers`

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

`model` is the name a tokenizer was loaded under: a worker's model ID, the `--model-path` or `--tokenizer-path` value the gateway started with, or the `name` given to `POST /v1/tokenizers`. A tokenizer ID also works, and an empty string or `"unknown"` picks the loaded tokenizer whose name sorts first. The endpoint doesn't add special tokens such as BOS.

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

`smg.tokenizer.Tokenizer` encodes and decodes in Python with the tokenizer SMG serves with: the same Rust code loads the checkpoint, so a training or evaluation pipeline gets the token ids the gateway feeds the model, without running a gateway. It ships in the `smg` package:

```bash
pip install smg
```

```python
from smg.tokenizer import Tokenizer

tokenizer = Tokenizer("/models/Qwen3-8B")           # what the gateway loads
ids = tokenizer.encode("Hello world")               # list[int]
text = tokenizer.decode(ids)
batch = tokenizer.encode_batch(["Hello", "World"])  # parallel, on a Rust thread pool
```

### Loading

`Tokenizer(source)` — or `Tokenizer.from_pretrained(source)`, which is the same — accepts a path or a model name:

| `source` | Behavior |
|---|---|
| Checkpoint directory or tokenizer file | Loaded in place |
| Hugging Face model id | Downloaded as the gateway downloads it: `HF_TOKEN`, `HF_HOME` and `HF_ENDPOINT` are honored, and the revision is always `main`. To pin a revision or run offline, download with `huggingface_hub.snapshot_download` and pass the directory |
| OpenAI model name (`gpt-4o`) | tiktoken's encoding, which ships with the package; nothing is downloaded |

A name that can only be a path — an absolute path, one starting with `.` or `~`, or one with more than two `/`-separated segments — raises `FileNotFoundError` when it doesn't exist, instead of being read as a model name.

To reproduce a gateway's token ids, load what that gateway loads: its `--tokenizer-path` if set, otherwise its `--model-path`. `config.json` and `generation_config.json` beside the tokenizer files supply the stop ids (`eos_token_ids` below). Builds must match too: `Tokenizer.smg_build` names the smg release and commit the library was built from, and the gateway prints its own with `--version-verbose`.

### Encode and Decode

| Method | Returns |
|---|---|
| `encode(text, *, add_special_tokens=True)` | `list[int]` |
| `encode_batch(texts, *, add_special_tokens=True)` | `list[list[int]]`, one per text |
| `decode(token_ids, *, skip_special_tokens=False)` | `str` |
| `decode_batch(sequences, *, skip_special_tokens=False)` | `list[str]`, one per sequence |

- `add_special_tokens=True` adds what the tokenizer's post-processor adds — a BOS for Llama-style checkpoints, nothing for most byte-level ones — as `transformers` does by default. SMG encodes `/v1/completions`, `/generate` and `/v1/tokenize` text with `add_special_tokens=False` and embedding input with `True`, so pass `False` to match the prompt ids of a generation request (and the output of [`/v1/tokenize`](#tokenize) above).
- Special-token text in the input, such as `<|im_end|>`, encodes to the special token.
- `decode` takes a list or tuple of ids, a numpy array, or a torch tensor (`decode_batch` also takes a 2-D one) and does not apply `transformers`' `clean_up_tokenization_spaces`.
- `skip_special_tokens` defaults to `False` — special tokens stay in the decoded text — while [`/v1/detokenize`](#detokenize) above defaults it to `true`.
- A legacy-format Llama-class `tokenizer.json` (one with a `Prepend` normalizer) is encoded as `transformers`' `LlamaTokenizer` class builds it — the pipeline the engines load — which can differ from `transformers` 4.x in leading-space pieces.

### Vocabulary and Special Tokens

| Member | Value |
|---|---|
| `vocab_size` | The base vocabulary without added tokens, as `transformers` reports `vocab_size` |
| `len(tokenizer)` | The vocabulary with added tokens |
| `bos_token`, `eos_token`, `pad_token`, `unk_token`, and their `*_id` counterparts | The checkpoint's special tokens; `None` when it has none |
| `special_tokens` | All roles (`bos_token`, `eos_token`, `unk_token`, `sep_token`, `pad_token`, `cls_token`, `mask_token`) plus `additional_special_tokens`: every added token marked special, sorted by id |
| `eos_token_ids` | The ids SMG stops generation at: `eos_token_id` from `config.json` and `generation_config.json` merged with the tokenizer's own EOS, deduplicated and sorted |
| `token_to_id(token)`, `id_to_token(id)` | Vocabulary lookups in the vocabulary's own spelling (`Ġthe` for byte-level vocabularies); `None` on a miss |
| `source` | The absolute path of the loaded checkpoint or Hub snapshot, or the OpenAI model name |
| `Tokenizer.smg_build` | The smg release and commit the library was built from |

### Threads and Worker Processes

An encode of less than 16 KiB of text, or a decode of fewer than 4096 ids, runs on the calling thread with the GIL held, like a single encode in Hugging Face's `tokenizers`; longer calls release it. The batch methods, long encodes, and loading run on the library's own thread pool, one per process:

- `SMG_TOKENIZER_THREADS` sets the pool size; it must be a positive integer.
- Without it, the pool has one thread when `TOKENIZERS_PARALLELISM` is off (read as Hugging Face's `tokenizers` reads it), and otherwise `RAYON_NUM_THREADS` threads, or one per CPU when that is unset too.

To encode many texts, prefer one `encode_batch` call over a Python thread per text. Each worker of a multi-process pipeline — a torch `DataLoader` worker, `datasets.map(num_proc=...)` — builds its own pool on first use, including a process forked after the parent used the tokenizer, so size `SMG_TOKENIZER_THREADS` for the workers that run beside each other.

### Library Errors

| Exception | Cause |
|---|---|
| `ValueError` | The tokenizer rejected the input; a batch failure names the item, as in `texts[3]: ...` |
| `RuntimeError` | The checkpoint didn't load, the thread pool couldn't start, or the tokenizer failed internally |
| `FileNotFoundError` | A path that isn't there |
| `TypeError` | A single string passed to `encode_batch`, which takes a sequence of texts |

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

Each request gets a fresh parser in its default state, with no prompt behind it. Parsers that start in reasoning (`deepseek_r1`, `qwen3_thinking`, `kimi_thinking`, `step3`, `minimax`) treat text before the end marker as reasoning even without a start marker. The others need the start marker, such as `<think>`, in `text`. The serving pipeline differs: there, a request with thinking on starts the parser in reasoning mode. An unknown parser name returns 400 with `{"error": "Unknown reasoning parser: <name>", "success": false}`.

---

## Auth Notes

- `/v1/tokenize` and `/v1/detokenize` are serving routes. When `--api-key` or `--tenant-api-key` is set, send one of those keys as a bearer token. The same admission control as inference requests applies (concurrency limit or priority scheduler).
- `/parse/function_call` and `/parse/reasoning` are control-plane routes. With [control-plane auth](control-plane-auth.md) configured (`--control-plane-api-keys`, or `--jwt-issuer` with `--jwt-audience`), they need a bearer credential with the admin role. Without it, they accept only the shared `--api-key`, never a tenant key. If only `--tenant-api-key` is set, they reject every request with 401, and with no keys configured at all they're open.

---

## Next Steps

- [gRPC Pipeline](../concepts/architecture/grpc-pipeline.md) — Parser formats, auto-detection, and per-model parser overrides
- [Gateway Extensions API](../reference/api/extensions.md) — Route groups for tokenizer management and parser utilities
- [Admin API Reference](../reference/api/admin.md) — Add, list, and remove tokenizers with `/v1/tokenizers`
