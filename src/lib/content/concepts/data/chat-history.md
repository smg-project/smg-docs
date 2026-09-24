---
title: Chat History
---

# Chat History

SMG supports multiple storage backends for persisting the conversations, conversation items, and stored responses behind the Responses and Conversations APIs, for analytics, debugging, and compliance.

---

## Overview

<div class="grid" markdown>

<div class="card" markdown>

### :material-database: Multiple Backends

Choose from in-memory, PostgreSQL, Redis, or Oracle based on your requirements.

</div>

<div class="card" markdown>

### :material-message-text: Conversation Tracking

Store complete conversation history including messages, tool calls, and reasoning.

</div>

<div class="card" markdown>

### :material-tune: Configurable Retention

Redis entries expire after `--redis-retention-days` (default 30 days). The other backends keep data until it's deleted (the in-memory backend, until the gateway restarts).

</div>

</div>

---

## Backend Comparison

| Backend | Use Case | Persistence | Scalability |
|---------|----------|-------------|-------------|
| `memory` | Development, testing | Process lifetime | Single instance |
| `none` | Stateless deployments | None | N/A |
| `postgres` | Production, self-hosted | Durable | High |
| `redis` | Caching, ephemeral storage | Configurable TTL | High |
| `oracle` | Enterprise, OCI deployments | Durable | High |

---

## Configuration

### Backend Selection

```bash
smg --history-backend <backend> [backend-specific options]
```

| Option | Default | Description |
|--------|---------|-------------|
| `--history-backend` | `memory` | Storage backend: `memory`, `none`, `oracle`, `postgres`, `redis` |

---

## Memory Backend

The default in-process storage. Suitable for development and testing.

```bash
smg --history-backend memory
```

<div class="grid" markdown>

<div class="card" markdown>

#### :material-check-circle: Advantages

- Zero configuration
- Fast access
- No external dependencies

</div>

<div class="card" markdown>

#### :material-close-circle: Limitations

- Data lost on restart
- Not shared across instances
- Memory grows with conversations

</div>

</div>

---

## None Backend

Disables history storage entirely. Use for stateless deployments where persistence isn't needed.

```bash
smg --history-backend none
```

**Use when**: Privacy requirements prohibit storing conversations, or external systems handle logging.

---

## PostgreSQL Backend

Production-ready storage with PostgreSQL.

### Configuration Options

| Option | Default | Description |
|--------|---------|-------------|
| `--postgres-db-url` | - | PostgreSQL connection URL |
| `--postgres-pool-max-size` | `16` | Maximum connection pool size |

### Connection URL Format

```
postgres://[user[:password]@]host[:port]/database[?param=value]
```

The scheme can also be `postgresql://`. SMG requires a host and a database name.

### Examples

<div class="grid" markdown>

<div class="card" markdown>

#### Basic Connection

```bash
smg --history-backend postgres \
  --postgres-db-url "postgres://user:password@localhost:5432/smg"
```

</div>

<div class="card" markdown>

#### With a Connect Timeout

```bash
smg --history-backend postgres \
  --postgres-db-url "postgres://user:password@db.example.com:5432/smg?connect_timeout=30"
```

</div>

</div>

!!! warning "No TLS to PostgreSQL"
    In v1.11.0, SMG connects to PostgreSQL without TLS, so the connection is unencrypted. `sslmode=disable` and `sslmode=prefer` (the default) both connect in plaintext, `sslmode=require` fails to connect, and any other `sslmode` value (such as `verify-full`) is rejected as an invalid URL. To encrypt traffic to the database, put a TLS tunnel between SMG and PostgreSQL, such as a sidecar proxy that connects to the database over TLS.

---

## Redis Backend

High-performance caching with optional persistence and TTL-based retention.

### Configuration Options

| Option | Default | Description |
|--------|---------|-------------|
| `--redis-url` | - | Redis connection URL |
| `--redis-pool-max-size` | `16` | Maximum connection pool size |
| `--redis-retention-days` | `30` | Data retention in days (-1 for persistent; write it as `--redis-retention-days=-1`, since the Rust CLI rejects a separate `-1` argument) |

### Connection URL Format

```
redis://[:password@]host[:port][/db]
```

!!! warning "No TLS to Redis"
    SMG v1.11.0 is built without Redis TLS support. A `rediss://` URL passes validation, but SMG fails to start because it cannot create the connection pool.

### Examples

<div class="grid" markdown>

<div class="card" markdown>

#### Basic Connection

```bash
smg --history-backend redis \
  --redis-url "redis://localhost:6379"
```

</div>

<div class="card" markdown>

#### With a Password

```bash
smg --history-backend redis \
  --redis-url "redis://:password@redis.example.com:6379"
```

</div>

<div class="card" markdown>

#### Persistent Storage

```bash
smg --history-backend redis \
  --redis-url "redis://localhost:6379" \
  --redis-retention-days=-1
```

</div>

</div>

---

## Oracle Backend

Enterprise-grade storage using Oracle Autonomous Database.

### Configuration Options

| Option | Environment Variable | Default | Description |
|--------|---------------------|---------|-------------|
| `--oracle-wallet-path` | `ATP_WALLET_PATH` | - | Path to ATP wallet directory |
| `--oracle-tns-alias` | `ATP_TNS_ALIAS` | - | TNS alias from tnsnames.ora |
| `--oracle-dsn` | `ATP_DSN` | - | Direct connection descriptor |
| `--oracle-user` | `ATP_USER` | - | Database username |
| `--oracle-password` | `ATP_PASSWORD` | - | Database password |
| `--oracle-external-auth` | `ATP_EXTERNAL_AUTH` | `false` | Use external (OS) authentication instead of username/password; leave `--oracle-user` and `--oracle-password` unset |
| `--oracle-pool-min` | `ATP_POOL_MIN` | `1` | Must be at least 1 and at most `--oracle-pool-max`. The pool opens connections on demand and doesn't hold a minimum open |
| `--oracle-pool-max` | `ATP_POOL_MAX` | `16` | Maximum connection pool size |
| `--oracle-pool-timeout-secs` | `ATP_POOL_TIMEOUT_SECS` | `30` | How long a request waits for a free pooled connection, in seconds. Must be greater than 0 |

