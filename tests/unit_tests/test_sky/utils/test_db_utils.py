"""Unit tests for database utilities with SKY_RUNTIME_DIR environment variable."""
import os
import sqlite3
from unittest import mock

import pytest

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


@pytest.mark.asyncio
async def test_async_connection_wrapper_discards_bad_connection(monkeypatch):
    """Connections encountering sqlite errors should be discarded and rebuilt."""
    created_connections = []
    closed_connections = []

    class FakeConn:

        def __init__(self):
            self._conn = object()

        async def close(self):
            closed_connections.append(self)

        async def _execute(self, func, *args, **kwargs):
            return await func(*args, **kwargs)

        async def execute_fetchall(self, sql, parameters=None):
            return []

    async def fake_connect(db_path):
        conn = FakeConn()
        created_connections.append(conn)
        return conn

    monkeypatch.setattr(db_utils.aiosqlite, 'connect', fake_connect)

    wrapper = await db_utils._AsyncConnectionWrapper.create('test_path')

    async def failing_sql(_):
        raise sqlite3.Error('boom')

    with pytest.raises(sqlite3.Error):
        await wrapper._run(failing_sql)

    assert wrapper._connection is None
    assert created_connections[0] in closed_connections

    async def successful_sql(_):
        return 'ok'

    result = await wrapper._run(successful_sql)
    assert result == 'ok'
    assert wrapper._connection is created_connections[1]
