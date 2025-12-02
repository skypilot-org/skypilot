"""Unit tests for sky.utils.locks module."""

import os
from unittest import mock

import pytest

from sky import global_user_state
from sky.skylet import runtime_utils
from sky.utils import locks
from sky.utils.db import db_utils


class TestLockTimeout:
    """Test LockTimeout exception."""

    def test_lock_timeout_is_runtime_error(self):
        """Test that LockTimeout inherits from RuntimeError."""
        assert issubclass(locks.LockTimeout, RuntimeError)

    def test_lock_timeout_message(self):
        """Test LockTimeout with custom message."""
        error = locks.LockTimeout("Test timeout message")
        assert str(error) == "Test timeout message"


class TestAcquireReturnProxy:
    """Test AcquireReturnProxy context manager."""

    def test_acquire_return_proxy_enter_returns_lock(self):
        """Test that __enter__ returns the underlying lock."""
        mock_lock = mock.Mock()
        proxy = locks.AcquireReturnProxy(mock_lock)

        result = proxy.__enter__()
        assert result is mock_lock

    def test_acquire_return_proxy_exit_releases_lock(self):
        """Test that __exit__ calls release on the lock."""
        mock_lock = mock.Mock()
        proxy = locks.AcquireReturnProxy(mock_lock)

        proxy.__exit__(None, None, None)
        mock_lock.release.assert_called_once()

    def test_acquire_return_proxy_context_manager(self):
        """Test AcquireReturnProxy as context manager."""
        mock_lock = mock.Mock()
        proxy = locks.AcquireReturnProxy(mock_lock)

        with proxy as result:
            assert result is mock_lock

        mock_lock.release.assert_called_once()


class TestFileLock:
    """Test FileLock implementation."""

    def setUp(self):
        """Setup test environment."""
        # Ensure locks directory exists
        os.makedirs(locks.SKY_LOCKS_DIR, exist_ok=True)

    def tearDown(self):
        """Clean up after tests."""
        # Clean up any test lock files
        if os.path.exists(locks.SKY_LOCKS_DIR):
            for file in os.listdir(locks.SKY_LOCKS_DIR):
                if file.startswith('.test_') and file.endswith('.lock'):
                    try:
                        os.remove(os.path.join(locks.SKY_LOCKS_DIR, file))
                    except OSError:
                        pass

    def test_file_lock_initialization(self):
        """Test FileLock initialization."""
        lock = locks.FileLock('test_lock')

        assert lock.lock_id == 'test_lock'
        assert lock.timeout is None  # Base class stores original None
        assert lock.poll_interval == 0.1
        assert lock.lock_path == os.path.join(locks.SKY_LOCKS_DIR,
                                              '.test_lock.lock')
        # Internal filelock should have -1 for None timeout
        assert lock._filelock.timeout == -1

    def test_file_lock_initialization_with_timeout(self):
        """Test FileLock initialization with timeout."""
        lock = locks.FileLock('test_lock', timeout=5.0)

        assert lock.timeout == 5.0
        assert lock._filelock.timeout == 5.0

    def test_file_lock_acquire_and_release(self):
        """Test basic file lock acquire and release."""
        lock = locks.FileLock('test_acquire_release')

        proxy = lock.acquire()
        assert isinstance(proxy, locks.AcquireReturnProxy)
        assert os.path.exists(lock.lock_path)

        lock.release()
        # File should still exist but be unlocked

    def test_file_lock_acquire_non_blocking_success(self):
        """Test non-blocking acquire when lock is available."""
        lock = locks.FileLock('test_nonblocking_success')

        proxy = lock.acquire(blocking=False)
        assert isinstance(proxy, locks.AcquireReturnProxy)
        lock.release()

    def test_file_lock_acquire_non_blocking_failure(self):
        """Test non-blocking acquire when lock is unavailable."""
        lock1 = locks.FileLock('test_nonblocking_fail', timeout=0.1)
        lock2 = locks.FileLock('test_nonblocking_fail', timeout=0.1)

        # First lock acquires successfully
        proxy1 = lock1.acquire()

        # Second lock should fail immediately in non-blocking mode
        with pytest.raises(locks.LockTimeout):
            lock2.acquire(blocking=False)

        lock1.release()

    def test_file_lock_timeout(self):
        """Test file lock timeout behavior."""
        lock1 = locks.FileLock('test_timeout')
        lock2 = locks.FileLock('test_timeout', timeout=0.1)

        # First lock acquires successfully
        proxy1 = lock1.acquire()

        # Second lock should timeout
        with pytest.raises(locks.LockTimeout):
            lock2.acquire()

        lock1.release()

    def test_file_lock_context_manager(self):
        """Test FileLock as context manager."""
        lock = locks.FileLock('test_context')

        with lock:
            assert os.path.exists(lock.lock_path)

        # Lock should be released after context

    def test_file_lock_force_unlock(self):
        """Test force unlock functionality."""
        lock = locks.FileLock('test_force_unlock')

        with lock:
            assert os.path.exists(lock.lock_path)
            lock.force_unlock()
            assert not os.path.exists(lock.lock_path)

    def test_file_lock_directories_created(self, tmp_path, monkeypatch):
        """Test that lock directories are created."""

        # SKY_RUNTIME_DIR is evaluated at import time, so we need to reload the module to get the new value.
        # But we can't reload the module here as it affects other tests, so just manually
        # test that runtime_utils.get_runtime_dir_path() respects SKY_RUNTIME_DIR.
        monkeypatch.setenv('SKY_RUNTIME_DIR', str(tmp_path))
        expected_locks_dir = str(tmp_path / '.sky/locks')
        assert runtime_utils.get_runtime_dir_path(
            '.sky/locks') == expected_locks_dir

        # Test that creating a lock creates the directory.
        original_locks_dir = locks.SKY_LOCKS_DIR
        try:
            locks.SKY_LOCKS_DIR = expected_locks_dir
            assert not os.path.exists(locks.SKY_LOCKS_DIR)
            lock = locks.FileLock('test_dir_creation')
            assert os.path.exists(locks.SKY_LOCKS_DIR)
        finally:
            locks.SKY_LOCKS_DIR = original_locks_dir


