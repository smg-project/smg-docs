---
title: Tokenizer Caching
---

# Tokenizer Caching

SMG implements a two-level tokenizer cache that reduces tokenization overhead for repeated content. It applies where the gateway tokenizes prompts itself: requests to gRPC and ZMQ workers, and `/v1/tokenize`. HTTP workers tokenize on the engine, so the cache doesn't affect their requests.

---

## Overview

<div class="grid" markdown>

<div class="card" markdown>

### :material-lightning-bolt: L0 Cache (Exact Match)

Hash-based O(1) lookup for complete tokenization results. Hits only when an entire input repeats exactly, such as identical prompts in a batch or a resent request.

</div>

<div class="card" markdown>

### :material-layers: L1 Cache (Prefix Match)

Boundary-aligned prefix matching that tokenizes only the suffix on hit. Ideal for multi-turn conversations with growing context.

</div>

<div class="card" markdown>

### :material-memory: Bounded Memory

L0 is capped by entry count and L1 by an approximate byte budget. An L0 entry holds the whole input and its encoding, so its size grows with prompt length.

</div>

<div class="card" markdown>

### :material-chart-line: Observable

Prometheus counters for lookups (hits and misses), evictions, and reused input bytes, per layer. There are no memory or cache-size metrics.

</div>

</div>

---

## Why Cache Tokenization?

The gateway tokenizes—converts text to token IDs—every request it sends to a gRPC or ZMQ worker. Each tokenization is fast, but the cost grows with prompt length and adds up at scale.

<div class="grid" markdown>

<div class="card" markdown>

### :material-robot: System Prompts

Same instructions sent with every request. L1 reuses the tokens up to the last special token the prompts share; L0 helps only when the whole prompt repeats.

</div>

<div class="card" markdown>

### :material-forum: Multi-Turn Conversations

Growing context with shared prefix. L1 cache tokenizes only new messages.

</div>

<div class="card" markdown>

### :material-file-document-multiple: RAG Applications

Queries that share leading context (instructions or documents placed first) reuse it through L1, up to the last special token before the prompts differ.

</div>

<div class="card" markdown>

### :material-tray-full: Batch Processing

Identical prompts repeated across a batch hit L0. Prompts that share a template but vary inside it can hit L1 only up to the last special token before the first difference.

</div>

</div>

---

## Cache Architecture

<div class="grid" markdown>

<div class="card" markdown>

### :material-lightning-bolt: L0 Cache (Exact Match)

**Gateway-side cache** storing complete tokenization results for exact string matches.

- Hash-based O(1) lookup, keyed on the whole input text
- Entry size grows with input length
- Approximate LRU eviction when full (samples 8 entries and evicts the least recently used)

**Best for**: Identical requests, repeated batch inputs

</div>

<div class="card" markdown>

### :material-layers: L1 Cache (Prefix Match)

**Gateway-side cache** storing tokens at special token boundaries for prefix reuse.

- Tokenize only the suffix on hit
- Cross-request deduplication
- Memory-bounded (configurable)
- Automatic boundary detection

**Best for**: Multi-turn conversations, growing contexts, incremental content

</div>

</div>

