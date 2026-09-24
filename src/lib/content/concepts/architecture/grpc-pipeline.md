---
title: gRPC Pipeline
---

# gRPC Pipeline

When workers connect over gRPC, SMG runs the whole OpenAI-compatible serving pipeline itself. It renders the chat template, tokenizes, builds constrained-decoding grammars, detokenizes, extracts reasoning, parses tool calls, and runs MCP tool loops. Workers only run inference on token IDs. Workers on the [ZMQ direct backend](../../getting-started/zmq-workers.md) go through the same pipeline.

---

## Overview

<div class="grid" markdown>

<div class="card" markdown>

### :material-comment-processing: Chat Templates

Jinja2 chat templates for most models, plus native prompt encoders for DeepSeek-V3.2, V4 and V4.1 and Kimi-K3 checkpoints.

</div>

<div class="card" markdown>

### :material-memory: Tokenization Caching

Optional L0 (exact match) and L1 (prefix) caches skip re-encoding prompt content the gateway has already seen.

</div>

<div class="card" markdown>

### :material-brain: Reasoning Extraction

19 registered reasoning parsers move chain-of-thought into `reasoning_content` for DeepSeek, Qwen, Kimi, GLM, MiniMax, Nemotron, and more.

</div>

<div class="card" markdown>

### :material-function: Tool Call Parsing

24 registered tool-call parsers turn each model family's native call format into OpenAI `tool_calls`. MCP tools run in the gateway.

</div>

<div class="card" markdown>

### :material-lock-check: Constrained Decoding

XGrammar structural tags and JSON schemas make forced tool calls and structured outputs match the declared schemas.

</div>

</div>

---

## Pipeline Architecture

<div class="architecture-diagram" markdown>

![gRPC Pipeline Architecture](../../assets/images/grpc-pipeline.svg)

</div>

<div class="grid" markdown>

<div class="card" markdown>

### :material-lightning-bolt: gRPC Mode

**Gateway = Full Server**

SMG handles tokenization, chat templates, tool parsing, MCP loops, and detokenization. Workers run raw inference.

</div>

<div class="card" markdown>

### :material-swap-horizontal: HTTP Mode

**Gateway = Smart Proxy**

SMG handles routing, load balancing, and failover. Workers run full OpenAI-compatible servers.

</div>

</div>

### Responsibility Comparison

| Capability | gRPC Mode (Gateway) | HTTP Mode (Worker) |
|------------|--------------------|--------------------|
| Chat template | Gateway | Worker |
| Tokenization | Gateway (cached) | Worker |
| Constrained decoding (XGrammar) | Gateway builds, worker enforces | Worker |
| Cache-aware routing key | Token IDs from the gateway's tokenizer | Request text, or token IDs when the request is pre-tokenized |
| Reasoning extraction | Gateway | Worker |
| Tool call parsing | Gateway | Worker |
| MCP execution (Responses API) | Gateway | Not handled by the gateway |

The same pipeline serves Chat Completions, the Messages API and the Responses API. gpt-oss models use a separate Harmony pipeline. SMG selects it when a worker's model card lists the `GptOssForCausalLM` architecture or the `gpt_oss` model type, or when the model name contains `gpt-oss`. Harmony has its own encoding and output channels, so the chat templates and parser registries on this page don't apply to it.

---

## Parser Selection

Every request resolves one tool-call parser and one reasoning parser for its model. SMG checks these sources in order:

1. **Per-model override**: the `tool_parser` / `reasoning_parser` of the model's card on its workers.
2. **Gateway flag**: `--tool-call-parser` / `--reasoning-parser`, which applies to every model.
3. **Automatic detection** from the model name.

The gateway checks the flag values when it starts. An unknown `--tool-call-parser` or `--reasoning-parser` value stops startup with `unknown tool-call parser '<name>'` or `unknown reasoning parser '<name>'`. Names must match a registered parser exactly (`deepseek_r1`, not `deepseek-r1`).

### Per-Model Overrides

The process-wide flags suit a gateway that serves one model family. To serve several families from one gateway, give each model its own parsers when you register its workers. Either form works:

- **Labels**: `tool_parser` and `reasoning_parser` keys in the worker's `labels`.
- **Model card**: `tool_parser` and `reasoning_parser` fields on an explicit entry in the worker's `models` list. An explicit card keeps its own values; labels only fill the fields it leaves empty.

