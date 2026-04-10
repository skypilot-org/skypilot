"""Abstract interface for file mounts blob storage.

Blobs are zip archives of local file mounts, content-addressed by SHA256.
The default implementation stores extracted directories on the local
filesystem with filelock-based coordination.  Plugins may provide
alternative implementations (e.g., shared-FS with PG advisory locks)
via ``ExtensionContext.register_blob_storage()``.
"""

from __future__ import annotations

import abc
import contextlib
import os
import pathlib
import shutil
import time
from typing import Generator, List, Optional, Tuple

import filelock

from sky import sky_logging
from sky.skylet import constants

logger = sky_logging.init_logger(__name__)

# Grace period before an unreferenced blob is eligible for GC (seconds).
_GC_GRACE_SECONDS = 3600


class BlobStorage(abc.ABC):
    """Abstract interface for file mounts blob storage."""

    # --- Async (called from FastAPI upload handlers) ---

    @abc.abstractmethod
    async def blob_exists(self, user_id: str, blob_id: str) -> bool:
        """Check if a blob exists.  If it does, refresh its mtime."""
        raise NotImplementedError

    @abc.abstractmethod
    @contextlib.asynccontextmanager
    async def acquire_upload_lock(self, user_id: str, blob_id: str):
        """Context manager for coordinating concurrent uploads of the same blob.

        For LocalFilesystem: wraps ``filelock.AsyncFileLock``.
        For SharedFS: wraps a PG advisory lock.
        """
        raise NotImplementedError
        yield  # pylint: disable=unreachable

    @abc.abstractmethod
    async def store_blob(self, user_id: str, blob_id: str,
                         staging_dir: pathlib.Path) -> None:
        """Atomically move a staging directory to the final blob location.

        Called inside ``acquire_upload_lock``.  The *staging_dir* already
        contains the extracted contents.
        """
        raise NotImplementedError

    # --- Sync (called from GC thread / executor resolve) ---

    @abc.abstractmethod
    def resolve_blob_to_dir(self, user_id: str, blob_id: str) -> str:
        """Return the absolute path to the extracted blob directory.

        Raises ``FileNotFoundError`` if the blob does not exist.
        """
        raise NotImplementedError

    @abc.abstractmethod
    def delete_blob(self, user_id: str, blob_id: str) -> None:
        """Delete a blob directory."""
        raise NotImplementedError

    @abc.abstractmethod
    def list_blob_ids(self, user_id: str) -> List[Tuple[str, float]]:
        """List ``(blob_id, mtime)`` pairs for a user.

        Excludes internal directories (``.locks``, ``.staging``).
        """
        raise NotImplementedError

    @abc.abstractmethod
    def release_stale_uploads(self, user_id: str) -> None:
        """Clean up stale staging directories for a user."""
        raise NotImplementedError

    @abc.abstractmethod
    def list_users(self) -> List[str]:
        """List user IDs that have blob directories.

        Used by GC to discover which users need cleanup.
        """
        raise NotImplementedError

    # --- GC coordination ---

    @contextlib.contextmanager
    def gc_lock(self) -> Generator[bool, None, None]:
        """Acquire a GC coordination lock.

        Yields ``True`` if this caller should run GC, ``False`` otherwise.
        Default: always yields ``True`` (single-replica mode).
        """
        yield True

    # --- Path helpers ---

    @abc.abstractmethod
    def blobs_dir(self, user_id: str) -> pathlib.Path:
        """Return the base blobs directory for a user."""
        raise NotImplementedError

    def file_mounts_tmp_dir(self) -> str:
        """Return a base directory for temporary file-mount hard-link staging.

        The directory **must** be on the same filesystem / block device as
        the blob directories so that ``os.link()`` works without falling
        back to a full copy.  The default returns ``~/.sky/tmp/`` which is
        on the same device as the default blob location under ``~/.sky/``.
        """
        return os.path.expanduser(constants.FILE_MOUNTS_LOCAL_TMP_BASE_PATH)

    def get_staging_dir(self, user_id: str, blob_id: str) -> pathlib.Path:
        """Return the staging directory path for an in-progress upload."""
        return self.blobs_dir(user_id) / '.staging' / blob_id

    def get_target_dir(self, user_id: str, blob_id: str) -> pathlib.Path:
        """Return the final blob directory path."""
        return self.blobs_dir(user_id) / blob_id


