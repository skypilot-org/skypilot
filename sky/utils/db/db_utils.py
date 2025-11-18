"""Utils for sky databases."""
import asyncio
import contextlib
import enum
import os
import pathlib
import sqlite3
import threading
import typing
from typing import Any, Callable, Dict, Iterable, Literal, Optional, Union

import aiosqlite
import aiosqlite.context
import sqlalchemy
from sqlalchemy import exc as sqlalchemy_exc
from sqlalchemy.ext import asyncio as sqlalchemy_async

from sky import sky_logging
from sky.skylet import constants

logger = sky_logging.init_logger(__name__)
if typing.TYPE_CHECKING:
    from sqlalchemy.orm import Session

# This parameter (passed to sqlite3.connect) controls how long we will wait to
# obtains a database lock (not necessarily during connection, but whenever it is
# needed). It is not a connection timeout.
# Even in WAL mode, only a single writer is allowed at a time. Other writers
# will block until the write lock can be obtained. This behavior is described in
# the SQLite documentation for WAL: https://www.sqlite.org/wal.html
# Python's default timeout is 5s. In normal usage, lock contention is very low,
# and this is more than sufficient. However, in some highly concurrent cases,
# such as a jobs controller suddenly recovering thousands of jobs at once, we
# can see a small number of processes that take much longer to obtain the lock.
# In contrived highly contentious cases, around 0.1% of transactions will take
# >30s to take the lock. We have not seen cases that take >60s. For cases up to
# 1000x parallelism, this is thus thought to be a conservative setting.
# For more info, see the PR description for #4552.
_DB_TIMEOUT_S = 60


class UniqueConstraintViolationError(Exception):
    """Exception raised for unique constraint violation.
    Attributes:
        value -- the input value that caused the error
        message -- explanation of the error
    """

    def __init__(self, value, message='Unique constraint violation'):
        self.value = value
        self.message = message
        super().__init__(self.message)

    def __str__(self):
        return (f'UniqueConstraintViolationError: {self.message} '
                f'(Value: {self.value})')


class SQLAlchemyDialect(enum.Enum):
    SQLITE = 'sqlite'
    POSTGRESQL = 'postgresql'


@contextlib.contextmanager
def safe_cursor(db_path: str):
    """A newly created, auto-committing, auto-closing cursor."""
    conn = sqlite3.connect(db_path, timeout=_DB_TIMEOUT_S)
    cursor = conn.cursor()
    try:
        yield cursor
    finally:
        cursor.close()
        conn.commit()
        conn.close()


def add_column_to_table(
    cursor: 'sqlite3.Cursor',
    conn: 'sqlite3.Connection',
    table_name: str,
    column_name: str,
    column_type: str,
    copy_from: Optional[str] = None,
    value_to_replace_existing_entries: Optional[Any] = None,
):
    """Add a column to a table."""
    for row in cursor.execute(f'PRAGMA table_info({table_name})'):
        if row[1] == column_name:
            break
    else:
        try:
            add_column_cmd = (f'ALTER TABLE {table_name} '
                              f'ADD COLUMN {column_name} {column_type}')
            cursor.execute(add_column_cmd)
            if copy_from is not None:
                cursor.execute(f'UPDATE {table_name} '
                               f'SET {column_name} = {copy_from}')
            if value_to_replace_existing_entries is not None:
                cursor.execute(
                    f'UPDATE {table_name} '
                    f'SET {column_name} = (?) '
                    f'WHERE {column_name} IS NULL',
                    (value_to_replace_existing_entries,))
        except sqlite3.OperationalError as e:
            if 'duplicate column name' in str(e):
                # We may be trying to add the same column twice, when
                # running multiple threads. This is fine.
                pass
            else:
                raise
    conn.commit()