```bash
curl -X POST http://localhost:30000/workers \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer ${ADMIN_TOKEN}" \
  -d '{
    "url": "grpc://coder-worker:50051",
    "models": [
      {"id": "my-coder", "tool_parser": "qwen_xml", "reasoning_parser": "qwen3"}
    ]
  }'
```

Registration fails if an override names an unknown parser (`worker <url> declares unknown tool_parser '<name>' for model '<id>'`). Workers of the same model should agree. If they don't, for example during a rolling upgrade, SMG logs a warning at registration and uses the lexicographically smallest name. Kubernetes service discovery sets no parser labels, so discovered workers rely on the flags. See the [Admin API](../../reference/api/admin.md) for the worker endpoints.

### Automatic Detection

Without an override or a flag, SMG matches the request's `model` field against name patterns. It uses the canonical name, after [model aliases](../../reference/configuration.md) resolve. Matching is case-insensitive and finds the pattern anywhere in the name, so `Qwen/Qwen3.8-2.4T-A95B` matches `qwen3.8`. The two registries break ties differently:

- **Tool-call parsers**: the longest matching pattern wins, so `deepseek-v4.1` beats `deepseek-v4`. If nothing matches, SMG doesn't parse tool calls and returns the text as `content`.
- **Reasoning parsers**: the first match in the reference table's order wins, which is why the specific DeepSeek, Qwen-thinking, Kimi and MiniMax patterns come before the broad `qwen`, `kimi` and `minimax` ones. If nothing matches, reasoning stays in `content`.

