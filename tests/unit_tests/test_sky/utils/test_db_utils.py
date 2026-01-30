"""Unit tests for database utilities with SKY_RUNTIME_DIR environment variable."""
import os
import sqlite3
from unittest import mock

import pytest
import pytest_asyncio
import sqlalchemy

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


class TestGetEngine:
    """Tests for get_engine function."""

    @pytest.fixture(autouse=True)
    def clear_caches(self, monkeypatch):
        """Clear engine caches before each test."""
        # Clear the module-level caches
        db_utils._postgres_engine_cache.clear()
        db_utils._sqlite_engine_cache.clear()
        # Reset max_connections to default
        db_utils.set_max_connections(0)
        # Ensure we're not in server mode by default
        monkeypatch.delenv('IS_SKYPILOT_SERVER', raising=False)
        monkeypatch.delenv('SKYPILOT_DB_CONNECTION_URI', raising=False)

    def test_sqlite_sync_engine_creation(self, tmp_path, monkeypatch):
        """Test SQLite sync engine is created correctly."""
        monkeypatch.setenv('SKY_RUNTIME_DIR', str(tmp_path))

        with mock.patch('sqlalchemy.create_engine') as mock_create:
            mock_engine = mock.MagicMock()
            mock_create.return_value = mock_engine

            engine = db_utils.get_engine(db_name='test_db')

            mock_create.assert_called_once()
            call_args = mock_create.call_args
            assert 'sqlite:///' in call_args[0][0]
            assert 'test_db.db' in call_args[0][0]
            assert engine == mock_engine

    def test_sqlite_sync_engine_caching(self, tmp_path, monkeypatch):
        """Test SQLite sync engine is cached and reused."""
        monkeypatch.setenv('SKY_RUNTIME_DIR', str(tmp_path))

        with mock.patch('sqlalchemy.create_engine') as mock_create:
            mock_engine = mock.MagicMock()
            mock_create.return_value = mock_engine

            engine1 = db_utils.get_engine(db_name='cached_db')
            engine2 = db_utils.get_engine(db_name='cached_db')

            # Should only create once
            assert mock_create.call_count == 1
            assert engine1 is engine2

    def test_sqlite_async_engine_creation(self, tmp_path, monkeypatch):
        """Test SQLite async engine is created correctly."""
        monkeypatch.setenv('SKY_RUNTIME_DIR', str(tmp_path))

        with mock.patch(
                'sqlalchemy.ext.asyncio.create_async_engine') as mock_create:
            mock_engine = mock.MagicMock()
            mock_create.return_value = mock_engine

            engine = db_utils.get_engine(db_name='async_db', async_engine=True)

            mock_create.assert_called_once()
            call_args = mock_create.call_args
            assert 'sqlite+aiosqlite:///' in call_args[0][0]
            assert 'async_db.db' in call_args[0][0]
            assert call_args[1]['connect_args'] == {'timeout': 30}
            assert engine == mock_engine

    def test_sqlite_async_engine_not_cached(self, tmp_path, monkeypatch):
        """Test SQLite async engines are NOT cached (unlike sync engines)."""
        monkeypatch.setenv('SKY_RUNTIME_DIR', str(tmp_path))

        with mock.patch(
                'sqlalchemy.ext.asyncio.create_async_engine') as mock_create:
            mock_engine1 = mock.MagicMock()
            mock_engine2 = mock.MagicMock()
            mock_create.side_effect = [mock_engine1, mock_engine2]

            engine1 = db_utils.get_engine(db_name='async_db', async_engine=True)
            engine2 = db_utils.get_engine(db_name='async_db', async_engine=True)

            # Async SQLite engines are NOT cached, so create should be called twice
            assert mock_create.call_count == 2
            assert engine1 is not engine2

    def test_sqlite_db_name_required(self, monkeypatch):
        """Test that db_name is required for SQLite."""
        monkeypatch.delenv('IS_SKYPILOT_SERVER', raising=False)

        with pytest.raises(AssertionError,
                           match='db_name must be provided for SQLite'):
            db_utils.get_engine(db_name=None)

    def test_postgres_sync_engine_creation_with_nullpool(self, monkeypatch):
        """Test Postgres sync engine with NullPool when max_connections=0."""
        monkeypatch.setenv('IS_SKYPILOT_SERVER', 'true')
        monkeypatch.setenv('SKYPILOT_DB_CONNECTION_URI',
                           'postgresql://user:pass@localhost/db')
        db_utils.set_max_connections(0)

        with mock.patch('sqlalchemy.create_engine') as mock_create:
            mock_engine = mock.MagicMock()
            mock_create.return_value = mock_engine

            engine = db_utils.get_engine(db_name='ignored')

            mock_create.assert_called_once()
            call_args = mock_create.call_args
            assert call_args[0][0] == 'postgresql://user:pass@localhost/db'
            assert call_args[1]['poolclass'] == sqlalchemy.NullPool
            assert engine == mock_engine

    def test_postgres_sync_engine_creation_with_queuepool(self, monkeypatch):
        """Test Postgres sync engine with QueuePool when max_connections>0."""
        monkeypatch.setenv('IS_SKYPILOT_SERVER', 'true')
        monkeypatch.setenv('SKYPILOT_DB_CONNECTION_URI',
                           'postgresql://user:pass@localhost/db')
        db_utils.set_max_connections(10)

        with mock.patch('sqlalchemy.create_engine') as mock_create:
            mock_engine = mock.MagicMock()
            mock_create.return_value = mock_engine

            engine = db_utils.get_engine(db_name='ignored')

            mock_create.assert_called_once()
            call_args = mock_create.call_args
            assert call_args[0][0] == 'postgresql://user:pass@localhost/db'
            assert call_args[1]['poolclass'] == sqlalchemy.pool.QueuePool
            assert call_args[1]['pool_size'] == 10
            assert call_args[1]['max_overflow'] == 0  # max(0, 5-10)
            assert call_args[1]['pool_pre_ping'] is True
            assert call_args[1]['pool_recycle'] == 1800
            assert engine == mock_engine

    def test_postgres_sync_engine_queuepool_max_overflow_calculation(
            self, monkeypatch):
        """Test max_overflow calculation with different pool sizes."""
        monkeypatch.setenv('IS_SKYPILOT_SERVER', 'true')
        monkeypatch.setenv('SKYPILOT_DB_CONNECTION_URI',
                           'postgresql://user:pass@localhost/db')

        # Test with pool_size=2, max_overflow should be 3
        db_utils.set_max_connections(2)

        with mock.patch('sqlalchemy.create_engine') as mock_create:
            mock_engine = mock.MagicMock()
            mock_create.return_value = mock_engine

            db_utils.get_engine(db_name='ignored')

            call_args = mock_create.call_args
            assert call_args[1]['max_overflow'] == 3  # max(0, 5-2)

    def test_postgres_async_engine_creation(self, monkeypatch):
        """Test Postgres async engine uses asyncpg and NullPool."""
        monkeypatch.setenv('IS_SKYPILOT_SERVER', 'true')
        monkeypatch.setenv('SKYPILOT_DB_CONNECTION_URI',
                           'postgresql://user:pass@localhost/db')

        with mock.patch(
                'sqlalchemy.ext.asyncio.create_async_engine') as mock_create:
            mock_engine = mock.MagicMock()
            mock_create.return_value = mock_engine

            engine = db_utils.get_engine(db_name='ignored', async_engine=True)

            mock_create.assert_called_once()
            call_args = mock_create.call_args
            # Connection string should be modified for asyncpg
            assert call_args[0][
                0] == 'postgresql+asyncpg://user:pass@localhost/db'
            assert call_args[1]['poolclass'] == sqlalchemy.NullPool
            assert engine == mock_engine

    def test_postgres_engine_caching(self, monkeypatch):
        """Test Postgres sync engines are cached and reused."""
        monkeypatch.setenv('IS_SKYPILOT_SERVER', 'true')
        monkeypatch.setenv('SKYPILOT_DB_CONNECTION_URI',
                           'postgresql://user:pass@localhost/db')
        db_utils.set_max_connections(0)

        with mock.patch('sqlalchemy.create_engine') as mock_create:
            mock_engine = mock.MagicMock()
            mock_create.return_value = mock_engine

            engine1 = db_utils.get_engine(db_name='ignored')
            engine2 = db_utils.get_engine(db_name='any_name')

            # Should only create once regardless of db_name
            assert mock_create.call_count == 1
            assert engine1 is engine2

    def test_postgres_async_engine_caching(self, monkeypatch):
        """Test Postgres async engines are cached and reused."""
        monkeypatch.setenv('IS_SKYPILOT_SERVER', 'true')
        monkeypatch.setenv('SKYPILOT_DB_CONNECTION_URI',
                           'postgresql://user:pass@localhost/db')

        with mock.patch(
                'sqlalchemy.ext.asyncio.create_async_engine') as mock_create:
            mock_engine = mock.MagicMock()
            mock_create.return_value = mock_engine

            engine1 = db_utils.get_engine(db_name='ignored', async_engine=True)
            engine2 = db_utils.get_engine(db_name='any_name', async_engine=True)

            # Should only create once regardless of db_name
            assert mock_create.call_count == 1
            assert engine1 is engine2

    def test_postgres_sync_and_async_engines_cached_separately(
            self, monkeypatch):
        """Test sync and async Postgres engines are cached separately."""
        monkeypatch.setenv('IS_SKYPILOT_SERVER', 'true')
        monkeypatch.setenv('SKYPILOT_DB_CONNECTION_URI',
                           'postgresql://user:pass@localhost/db')
        db_utils.set_max_connections(0)

        with mock.patch('sqlalchemy.create_engine') as mock_sync_create, \
             mock.patch('sqlalchemy.ext.asyncio.create_async_engine') as mock_async_create:
            mock_sync_engine = mock.MagicMock()
            mock_async_engine = mock.MagicMock()
            mock_sync_create.return_value = mock_sync_engine
            mock_async_create.return_value = mock_async_engine

            sync_engine = db_utils.get_engine(db_name='ignored')
            async_engine = db_utils.get_engine(db_name='ignored',
                                               async_engine=True)

            assert mock_sync_create.call_count == 1
            assert mock_async_create.call_count == 1
            assert sync_engine is not async_engine

    def test_postgres_db_name_ignored(self, monkeypatch):
        """Test that db_name is ignored when using Postgres."""
        monkeypatch.setenv('IS_SKYPILOT_SERVER', 'true')
        monkeypatch.setenv('SKYPILOT_DB_CONNECTION_URI',
                           'postgresql://user:pass@localhost/db')
        db_utils.set_max_connections(0)

        with mock.patch('sqlalchemy.create_engine') as mock_create:
            mock_engine = mock.MagicMock()
            mock_create.return_value = mock_engine

            # db_name can be None or any value for Postgres
            engine1 = db_utils.get_engine(db_name=None)
            engine2 = db_utils.get_engine(db_name='some_db')
            engine3 = db_utils.get_engine(db_name='other_db')

            # All should return the same cached engine
            assert engine1 is engine2 is engine3
            assert mock_create.call_count == 1

    def test_env_var_is_skypilot_server_required_for_postgres(
            self, monkeypatch):
        """Test IS_SKYPILOT_SERVER env var is required for Postgres mode."""
        # Only set DB_CONNECTION_URI, not IS_SKYPILOT_SERVER
        monkeypatch.delenv('IS_SKYPILOT_SERVER', raising=False)
        monkeypatch.setenv('SKYPILOT_DB_CONNECTION_URI',
                           'postgresql://user:pass@localhost/db')

        with mock.patch('sqlalchemy.create_engine') as mock_create:
            mock_engine = mock.MagicMock()
            mock_create.return_value = mock_engine

            # Should fall back to SQLite mode since IS_SKYPILOT_SERVER is not set
            with pytest.raises(AssertionError,
                               match='db_name must be provided'):
                db_utils.get_engine(db_name=None)

    def test_directory_created_for_sqlite(self, tmp_path, monkeypatch):
        """Test that parent directory is created for SQLite database."""
        runtime_dir = tmp_path / 'nonexistent' / 'path'
        monkeypatch.setenv('SKY_RUNTIME_DIR', str(runtime_dir))

        with mock.patch('sqlalchemy.create_engine') as mock_create:
            mock_engine = mock.MagicMock()
            mock_create.return_value = mock_engine

            db_utils.get_engine(db_name='test_db')

            # Parent directory should be created
            expected_dir = runtime_dir / '.sky'
            assert expected_dir.exists()