def add_all_tables_to_db_sqlalchemy(
    metadata: sqlalchemy.MetaData,
    engine: sqlalchemy.Engine,
):
    """Add tables to the database."""
    for table in metadata.tables.values():
        try:
            table.create(bind=engine, checkfirst=True)
        except (sqlalchemy_exc.OperationalError,
                sqlalchemy_exc.ProgrammingError) as e:
            if 'already exists' in str(e):
                pass
            else:
                raise


def add_table_to_db_sqlalchemy(
    metadata: sqlalchemy.MetaData,
    engine: sqlalchemy.Engine,
    table_name: str,
):
    """Add a specific table to the database."""
    try:
        table = metadata.tables[table_name]
    except KeyError as e:
        raise e

    try:
        table.create(bind=engine, checkfirst=True)
    except (sqlalchemy_exc.OperationalError,
            sqlalchemy_exc.ProgrammingError) as e:
        if 'already exists' in str(e):
            pass
        else:
            raise


def add_column_to_table_sqlalchemy(
    session: 'Session',
    table_name: str,
    column_name: str,
    column_type: sqlalchemy.types.TypeEngine,
    default_statement: Optional[str] = None,
    copy_from: Optional[str] = None,
    value_to_replace_existing_entries: Optional[Any] = None,
):
    """Add a column to a table."""
    # column type may be different for different dialects.
    # for example, sqlite uses BLOB for LargeBinary
    # while postgres uses BYTEA.
    column_type_str = column_type.compile(dialect=session.bind.dialect)
    default_statement_str = (f' {default_statement}'
                             if default_statement is not None else '')
    try:
        session.execute(
            sqlalchemy.text(f'ALTER TABLE {table_name} '
                            f'ADD COLUMN {column_name} {column_type_str}'
                            f'{default_statement_str}'))
        if copy_from is not None:
            session.execute(
                sqlalchemy.text(f'UPDATE {table_name} '
                                f'SET {column_name} = {copy_from}'))
        if value_to_replace_existing_entries is not None:
            session.execute(
                sqlalchemy.text(f'UPDATE {table_name} '
                                f'SET {column_name} = :replacement_value '
                                f'WHERE {column_name} IS NULL'),
                {'replacement_value': value_to_replace_existing_entries})
    #sqlite
    except sqlalchemy_exc.OperationalError as e:
        if 'duplicate column name' in str(e):
            pass
        else:
            raise
    #postgresql
    except sqlalchemy_exc.ProgrammingError as e:
        if 'already exists' in str(e):
            pass
        else:
            raise
    session.commit()


def add_column_to_table_alembic(
    table_name: str,
    column_name: str,
    column_type: sqlalchemy.types.TypeEngine,
    server_default: Optional[str] = None,
    copy_from: Optional[str] = None,
    value_to_replace_existing_entries: Optional[Any] = None,
    index: Optional[bool] = None,
):
    """Add a column to a table using Alembic operations.

    This provides the same interface as add_column_to_table_sqlalchemy but
    uses Alembic's connection context for proper migration support.

    Args:
        table_name: Name of the table to add column to
        column_name: Name of the new column
        column_type: SQLAlchemy column type
        server_default: Server-side default value for the column
        copy_from: Column name to copy values from (for existing rows)
        value_to_replace_existing_entries: Default value for existing NULL
            entries
        index: If True, create an index on this column. If None, no index
            is created.
    """
    from alembic import op  # pylint: disable=import-outside-toplevel

    try:
        # Create the column with server_default if provided
        column = sqlalchemy.Column(column_name,
                                   column_type,
                                   server_default=server_default,
                                   index=index)
        op.add_column(table_name, column)

        # Handle data migration
        if copy_from is not None:
            op.execute(
                sqlalchemy.text(
                    f'UPDATE {table_name} SET {column_name} = {copy_from}'))

        if value_to_replace_existing_entries is not None:
            # Use parameterized query for safety
            op.get_bind().execute(
                sqlalchemy.text(f'UPDATE {table_name} '
                                f'SET {column_name} = :replacement_value '
                                f'WHERE {column_name} IS NULL'),
                {'replacement_value': value_to_replace_existing_entries})
    except sqlalchemy_exc.ProgrammingError as e:
        if 'already exists' in str(e).lower():
            pass  # Column already exists, that's fine
        else:
            raise
    except sqlalchemy_exc.OperationalError as e:
        if 'duplicate column name' in str(e).lower():
            pass  # Column already exists, that's fine
        else:
            raise


