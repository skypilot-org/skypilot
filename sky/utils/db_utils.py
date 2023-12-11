"""Utils for sky databases."""
import contextlib
import sqlite3
import threading
from typing import Any, Callable, Optional, Union

import psycopg2
from psycopg2 import sql


@contextlib.contextmanager
def safe_cursor(db_path: str):
    """A newly created, auto-committing, auto-closing cursor."""
    #If using PG, assume the db exists
    if db_path.startswith('postgres://'):
        conn = psycopg2.connect(db_path)
        conn.autocommit = True
    else:
        conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    try:
        yield cursor
    except (sqlite3.OperationalError, psycopg2.Error) as e:
        print(f'Error in safe_cursor: {e}')
    finally:
        cursor.close()
        conn.commit()
        conn.close()


def add_column_to_table(
    cursor: Union[sqlite3.Cursor, psycopg2.extensions.cursor],
    conn: Union[sqlite3.Connection, psycopg2.extensions.connection],
    table_name: str,
    column_name: str,
    column_type: str,
    copy_from: Optional[str] = None,
    default_value_to_replace_nulls: Optional[Any] = None,
):
    """Add a column to a table."""

    # Checking if the column already exists
    try:
        if isinstance(conn, sqlite3.Connection):
            cursor.execute(f'PRAGMA table_info({table_name})')
            columns = [row[1] for row in cursor.fetchall()]
        elif isinstance(conn, psycopg2.extensions.connection):
            cursor.execute(f"SELECT column_name FROM \
                    information_schema.columns WHERE table_name='{table_name}'")  # pylint: disable=invalid-string-quote
            columns = [row[0] for row in cursor.fetchall()]
        else:
            raise ValueError('Unsupported database type')
    except Exception as e:
        raise RuntimeError('Error checking existing columns: ' + str(e)) from e

    if column_name not in columns:
        try:
            # Add the new column
            add_column_cmd = f'ALTER TABLE {table_name} \
                ADD COLUMN {column_name} {column_type}'

            cursor.execute(add_column_cmd)
            conn.commit()

            # Copy data from another column if required
            if copy_from is not None:
                cursor.execute(
                    f'UPDATE {table_name} SET {column_name} = {copy_from}')
                conn.commit()

            # Replace NULLs with a default value if required
            if default_value_to_replace_nulls is not None:
                update_cmd = f"""\
                    UPDATE {table_name} 
                    SET {column_name} = \
                        {'%s' if isinstance(conn, psycopg2.extensions.connection) else '?'} 
                    WHERE {column_name} IS NULL
                """
                cursor.execute(update_cmd, (default_value_to_replace_nulls,))
                conn.commit()

        except (sqlite3.OperationalError, psycopg2.Error) as e:
            # Specific error handling can be done here
            if 'duplicate column name' in str(e):
                pass  # Duplicate column name error, which can be ignored
            else:
                raise


def rename_column(
    cursor: Union[sqlite3.Cursor, psycopg2.extensions.cursor],
    conn: Union[sqlite3.Connection, psycopg2.extensions.connection],
    table_name: str,
    old_name: str,
    new_name: str,
):
    """Rename a column in a table, compatible with SQLite and PostgreSQL."""

    if isinstance(conn, sqlite3.Connection):
        # SQLite version
        # Check if the column exists
        cursor.execute(f'PRAGMA table_info({table_name})')
        if any(row[1] == old_name for row in cursor.fetchall()):
            cursor.execute(f'ALTER TABLE {table_name} \
                    RENAME COLUMN {old_name} TO {new_name}')
            conn.commit()
    elif isinstance(conn, psycopg2.extensions.connection):
        # PostgreSQL version
        cursor.execute(f'ALTER TABLE {table_name} \
                RENAME COLUMN {old_name} TO {new_name}')
        conn.commit()
    else:
        raise ValueError('Unsupported database type')


class DBConn(threading.local):
    """Thread-local connection to the postgres or sqlite3 database."""

    def __init__(self,
                 db_path: str,
                 create_table: Union[Callable, None] = None):
        super().__init__()
        self.conn: Union[sqlite3.Connection,
                         psycopg2.extensions.connection] = None
        self.db_path: str = db_path

        if db_path.startswith('postgres://'):
            self._init_postgres(db_path, create_table)
        else:
            self._init_sqlite(db_path, create_table)

    def _init_postgres(self, db_path: str, create_table: Union[Callable, None]):
        # Extract database name and connection info from db_path
        db_name = db_path.rsplit('/', 1)[-1]
        conn_info = db_path.rsplit('/', 1)[0]

        # Connect to PostgreSQL server without specifying a database
        conn = psycopg2.connect(conn_info, connect_timeout=1)
        conn.autocommit = True
        cursor = conn.cursor()

        # Check if the database exists
        cursor.execute(
            sql.SQL('SELECT 1 FROM pg_database\
                                WHERE datname = %s;'), (db_name,))
        exists = cursor.fetchone()

        # Create the database if it does not exist
        if not exists:
            cursor.execute(
                sql.SQL('CREATE DATABASE {}').format(sql.Identifier(db_name)))

        conn.close()

        # Connect to the specific database
        self.conn = psycopg2.connect(db_path)
        self.conn.autocommit = True
        self.cursor = self.conn.cursor()
        if create_table is not None:
            create_table(self.cursor, self.conn, pgs='%s')

    def _init_sqlite(self, db_path: str, create_table: Union[Callable, None]):
        # Connect to SQLite database (it creates the db if it doesn't exist)
        # NOTE: We use a timeout of 10 seconds to avoid database locked
        # errors. This is a hack, but it works.
        self.conn = sqlite3.connect(db_path, timeout=10)
        self.cursor = self.conn.cursor()
        if create_table is not None:
            create_table(self.cursor, self.conn, pgs='?')