`--oracle-dsn` takes precedence: with a DSN, SMG ignores the wallet and TNS alias. The Python launcher (`smg launch` from pip, and the container image) names two of these flags differently, `--oracle-username` and `--oracle-connect-descriptor`, and uses the TNS alias when both an alias and a DSN are given. See [Python Launcher Differences](../../reference/configuration.md#python-launcher-differences).

### Examples

<div class="grid" markdown>

<div class="card" markdown>

#### Using ATP Wallet

```bash
smg --history-backend oracle \
  --oracle-wallet-path /path/to/wallet \
  --oracle-tns-alias mydb_high \
  --oracle-user admin \
  --oracle-password "$ORACLE_PASSWORD"
```

</div>

<div class="card" markdown>

#### Using Direct DSN

```bash
smg --history-backend oracle \
  --oracle-dsn "(DESCRIPTION=(ADDRESS=(PROTOCOL=TCP)(HOST=db.example.com)(PORT=1521))(CONNECT_DATA=(SERVICE_NAME=myservice)))" \
  --oracle-user admin \
  --oracle-password "$ORACLE_PASSWORD"
```

</div>

</div>

---

## Schema Migrations

At startup, the PostgreSQL and Oracle backends create their tables if they don't exist, then compare the database's recorded schema version (the `_schema_versions` table) with the migrations SMG ships. SMG applies pending migrations only when auto-migration is on. Otherwise it refuses to start and prints the SQL to apply by hand. A new, empty database has pending migrations too, so turn auto-migration on for the first start:

```bash
DB_AUTO_MIGRATE=true smg launch --history-backend postgres \
  --postgres-db-url "postgres://user:password@localhost:5432/smg"
```

`DB_AUTO_MIGRATE=true` (or `1`) turns it on when no `--schema-config` file sets `auto_migrate`. In a schema config file, `auto_migrate: true` turns it on, and `version: <n>` marks migrations up to `<n>` as already applied. Redis and the in-memory backend have no migrations. See [Schema Migrations](../../reference/configuration.md#schema-migrations).

---

## What Gets Stored

### Conversations

Container for a sequence of interactions:

- Conversation ID
- Creation timestamp
- Metadata (the key-value object the client attaches)

### Conversation Items

Individual items within a conversation:

| Type | Description |
|------|-------------|
| **Messages** | User and assistant messages with content |
| **Reasoning** | Model reasoning/thinking steps |
| **Tool Calls** | Tool invocations and results |
| **MCP Calls** | MCP server interactions |
| **Function Calls** | Function calling results |

### Responses

Complete response records including:

- Input items
- Output (model response)
- Tool calls executed
- Model information
- Timestamps and metadata
- Token usage

---

## Recommended Configurations

<div class="grid" markdown>

<div class="card" markdown>

### :material-laptop: Development

In-memory for fast iteration.

```bash
smg --history-backend memory
```

</div>

<div class="card" markdown>

### :material-server-network: Production (Self-Hosted)

PostgreSQL for durable storage.

```bash
smg --history-backend postgres \
  --postgres-db-url "postgres://smg:$DB_PASSWORD@postgres:5432/smg" \
  --postgres-pool-max-size 32
```

</div>

<div class="card" markdown>

### :material-office-building: Enterprise (OCI)

Oracle for enterprise deployments.

```bash
smg --history-backend oracle \
  --oracle-wallet-path /etc/smg/wallet \
  --oracle-tns-alias smg_high \
  --oracle-user smg_app \
  --oracle-password "$ATP_PASSWORD" \
  --oracle-pool-max 32
```

</div>

<div class="card" markdown>

### :material-lightning-bolt: Caching Layer

Redis for high-performance ephemeral storage.

```bash
smg --history-backend redis \
  --redis-url "redis://:$REDIS_PASSWORD@redis.example.com:6379" \
  --redis-retention-days 7 \
  --redis-pool-max-size 64
```

</div>

</div>

---

## Troubleshooting

| Symptom | Cause | Solution |
|---------|-------|----------|
| Connection timeouts | Slow network | Increase pool timeout |
| Pool exhaustion | High concurrency | Increase pool size |
| Data not persisting | Wrong backend | Verify `--history-backend` setting |
| Redis data expiring | TTL too short | Increase `--redis-retention-days` |

### Pool Configuration

```bash
# PostgreSQL
--postgres-db-url "postgres://...?connect_timeout=30"
--postgres-pool-max-size 64

# Oracle
--oracle-pool-timeout-secs 60
--oracle-pool-max 64

# Redis
--redis-pool-max-size 64
```

---

## What's Next?

<div class="grid" markdown>

<div class="card" markdown>

### :material-shield-lock: Authentication

Secure access to your SMG deployment.

[Authentication →](../security/authentication.md)

</div>

<div class="card" markdown>

### :material-chart-box: Metrics Reference

Monitor storage backend performance.

[Metrics Reference →](../../reference/metrics.md)

</div>

<div class="card" markdown>

### :material-shield-check: High Availability

Deploy SMG in a highly available configuration.

[High Availability →](../architecture/high-availability.md)

</div>

</div>