class TestPostgresLock:
    """Test PostgresLock implementation."""

    @pytest.fixture
    def mock_engine(self):
        """Mock database engine."""
        engine = mock.Mock()
        engine.dialect.name = db_utils.SQLAlchemyDialect.POSTGRESQL.value
        return engine

    @pytest.fixture
    def mock_connection(self):
        """Mock database connection."""
        connection = mock.Mock()
        cursor = mock.Mock()
        connection.cursor.return_value = cursor
        return connection, cursor

    def test_postgres_lock_initialization(self):
        """Test PostgresLock initialization."""
        lock = locks.PostgresLock('test_lock')

        assert lock.lock_id == 'test_lock'
        assert lock.timeout is None
        assert lock.poll_interval == 1
        assert not lock._acquired
        assert lock._connection is None
        assert not lock._shared_lock

    def test_postgres_lock_string_to_lock_key(self):
        """Test string to lock key conversion is deterministic across processes."""
        lock = locks.PostgresLock('test_lock')

        # Test deterministic behavior - same input always produces same output
        key1 = lock._string_to_lock_key('test_string')
        key2 = lock._string_to_lock_key('test_string')
        key3 = lock._string_to_lock_key('different_string')

        # Same string should produce same key
        assert key1 == key2
        # Different strings should produce different keys
        assert key1 != key3
        # Should be positive 64-bit integer
        assert 0 <= key1 < (1 << 63)

        # Test specific known values to ensure deterministic hashing
        # These values must remain constant across Python processes/versions
        # This test would have caught the original hash() bug
        test_cases = [
            ('test_status', 7396834654636105082),
            ('cluster_lock', 1956631540824474243),
            ('job_queue', 6631142408643071799),
        ]

        for input_str, expected_key in test_cases:
            actual_key = lock._string_to_lock_key(input_str)
            assert actual_key == expected_key, (
                f"Expected deterministic key {expected_key} for '{input_str}', "
                f"but got {actual_key}. This indicates the hash function is "
                f"not deterministic across processes.")

    @mock.patch.object(locks.PostgresLock, '_get_connection')
    def test_postgres_lock_acquire_success(self, mock_get_connection,
                                           mock_connection):
        """Test successful postgres lock acquisition."""
        connection, cursor = mock_connection
        mock_get_connection.return_value = connection
        cursor.fetchone.return_value = [True]  # Lock acquired

        lock = locks.PostgresLock('test_lock')
        proxy = lock.acquire()

        assert isinstance(proxy, locks.AcquireReturnProxy)
        assert lock._acquired
        assert lock._connection is connection
        cursor.execute.assert_called_once_with(
            'SELECT pg_try_advisory_lock(%s)', (mock.ANY,))

    @mock.patch.object(locks.PostgresLock, '_get_connection')
    def test_postgres_lock_acquire_already_acquired(self, mock_get_connection,
                                                    mock_connection):
        """Test postgres lock acquire when already acquired."""
        connection, cursor = mock_connection
        mock_get_connection.return_value = connection

        lock = locks.PostgresLock('test_lock')
        lock._acquired = True

        proxy = lock.acquire()
        assert isinstance(proxy, locks.AcquireReturnProxy)
        # Should not call database again
        mock_get_connection.assert_not_called()

    @mock.patch.object(locks.PostgresLock, '_get_connection')
    def test_postgres_lock_acquire_timeout(self, mock_get_connection,
                                           mock_connection):
        """Test postgres lock acquisition timeout."""
        connection, cursor = mock_connection
        mock_get_connection.return_value = connection
        cursor.fetchone.return_value = [False]  # Lock not acquired

        lock = locks.PostgresLock('test_lock', timeout=0.1)

        with pytest.raises(locks.LockTimeout):
            lock.acquire()

    @mock.patch.object(locks.PostgresLock, '_get_connection')
    def test_postgres_lock_acquire_non_blocking_failure(self,
                                                        mock_get_connection,
                                                        mock_connection):
        """Test non-blocking postgres lock acquisition failure."""
        connection, cursor = mock_connection
        mock_get_connection.return_value = connection
        cursor.fetchone.return_value = [False]  # Lock not acquired

        lock = locks.PostgresLock('test_lock')

        with pytest.raises(locks.LockTimeout):
            lock.acquire(blocking=False)

    @mock.patch.object(locks.PostgresLock, '_get_connection')
    def test_postgres_lock_acquire_exception_cleanup(self, mock_get_connection,
                                                     mock_connection):
        """Test postgres lock cleans up on exception."""
        connection, cursor = mock_connection
        mock_get_connection.return_value = connection
        cursor.execute.side_effect = Exception("Database error")

        lock = locks.PostgresLock('test_lock')

        with pytest.raises(Exception):
            lock.acquire()

        # Connection should be closed on exception
        connection.close.assert_called_once()
        assert lock._connection is None

    def test_postgres_lock_release_not_acquired(self):
        """Test postgres lock release when not acquired."""
        lock = locks.PostgresLock('test_lock')

        # Should not raise exception
        lock.release()
        assert not lock._acquired

    @mock.patch.object(locks.PostgresLock, '_get_connection')
    def test_postgres_lock_release_success(self, mock_get_connection,
                                           mock_connection):
        """Test successful postgres lock release."""
        connection, cursor = mock_connection
        lock = locks.PostgresLock('test_lock')
        lock._acquired = True
        lock._connection = connection

        lock.release()

        cursor.execute.assert_called_once_with('SELECT pg_advisory_unlock(%s)',
                                               (mock.ANY,))
        connection.commit.assert_called_once()
        connection.close.assert_called_once()
        assert not lock._acquired
        assert lock._connection is None

    @mock.patch.object(locks.PostgresLock, '_get_connection')
    def test_postgres_lock_force_unlock(self, mock_get_connection,
                                        mock_connection):
        """Test postgres force unlock."""
        connection, cursor = mock_connection
        mock_get_connection.return_value = connection
        cursor.fetchone.return_value = [True]  # pg_advisory_unlock succeeds

        lock = locks.PostgresLock('test_lock')
        lock.force_unlock()

        cursor.execute.assert_called_once_with('SELECT pg_advisory_unlock(%s)',
                                               (mock.ANY,))
        connection.commit.assert_called_once()
        connection.close.assert_called_once()

    @mock.patch.object(locks.PostgresLock, '_get_connection')
    def test_postgres_lock_force_unlock_exception(self, mock_get_connection,
                                                  mock_connection):
        """Test postgres force unlock with exception."""
        connection, cursor = mock_connection
        mock_get_connection.return_value = connection
        cursor.execute.side_effect = Exception("Database error")

        lock = locks.PostgresLock('test_lock')

        with pytest.raises(RuntimeError) as exc_info:
            lock.force_unlock()

        assert "Failed to force unlock postgres lock" in str(exc_info.value)
        connection.close.assert_called_once()

    @mock.patch.object(locks.PostgresLock, '_get_connection')
    def test_postgres_lock_force_unlock_terminate_backend(
            self, mock_get_connection, mock_connection):
        """Test postgres force unlock that terminates another backend."""
        connection, cursor = mock_connection
        mock_get_connection.return_value = connection

        # First unlock call fails (returns False)
        # Second call finds a PID to terminate
        cursor.fetchone.return_value = [False]  # pg_advisory_unlock fails
        cursor.fetchall.return_value = [[12345], [67890]
                                       ]  # pg_locks query returns multiple PIDs

        lock = locks.PostgresLock('test_lock')
        lock.force_unlock()

        # Should call pg_advisory_unlock, then query pg_locks, then terminate
        expected_calls = [
            mock.call('SELECT pg_advisory_unlock(%s)', (mock.ANY,)),
            mock.call(('SELECT pid FROM pg_locks WHERE locktype = \'advisory\' '
                       'AND ((classid::bigint << 32) | objid::bigint) = %s'),
                      (mock.ANY,)),
            mock.call('SELECT pg_terminate_backend(%s)', (12345,)),
            mock.call('SELECT pg_terminate_backend(%s)', (67890,))
        ]
        cursor.execute.assert_has_calls(expected_calls)
        connection.commit.assert_called_once()
        connection.close.assert_called_once()

    @mock.patch.object(locks.PostgresLock, '_get_connection')
    def test_postgres_lock_force_unlock_no_lock_found(self, mock_get_connection,
                                                      mock_connection):
        """Test postgres force unlock when no lock is found in pg_locks."""
        connection, cursor = mock_connection
        mock_get_connection.return_value = connection

        # First unlock call fails (returns False)
        # Second call finds no lock in pg_locks
        cursor.fetchone.return_value = [False]  # pg_advisory_unlock fails
        cursor.fetchall.return_value = []  # pg_locks query returns no results

        lock = locks.PostgresLock('test_lock')
        lock.force_unlock()

        # Should call pg_advisory_unlock, then query pg_locks, but not terminate
        expected_calls = [
            mock.call('SELECT pg_advisory_unlock(%s)', (mock.ANY,)),
            mock.call(('SELECT pid FROM pg_locks WHERE locktype = \'advisory\' '
                       'AND ((classid::bigint << 32) | objid::bigint) = %s'),
                      (mock.ANY,))
        ]
        cursor.execute.assert_has_calls(expected_calls)
        connection.commit.assert_not_called()
        connection.close.assert_called_once()

    @mock.patch.object(global_user_state, 'initialize_and_get_db')
    def test_postgres_lock_get_connection_success(self, mock_init_db):
        """Test successful database connection."""
        mock_engine = mock.Mock()
        mock_engine.dialect.name = db_utils.SQLAlchemyDialect.POSTGRESQL.value
        mock_connection = mock.Mock()
        mock_engine.raw_connection.return_value = mock_connection
        mock_init_db.return_value = mock_engine

        lock = locks.PostgresLock('test_lock')
        result = lock._get_connection()

        assert result is mock_connection
        mock_init_db.assert_called_once()
        mock_engine.raw_connection.assert_called_once()

    @mock.patch.object(global_user_state, 'initialize_and_get_db')
    def test_postgres_lock_get_connection_wrong_dialect(self, mock_init_db):
        """Test database connection with wrong dialect."""
        mock_engine = mock.Mock()
        mock_engine.dialect.name = 'sqlite'
        mock_init_db.return_value = mock_engine

        lock = locks.PostgresLock('test_lock')

        with pytest.raises(ValueError) as exc_info:
            lock._get_connection()

        assert "PostgresLock requires PostgreSQL database" in str(
            exc_info.value)

    def test_postgres_lock_initialization_shared_mode(self):
        """Test PostgresLock initialization with shared mode."""
        lock = locks.PostgresLock('test_lock', shared_lock=True)

        assert lock._shared_lock
        assert lock.lock_id == 'test_lock'
        assert not lock._acquired

    @mock.patch.object(locks.PostgresLock, '_get_connection')
    def test_postgres_lock_acquire_shared_success(self, mock_get_connection,
                                                  mock_connection):
        """Test successful shared lock acquisition."""
        connection, cursor = mock_connection
        mock_get_connection.return_value = connection
        cursor.fetchone.return_value = [True]

        lock = locks.PostgresLock('test_lock', shared_lock=True)
        proxy = lock.acquire()

        assert isinstance(proxy, locks.AcquireReturnProxy)
        assert lock._acquired
        cursor.execute.assert_called_once_with(
            'SELECT pg_try_advisory_lock_shared(%s)', (mock.ANY,))

    @mock.patch.object(locks.PostgresLock, '_get_connection')
    def test_postgres_lock_release_shared(self, mock_get_connection,
                                          mock_connection):
        """Test shared lock release."""
        connection, cursor = mock_connection
        lock = locks.PostgresLock('test_lock', shared_lock=True)
        lock._acquired = True
        lock._connection = connection

        lock.release()

        cursor.execute.assert_called_once_with(
            'SELECT pg_advisory_unlock_shared(%s)', (mock.ANY,))
        connection.commit.assert_called_once()
        connection.close.assert_called_once()
        assert not lock._acquired
        assert lock._connection is None

    @mock.patch.object(locks.PostgresLock, '_get_connection')
    def test_postgres_lock_force_unlock_shared(self, mock_get_connection,
                                               mock_connection):
        """Test force unlock for shared locks."""
        connection, cursor = mock_connection
        mock_get_connection.return_value = connection
        cursor.fetchone.return_value = [True]

        lock = locks.PostgresLock('test_lock', shared_lock=True)
        lock.force_unlock()

        cursor.execute.assert_called_once_with(
            'SELECT pg_advisory_unlock_shared(%s)', (mock.ANY,))
        connection.commit.assert_called_once()
        connection.close.assert_called_once()

    @mock.patch.object(locks.PostgresLock, '_get_connection')
    def test_postgres_lock_shared_timeout(self, mock_get_connection,
                                          mock_connection):
        """Test shared lock acquisition timeout."""
        connection, cursor = mock_connection
        mock_get_connection.return_value = connection
        cursor.fetchone.return_value = [False]

        lock = locks.PostgresLock('test_lock', shared_lock=True, timeout=0.1)

        with pytest.raises(locks.LockTimeout) as exc_info:
            lock.acquire()

        assert 'shared' in str(exc_info.value).lower()

    @mock.patch.object(locks.PostgresLock, '_get_connection')
    def test_postgres_lock_shared_non_blocking(self, mock_get_connection,
                                               mock_connection):
        """Test shared lock non-blocking acquisition fails immediately."""
        connection, cursor = mock_connection
        mock_get_connection.return_value = connection
        cursor.fetchone.return_value = [False]

        lock = locks.PostgresLock('test_lock', shared_lock=True)

        with pytest.raises(locks.LockTimeout) as exc_info:
            lock.acquire(blocking=False)

        error_msg = str(exc_info.value).lower()
        assert 'immediately' in error_msg
        assert 'shared' in error_msg