def drop_column_from_table_alembic(
    table_name: str,
    column_name: str,
):
    """Drop a column from a table using Alembic operations.

    Args:
        table_name: Name of the table to drop column from.
        column_name: Name of the column to drop.
    """
    from alembic import op  # pylint: disable=import-outside-toplevel

    # Check if column exists before trying to drop it
    bind = op.get_bind()
    inspector = sqlalchemy.inspect(bind)
    columns = [col['name'] for col in inspector.get_columns(table_name)]

    if column_name not in columns:
        # Column doesn't exist; nothing to do
        return

    try:
        op.drop_column(table_name, column_name)
    except (sqlalchemy_exc.ProgrammingError,
            sqlalchemy_exc.OperationalError) as e:
        if 'does not exist' in str(e).lower():
            pass  # Already dropped
        else:
            raise


class SQLiteConn(threading.local):
    """Thread-local connection to the sqlite3 database."""

    def __init__(self, db_path: str, create_table: Callable):
        super().__init__()
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path, timeout=_DB_TIMEOUT_S)
        self.cursor = self.conn.cursor()
        create_table(self.cursor, self.conn)
        self._async_conn: Optional[aiosqlite.Connection] = None
        self._async_conn_lock: Optional[asyncio.Lock] = None

    async def _get_async_conn(self) -> aiosqlite.Connection:
        """Get the shared aiosqlite connection for current thread.

        Typically, external caller should not get the connection directly,
        instead, SQLiteConn.{operation}_async methods should be used. This
        is to avoid txn interleaving on the shared aiosqlite connection.
        E.g.
        coroutine 1:
            A: await write(row1)
            B: cursor = await conn.execute(read_row1)
            C: await cursor.fetchall()
        coroutine 2:
            D: await write(row2)
            E: cursor = await conn.execute(read_row2)
            F: await cursor.fetchall()
        The A -> B -> D -> E -> C time sequence will cause B and D read at the
        same snapshot point when B started, thus cause coroutine2 lost the
        read-after-write consistency. When you are adding new async operations
        to SQLiteConn, make sure the txn pattern does not cause this issue.
        """
        # Python 3.8 binds current event loop to asyncio.Lock(), which requires
        # a loop available in current thread. Lazy-init the lock to avoid this
        # dependency. The correctness is guranteed since SQLiteConn is
        # thread-local so there is no race condition between check and init.
        if self._async_conn_lock is None:
            self._async_conn_lock = asyncio.Lock()
        if self._async_conn is None:
            async with self._async_conn_lock:
                if self._async_conn is None:
                    # Init logic like requests.init_db_within_lock will handle
                    # initialization like setting the WAL mode, so we do not
                    # duplicate that logic here.
                    self._async_conn = await aiosqlite.connect(self.db_path)
        return self._async_conn

    async def execute_and_commit_async(self,
                                       sql: str,
                                       parameters: Optional[
                                           Iterable[Any]] = None) -> None:
        """Execute the sql and commit the transaction in a sync block."""
        conn = await self._get_async_conn()

        if parameters is None:
            parameters = []

        def exec_and_commit(sql: str, parameters: Optional[Iterable[Any]]):
            # pylint: disable=protected-access
            conn._conn.execute(sql, parameters)
            conn._conn.commit()

        # pylint: disable=protected-access
        await conn._execute(exec_and_commit, sql, parameters)

    @aiosqlite.context.contextmanager
    async def execute_fetchall_async(self,
                                     sql: str,
                                     parameters: Optional[Iterable[Any]] = None
                                    ) -> Iterable[sqlite3.Row]:
        conn = await self._get_async_conn()
        return await conn.execute_fetchall(sql, parameters)

    async def execute_get_returning_value_async(
            self,
            sql: str,
            parameters: Optional[Iterable[Any]] = None
    ) -> Optional[sqlite3.Row]:
        conn = await self._get_async_conn()

        if parameters is None:
            parameters = []

        def exec_and_get_returning_value(sql: str,
                                         parameters: Optional[Iterable[Any]]):
            # pylint: disable=protected-access
            row = conn._conn.execute(sql, parameters).fetchone()
            conn._conn.commit()
            return row

        # pylint: disable=protected-access
        return await conn._execute(exec_and_get_returning_value, sql,
                                   parameters)

    async def close(self):
        if self._async_conn is not None:
            await self._async_conn.close()
        self.conn.close()


