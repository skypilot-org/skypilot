"""Lock for SkyPilot.

This module provides an abstraction for locking that can use
either local file locks or database-based distributed locks.
"""
import abc
import hashlib
import logging
import os
import time
from typing import Any, Optional

import filelock
import psycopg2
import sqlalchemy

from sky import global_user_state
from sky.skylet import runtime_utils
from sky.utils import common_utils
from sky.utils.db import db_utils

logger = logging.getLogger(__name__)

# The directory for file locks.
SKY_LOCKS_DIR = runtime_utils.get_runtime_dir_path('.sky/locks')


class LockTimeout(RuntimeError):
    """Raised when a lock acquisition times out."""
    pass


class AcquireReturnProxy:
    """A context manager that releases the lock when exiting.

    This proxy is returned by acquire() and ensures proper cleanup
    when used in a with statement.
    """

    def __init__(self, lock: 'DistributedLock') -> None:
        self.lock = lock

    def __enter__(self) -> 'DistributedLock':
        return self.lock

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.lock.release()


class DistributedLock(abc.ABC):
    """Abstract base class for a distributed lock.

    Provides a context manager interface for acquiring and releasing locks
    that can work across multiple processes and potentially multiple machines.
    """

    def __init__(self,
                 lock_id: str,
                 timeout: Optional[float] = None,
                 poll_interval: float = 0.1):
        """Initialize the lock.

        Args:
            lock_id: Unique identifier for the lock.
            timeout: Maximum time to wait for lock acquisition.
                If None, wait indefinitely.
            poll_interval: Interval in seconds to poll for lock acquisition.
        """
        self.lock_id = lock_id
        self.timeout = timeout
        self.poll_interval = poll_interval

    @abc.abstractmethod
    def acquire(self, blocking: bool = True) -> AcquireReturnProxy:
        """Acquire the lock.

        Args:
            blocking: If True, block until lock is acquired or timeout.
                     If False, return immediately.

        Returns:
            AcquireReturnProxy that can be used as a context manager.

        Raises:
            LockTimeout: If lock cannot be acquired.
        """
        pass

    @abc.abstractmethod
    def release(self) -> None:
        """Release the lock."""
        pass

    @abc.abstractmethod
    def force_unlock(self) -> None:
        """Force unlock the lock if it is acquired."""
        pass

    @abc.abstractmethod
    def is_locked(self) -> bool:
        """Check if the lock is acquired."""
        pass

    def __enter__(self) -> 'DistributedLock':
        """Context manager entry."""
        self.acquire()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Context manager exit."""
        self.release()


class FileLock(DistributedLock):
    """A wrapper around filelock.FileLock.

    This implements a distributed lock that works across multiple processes
    when they share the same filesystem.
    """

    def __init__(self,
                 lock_id: str,
                 timeout: Optional[float] = None,
                 poll_interval: float = 0.1):
        """Initialize the file lock.

        Args:
            lock_id: Unique identifier for the lock.
            timeout: Maximum time to wait for lock acquisition.
            poll_interval: Interval in seconds to poll for lock acquisition.
        """
        super().__init__(lock_id, timeout, poll_interval)
        os.makedirs(SKY_LOCKS_DIR, exist_ok=True)
        self.lock_path = os.path.join(SKY_LOCKS_DIR, f'.{lock_id}.lock')
        if timeout is None:
            timeout = -1
        self._filelock: filelock.FileLock = filelock.FileLock(self.lock_path,
                                                              timeout=timeout)

    def acquire(self, blocking: bool = True) -> AcquireReturnProxy:
        """Acquire the file lock."""
        try:
            acquired = self._filelock.acquire(blocking=blocking)
            if not acquired:
                raise LockTimeout(f'Failed to acquire file lock {self.lock_id}')
            return AcquireReturnProxy(self)
        except filelock.Timeout as e:
            raise LockTimeout(
                f'Failed to acquire file lock {self.lock_id}') from e

    def release(self) -> None:
        """Release the file lock."""
        self._filelock.release()

    def force_unlock(self) -> None:
        """Force unlock the file lock."""
        common_utils.remove_file_if_exists(self.lock_path)

    def is_locked(self) -> bool:
        return self._filelock.is_locked


class PostgresLock(DistributedLock):
    """PostgreSQL advisory lock implementation.

    Uses PostgreSQL advisory locks to implement distributed locking
    that works across multiple machines sharing the same database.
    Supports both exclusive and shared lock modes.

    References:
    # pylint: disable=line-too-long
    - https://www.postgresql.org/docs/current/explicit-locking.html#ADVISORY-LOCKS
    - https://www.postgresql.org/docs/current/functions-admin.html#FUNCTIONS-ADVISORY-LOCKS
    # TODO(cooperc): re-enable pylint line-too-long
    """

    def __init__(self,
                 lock_id: str,
                 timeout: Optional[float] = None,
                 poll_interval: float = 1,
                 shared_lock: bool = False):
        """Initialize the postgres lock.

        Args:
            lock_id: Unique identifier for the lock.
            timeout: Maximum time to wait for lock acquisition.
            poll_interval: Interval in seconds to poll for lock acquisition,
                default to 1 second to avoid storming the database.
            shared_lock: Whether to use shared advisory lock or exclusive
                advisory lock (default).
        """
        super().__init__(lock_id, timeout, poll_interval)
        # Convert string lock_id to integer for postgres advisory locks
        self._lock_key = self._string_to_lock_key(lock_id)
        self._shared_lock = shared_lock
        self._acquired = False
        self._connection: Optional[sqlalchemy.pool.PoolProxiedConnection] = None

    def _string_to_lock_key(self, s: str) -> int:
        """Convert string to a 64-bit integer for advisory lock key."""
        hash_digest = hashlib.sha256(s.encode('utf-8')).digest()
        # Take first 8 bytes and convert to int, ensure positive 64-bit
        return int.from_bytes(hash_digest[:8], 'big') & ((1 << 63) - 1)

    def _get_connection(self) -> sqlalchemy.pool.PoolProxiedConnection:
        """Get database connection."""
        engine = global_user_state.initialize_and_get_db()
        if engine.dialect.name != db_utils.SQLAlchemyDialect.POSTGRESQL.value:
            raise ValueError('PostgresLock requires PostgreSQL database. '
                             f'Current dialect: {engine.dialect.name}')
        # Borrow a dedicated connection from the pool.
        return engine.raw_connection()

    def acquire(self, blocking: bool = True) -> AcquireReturnProxy:
        """Acquire the postgres advisory lock."""
        if self._acquired:
            return AcquireReturnProxy(self)

        self._connection = self._get_connection()
        cursor = self._connection.cursor()

        start_time = time.time()

        if self._shared_lock:
            lock_func = 'pg_try_advisory_lock_shared'
        else:
            lock_func = 'pg_try_advisory_lock'

        try:
            while True:
                cursor.execute(f'SELECT {lock_func}(%s)', (self._lock_key,))
                result = cursor.fetchone()[0]

                if result:
                    self._acquired = True
                    return AcquireReturnProxy(self)

                mode_str = ('shared' if self._shared_lock else 'exclusive')
                if not blocking:
                    raise LockTimeout(
                        f'Failed to immediately acquire {mode_str} '
                        f'postgres lock {self.lock_id}')

                if (self.timeout is not None and
                        time.time() - start_time > self.timeout):
                    raise LockTimeout(
                        f'Failed to acquire {mode_str} postgres lock '
                        f'{self.lock_id} within {self.timeout} '
                        f'seconds')

                time.sleep(self.poll_interval)

        except Exception:
            self._close_connection()
            raise

    def release(self) -> None:
        """Release the postgres advisory lock."""
        if not self._acquired or not self._connection:
            return

        connection_lost = False
        try:
            cursor = self._connection.cursor()
            if self._shared_lock:
                unlock_func = 'pg_advisory_unlock_shared'
            else:
                unlock_func = 'pg_advisory_unlock'
            cursor.execute(f'SELECT {unlock_func}(%s)', (self._lock_key,))
            self._connection.commit()
            self._acquired = False
        except psycopg2.OperationalError as e:
            # Lost connection to the database, likely the lock is force unlocked
            # by other routines.
            logger.debug(f'Failed to release postgres lock {self.lock_id}: {e}')
            connection_lost = True
        finally:
            # Invalidate if connection was lost to prevent SQLAlchemy from
            # trying to reset a dead connection
            self._close_connection(invalidate=connection_lost)

    def force_unlock(self) -> None:
        """Force unlock the postgres advisory lock."""
        try:
            # The lock is held by current routine, gracefully unlock it
            if self._acquired:
                self.release()
                return

            # The lock is held by another routine, force unlock it.
            if self._connection is None:
                self._connection = self._get_connection()
            cursor = self._connection.cursor()
            if self._shared_lock:
                unlock_func = 'pg_advisory_unlock_shared'
            else:
                unlock_func = 'pg_advisory_unlock'

            cursor.execute(f'SELECT {unlock_func}(%s)', (self._lock_key,))
            result = cursor.fetchone()[0]
            if result:
                # The lock is held by current routine and unlock succeed
                self._connection.commit()
                self._acquired = False
                return
            cursor.execute(
                ('SELECT pid FROM pg_locks WHERE locktype = \'advisory\' '
                 'AND ((classid::bigint << 32) | objid::bigint) = %s'),
                (self._lock_key,))
            rows = cursor.fetchall()
            if rows:
                # There can be multiple PIDs holding the lock, it is not enough
                # to only kill some of them. For example, if pid 1 is holding a
                # shared lock, and pid 2 is waiting to grab an exclusive lock,
                # killing pid 1 will transfer the lock to pid 2, so the lock
                # will still not be released.
                for row in rows:
                    cursor.execute('SELECT pg_terminate_backend(%s)', (row[0],))
                self._connection.commit()
                return
        except Exception as e:
            raise RuntimeError(
                f'Failed to force unlock postgres lock {self.lock_id}: {e}'
            ) from e
        finally:
            self._close_connection()

    def _close_connection(self, invalidate: bool = False) -> None:
        """Close the postgres connection.

        Args:
            invalidate: If True, invalidate connection instead of closing it.
                Use this when the connection might be broken (e.g., after
                pg_terminate_backend) to prevent SQLAlchemy from trying to
                reset it (which would result in an error being logged).
        """
        if self._connection:
            try:
                if invalidate:
                    self._connection.invalidate()
                else:
                    self._connection.close()
            except Exception as e:  # pylint: disable=broad-except
                if invalidate:
                    logger.debug(
                        f'Failed to invalidate postgres connection: {e}')
                else:
                    logger.debug(f'Failed to close postgres connection: {e}')
            self._connection = None

    def is_locked(self) -> bool:
        """Check if the postgres advisory lock is acquired."""
        return self._acquired


def get_lock(lock_id: str,
             timeout: Optional[float] = None,
             lock_type: Optional[str] = None,
             poll_interval: Optional[float] = None,
             shared_lock: bool = False) -> DistributedLock:
    """Create a distributed lock instance.

    Args:
        lock_id: Unique identifier for the lock.
        timeout: Maximum time seconds to wait for lock acquisition,
                 None means wait indefinitely.
        lock_type: Type of lock to create ('filelock' or 'postgres').
                   If None, auto-detect based on database configuration.
        poll_interval: Interval in seconds to poll for lock acquisition.
        shared_lock: Whether to use shared lock or exclusive lock (default).
                     NOTE: Only applicable for PostgresLock.

    Returns:
        DistributedLock instance.
    """
    if lock_type is None:
        lock_type = _detect_lock_type()

    if lock_type == 'postgres':
        if poll_interval is None:
            return PostgresLock(lock_id, timeout, shared_lock=shared_lock)
        return PostgresLock(lock_id,
                            timeout,
                            poll_interval,
                            shared_lock=shared_lock)
    elif lock_type == 'filelock':
        # The filelock library we use does not support shared locks.
        # It explicitly uses fcntl.LOCK_EX on Unix systems,
        # whereas fcntl.LOCK_SH is needed for shared locks.

        # This should be fine as it should not introduce correctness issues,
        # just that concurrency is reduced and so is performance, because
        # read-only operations can't run at the same time, each of them need
        # to wait to exclusively hold the lock.

        # But given that we recommend users to use Postgres in production,
        # the impact of this should be limited to local API server mostly.
        del shared_lock
        if poll_interval is None:
            return FileLock(lock_id, timeout)
        return FileLock(lock_id, timeout, poll_interval)
    else:
        raise ValueError(f'Unknown lock type: {lock_type}')


def _detect_lock_type() -> str:
    """Auto-detect the appropriate lock type based on configuration."""
    try:
        engine = global_user_state.initialize_and_get_db()
        if engine.dialect.name == db_utils.SQLAlchemyDialect.POSTGRESQL.value:
            return 'postgres'
    except Exception:  # pylint: disable=broad-except
        # Fall back to filelock if database detection fails
        pass

    return 'filelock'
