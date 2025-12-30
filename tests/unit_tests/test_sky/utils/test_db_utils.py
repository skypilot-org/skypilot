"""Unit tests for database utilities with SKY_RUNTIME_DIR environment variable."""
import os
import sqlite3
from unittest import mock

import pytest
import pytest_asyncio

from sky.utils.db import db_utils


class TestSkyRuntimeDirEnvVar:
    """Test that db_utils correctly uses SKY_RUNTIME_DIR for database paths."""

    @pytest.mark.parametrize('use_custom_dir', [False, True])
    def test_get_engine_runtime_dir(self, tmp_path, monkeypatch,
                                    use_custom_dir):
        """Test get_engine respects SKY_RUNTIME_DIR when set."""
        monkeypatch.delenv('SKY_RUNTIME_DIR', raising=False)
        if use_custom_dir:
            monkeypatch.setenv('SKY_RUNTIME_DIR', str(tmp_path))

        with mock.patch('sqlalchemy.create_engine') as mock_create:
            db_utils.get_engine(db_name='test')

            call_args = mock_create.call_args
            db_path = call_args[0][0]
            if use_custom_dir:
                expected_path = str(tmp_path / '.sky/test.db')
            else:
                expected_path = os.path.expanduser('~/.sky/test.db')
            assert expected_path in db_path

    def test_get_engine_async_custom_runtime_dir(self, tmp_path, monkeypatch):
        """Test async engine creation uses custom SKY_RUNTIME_DIR."""
        monkeypatch.setenv('SKY_RUNTIME_DIR', str(tmp_path))

        with mock.patch(
                'sqlalchemy.ext.asyncio.create_async_engine') as mock_create:
            db_utils.get_engine(db_name='test', async_engine=True)

            call_args = mock_create.call_args
            db_path = call_args[0][0]
            expected_path = str(tmp_path / '.sky/test.db')
            assert expected_path in db_path


@pytest_asyncio.fixture
async def isolated_database(tmp_path):
    """Create an isolated SQLiteConn backed by a real SQLite file."""
    db_path = tmp_path / 'db_utils_async.db'

    def create_table(cursor, conn):
        cursor.execute('CREATE TABLE IF NOT EXISTS items ('
                       'id INTEGER PRIMARY KEY AUTOINCREMENT, value TEXT)')
        conn.commit()

    conn = db_utils.SQLiteConn(str(db_path), create_table)
    try:
        yield conn, str(db_path)
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_execute_fetchall_async_error_does_not_stall_read_txn(
        isolated_database):
    conn, db_path = isolated_database

    await conn.execute_and_commit_async('INSERT INTO items (value) VALUES (?)',
                                        ('initial',))
    with pytest.raises(sqlite3.OperationalError):
        with mock.patch.object(db_utils,
                               'fault_point',
                               side_effect=sqlite3.OperationalError('BOOM')):
            async with conn.execute_fetchall_async('SELECT * FROM items') as _:
                pass

    # Another connection writes to the database while the failed read is cleaned
    # up to ensure there is no lingering read transaction.
    with sqlite3.connect(db_path) as external_conn:
        external_conn.execute('INSERT INTO items (value) VALUES (?)',
                              ('external',))

    # The original connection should be able to promote to a write txn without
    # hitting "database is locked".
    await conn.execute_and_commit_async('INSERT INTO items (value) VALUES (?)',
                                        ('after_error',))

    async with conn.execute_fetchall_async(
            'SELECT value FROM items ORDER BY id') as rows:
        values = [row[0] for row in rows]

    assert values == ['initial', 'external', 'after_error']
