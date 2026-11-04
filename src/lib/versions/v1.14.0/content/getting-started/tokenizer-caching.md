---
title: Tokenizer Caching
---

# Tokenizer Caching

SMG provides a two-level tokenizer cache that reduces tokenization overhead for repeated content. It applies only where the gateway tokenizes: requests to gRPC and ZMQ workers, and `/v1/tokenize`. Tokenizers added through `POST /v1/tokenizers` are not cached.

<div class="prerequisites" markdown>

#### Before you begin

- Completed the [Getting Started](index.md) guide
- Using gRPC or ZMQ workers (tokenization happens at the gateway)
- A tokenizer the gateway can load: from `--model-path` or `--tokenizer-path`, or loaded automatically for each gRPC or ZMQ worker's model

</div>

---

## How It Works

| Cache Level | Strategy | Best For |
|-------------|----------|----------|
| **L0** (Exact Match) | Hash-based O(1) lookup for identical whole inputs | Identical prompts, such as repeated batch inputs or resent requests |
| **L1** (Prefix Match) | Boundary-aligned prefix matching, tokenizes only the suffix | Multi-turn conversations, growing contexts |

On a multi-turn conversation, L1 avoids re-tokenizing the entire history — only new messages are tokenized.

---

## Enable Caching

Both cache levels are disabled by default. Enable them with CLI flags:

### L0 Only (Exact Match)

Best for workloads that send the same whole prompt many times (batch processing, resent requests):

```bash
smg \
  --worker-urls grpc://worker:50051 \
  --model-path meta-llama/Llama-3.1-8B-Instruct \
  --tokenizer-cache-enable-l0 \
  --tokenizer-cache-l0-max-entries 10000
```

### L0 + L1 (Exact + Prefix Match)

Best for multi-turn chat applications:

```bash
smg \
  --worker-urls grpc://worker:50051 \
  --model-path meta-llama/Llama-3.1-8B-Instruct \
  --tokenizer-cache-enable-l0 \
  --tokenizer-cache-l0-max-entries 20000 \
  --tokenizer-cache-enable-l1 \
  --tokenizer-cache-l1-max-memory 104857600
```

---

## Configuration Reference

### L0 Cache

| Parameter | Default | Description |
|-----------|---------|-------------|
| `--tokenizer-cache-enable-l0` | `false` | Enable exact match cache |
| `--tokenizer-cache-l0-max-entries` | `10000` | Maximum number of cached entries |
| `--tokenizer-cache-l0-max-memory` | `268435456` (256 MB) | Byte budget for the entries' texts, token IDs and per-entry overhead |

Each entry keeps the full input text and its token IDs, so its size grows with prompt length.

### L1 Cache

| Parameter | Default | Description |
|-----------|---------|-------------|
| `--tokenizer-cache-enable-l1` | `false` | Enable prefix match cache |
| `--tokenizer-cache-l1-max-memory` | `52428800` (50 MB) | Maximum memory in bytes |

---

## Memory Planning

### L0 Sizing

L0 is bounded two ways: by entry count and by a byte budget that charges each entry its text, 4 bytes per token ID, and a fixed overhead. An input whose entry alone would take more than a quarter of the byte budget is not cached, and when either bound would be exceeded, approximately least-recently-used entries are evicted to make room. Size `--tokenizer-cache-l0-max-entries` from the number of distinct whole prompts that repeat in your traffic; `--tokenizer-cache-l0-max-memory` caps what they can hold together.

### L1 Sizing

| Memory | Recommended For |
|--------|-----------------|
| 25 MB | Memory-constrained environments |
| 50 MB | Standard deployments (default) |
| 100 MB | Multi-turn conversation heavy |
| 200 MB | Long context applications |

On a miss, L1 tokenizes the input once and stores an entry at every special-token boundary of the input. Each entry holds the token IDs of the whole prefix up to its boundary. On a hit it stores the prefix up to the input's own deepest boundary when that boundary lies past the match, so each turn of a conversation leaves the entry its next turn matches. When boundaries lie between the match and the deepest, it also stores a speculative entry at the boundary right after the match; eviction removes that entry ahead of matched entries unless another request matches it first.

Each entry is charged the prefix's length in bytes plus 4 bytes per token. Because a miss stores the cumulative prefix at every boundary, an input with many special tokens can charge the cache several times its own length; hits as well as misses grow the cache toward the memory bound, and an entry larger than the whole budget is not stored.

---

## Recommended Configurations

=== "High-Throughput Chat"

    For workloads that resend identical prompts:

    ```bash
    smg \
      --worker-urls grpc://worker:50051 \
      --model-path meta-llama/Llama-3.1-8B-Instruct \
      --tokenizer-cache-enable-l0 \
      --tokenizer-cache-l0-max-entries 50000
    ```

=== "Multi-Turn Conversations"

    For chat applications with growing conversation history:

    ```bash
    smg \
      --worker-urls grpc://worker:50051 \
      --model-path Qwen/Qwen2.5-7B-Instruct \
      --tokenizer-cache-enable-l0 \
      --tokenizer-cache-l0-max-entries 20000 \
      --tokenizer-cache-enable-l1 \
      --tokenizer-cache-l1-max-memory 104857600
    ```

=== "Memory-Constrained"

    Moderate benefit with minimal memory:

    ```bash
    smg \
      --worker-urls grpc://worker:50051 \
      --model-path meta-llama/Llama-3.1-8B-Instruct \
      --tokenizer-cache-enable-l0 \
      --tokenizer-cache-l0-max-entries 5000
    ```

---

## Monitor Cache Activity

SMG exports cache counters on its Prometheus endpoint, each labeled by `layer` (`l0` or `l1`): `smg_tokenizer_cache_lookups_total` (with a `result` label, `hit` or `miss`), `smg_tokenizer_cache_evictions_total`, and `smg_tokenizer_cache_reused_bytes_total`. Check each layer's hit ratio:

```promql
sum by (layer) (rate(smg_tokenizer_cache_lookups_total{result="hit"}[5m]))
/
sum by (layer) (rate(smg_tokenizer_cache_lookups_total[5m]))
```

A low hit ratio together with a steady eviction rate means the cache is too small for the workload. See the [Metrics Reference](../reference/metrics.md#tokenizer-cache-metrics) for the full definitions.

---

## Next Steps

- [Tokenizer Caching Concepts](../concepts/performance/tokenizer-caching.md) — Cache architecture, special token boundaries, monitoring metrics, PromQL queries
- [gRPC Workers](grpc-workers.md) — Enable gateway-level tokenization with gRPC mode
- [Load Balancing](load-balancing.md) — Choose a routing policy (cache-aware routing uses tokenizer results)