Each tokenizer SMG loads (from `--model-path` or `--tokenizer-path`, or for a gRPC or ZMQ worker's model) gets its own L0 and L1 caches with the configured limits. Tokenizers added through `POST /v1/tokenizers` are not cached. Decoding is never cached.

---

## Special Token Boundaries (L1)

L1 splits inputs right after every special token the tokenizer declares: its BOS, EOS, UNK, SEP, PAD, CLS, and MASK tokens, and every added token marked special. An input without special tokens always misses L1. For example:

| Model Family | Boundary Tokens | Example |
|--------------|-----------------|---------|
| **ChatML** (Qwen, Yi) | `<\|im_start\|>`, `<\|im_end\|>` | Each message boundary |
| **Llama 3** | `<\|begin_of_text\|>`, `<\|eot_id\|>`, `<\|start_header_id\|>` | Text start, turn end |
| **GPT** | `<\|endoftext\|>` | Document end |

---

## Multi-Turn Conversation Example

Consider how caching helps a typical chat application:

<div class="grid" markdown>

<div class="card" markdown>

#### Turn 1 (Cold)

```
System: You are a helpful assistant.
User: What is Python?
```

**L0**: Miss → Full tokenization
**L1**: Miss → Store at boundaries

</div>

<div class="card" markdown>

#### Turn 2 (Warm)

```
System: You are a helpful assistant.
User: What is Python?
Assistant: Python is a programming language...
User: How do I install it?
```

**L0**: Miss (text changed)
**L1**: **Hit!** → Only tokenize the text after the longest cached boundary

</div>

</div>

**Result**: Turn 2 tokenizes only the new messages, not the shared history.

---

## Configuration

### Model & Tokenizer Paths

#### `--model-path`

HuggingFace model ID or local path to load the tokenizer from.

| Option | `--model-path` |
|--------|----------------|
| Default | None |

**Usage**:

```bash
# HuggingFace model ID (downloads automatically)
smg --model-path meta-llama/Llama-3.1-8B-Instruct ...

# Local path to model directory
smg --model-path /models/llama-3.1-8b-instruct ...

# Local path to tokenizer.json file
smg --model-path /models/llama-3.1-8b-instruct/tokenizer.json ...
```

When pointing to a local directory, SMG looks for a HuggingFace
`tokenizer.json`, a `vocab.json` plus `merges.txt` pair, or a tiktoken file
(`tiktoken.model` or `*.tiktoken`). When
pulling from the HuggingFace Hub, SMG additionally falls back to
`tokenizer_config.json` and `vocab.json` in the downloaded snapshot if a
primary tokenizer file is not present.

#### `--tokenizer-path`

Explicit path to a tokenizer file. Overrides `--model-path` for tokenizer loading.

| Option | `--tokenizer-path` |
|--------|-------------------|
| Default | None |

**When to use**:

- When the tokenizer is stored separately from the model
- When using a custom tokenizer with a standard model
- When the model directory structure is non-standard

```bash
# Load the tokenizer from its own file (--tokenizer-path wins over --model-path)
smg \
  --model-path meta-llama/Llama-3.1-8B-Instruct \
  --tokenizer-path /custom/tokenizers/llama3-tokenizer.json \
  ...
```

---

### Chat Templates

Chat templates convert structured messages (system, user, assistant roles) into the prompt format expected by specific models. SMG uses Jinja2 templates, the same format used by HuggingFace Transformers.

#### `--chat-template`

Path to a Jinja2 chat template file.

| Option | `--chat-template` |
|--------|-------------------|
| Default | Auto-discovered from model |

**Template discovery priority**:

1. Explicit `--chat-template` path (highest priority)
2. `chat_template.json` in model directory
3. `chat_template.jinja` in model directory
4. Any `.jinja` file in model directory
5. `chat_template` field in `tokenizer_config.json`

#### Template Variables

Chat templates use Jinja2 syntax with access to:

| Variable | Description |
|----------|-------------|
| `messages` | Array of message objects with `role` and `content` |
| `add_generation_prompt` | Boolean to add assistant prompt prefix |
| `tools` | Optional array of tool definitions |
| `documents` | Optional array of document context |
| `bos_token`, `eos_token`, `unk_token`, `pad_token` | The tokenizer's special tokens, when it defines them |

Keys in a request's `chat_template_kwargs` are passed to the template as extra variables.

#### Template Examples

<div class="grid" markdown>

<div class="card" markdown>

**ChatML** (Qwen, Yi)

```jinja
{%- for message in messages %}
<|im_start|>{{ message.role }}
{{ message.content }}<|im_end|>
{% endfor %}
{%- if add_generation_prompt %}
<|im_start|>assistant
{% endif %}
```

</div>

<div class="card" markdown>

**Llama 3**

```jinja
<|begin_of_text|>{% for message in messages %}
<|start_header_id|>{{ message.role }}<|end_header_id|>

{{ message.content }}<|eot_id|>
{% endfor %}
{% if add_generation_prompt %}<|start_header_id|>assistant<|end_header_id|>

{% endif %}
```

</div>

</div>

---

### L0 Cache Configuration

The L0 cache stores complete tokenization results for exact string matches.

<div class="grid" markdown>

<div class="card" markdown>

#### `--tokenizer-cache-enable-l0`

Enable the L0 exact match cache.

| Option | `--tokenizer-cache-enable-l0` |
|--------|-------------------------------|
| Default | `false` |

</div>

<div class="card" markdown>

#### `--tokenizer-cache-l0-max-entries`

Maximum number of entries in the L0 cache.

| Option | `--tokenizer-cache-l0-max-entries` |
|--------|-----------------------------------|
| Default | `10000` |

</div>

</div>

### L1 Cache Configuration

The L1 cache stores tokenization results at special token boundaries.

<div class="grid" markdown>

<div class="card" markdown>

#### `--tokenizer-cache-enable-l1`

Enable the L1 prefix matching cache.

| Option | `--tokenizer-cache-enable-l1` |
|--------|-------------------------------|
| Default | `false` |

</div>

<div class="card" markdown>

#### `--tokenizer-cache-l1-max-memory`

Maximum memory for the L1 cache in bytes.

| Option | `--tokenizer-cache-l1-max-memory` |
|--------|----------------------------------|
| Default | `52428800` (50 MB) |

</div>

</div>

---

## Memory Planning

### L0 Cache Sizing

L0 is capped by entry count, not bytes. Each entry keeps the full input text and its encoding, so its size grows with the input: small for a short prompt, and roughly 2 MB per entry was observed for large inputs (smg-project/smg#2603). Size `--tokenizer-cache-l0-max-entries` from the number of distinct whole prompts that actually repeat in your traffic, and watch process memory when you raise it.

### L1 Cache Sizing

L1 cache is bounded by total memory:

| Memory | Recommended For |
|--------|-----------------|
| 25 MB | Memory-constrained environments |
| 50 MB | Standard deployments (default) |
| 100 MB | Multi-turn conversation heavy |
| 200 MB | Long context applications |

L1 keeps one entry per special-token boundary of each input it sees: the token IDs of the whole prefix up to that boundary. SMG charges each entry the prefix's length in bytes plus 4 bytes per token, so a long multi-turn prompt with many boundaries counts for many times its own length. When a new input's entries would exceed the budget, SMG evicts approximately least recently used entries (sampling 32 at a time) to make room. The budget is an estimate of cache contents, not a cap on process memory.

---

## Recommended Configurations

<div class="grid" markdown>

<div class="card" markdown>

### :material-flash: High-Throughput Chat

For workloads that resend identical prompts, such as batch jobs and client retries.

```bash
smg \
  --model-path meta-llama/Llama-3.1-8B-Instruct \
  --tokenizer-cache-enable-l0 \
  --tokenizer-cache-l0-max-entries 50000
```

**Expected**: hits on exact repeats of a whole prompt

</div>

<div class="card" markdown>

### :material-forum: Multi-Turn Conversations

For chat applications with varying conversation lengths.

```bash
smg \
  --model-path Qwen/Qwen2.5-7B-Instruct \
  --tokenizer-cache-enable-l0 \
  --tokenizer-cache-l0-max-entries 20000 \
  --tokenizer-cache-enable-l1 \
  --tokenizer-cache-l1-max-memory 104857600
```

**Expected**: L0 catches exact repeats, L1 accelerates prefix sharing

</div>

<div class="card" markdown>

### :material-memory: Memory-Constrained

For deployments with limited memory.

```bash
smg \
  --model-path meta-llama/Llama-3.1-8B-Instruct \
  --tokenizer-cache-enable-l0 \
  --tokenizer-cache-l0-max-entries 5000
```

**Expected**: a lower entry cap bounds L0 memory; hits only on exact repeats

</div>

<div class="card" markdown>

### :material-close-circle: No Caching

For stateless deployments or when memory is critical.

```bash
smg \
  --model-path meta-llama/Llama-3.1-8B-Instruct
# Caching is disabled by default
```

**Use when**: Diverse, unique requests dominate

</div>

</div>

---

## Complete Example

Production configuration with tokenizer and caching:

```bash
smg \
  --worker-urls grpc://worker1:50051 grpc://worker2:50051 \
  --policy cache_aware \
  --model-path meta-llama/Llama-3.1-70B-Instruct \
  --chat-template /templates/llama3.jinja \
  --tokenizer-cache-enable-l0 \
  --tokenizer-cache-l0-max-entries 25000 \
  --tokenizer-cache-enable-l1 \
  --tokenizer-cache-l1-max-memory 104857600 \
  --host 0.0.0.0 \
  --port 8080
```

---

## Monitoring & Observability

The gateway exports cache activity on its Prometheus `/metrics` endpoint
(smg-project/smg#2603):

| Metric | Labels | Meaning |
|--------|--------|---------|
| `smg_tokenizer_cache_lookups_total` | `layer` (`l0`, `l1`), `result` (`hit`, `miss`) | One outcome per lookup |
| `smg_tokenizer_cache_evictions_total` | `layer` | Entries removed to make room (clears and replacements excluded) |
| `smg_tokenizer_cache_reused_bytes_total` | `layer` | Input bytes served by hits: whole inputs for L0, matched prefixes for L1 |

The counters are totals across all tokenizers in the process, with no model
label, and a disabled layer reports zeros. An L0 hit never reaches L1, and L1
counts inputs without special-token boundaries as misses, so compute each
layer's hit ratio against its own lookups:

```promql
sum by (layer) (rate(smg_tokenizer_cache_lookups_total{result="hit"}[5m]))
/
sum by (layer) (rate(smg_tokenizer_cache_lookups_total[5m]))
```

See the [Metrics Reference](../../reference/metrics.md#tokenizer-cache-metrics)
for details.

### Sizing Signals to Watch

Use these signals when tuning `--tokenizer-cache-l0-max-entries` and
`--tokenizer-cache-l1-max-memory`:

- A low L0 hit ratio with a steady L0 eviction rate
  (`rate(smg_tokenizer_cache_evictions_total{layer="l0"}[5m])`) means more
  distinct inputs arrive than L0 holds; raise `max-entries` if the workload
  repeats inputs at all.
- L1 evictions mean the prefix cache is at its memory bound. Multi-turn chat
  traffic with growing context benefits from a larger L1 budget; see
  [L1 Cache Sizing](#l1-cache-sizing).
- `smg_tokenizer_cache_reused_bytes_total` separates many small prefix hits
  from reuse of large prompts: a high hit ratio with little reused volume
  saves little tokenization work.
- Each L0 entry keeps the full input text and its encoding, so entry size
  grows with prompt length; the out-of-memory investigation that led to these
  metrics (smg-project/smg#2603) observed entries of roughly 2 MB for large
  inputs. Watch `smg_allocator_allocated_bytes` when raising `max-entries`.

---

## Integration with Other Caching Layers

Tokenizer caching is part of SMG's **three-level caching strategy**:

| Layer | What's Cached | Benefit |
|-------|--------------|---------|
| **Tokenizer L0/L1** | Token IDs | Skip tokenization |
| **Router radix tree** | Prefix → worker mapping | Consistent routing decisions |
| **Worker KV cache** | Attention states | Skip prefill computation |

!!! info "Synergy with Cache-Aware Routing"
    With gRPC or ZMQ workers, the `cache_aware` policy routes on the token IDs the gateway produced for the request, so a tokenizer cache hit also shortens the work before routing. HTTP requests route on text (or on an `x-smg-routing-tokens` hint) and don't use the tokenizer cache.

---

## What's Next?

<div class="grid" markdown>

<div class="card" markdown>

### :material-routes: Cache-Aware Routing

Maximize KV cache hits with prefix-based worker affinity.

[Cache-Aware Routing →](../routing/cache-aware.md)

</div>

<div class="card" markdown>

### :material-chart-box: Metrics Reference

Complete list of cache-related metrics.

[Metrics Reference →](../../reference/metrics.md)

</div>

<div class="card" markdown>

### :material-scale-balance: Load Balancing

Compare all available routing policies.

[Load Balancing →](../routing/load-balancing.md)

</div>

</div>
