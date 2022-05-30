"""Utils for sky databases."""
import typing
if typing.TYPE_CHECKING:
    import sqlite3


def add_column_to_table(
    cursor: 'sqlite3.Cursor',
    conn: 'sqlite3.Connection',
    table_name: str,
    column_name: str,
    column_type: str,
):
    """Add a column to a table."""
    for row in cursor.execute(f'PRAGMA table_info({table_name})'):
        if row[1] == column_name:
            break
    else:
        cursor.execute(f'ALTER TABLE {table_name} '
                       f'ADD COLUMN {column_name} {column_type}')
    conn.commit()


def rename_column(
    cursor: 'sqlite3.Cursor',
    conn: 'sqlite3.Connection',
    table_name: str,
    old_name: str,
    new_name: str,
):
    """Rename a column in a table."""
    # NOTE: This only works for sqlite3 >= 3.25.0. Be careful to use this.

    for row in cursor.execute(f'PRAGMA table_info({table_name})'):
        if row[1] == old_name:
            cursor.execute(f'ALTER TABLE {table_name} '
                           f'RENAME COLUMN {old_name} to {new_name}')
            break
    conn.commit()