class TestGetLock:
    """Test get_lock factory function."""

    @mock.patch.object(locks, '_detect_lock_type')
    def test_get_lock_auto_detect_postgres(self, mock_detect):
        """Test get_lock with auto-detection returning postgres."""
        mock_detect.return_value = 'postgres'

        lock = locks.get_lock('test_lock')

        assert isinstance(lock, locks.PostgresLock)
        assert lock.lock_id == 'test_lock'
        mock_detect.assert_called_once()

    @mock.patch.object(locks, '_detect_lock_type')
    def test_get_lock_auto_detect_filelock(self, mock_detect):
        """Test get_lock with auto-detection returning filelock."""
        mock_detect.return_value = 'filelock'

        lock = locks.get_lock('test_lock')

        assert isinstance(lock, locks.FileLock)
        assert lock.lock_id == 'test_lock'
        mock_detect.assert_called_once()

    def test_get_lock_explicit_postgres(self):
        """Test get_lock with explicit postgres type."""
        lock = locks.get_lock('test_lock', lock_type='postgres')

        assert isinstance(lock, locks.PostgresLock)
        assert lock.lock_id == 'test_lock'

    def test_get_lock_explicit_filelock(self):
        """Test get_lock with explicit filelock type."""
        lock = locks.get_lock('test_lock', lock_type='filelock')

        assert isinstance(lock, locks.FileLock)
        assert lock.lock_id == 'test_lock'

    def test_get_lock_with_timeout_and_poll_interval(self):
        """Test get_lock with timeout and poll interval."""
        lock = locks.get_lock('test_lock',
                              timeout=5.0,
                              lock_type='filelock',
                              poll_interval=0.5)

        assert isinstance(lock, locks.FileLock)
        assert lock.timeout == 5.0
        assert lock.poll_interval == 0.5

    def test_get_lock_invalid_type(self):
        """Test get_lock with invalid lock type."""
        with pytest.raises(ValueError) as exc_info:
            locks.get_lock('test_lock', lock_type='invalid')

        assert "Unknown lock type: invalid" in str(exc_info.value)

    def test_get_lock_postgres_with_shared_mode(self):
        """Test get_lock factory with shared mode for postgres."""
        lock = locks.get_lock('test_lock',
                              lock_type='postgres',
                              shared_lock=True)

        assert isinstance(lock, locks.PostgresLock)
        assert lock._shared_lock

    def test_get_lock_filelock_with_shared_mode(self):
        """Test FileLock with shared mode (no effect)."""
        lock = locks.get_lock('test_lock',
                              lock_type='filelock',
                              shared_lock=True)

        assert isinstance(lock, locks.FileLock)