_max_connections = 0
_postgres_engine_cache: Dict[str, sqlalchemy.engine.Engine] = {}
_sqlite_engine_cache: Dict[str, sqlalchemy.engine.Engine] = {}

_db_creation_lock = threading.Lock()


def set_max_connections(max_connections: int):
    global _max_connections
    _max_connections = max_connections


def get_max_connections():
    return _max_connections


@typing.overload
def get_engine(
        db_name: Optional[str],
        async_engine: Literal[False] = False) -> sqlalchemy.engine.Engine:
    ...


@typing.overload
def get_engine(db_name: Optional[str],
               async_engine: Literal[True]) -> sqlalchemy_async.AsyncEngine:
    ...


def get_engine(
    db_name: Optional[str],
    async_engine: bool = False
) -> Union[sqlalchemy.engine.Engine, sqlalchemy_async.AsyncEngine]:
    """Get the engine for the given database name.

    Args:
        db_name: The name of the database. ONLY used for SQLite. On Postgres,
        we use a single database, which we get from the connection string.
        async_engine: Whether to return an async engine.
    """
    conn_string = None
    if os.environ.get(constants.ENV_VAR_IS_SKYPILOT_SERVER) is not None:
        conn_string = os.environ.get(constants.ENV_VAR_DB_CONNECTION_URI)
    if conn_string:
        if async_engine:
            conn_string = conn_string.replace('postgresql://',
                                              'postgresql+asyncpg://')
            # This is an AsyncEngine, instead of a (normal, synchronous) Engine,
            # so we should not put it in the cache. Instead, just return.
            return sqlalchemy_async.create_async_engine(
                conn_string, poolclass=sqlalchemy.NullPool)
        with _db_creation_lock:
            if conn_string not in _postgres_engine_cache:
                logger.debug('Creating a new postgres engine with '
                             f'maximum {_max_connections} connections')
                if _max_connections == 0:
                    _postgres_engine_cache[conn_string] = (
                        sqlalchemy.create_engine(
                            conn_string, poolclass=sqlalchemy.pool.NullPool))
                else:
                    _postgres_engine_cache[conn_string] = (
                        sqlalchemy.create_engine(
                            conn_string,
                            poolclass=sqlalchemy.pool.QueuePool,
                            pool_size=_max_connections,
                            max_overflow=max(0, 5 - _max_connections),
                            pool_pre_ping=True,
                            pool_recycle=1800))
            engine = _postgres_engine_cache[conn_string]
    else:
        assert db_name is not None, 'db_name must be provided for SQLite'
        db_path = os.path.expanduser(f'~/.sky/{db_name}.db')
        pathlib.Path(db_path).parents[0].mkdir(parents=True, exist_ok=True)
        if async_engine:
            # This is an AsyncEngine, instead of a (normal, synchronous) Engine,
            # so we should not put it in the cache. Instead, just return.
            return sqlalchemy_async.create_async_engine(
                'sqlite+aiosqlite:///' + db_path, connect_args={'timeout': 30})
        if db_path not in _sqlite_engine_cache:
            _sqlite_engine_cache[db_path] = sqlalchemy.create_engine(
                'sqlite:///' + db_path)
        engine = _sqlite_engine_cache[db_path]
    return engine
