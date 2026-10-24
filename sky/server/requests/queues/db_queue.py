"""The request queue, stored as a table in the request database.

Each queued entry is one row in ``task_queue``. A producer inserts a row.
A consumer claims the oldest row of its schedule type by deleting it. SQLite
gives each row to exactly one deleter, so the queue works across the server
process, the uvicorn workers and the executors, with no manager process.

The request database file is deleted at every server start, so the queue never
brings back a request from a previous run.
"""
import os
import time
from typing import Optional, Tuple

from sky.server import constants as server_constants
from sky.utils.db import db_utils

QUEUE_TABLE = 'task_queue'

# (request_id, ignore_return_value, retryable)
QueueItem = Tuple[str, bool, bool]


def delete_by_request_ids_statement(count: int) -> str:
    """SQL that removes the queue entries of the given number of requests."""
    placeholders = ','.join(['?'] * count)
    return f'DELETE FROM {QUEUE_TABLE} WHERE request_id IN ({placeholders})'


def get_db_path() -> str:
    return os.path.expanduser(server_constants.API_SERVER_REQUEST_DB_PATH)


def create_table(cursor, conn) -> None:
    """Creates the queue table. Safe to run from many processes."""
    db_utils.enable_wal_mode(cursor)
    # `id` is the enqueue order. A re-put gets a new, larger id, so it goes
    # to the back of its schedule type, as with an in-memory FIFO.
    cursor.execute(f"""\
        CREATE TABLE IF NOT EXISTS {QUEUE_TABLE} (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        request_id TEXT NOT NULL,
        schedule_type TEXT NOT NULL,
        ignore_return_value INTEGER NOT NULL,
        retryable INTEGER NOT NULL,
        enqueued_at REAL NOT NULL
        )""")
    # Every poll of an empty queue reads this index.
    cursor.execute(f"""\
        CREATE INDEX IF NOT EXISTS task_queue_type_idx
        ON {QUEUE_TABLE} (schedule_type, id)""")
    conn.commit()


_INSERT_SQL = (f'INSERT INTO {QUEUE_TABLE} (request_id, schedule_type, '
               'ignore_return_value, retryable, enqueued_at) '
               'VALUES (?, ?, ?, ?, ?)')
_PEEK_SQL = (f'SELECT id FROM {QUEUE_TABLE} WHERE schedule_type=? '
             'ORDER BY id LIMIT 1')
_CLAIM_SQL = (f'DELETE FROM {QUEUE_TABLE} WHERE id=? '
              'RETURNING request_id, ignore_return_value, retryable, '
              'enqueued_at')
_SIZE_SQL = f'SELECT COUNT(*) FROM {QUEUE_TABLE} WHERE schedule_type=?'


def _insert_params(schedule_type: str, item: QueueItem) -> Tuple[object, ...]:
    request_id, ignore_return_value, retryable = item
    return (request_id, schedule_type, int(ignore_return_value), int(retryable),
            time.time())


def put(db: db_utils.SQLiteConn, schedule_type: str, item: QueueItem) -> None:
    with db_utils.safe_cursor_on_connection(db.conn) as cursor:
        cursor.execute(_INSERT_SQL, _insert_params(schedule_type, item))


async def put_async(db: db_utils.SQLiteConn, schedule_type: str,
                    item: QueueItem) -> None:
    await db.execute_and_commit_async(_INSERT_SQL,
                                      _insert_params(schedule_type, item))


def claim(db: db_utils.SQLiteConn,
          schedule_type: str) -> Optional[Tuple[QueueItem, float]]:
    """Removes and returns the oldest entry and its enqueue time, or None."""
    while True:
        # The peek is a read. In WAL mode a read never waits for a writer, so
        # polling an empty queue never takes the write lock.
        with db_utils.safe_cursor_on_connection(db.conn) as cursor:
            cursor.execute(_PEEK_SQL, (schedule_type,))
            row = cursor.fetchone()
            if row is None:
                return None
            cursor.execute(_CLAIM_SQL, (row[0],))
            claimed = cursor.fetchall()
        # Only one deleter gets the row. A deleter that lost the race to
        # another consumer peeks again.
        if claimed:
            request_id, ignore_return_value, retryable, enqueued_at = claimed[0]
            return ((request_id, bool(ignore_return_value), bool(retryable)),
                    enqueued_at)


def size(db: db_utils.SQLiteConn, schedule_type: str) -> int:
    with db_utils.safe_cursor_on_connection(db.conn) as cursor:
        cursor.execute(_SIZE_SQL, (schedule_type,))
        return cursor.fetchone()[0]
