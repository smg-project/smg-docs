---
title: Tokenization and Parsing APIs
---

# Tokenization and Parsing APIs

SMG exposes utility endpoints for tokenization, detokenization, function-call parsing, and reasoning separation. They use the gateway's own tokenizers and the same parser registries as the [gRPC pipeline](../concepts/architecture/grpc-pipeline.md). You can use them to see exactly how SMG tokenizes a prompt or splits a model's raw output.

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

`model` is the name a tokenizer was loaded under: a worker's model ID, the `--model-path` or `--tokenizer-path` value the gateway started with, or the `name` given to `POST /v1/tokenizers`. A tokenizer ID also works, and an empty string or `"unknown"` picks the first loaded tokenizer. The endpoint doesn't add special tokens such as BOS.

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

A batch request returns `text` as an array of strings. For tiktoken-based tokenizers such as Kimi's, `skip_special_tokens` drops only the tokens marked `"special": true` in `tokenizer_config.json`. Control markers without that flag stay in the text (smg-project/smg#2421).

### Errors

Errors use the body `{"error": {"message": "...", "type": "..."}}`:

| Status | `type` | Cause |
|--------|--------|-------|
| 400 | `tokenizer_not_found` | No tokenizer is loaded under that name (the message lists the loaded names), or none is loaded at all |
| 500 | `tokenization_error` / `detokenization_error` | The tokenizer failed on the input |

---

## Parse Function Calls

`POST /parse/function_call`

Runs one tool-call parser over complete model output, the same way the gateway parses a non-streaming response.

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