class TestDetectLockType:
    """Test _detect_lock_type function."""

    @mock.patch.object(global_user_state, 'initialize_and_get_db')
    def test_detect_lock_type_postgres(self, mock_init_db):
        """Test detection of postgres lock type."""
        mock_engine = mock.Mock()
        mock_engine.dialect.name = db_utils.SQLAlchemyDialect.POSTGRESQL.value
        mock_init_db.return_value = mock_engine

        result = locks._detect_lock_type()

        assert result == 'postgres'
        mock_init_db.assert_called_once()

    @mock.patch.object(global_user_state, 'initialize_and_get_db')
    def test_detect_lock_type_sqlite_fallback(self, mock_init_db):
        """Test fallback to filelock for sqlite."""
        mock_engine = mock.Mock()
        mock_engine.dialect.name = 'sqlite'
        mock_init_db.return_value = mock_engine

        result = locks._detect_lock_type()

        assert result == 'filelock'

    @mock.patch.object(global_user_state, 'initialize_and_get_db')
    def test_detect_lock_type_exception_fallback(self, mock_init_db):
        """Test fallback to filelock on exception."""
        mock_init_db.side_effect = Exception("Database error")

        result = locks._detect_lock_type()

        assert result == 'filelock'


class TestDistributedLockIntegration:
    """Integration tests for distributed locks."""

    def test_file_lock_multiple_processes_simulation(self):
        """Test file lock behavior with multiple 'processes' (locks)."""
        lock1 = locks.FileLock('integration_test')
        lock2 = locks.FileLock('integration_test', timeout=0.1)

        # First lock should acquire successfully
        with lock1:
            # Second lock should fail due to timeout
            with pytest.raises(locks.LockTimeout):
                with lock2:
                    pass

        # After first lock is released, second should work
        with lock2:
            pass

    def test_lock_cleanup_on_exception(self):
        """Test that locks are properly cleaned up on exceptions."""
        lock = locks.FileLock('cleanup_test')

        try:
            with lock:
                assert os.path.exists(lock.lock_path)
                raise RuntimeError("Test exception")
        except RuntimeError:
            pass

        # Lock file should still exist but be unlocked
        # (filelock doesn't delete the file, just releases the lock)

    def test_nested_lock_contexts(self):
        """Test nested lock contexts with different lock IDs."""
        lock1 = locks.FileLock('nested1')
        lock2 = locks.FileLock('nested2')

        with lock1:
            with lock2:
                # Both locks should be active
                assert os.path.exists(lock1.lock_path)
                assert os.path.exists(lock2.lock_path)

    @mock.patch.object(locks, '_detect_lock_type')
    def test_context_manager_with_auto_detection(self, mock_detect):
        """Test context manager with auto-detection."""
        mock_detect.return_value = 'filelock'

        with locks.get_lock('auto_detect_test') as lock:
            assert isinstance(lock, locks.FileLock)

        mock_detect.assert_called_once()