class LocalFilesystemBlobStorage(BlobStorage):
    """Local-filesystem blob storage (default, single-replica).

    Preserves the exact current behaviour from ``server.py`` and
    ``common.py``.
    """

    def blobs_dir(self, user_id: str) -> pathlib.Path:
        # pylint: disable=import-outside-toplevel
        from sky.server import common as server_common

        return (server_common.API_SERVER_CLIENT_DIR.expanduser().resolve() /
                user_id / 'file_mounts' / 'blobs')

    # --- Async ---

    async def blob_exists(self, user_id: str, blob_id: str) -> bool:
        target = self.get_target_dir(user_id, blob_id)
        if target.is_dir():
            os.utime(target)
            return True
        return False

    @contextlib.asynccontextmanager
    async def acquire_upload_lock(self, user_id: str, blob_id: str):
        # pylint: disable=import-outside-toplevel
        from sky.server.requests import executor

        locks_dir = self.blobs_dir(user_id) / '.locks'
        locks_dir.mkdir(parents=True, exist_ok=True)
        lock = filelock.AsyncFileLock(
            lock_file=str(locks_dir / f'{blob_id}.lock'),
            executor=executor.get_request_thread_executor(),
        )
        async with lock:
            yield

    async def store_blob(self, user_id: str, blob_id: str,
                         staging_dir: pathlib.Path) -> None:
        import asyncio  # pylint: disable=import-outside-toplevel

        target = self.get_target_dir(user_id, blob_id)
        await asyncio.to_thread(os.rename, str(staging_dir), str(target))

    # --- Sync ---

    def resolve_blob_to_dir(self, user_id: str, blob_id: str) -> str:
        target = self.get_target_dir(user_id, blob_id)
        if not target.is_dir():
            raise FileNotFoundError(
                f'Blob not found: {target}. The file mounts blob may '
                'have been garbage collected before execution started.')
        return str(target)

    def delete_blob(self, user_id: str, blob_id: str) -> None:
        target = self.get_target_dir(user_id, blob_id)
        shutil.rmtree(target, ignore_errors=True)

    def list_blob_ids(self, user_id: str) -> List[Tuple[str, float]]:
        bd = self.blobs_dir(user_id)
        if not bd.exists():
            return []
        result = []
        for entry in bd.iterdir():
            if not entry.is_dir():
                continue
            if entry.name in ('.locks', '.staging'):
                continue
            try:
                result.append((entry.name, entry.stat().st_mtime))
            except OSError:
                pass
        return result

    def release_stale_uploads(self, user_id: str) -> None:
        staging_base = self.blobs_dir(user_id) / '.staging'
        if not staging_base.exists():
            return
        grace_cutoff = time.time() - _GC_GRACE_SECONDS
        for entry in staging_base.iterdir():
            if entry.is_dir():
                try:
                    if entry.stat().st_mtime < grace_cutoff:
                        shutil.rmtree(entry, ignore_errors=True)
                except OSError:
                    pass

    def list_users(self) -> List[str]:
        # pylint: disable=import-outside-toplevel
        from sky.server import common as server_common

        clients_dir = server_common.API_SERVER_CLIENT_DIR.expanduser().resolve()
        if not clients_dir.exists():
            return []
        users = []
        for entry in clients_dir.iterdir():
            if entry.is_dir() and (entry / 'file_mounts' / 'blobs').is_dir():
                users.append(entry.name)
        return users


_blob_storage: Optional[BlobStorage] = None


def get_blob_storage() -> BlobStorage:
    """Get the registered blob storage backend.

    Returns the plugin-provided backend if one has been registered,
    otherwise lazily creates and returns the default
    ``LocalFilesystemBlobStorage``.
    """
    global _blob_storage
    if _blob_storage is None:
        logger.info('get_blob_storage: no backend registered, '
                    'using LocalFilesystemBlobStorage (default)')
        _blob_storage = LocalFilesystemBlobStorage()
    return _blob_storage


def set_blob_storage(backend: BlobStorage) -> None:
    """Set the blob storage backend.

    Called by plugins via
    ``ExtensionContext.register_blob_storage()``.
    """
    global _blob_storage
    _blob_storage = backend
