---
title: Data Connections
---

# Data Connections

This guide helps you quickly enable conversation history storage with a backend that matches your environment.

<div class="prerequisites" markdown>

#### Before you begin

- Completed the [Getting Started](index.md) guide
- SMG running with at least one worker

</div>

---

## Choose a Backend

SMG supports these history backends via `--history-backend`:

- `memory` (default): in-process, non-persistent, with bounded retention
- `none`: disable history storage
- `postgres`: durable relational storage
- `redis`: fast key-value storage with optional retention
- `oracle`: enterprise Oracle backend

---

## Quick Start Commands

### Memory (default)

```bash
smg \
  --worker-urls http://worker:8000 \
  --history-backend memory
```

The memory backend is bounded: it keeps at most 10,000 stored responses and 100,000 conversation items (the oldest go first) and drops records after 24 hours (a conversation counts from its last use). `--history-memory-max-responses`, `--history-memory-max-items`, and `--history-memory-retention-secs` adjust the bounds; a retention of `0` keeps records until a count bound drops them.

### No history

```bash
smg \
  --worker-urls http://worker:8000 \
  --history-backend none
```

### PostgreSQL

```bash
smg \
  --worker-urls http://worker:8000 \
  --history-backend postgres \
  --postgres-db-url "postgres://user:password@localhost:5432/smg" \
  --postgres-pool-max-size 16
```

A new database is created at the latest schema version on first start; when upgrading an existing database, see [Schema migrations](#schema-migrations).

### Redis

```bash
smg \
  --worker-urls http://worker:8000 \
  --history-backend redis \
  --redis-url "redis://localhost:6379" \
  --redis-pool-max-size 16 \
  --redis-retention-days 30
```

Set `--redis-retention-days -1` for persistent retention. Any other value must be at least 1.

### Oracle

```bash
smg \
  --worker-urls http://worker:8000 \
  --history-backend oracle \
  --oracle-wallet-path /path/to/wallet \
  --oracle-tns-alias mydb_high \
  --oracle-user admin \
  --oracle-password "$ORACLE_PASSWORD"
```

!!! note "Python launcher flag names"
    These commands use the Rust `smg` binary's flag names. The Python launcher (`smg launch` from pip, and the container image) spells four of them differently: `--postgres-pool-max`, `--redis-pool-max`, `--oracle-username`, and `--oracle-connect-descriptor` (for `--oracle-dsn`). See [Python Launcher Differences](../reference/configuration.md#python-launcher-differences).

---

## Schema migrations

PostgreSQL and Oracle track a schema version and refuse to start while migrations are pending, printing the SQL to apply by hand. Set `DB_AUTO_MIGRATE=true` (or `1`) to let SMG apply pending migrations at startup:

```bash
DB_AUTO_MIGRATE=true smg launch \
  --worker-urls http://worker:8000 \
  --history-backend postgres \
  --postgres-db-url "postgres://user:password@localhost:5432/smg"
```

A new PostgreSQL database has nothing pending: SMG creates its tables at the latest schema version on first start. A new Oracle database starts with every migration pending, so turn auto-migration on for its first start. A `--schema-config` file can set `auto_migrate` instead. See [Chat History](../concepts/data/chat-history.md#schema-migrations).

---

## Required Flags by Backend

| Backend | Required flags |
|---------|----------------|
| `memory` | none |
| `none` | none |
| `postgres` | `--postgres-db-url` |
| `redis` | `--redis-url` |
| `oracle` | `--oracle-user`, `--oracle-password`, and one of (`--oracle-dsn`) or (`--oracle-wallet-path` + `--oracle-tns-alias`) (omit user/password when `--oracle-external-auth` is set) |

---

## Environment Variables

You can provide Oracle credentials via environment variables (both the Rust binary and the Python launcher read them):

- `ATP_WALLET_PATH`
- `ATP_TNS_ALIAS`
- `ATP_DSN`
- `ATP_USER`
- `ATP_PASSWORD`
- `ATP_EXTERNAL_AUTH`
- `ATP_POOL_MIN`
- `ATP_POOL_MAX`
- `ATP_POOL_TIMEOUT_SECS`

The Rust `smg` binary reads no environment variables for PostgreSQL or Redis. Only the Python launcher reads `POSTGRES_DB_URL`, `POSTGRES_POOL_MAX`, `POSTGRES_OP_TIMEOUT_SECS`, `REDIS_URL`, `REDIS_POOL_MAX`, `REDIS_RETENTION_DAYS`, and `REDIS_OP_TIMEOUT_SECS`, as defaults for its flags.

---

## Verify

```bash
curl http://localhost:30000/health
```

If startup fails, SMG returns a config validation error (for example missing DB URL or Oracle credentials). Every remote backend is contacted at startup: SMG refuses to start when its history store is unreachable, and PostgreSQL and Oracle also create their tables and check migrations then.

Once running, a store that becomes unavailable is answered `503` with error code `storage_unavailable` on the endpoints that store or read history. Each call to the PostgreSQL or Redis store is bounded by `--postgres-op-timeout-secs` or `--redis-op-timeout-secs` (default 5 seconds); a call past the bound gets the same `503`.

---

## Next Steps

- [Chat History Concepts](../concepts/data/chat-history.md) — backend architecture and tradeoffs
- [Configuration Reference](../reference/configuration.md#storage-configuration) — full storage flag reference
