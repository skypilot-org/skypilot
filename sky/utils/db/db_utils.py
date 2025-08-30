"""Utils for sky databases."""
import contextlib
import enum
import sqlite3
import threading
import typing
from typing import Any, Callable, Optional

import sqlalchemy
from sqlalchemy import exc as sqlalchemy_exc

from sky import sky_logging

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
    #postgressql
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
    """
    from alembic import op  # pylint: disable=import-outside-toplevel

    try:
        # Create the column with server_default if provided
        column = sqlalchemy.Column(column_name,
                                   column_type,
                                   server_default=server_default)
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
