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

- `memory` (default): in-process, non-persistent
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

A new, empty database needs nothing more: the first start creates the tables at the latest schema version. An existing database that is behind a newer SMG build needs auto-migration; see [Schema migrations](#schema-migrations).

To keep the password off the command line, set `SMG_POSTGRES_DB_URL` or pass `--postgres-db-url-file` with a file holding the URL instead; see [Store URLs from the environment or a file](#store-urls-from-the-environment-or-a-file).

### Redis

```bash
smg \
  --worker-urls http://worker:8000 \
  --history-backend redis \
  --redis-url "redis://localhost:6379" \
  --redis-pool-max-size 16 \
  --redis-retention-days 30
```

Set `--redis-retention-days -1` for persistent retention.

To keep a password off the command line, set `SMG_REDIS_URL` or pass `--redis-url-file` with a file holding the URL instead; see [Store URLs from the environment or a file](#store-urls-from-the-environment-or-a-file).

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

On a new database, also set `DB_AUTO_MIGRATE=true` for the first start; see [Schema migrations](#schema-migrations).

!!! note "Python launcher flag names"
    These commands use the Rust `smg` binary's flag names. The Python launcher (`smg launch` from pip, and the container image) spells four of them differently: `--postgres-pool-max`, `--redis-pool-max`, `--oracle-username`, and `--oracle-connect-descriptor` (for `--oracle-dsn`). See [Python Launcher Differences](../reference/configuration.md#python-launcher-differences).

---

## Schema migrations

PostgreSQL and Oracle track a schema version and refuse to start while migrations are pending, printing the SQL to apply by hand. A new, empty PostgreSQL database has nothing pending: the first start creates its tables at the latest schema version. On PostgreSQL, gateways starting together on one database take turns at schema setup and all come up.

A new Oracle database starts with every migration pending, and an existing database on either backend can fall behind after an SMG upgrade. Set `DB_AUTO_MIGRATE=true` (or `1`) to let SMG apply pending migrations at startup:

```bash
DB_AUTO_MIGRATE=true smg launch \
  --worker-urls http://worker:8000 \
  --history-backend postgres \
  --postgres-db-url "postgres://user:password@localhost:5432/smg"
```

A `--schema-config` file can set `auto_migrate` instead. See [Chat History](../concepts/data/chat-history.md#schema-migrations).

---

## Required Settings by Backend

| Backend | Required settings |
|---------|-------------------|
| `memory` | none |
| `none` | none |
| `postgres` | the PostgreSQL connection URL, via `--postgres-db-url`, `SMG_POSTGRES_DB_URL`, or a [URL file](#store-urls-from-the-environment-or-a-file) |
| `redis` | the Redis connection URL, via `--redis-url`, `SMG_REDIS_URL`, or a [URL file](#store-urls-from-the-environment-or-a-file) |
| `oracle` | `--oracle-user`, `--oracle-password`, and one of (`--oracle-dsn`) or (`--oracle-wallet-path` + `--oracle-tns-alias`), each flag settable by its [`ATP_*` environment variable](#environment-variables) instead (omit user/password when `--oracle-external-auth` is set) |

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

`POSTGRES_POOL_MAX`, `POSTGRES_OP_TIMEOUT_SECS`, `REDIS_POOL_MAX`, `REDIS_RETENTION_DAYS`, and `REDIS_OP_TIMEOUT_SECS` are read only by the Python launcher, as defaults for its pool, timeout, and retention flags; the Rust binary takes those settings from flags alone.

### Store URLs from the environment or a file

A PostgreSQL or Redis connection URL can carry the store's password, and a URL on the command line is readable by other processes (`/proc/<pid>/cmdline`, process listings, a Deployment's args). Both the Rust `smg` binary and the Python launcher also read each URL from an environment variable, or from a file such as a mounted Kubernetes Secret:

| Setting | Flag | Environment | File flag | File environment |
|---------|------|-------------|-----------|------------------|
| PostgreSQL URL | `--postgres-db-url` | `SMG_POSTGRES_DB_URL` | `--postgres-db-url-file` | `SMG_POSTGRES_DB_URL_FILE` |
| Redis URL | `--redis-url` | `SMG_REDIS_URL` | `--redis-url-file` | `SMG_REDIS_URL_FILE` |

- The flag beats the environment. A URL (from the flag or the environment) together with a URL file for the same store is refused at startup rather than ranked.
- A URL file must hold the URL alone. Surrounding whitespace is dropped from the file's content and from a URL set in the environment, including the trailing newline a Secret made from a file carries. An empty file, or one holding more than one line, is refused.
- A URL file is read only for the selected `--history-backend`: a `--redis-url-file` passed to a `postgres` start is ignored, not opened.
- The Python launcher also accepts the older `POSTGRES_DB_URL` and `REDIS_URL` names when the `SMG_` names are unset; the Rust binary reads only the `SMG_` names.
- No URL is echoed: a refusal names the sources and the file path, never the value. The Rust binary's startup log (`CREDENTIAL SOURCES`) records whether each URL came from a flag, the environment, or a file; the Python launcher prints no such record.

---

## Verify

```bash
curl http://localhost:30000/health
```

A missing or invalid required setting (for example the DB URL or Oracle credentials) fails startup with a config validation error.

Every remote backend is contacted at startup, and SMG refuses to start when its store cannot be reached. For PostgreSQL and Redis, a store that is unreachable, or that accepts connections but does not answer, stops startup with an error naming the backend and the store URL (password masked). PostgreSQL and Oracle also create tables and check migrations at startup, so pending migrations stop startup too (see [Schema migrations](#schema-migrations)).

---

## Next Steps

- [Chat History Concepts](../concepts/data/chat-history.md) — backend architecture and tradeoffs
- [Configuration Reference](../reference/configuration.md#storage-configuration) — full storage flag reference
