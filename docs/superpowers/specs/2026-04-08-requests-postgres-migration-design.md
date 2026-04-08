# Migrate requests.py and sessions.py to SQLAlchemy for PostgreSQL Support

## Problem

SkyPilot supports both SQLite and PostgreSQL as its backing database. The main
state databases (global user state, managed jobs, serve state, recipes, KV cache)
have been migrated to SQLAlchemy with dialect-aware code. However, two
server-side modules still use raw SQLite:

- `sky/server/requests/requests.py` -- the API request queue (32 query functions)
- `sky/server/auth/sessions.py` -- auth session storage (~90 lines)

This means request data and auth sessions are stored in a local SQLite file even
when the server is configured for PostgreSQL, which prevents sharing this state
across multiple API server replicas.

## Approach

Full SQLAlchemy Table API migration of both modules, matching the existing
patterns in `global_user_state.py` and `jobs/state.py`. Both sync and async
paths are migrated: sync uses `orm.Session`, async uses
`sqlalchemy_async.AsyncSession`.

## Design

### 1. Table Definitions

Define tables using the SQLAlchemy Table API on shared `Base.metadata`.

**`requests_table`:**

```python
from sqlalchemy.ext import declarative

Base = declarative.declarative_base()

requests_table = sqlalchemy.Table(
    'requests', Base.metadata,
    sqlalchemy.Column('request_id', sqlalchemy.Text, primary_key=True),
    sqlalchemy.Column('name', sqlalchemy.Text),
    sqlalchemy.Column('entrypoint', sqlalchemy.Text),
    sqlalchemy.Column('request_body', sqlalchemy.Text),
    sqlalchemy.Column('status', sqlalchemy.Text),
    sqlalchemy.Column('created_at', sqlalchemy.Float),
    sqlalchemy.Column('return_value', sqlalchemy.Text),
    sqlalchemy.Column('error', sqlalchemy.LargeBinary),
    sqlalchemy.Column('pid', sqlalchemy.Integer),
    sqlalchemy.Column('cluster_name', sqlalchemy.Text),
    sqlalchemy.Column('schedule_type', sqlalchemy.Text),
    sqlalchemy.Column('user_id', sqlalchemy.Text),
    sqlalchemy.Column('status_msg', sqlalchemy.Text),
    sqlalchemy.Column('should_retry', sqlalchemy.Integer),
    sqlalchemy.Column('finished_at', sqlalchemy.Float),
    sqlalchemy.Column('file_mounts_blob_id', sqlalchemy.Text),
)
```

**Indexes** (including 2 partial indexes, supported by both SQLite and
PostgreSQL):

```python
sqlalchemy.Index('status_name_idx', requests_table.c.status,
                 requests_table.c.name,
                 sqlite_where=(requests_table.c.status.in_(['PENDING', 'RUNNING'])),
                 postgresql_where=(requests_table.c.status.in_(['PENDING', 'RUNNING'])))

sqlalchemy.Index('cluster_name_idx', requests_table.c.cluster_name,
                 sqlite_where=(requests_table.c.status.in_(['PENDING', 'RUNNING'])),
                 postgresql_where=(requests_table.c.status.in_(['PENDING', 'RUNNING'])))

sqlalchemy.Index('created_at_idx', requests_table.c.created_at)
```

**`auth_sessions_table`:**

```python
auth_sessions_table = sqlalchemy.Table(
    'auth_sessions', Base.metadata,
    sqlalchemy.Column('code_challenge', sqlalchemy.Text, primary_key=True),
    sqlalchemy.Column('token', sqlalchemy.Text, nullable=False),
    sqlalchemy.Column('created_at', sqlalchemy.Float, nullable=False),
)
```

### 2. Database Initialization and Engine Management

Replace `SQLiteConn` + `_init_db_within_lock()` with `DatabaseManager`.

Register constants in `migration_utils.py`:

```python
REQUESTS_DB_NAME = 'requests_db'
REQUESTS_VERSION = '001'
REQUESTS_LOCK_PATH = f'~/.sky/locks/.{REQUESTS_DB_NAME}.lock'
```

Create table function:

```python
def _create_table(engine: sqlalchemy.engine.Engine):
    # WAL mode for SQLite only
    if (engine.dialect.name == db_utils.SQLAlchemyDialect.SQLITE.value
            and not common_utils.is_wsl()):
        try:
            with orm.Session(engine) as session:
                session.execute(sqlalchemy.text('PRAGMA journal_mode=WAL'))
                session.commit()
        except sqlalchemy_exc.OperationalError as e:
            if 'database is locked' not in str(e):
                raise

    # Create tables + indexes via SQLAlchemy metadata
    db_utils.add_all_tables_to_db_sqlalchemy(Base.metadata, engine)

    # Run Alembic migrations
    migration_utils.safe_alembic_upgrade(
        engine,
        migration_utils.REQUESTS_DB_NAME,
        migration_utils.REQUESTS_VERSION)


def _post_init(engine: sqlalchemy.engine.Engine):
    """Validate SQLite version supports RETURNING (3.35+)."""
    if engine.dialect.name == db_utils.SQLAlchemyDialect.SQLITE.value:
        with orm.Session(engine) as session:
            row = session.execute(
                sqlalchemy.text('SELECT sqlite_version()')).fetchone()
            version_str = row[0]
            parts = version_str.split('.')
            major, minor = int(parts[0]), int(parts[1])
            if not ((major > 3) or (major == 3 and minor >= 35)):
                raise RuntimeError(
                    f'SQLite version {version_str} is not supported. '
                    'Please upgrade to SQLite 3.35.0 or later.')


_db_manager = db_utils.DatabaseManager(
    'requests_db', _create_table, post_init_fn=_post_init)
```

The `@init_db` / `@init_db_async` decorators are removed. Functions call
`_db_manager.get_engine()` or `await _db_manager.get_async_engine()` directly,
which handle lazy initialization internally.

**`sessions.py`** imports `_db_manager` from `requests.py` (both tables share
the same DB, matching the current behavior where they share
`API_SERVER_REQUEST_DB_PATH`).

**SQLite path change:** The DB file moves from `~/.sky/api_server/requests.db`
to `~/.sky/requests_db.db`. Since requests have 24hr retention GC and the server
wipes the DB on restart via `reset_db_and_logs()`, no data migration is needed.

### 3. Query Conversion Patterns

#### Upserts (INSERT OR REPLACE)

`_add_or_update_request_no_lock` and its async variant use dialect-specific
insert + `on_conflict_do_update`:

```python
def _add_or_update_request_no_lock(request: Request):
    engine = _db_manager.get_engine()
    with orm.Session(engine) as session:
        if engine.dialect.name == db_utils.SQLAlchemyDialect.SQLITE.value:
            insert_func = sqlite.insert
        else:
            insert_func = postgresql.insert

        values = request.to_dict()
        stmt = insert_func(requests_table).values(**values)
        stmt = stmt.on_conflict_do_update(
            index_elements=['request_id'],
            set_=values)
        session.execute(stmt)
        session.commit()
```

Async version uses `await _db_manager.get_async_engine()` +
`sql_async.AsyncSession`.

#### Conditional Insert (create_if_not_exists_async)

Uses `on_conflict_do_nothing()` + `rowcount`:

```python
async def create_if_not_exists_async(request: Request) -> bool:
    engine = await _db_manager.get_async_engine()
    async with sql_async.AsyncSession(engine) as session:
        if engine.dialect.name == db_utils.SQLAlchemyDialect.SQLITE.value:
            insert_func = sqlite.insert
        else:
            insert_func = postgresql.insert

        stmt = insert_func(requests_table).values(**request.to_dict())
        stmt = stmt.on_conflict_do_nothing(index_elements=['request_id'])
        result = await session.execute(stmt)
        await session.commit()
        return result.rowcount > 0
```

#### Selects

Raw SQL `SELECT ... WHERE request_id LIKE ?` becomes `sqlalchemy.select()`:

```python
def _get_request_no_lock(request_id, fields=None):
    engine = _db_manager.get_engine()
    columns = ([requests_table.c[f] for f in fields]
               if fields else [requests_table])
    with orm.Session(engine) as session:
        row = session.execute(
            sqlalchemy.select(*columns).where(
                requests_table.c.request_id.like(f'{request_id}%'))
        ).fetchone()
        ...
```

#### RequestTaskFilter.build_query()

Converted from string-building to returning a `sqlalchemy.Select` object:

```python
def build_query(self) -> sqlalchemy.Select:
    columns = ([requests_table.c[f] for f in self.fields]
               if self.fields else [requests_table])
    query = sqlalchemy.select(*columns)
    if self.status is not None:
        query = query.where(
            requests_table.c.status.in_([s.value for s in self.status]))
    if self.user_id is not None:
        query = query.where(requests_table.c.user_id == self.user_id)
    if self.cluster_names is not None:
        query = query.where(
            requests_table.c.cluster_name.in_(self.cluster_names))
    if self.exclude_request_names is not None:
        query = query.where(
            requests_table.c.name.notin_(self.exclude_request_names))
    if self.include_request_names is not None:
        query = query.where(
            requests_table.c.name.in_(self.include_request_names))
    if self.finished_before is not None:
        query = query.where(
            requests_table.c.finished_at < self.finished_before)
    if self.finished_after is not None:
        query = query.where(
            sqlalchemy.or_(
                requests_table.c.finished_at >= self.finished_after,
                requests_table.c.finished_at.is_(None)))
    if self.sort:
        query = query.order_by(requests_table.c.created_at.desc())
    if self.limit is not None:
        query = query.limit(self.limit)
    return query
```

#### Deletes

Replaces string interpolation with parameterized `in_()`:

```python
async def _delete_requests(request_ids: List[str]):
    engine = await _db_manager.get_async_engine()
    async with sql_async.AsyncSession(engine) as session:
        await session.execute(
            sqlalchemy.delete(requests_table).where(
                requests_table.c.request_id.in_(request_ids)))
        await session.commit()
```

#### sessions.py

`INSERT OR REPLACE` becomes `on_conflict_do_update`.
`DELETE ... RETURNING` becomes `sqlalchemy.delete().returning()` (supported by
both dialects in SQLAlchemy).

### 4. Request Data Model Change

Add `Request.to_dict()` method returning a dict mapping column names to values,
which SQLAlchemy's `.values(**dict)` consumes. The existing `to_row()` can remain
for any non-DB callers but the DB layer stops using it.

### 5. reset_db_and_logs()

The SQLite version check becomes dialect-aware: only run for SQLite, skip for
PostgreSQL (which always supports RETURNING). Moved into `_post_init` on the
`DatabaseManager`.

`clear_local_api_server_database()` becomes dialect-aware: delete the SQLite file
when in SQLite mode, truncate tables (or no-op, since Alembic creates fresh
tables) when in PostgreSQL mode.

### 6. Data Migration

No data migration is needed:

- Requests are ephemeral with 24hr retention GC.
- Auth sessions expire after `AUTH_SESSION_TIMEOUT_SECONDS`.
- `reset_db_and_logs()` wipes the DB on every server start.
- Switching from SQLite to PostgreSQL means deploying a new server instance with
  `SKYPILOT_DB_CONNECTION_URI` configured -- there is no live migration scenario.
- The old SQLite file at `~/.sky/api_server/requests.db` is cleaned up by the
  existing `clear_local_api_server_database()` call.

## Files Changed

| File | Change |
|------|--------|
| `sky/server/requests/requests.py` | Replace `SQLiteConn` with `DatabaseManager`, define `requests_table` + `auth_sessions_table` via SQLAlchemy Table API, convert all 32 query functions to SQLAlchemy, remove `sqlite3` import |
| `sky/server/auth/sessions.py` | Rewrite to import `_db_manager` from `requests.py`, replace raw SQL with SQLAlchemy queries |
| `sky/utils/db/migration_utils.py` | Add `REQUESTS_DB_NAME`, `REQUESTS_VERSION`, `REQUESTS_LOCK_PATH` constants |
| `sky/schemas/db/requests/001_initial_schema.py` | New Alembic migration for initial schema |
| `sky/setup_files/alembic.ini` | Add `[requests_db]` section pointing to `sky/schemas/db/requests/` |
| `sky/server/common.py` | Make `clear_local_api_server_database()` dialect-aware |
| `tests/unit_tests/test_sky/server/requests/test_requests.py` | Update `isolated_database` fixture to work with `DatabaseManager` instead of raw `_DB` |
| `tests/unit_tests/test_sky/server/requests/test_executor.py` | Same fixture update |

## Out of Scope

- `sky/skylet/job_lib.py` and `sky/skylet/configs.py` -- run on cluster VMs,
  SQLite is correct by design.
- `db_utils.SQLiteConn` class -- still needed by `job_lib.py`, not removed.
- `db_utils.safe_cursor()` / `add_column_to_table()` -- still needed by
  on-cluster modules, not removed.
- New PostgreSQL-specific optimizations (connection pooling tuning, etc.).
- Removing the `to_row()` method from `Request` (may have non-DB callers).

## Testing

- Existing unit tests in `test_requests.py` and `test_executor.py` are updated
  to use the new `DatabaseManager` pattern and should pass with SQLite.
- The existing PostgreSQL smoke tests (`/smoke-test --kubernetes --postgres`)
  will validate the PostgreSQL path end-to-end.
- Manual testing: `sky api stop && sky api start && sky status` to verify the
  request lifecycle works with the new DB layer.