The reference tables under [Reasoning Parsers](#reasoning-parsers) and [Tool Call Parsers](#tool-call-parsers) list every pattern.

!!! tip "Names the patterns don't cover"
    Detection only sees the name clients send. A model served as `my-model` carries no family name, and some official names miss a pattern: `zai-org/GLM-4.7` contains `glm-4.7`, not `glm47`, so it gets the `glm47_moe` tool parser but no reasoning parser. For these, set `--reasoning-parser glm45` (or the right parser for your model) or a per-model override.

---

## Chat Templates and Native Renderers

In gRPC mode the gateway builds the prompt text itself. Most models use a Jinja2 chat template. SMG takes it from a model card's `chat_template` path or `--chat-template`, then from a `chat_template.json` or `.jinja` file in the model directory, and finally from the `chat_template` field of `tokenizer_config.json`. Templates render with minijinja 2.24, so two Python Jinja2 behaviors work: conditional expressions inside keyword arguments (`namespace(name=x if x else '')`) compile, and booleans and none render as `True`, `False` and `None` (smg-project/smg#2277).

### Native Renderers

Some checkpoints ship a Python prompt encoder instead of a Jinja template. For these, SMG uses a native Rust port of that encoder, tested against the reference output. The tokenizer picks the renderer from the `config.json` next to the tokenizer files. The DeepSeek and Kimi-K3 renderers replace the Jinja template entirely, so `--chat-template` has no effect on those checkpoints.

| Renderer | Selected when `config.json` has | Thinking key (renderer default) | Native `reasoning_effort` values | Notes |
|----------|----------------------------------|--------------|----------------------------------|-------|
| DeepSeek-V3.2 | architecture `DeepseekV32ForCausalLM` | `thinking` (default off) | None | Tools are attached to the leading system or developer message |
| DeepSeek-V4 | architecture `DeepseekV4ForCausalLM` | `thinking` (default off) | `high`, `max` (original checkpoints); `low`, `high`, `max` (0731 checkpoints) | A native effort value turns thinking on |
| DeepSeek-V4.1 | architecture `DeepseekV41ForCausalLM` or `model_type: deepseek_v41` | `thinking`, or vLLM's `enable_thinking` alias (default on) | `low`, `high`, `xhigh`, `max`, or an integer budget from 1 to 100 | Continues a trailing assistant message natively with `continue_final_message`; reads tool-call `arguments` as written |
| Kimi-K3 | architecture `KimiK3ForConditionalGeneration` or `model_type: kimi_k3` | `thinking` (default on) | None | Encodes the prompt piece by piece, so control-token text inside a message stays text; reads tool-call `arguments` as written |
| Kimi-K2.5 | architecture `KimiK25ForConditionalGeneration` or `model_type: kimi_k25` | From the Jinja template | None | Keeps the Jinja template, but renders tool declarations as TypeScript with a port of the checkpoint's `tool_declaration_ts.py` |

DeepSeek-V4 checkpoints share one `config.json`, but the 0731 refresh changed what the effort levels render. SMG tells the revisions apart from the checkpoint's `encoding/encoding_dsv4.py`. If that file is missing, it looks for a `0731` marker in the model path, and otherwise assumes the original encoding (smg-project/smg#2080). A request goes through SMG's DeepSeek request profile when a `/`-separated part of its model name is `deepseek-v4-flash`, `deepseek-v4-pro`, `deepseek-v4.1-flash` or `deepseek-flash` (in any case), as in `deepseek-ai/DeepSeek-V4-Flash`. The profile turns thinking on unless the request turns it off, which overrides the V4 renderer's default (smg-project/smg#2671). Kimi-K3 appends a response-channel stub after the assistant header. The engine receives the stub, but the `prompt_tokens` that clients see leave it out, matching Moonshot's billing (smg-project/smg#2564).

### Thinking and Reasoning Effort

Reasoning controls reach the template as template variables. The request fields are documented in the [OpenAI-Compatible API](../../reference/api/openai.md).

- **Template kwargs**: `reasoning_effort` (or `thinking.effort`, which takes precedence), `tool_choice` and `response_format` are passed to the template as kwargs of the same name. An entry in `chat_template_kwargs` overrides any of them.
- **Thinking on or off**: `thinking.type` (`enabled` or `disabled`) sets the preference. Without it, a `reasoning_effort` of `"none"` or `"minimal"` means off. SMG writes the preference under the key the template actually reads: `enable_thinking` (Qwen3, GLM, Nemotron), `thinking` (DeepSeek, Kimi), or `thinking_mode` set to `"enabled"` or `"disabled"` (MiniMax-M3). A value you pass for that key in `chat_template_kwargs` wins.
- **Native effort names**: the DeepSeek-V4 and V4.1 renderers turn the values in the table above into their own effort prompt text.
- **Parser arming**: the reasoning parser follows the same decision. When thinking is on, it starts in reasoning mode, so the prompt and the parser agree.

---

## Reasoning Parsers

Reasoning parsers separate chain-of-thought from the final answer. Models that emit thinking tokens before their response need one.

### Configuration

| Option | `--reasoning-parser` |
|--------|---------------------|
| Default | [Per-model override](#per-model-overrides), otherwise auto-detected from the model name |

### Supported Parsers

| Model family | Parser |
|--------------|--------|
| DeepSeek-R1 | `deepseek_r1` |
| DeepSeek-V3.1 | `deepseek_v31` |
| DeepSeek-V4 | `deepseek_v4` |
| DeepSeek-V4.1 | `deepseek_v41` |
| Qwen3 and later hybrid-thinking Qwen models | `qwen3` |
| Qwen3 `-Thinking` checkpoints | `qwen3_thinking` |
| GLM-4.5, GLM-4.7, GLM-5.x | `glm45` |
| Kimi-K2 (Instruct) | `kimi` |
| Kimi-K2-Thinking | `kimi_thinking` |
| Kimi-K2.5 | `kimi_k25` |
| Kimi-K3 | `kimi_k3` |
| MiniMax-M2 | `minimax` |
| MiniMax-M3 | `minimax_m3` |
| Nemotron Nano, Nemotron Super, Nemotron-3 family | `nano_v3` |
| Command-R, Command-A | `cohere_cmd` |
| Step-3 | `step3` |
| Inkling | `inkling` |

### Complete Parser Reference

All 19 registered reasoning parsers, in auto-detection order (the first match wins):

| Parser | Auto-detected when the model name contains | Markers | Starts in reasoning |
|--------|--------------------------------------------|---------|---------------------|
| `deepseek_r1` | `deepseek-r1` | `<think>` / `</think>` | Yes |
| `deepseek_v41` | `deepseek-v4.1`, `deepseek_v41`, `deepseek-v41` | `<think>` / `</think>`; a `<｜DSML｜ calls>` tool block also ends reasoning | No |
| `deepseek_v4` | `deepseek-v4`, `deepseek_v4` | `<think>` / `</think>` | No |
| `deepseek_v31` | `deepseek-v3.1`, `deepseek-v3-1` | `<think>` / `</think>` | No |
| `qwen3_thinking` | `qwen3-thinking`, `qwen-thinking` | `<think>` / `</think>` | Yes |
| `qwen3` | `qwen3`, `qwen` | `<think>` / `</think>` | No |
| `glm45` | `glm45`, `glm47`, `glm-5` | `<think>` / `</think>` | No |
| `kimi_thinking` | `kimi-k2-thinking` | `<think>` / `</think>` | Yes |
| `kimi_k25` | `kimi-k2.5` | `<think>` / `</think>` | No |
| `kimi_k3` | `kimi-k3`, `kimi_k3` | XTML `think` channel (`<\|open\|>think<\|sep\|>` ... `<\|close\|>think<\|sep\|>`) | No |
| `kimi` | `kimi` | `◁think▷` / `◁/think▷` | No |
| `step3` | `step3` | `<think>` / `</think>` | Yes |
| `minimax_m3` | `minimax-m3`, `mm-m3` | `<mm:think>` / `</mm:think>`; stray `</mm:think>` markers before the answer are dropped | No |
| `minimax` | `minimax`, `minimax-m2`, `mm-m2` | `<think>` / `</think>` | Yes |
| `cohere_cmd` | `command-r`, `command-a`, `c4ai-command`, `cohere` | `<\|START_THINKING\|>` / `<\|END_THINKING\|>` | No |
| `nano_v3` | `nemotron-nano`, `nemotron-super`, `nano-v3`, `nemotron-3` | `<think>` / `</think>` | No |
| `inkling` | `inkling` | TML typed blocks such as `<\|content_thinking\|>`; decoded with special tokens kept | No |
| `base` | Not auto-detected | `<think>` / `</think>` | No |
| `passthrough` | Not auto-detected | None; all text stays in `content` | No |

"Starts in reasoning" is the parser's own default. The gateway also starts a parser in reasoning mode when a request has thinking on for a template or renderer with a thinking toggle, such as Qwen3, GLM, Nemotron, DeepSeek-V4 and V4.1, and Kimi-K3. The model's first tokens then count as reasoning.

### Output Format

Reasoning separation is on by default (`separate_reasoning` defaults to `true`). The extracted text is returned in `reasoning_content`:

```json
{
  "choices": [{
    "message": {
      "role": "assistant",
      "content": "The answer is 42.",
      "reasoning_content": "Let me think step by step..."
    }
  }]
}
```

Set `"separate_reasoning": false` in a request to leave the reasoning text in `content`. When streaming, a parser holds back text that might be the start of a marker, such as a trailing `</thi`. At the end of the stream it releases that text to the correct field instead of dropping it (smg-project/smg#2523).

---

## Tool Call Parsers

Tool call parsers turn each model's native tool-call syntax into OpenAI `tool_calls`.

### Configuration

| Option | `--tool-call-parser` |
|--------|----------------|
| Default | [Per-model override](#per-model-overrides), otherwise auto-detected from the model name |

### Supported Parsers

| Model family | Parser |
|--------------|--------|
| GPT, Claude, Gemini, Gemma, and other Llama and GLM models that emit plain JSON | `json` |
| Llama 3.2 | `llama` |
| Llama 4 | `pythonic` |
| Mistral, Mixtral | `mistral` |
| Qwen2.5, Qwen3 | `qwen` |
| Qwen3-Coder, Qwen3.5, Qwen3.6, Qwen3.8 | `qwen_xml` |
| Nemotron-3 family (Nano, Super, Ultra, 3.5) | `qwen_xml` (alias `nemotron`) |
| DeepSeek-V3 | `deepseek` |
| DeepSeek-V3.1, DeepSeek-V3.2-Exp | `deepseek31` |
| DeepSeek-V3.2 | `deepseek32` |
| DeepSeek-V4 | `deepseek_v4` |
| DeepSeek-V4.1 | `deepseek_v41` |
| GLM-4.5, GLM-4.6 | `glm45_moe` |
| GLM-4.7, GLM-5.x | `glm47_moe` |
| Kimi-K2 | `kimik2` |
| Kimi-K3 | `kimi_k3` |
| MiniMax-M2 | `minimax_m2` |
| MiniMax-M3 | `minimax_m3` |
| Command-R, Command-A | `cohere` |
| Step-3 | `step3` |
| Sarashina | `sarashina` |
| Inkling | `inkling` |

### Complete Parser Reference

All 24 registered tool-call parsers. For auto-detection, the longest matching pattern wins. The last column shows the constraint sent for a forced `tool_choice` when the parser is set by `--tool-call-parser` or an override (see [Constrained Decoding](#constrained-decoding-xgrammar)).

| Parser | Auto-detected when the model name contains | Format | Forced-call constraint |
|--------|--------------------------------------------|--------|------------------------|
| `passthrough` | Not auto-detected | No parsing; text is returned unchanged | JSON schema |
| `json` | `gpt-4`, `gpt-3.5`, `claude-`, `gemini-`, `gemma-`, `palm-`, `llama-`, `meta-llama-`, `glm-` | `{"name": ..., "arguments": {...}}`, or an array of these (`parameters` is also accepted) | JSON schema |
| `mistral` | `mistral-`, `mixtral-` | `[TOOL_CALLS] [{"name": ..., "arguments": {...}}]` | Structural tag |
| `qwen` | `qwen` | `<tool_call>{"name": ..., "arguments": {...}}</tool_call>` | JSON schema |
| `qwen_xml` | `qwen3.5`, `qwen3.6`, `qwen3.8`, `qwen3-coder`, `nemotron-3` | `<tool_call><function=NAME><parameter=KEY>VALUE</parameter></function></tool_call>` | JSON schema |
| `qwen_coder` | Not auto-detected (alias of `qwen_xml`) | Same as `qwen_xml` | JSON schema |
| `nemotron` | Not auto-detected (alias of `qwen_xml`) | Same as `qwen_xml` | JSON schema |
| `pythonic` | `llama-4`, `meta-llama-4`, `deepseek-` | `[get_weather(city="SF"), ...]` with Python literals | JSON schema |
| `llama` | `llama-3.2`, `meta-llama-3.2` | `<\|python_tag\|>{"name": ..., "parameters": {...}}` (the tag is optional) | JSON schema |
| `deepseek` | `deepseek-v3` | `<｜tool▁call▁begin｜>function<｜tool▁sep｜>NAME` followed by a fenced JSON block | JSON schema |
| `deepseek31` | `deepseek-v3.1`, `deepseek-v3.2-exp` | `<｜tool▁call▁begin｜>NAME<｜tool▁sep｜>{json}<｜tool▁call▁end｜>` | JSON schema |
| `deepseek32` | `deepseek-v3.2` | DSML: `<｜DSML｜invoke name="...">` and `<｜DSML｜parameter name="..." string="true\|false">` inside a `<｜DSML｜function_calls>` block | JSON schema |
| `deepseek_v4` | `deepseek-v4` | DSML inside a `<｜DSML｜tool_calls>` block | JSON schema |
| `deepseek_v41` | `deepseek-v4.1`, `deepseek-v41`, `deepseek_v41` | Spaced DSML: `<｜DSML｜ calls>`, `<｜DSML｜ invoke ...>`, `<｜DSML｜ parameter ...>` | Structural tag |
| `glm45_moe` | `glm-4.5`, `glm-4.6` | `<tool_call>NAME`, then `<arg_key>K</arg_key>` / `<arg_value>V</arg_value>` pairs on separate lines | JSON schema |
| `glm47_moe` | `glm-4.7`, `glm-5` | `<tool_call>NAME<arg_key>K</arg_key><arg_value>V</arg_value></tool_call>` | Structural tag, reasoning-aware |
| `step3` | `step3`, `step-3` | `<｜tool_call_begin｜>function<｜tool_sep｜><steptml:invoke name="...">` with `<steptml:parameter>` elements | JSON schema |
| `kimik2` | `kimi-k2` | `<\|tool_call_begin\|>functions.NAME:IDX<\|tool_call_argument_begin\|>{json}<\|tool_call_end\|>` | Structural tag |
| `kimi_k3` | `kimi-k3`, `kimi_k3` | XTML `tools` channel: `<\|open\|>call tool="NAME" index="N"<\|sep\|>` with `argument` elements | Structural tag |
| `inkling` | `inkling` | TML: `<\|message_model\|>NAME<\|content_invoke_tool_json\|>{"name": ..., "args": {...}}<\|end_message\|>` | Structural tag |
| `minimax_m2` | `minimax` | `<minimax:tool_call><invoke name="..."><parameter name="...">V</parameter></invoke></minimax:tool_call>` | JSON schema |
| `minimax_m3` | `minimax-m3`, `mm-m3` | Every tag is prefixed with `]<]minimax[>[`; parameters nest as XML elements | JSON schema |
| `cohere` | `command-r`, `command-a`, `c4ai-command`, `cohere` | `<\|START_ACTION\|>{"tool_name": ..., "parameters": {...}}<\|END_ACTION\|>` | JSON schema |
| `sarashina` | `sarashina` | `[{'name': ..., 'arguments': {...}}]` as a Python literal, with an optional `<\|tool_calls\|>` prefix | JSON schema |

### Parsing Behavior

- **Schema-aware arguments**: the XML-style parsers (`qwen_xml` and its aliases, `glm45_moe`, `glm47_moe`, `minimax_m2`, `minimax_m3`) convert argument values to the types the tool's JSON schema declares. `minimax_m3` also resolves properties declared under `oneOf`, `anyOf` or `allOf`. It turns an empty container element into `[]` or `{}`, and recovers the missing closing tag of an empty nested container (smg-project/smg#2370, smg-project/smg#2422, smg-project/smg#2567).
- **Special tokens**: when a request carries tools and `tool_choice` isn't `none`, the gateway decodes with special tokens kept, so the parser sees its trigger tokens. Under a JSON-schema constraint the output has no trigger tokens, so the request's own `skip_special_tokens` applies.
- **Streaming**: arguments stream as deltas. If a parser buffers text as a possible tool call and it never becomes one, the text is sent as `content` instead of being dropped. The `json`, `llama`, `mistral`, `qwen`, `cohere`, DeepSeek DSML and `minimax_m3` parsers do this (smg-project/smg#2271). `minimax_m3` releases a false tool-call start as soon as it can no longer match (smg-project/smg#2423). `qwen_xml` keeps each call's arguments separate, even when several calls arrive in one chunk or a value contains `}` (smg-project/smg#2490).
- **Tool call IDs**: `call_` plus 24 hex characters by default. For model names containing `kimi`, IDs follow the Kimi reference format instead: `functions.NAME:N`, or `NAME_N` when the name also contains `k3`. `N` counts tool calls across the whole conversation (smg-project/smg#2104).

### Tool Execution Flow

1. **Parse**: the resolved parser extracts calls from the model output. Text outside the calls stays in `content`.
2. **Return**: Chat Completions returns the calls in `tool_calls` with `finish_reason: "tool_calls"`, unless the engine stopped for `length` or an error. The Messages API returns `tool_use` blocks with `stop_reason: "tool_use"`.
3. **Execute (Responses API)**: on `/v1/responses`, the gateway runs calls to MCP tools itself, appends the results to the conversation, and resumes generation. The request's `max_tool_calls`, capped by the gateway, bounds the loop. See [MCP](../extensibility/mcp.md).

---

## Constrained Decoding (XGrammar)

When a request forces a tool call or asks for structured output, SMG attaches a **constraint** to the gRPC request. The engine compiles it with its guided-decoding backend (XGrammar) and enforces it token by token, so the output matches the declared schemas before SMG's parsers ever see it.

### Where It Runs in the Pipeline

In the regular gRPC pipeline, the tool constraint is generated **after the prompt is rendered and tokenized** (`model_gateway/src/routers/grpc/regular/stages/chat/preparation.rs`):

1. Filter tools by `tool_choice`
2. Render the prompt (chat template or native renderer)
3. Tokenize
4. Process multimodal inputs (see [Multimodal Pipeline](multimodal.md))
5. **Build the tool constraint** (`generate_tool_constraint`)
6. Build the stop decoder, then worker selection and dispatch

The Harmony pipeline (gpt-oss) builds its structural tags before encoding (`model_gateway/src/routers/grpc/harmony/stages/preparation.rs`). It rejects a request that combines a forced tool call with `response_format`.

### When a Constraint Is Sent

| Request | Constraint |
|---------|------------|
| No tools, or `tool_choice` is `auto` or `none` | None. The parser extracts calls from free-form output |
| `tool_choice` is `required`, names a function, or is `allowed_tools` with `mode: "required"` | A **structural tag** when `--tool-call-parser` or an override sets a parser that has one; otherwise a **JSON schema** |

| Type | Parsers | What it constrains |
|------|---------|--------------------|
| `structural_tag` | `mistral`, `deepseek_v41`, `glm47_moe`, `kimik2`, `kimi_k3`, `inkling` | The model's own format: trigger tokens, call framing, and argument JSON. The model-specific parser reads the result |
| `json_schema` | Fallback for every other case, auto-detected parsers included | Plain JSON. A named function constrains its `parameters`. `required` constrains an array of `{"name", "parameters"}` objects, with `$defs` merged across tools; conflicting definitions return 400 `invalid_tool_configuration` |

Two parsers add rules on top of their structural tag:

- **`glm47_moe`**: when thinking is on, GLM-4.7-family templates end the prompt inside `<think>`. SMG then wraps the GLM tag in a reasoning prefix: free text that must close with `</think>`, with tool-call tokens excluded, followed by the forced call. The model reasons first, and the call can't end up inside the thinking block where the reasoning parser would absorb it. On SGLang workers the gateway then turns off SGLang's own `require_reasoning` deferral for that request (smg-project/smg#2550). GLM requests without tools, or with `tool_choice` `auto` or `none`, carry no grammar (smg-project/smg#2549, smg-project/smg#2550).
- **`kimi_k3`**: each argument grammar carries the tool schema's `$defs` / `definitions` block, so parameters that use `$ref` compile instead of failing the request (smg-project/smg#2392).

### Combining with Structured Output

A request carries at most one constraint. `response_format` becomes a JSON schema (`json_object` is `{"type": "object"}`), and `text` adds nothing. The `regex` and `ebnf` request extensions each add a constraint of their own. SGLang, vLLM and TokenSpeed workers return 400 for a request that sets more than one of these. If a forced tool call applies as well, SGLang, TensorRT-LLM and TokenSpeed workers keep the output-format constraint and drop the tool one, while vLLM workers keep the tool constraint.

### Enforcement

The constraint travels in the request's `SamplingParams.constraint` oneof (`crates/grpc_client/proto/`). The oneof has `json_schema`, `regex`, `ebnf_grammar` (`grammar` on vLLM) and `structural_tag` fields, and vLLM also accepts `json_object` and `choice`. TensorRT-LLM receives the same constraint as `GuidedDecodingParams`. The engine needs a grammar backend to enforce it:

- **TokenSpeed**: start it with `--grammar-backend xgrammar`. Its default is none.
- **TensorRT-LLM**: set `guided_decoding_backend: xgrammar` in its `--extra_llm_api_options` file.
- **MLX**: doesn't support constraints and rejects such requests.

See [gRPC Workers](../../getting-started/grpc-workers.md) for the launch commands.

---

## Configuration

### Parser CLI Options

| Option | Default | Description |
|--------|---------|-------------|
| `--reasoning-parser` | Auto | Reasoning parser for every model without a per-model override |
| `--tool-call-parser` | Auto | Tool-call parser for every model without a per-model override. Also opts the parser into structural-tag constraints |
| `--chat-template` | None | Jinja chat template file. Without it, SMG looks in the model directory. The DeepSeek and Kimi-K3 native renderers ignore it |
| `--mcp-config-path` | None | Path to MCP server configuration file |

### MCP Integration

When MCP is configured, the gateway executes MCP tool calls on `/v1/responses`:

```bash
smg launch \
  --worker-urls grpc://worker:50051 \
  --model-path meta-llama/Llama-3.2-3B-Instruct \
  --tool-call-parser llama \
  --mcp-config-path /path/to/mcp.json
```

See the [MCP Guide](../extensibility/mcp.md) for detailed configuration.

---

## Recommended Configurations

<div class="grid" markdown>

<div class="card" markdown>

### :material-brain: Thinking Model

DeepSeek-R1 with reasoning extraction.

```bash
smg launch \
  --model-path deepseek-ai/DeepSeek-R1 \
  --reasoning-parser deepseek_r1 \
  --worker-urls grpc://worker1:50051
```

</div>

<div class="card" markdown>

### :material-function: Tool Calling Model

Llama 3.2 with MCP tool execution.

```bash
smg launch \
  --model-path meta-llama/Llama-3.2-3B-Instruct \
  --tool-call-parser llama \
  --mcp-config-path /config/mcp.json \
  --worker-urls grpc://worker:50051
```

</div>

<div class="card" markdown>

### :material-all-inclusive: Full Pipeline

Qwen3 with reasoning, tool calls, MCP, and tokenizer caching.

```bash
smg launch \
  --model-path Qwen/Qwen3-32B \
  --reasoning-parser qwen3 \
  --tool-call-parser qwen \
  --mcp-config-path /config/mcp.json \
  --tokenizer-cache-enable-l0 \
  --tokenizer-cache-enable-l1 \
  --worker-urls grpc://worker:50051
```

</div>

</div>

---

## Monitoring

### Pipeline Metrics

Metrics recorded by this pipeline carry `router_type="grpc"`:

| Metric | Description |
|--------|-------------|
| `smg_router_requests_total` | Requests routed, by `model`, `endpoint` and `streaming` |
| `smg_router_ttft_seconds` | Time to first token (streaming requests) |
| `smg_router_tpot_seconds` | Time per output token (streaming requests) |
| `smg_router_tokens_total` | Input and output tokens, by `token_type` (streaming requests) |
| `smg_mcp_tool_calls_total` | MCP tool invocations, by `model`, `tool_name` and `result` |
| `smg_mcp_tool_iterations_total` | Responses API tool-loop iterations |

See the [Metrics Reference](../../reference/metrics.md) for every metric and label.

### Debug Logging

`RUST_LOG`, when set, replaces the filter built from `--log-level`. Targets are crate and module paths. Keep a base level such as `warn,smg=info` so the rest of the gateway still logs:

```bash
# gRPC pipeline stages (preparation, worker selection, response processing)
RUST_LOG=warn,smg=info,smg::routers::grpc=debug smg launch ...

# Tool-call and reasoning parsers
RUST_LOG=warn,smg=info,tool_parser=debug,reasoning_parser=debug smg launch ...

# Tokenizers, chat templates and native renderers
RUST_LOG=warn,smg=info,llm_tokenizer=debug smg launch ...
```

---

## Troubleshooting

| Symptom | Cause | Solution |
|---------|-------|----------|
| Reasoning stays in `content` | No reasoning parser matched the model name, or the request set `separate_reasoning: false` | Set `--reasoning-parser` or a per-model override; compare the name with the patterns above |
| Tool calls come back as text in `content` | No tool parser matched, or the parser doesn't fit the model's format | Set `--tool-call-parser` or a `tool_parser` override for that model |
| Gateway exits at startup with `unknown tool-call parser` or `unknown reasoning parser` | The name isn't registered | Use a name from the reference tables above; names use underscores |
| Worker registration fails with `declares unknown tool_parser` | A label or model card names an unregistered parser | Fix the override name |
| Forced tool calls or `response_format` not enforced | The engine runs without a grammar backend | Start TokenSpeed with `--grammar-backend xgrammar`; give TensorRT-LLM `guided_decoding_backend: xgrammar` |
| MCP tools time out | Slow tool execution | Check MCP server configuration |

---

## What's Next?

<div class="grid" markdown>

<div class="card" markdown>

### :material-server: gRPC Workers

Launch commands for vLLM, SGLang, TensorRT-LLM, TokenSpeed, and MLX workers.

[gRPC Workers →](../../getting-started/grpc-workers.md)

</div>

<div class="card" markdown>

### :material-image-multiple: Multimodal Pipeline

How images and video are fetched, preprocessed, and sent to workers.

[Multimodal Pipeline →](multimodal.md)

</div>

<div class="card" markdown>

### :material-memory: Tokenizer Caching

Learn about two-level tokenizer caching for performance.

[Tokenizer Caching →](../performance/tokenizer-caching.md)

</div>

<div class="card" markdown>

### :material-puzzle: MCP Integration

Configure Model Context Protocol servers for tool execution.

[MCP →](../extensibility/mcp.md)

</div>

<div class="card" markdown>

### :material-cached: Cache-Aware Routing

Maximize KV cache hits with prefix-based routing.

[Cache-Aware Routing →](../routing/cache-aware.md)

</div>

</div>
