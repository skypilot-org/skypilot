"""API server blob storage for filemounts."""

from __future__ import annotations

import abc
import contextlib
import os
import pathlib
from typing import Generator, List, Optional, Tuple

from sky import sky_logging
from sky.skylet import constants

logger = sky_logging.init_logger(__name__)

# Grace period before an unreferenced blob is eligible for GC (seconds).
GC_GRACE_SECONDS = 3600


class BlobStorage(abc.ABC):
    """Abstract interface for file mounts blob storage."""

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
        del user_id, blob_id
        yield None

    @abc.abstractmethod
    async def store_blob(self, user_id: str, blob_id: str,
                         staging_dir: pathlib.Path) -> None:
        """Atomically move a staging directory to the final blob location.

        Called inside ``acquire_upload_lock``.  The *staging_dir* already
        contains the extracted contents.
        """
        raise NotImplementedError

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

    @contextlib.contextmanager
    def gc_lock(self) -> Generator[bool, None, None]:
        """Acquire a GC coordination lock.

        Yields ``True`` if this caller should run GC, ``False`` otherwise.
        Default: always yields ``True`` (single-replica mode).
        """
        yield True

    @abc.abstractmethod
    def blobs_dir(self, user_id: str) -> pathlib.Path:
        """Return the base blobs directory for a user."""
        raise NotImplementedError

    def file_mounts_tmp_dir(self) -> str:
        """Return a base directory for temporary file-mount staging.

        The directory need to be on the same filesystem / block device as
        the blob directories to avoid the copy cost.
        """
        return os.path.expanduser(constants.FILE_MOUNTS_LOCAL_TMP_BASE_PATH)

    def download_tmp_dir(self) -> str:
        """Return a base directory for temporary log downloads.

        In multi-replica mode this must be on shared storage so that
        any replica can serve the ``/download`` request after another
        replica synced the logs from the cluster.
        """
        return os.path.expanduser('~/.sky/api_server/download_tmp')

    def get_staging_dir(self, user_id: str, blob_id: str) -> pathlib.Path:
        """Return the staging directory path for an in-progress upload."""
        return self.blobs_dir(user_id) / '.staging' / blob_id

    def get_target_dir(self, user_id: str, blob_id: str) -> pathlib.Path:
        """Return the final blob directory path."""
        return self.blobs_dir(user_id) / blob_id


_blob_storage: Optional[BlobStorage] = None


def get_blob_storage() -> BlobStorage:
    """Get the registered blob storage backend."""
    global _blob_storage
    if _blob_storage is None:
        logger.debug('get_blob_storage: no backend registered, '
                     'using LocalFilesystemBlobStorage')
        # Avoid circular deps
        # pylint: disable=import-outside-toplevel
        from sky.server.blob import local_blob_storage as lbs
        _blob_storage = lbs.LocalFilesystemBlobStorage()
        os.makedirs(_blob_storage.download_tmp_dir(), exist_ok=True)
    return _blob_storage


def set_blob_storage(backend: BlobStorage) -> None:
    """Set the blob storage backend.

    Called by plugins via
    ``ExtensionContext.register_blob_storage()``.
    """
    global _blob_storage
    _blob_storage = backend
