"""Local-file-system blob storage"""

import asyncio
import contextlib
import os
import pathlib
import shutil
import time
from typing import List, Optional, Set, Tuple

import anyio
import filelock

from sky import sky_logging
from sky.server import common as server_common
from sky.server.blob import blob_storage as bs
from sky.server.requests import executor

logger = sky_logging.init_logger(__name__)


def _remove_dir_contents_except(root: pathlib.Path,
                                keep: Set[pathlib.Path]) -> None:
    """Remove everything under ``root`` except the paths in ``keep``.

    ``root`` itself is never removed. Directories that contain a kept path
    are recursed into (and so survive); any directory or file that does not
    lead to a kept path is removed wholesale.
    """
    root = root.resolve()
    if root in keep:
        return
    has_kept_descendant = any(root in path.parents for path in keep)
    if not has_kept_descendant:
        if root.is_dir() and not root.is_symlink():
            shutil.rmtree(root, ignore_errors=True)
        else:
            try:
                root.unlink()
            except OSError:
                pass
        return
    for child in root.iterdir():
        _remove_dir_contents_except(child, keep)


class LocalFilesystemBlobStorage(bs.BlobStorage):
    """Local-filesystem blob storage"""

    def download_tmp_dir(self, user_hash: str) -> str:
        path = server_common.api_server_user_logs_dir_prefix(user_hash)
        path.expanduser().mkdir(parents=True, exist_ok=True)
        return str(path)

    def download_tmp_base_dir(self):
        # Downloads share the persistent log directory; no separate cleanup.
        return None

    def blobs_dir(self, user_id: str) -> pathlib.Path:

        return (server_common.API_SERVER_CLIENT_DIR.expanduser().resolve() /
                user_id / 'file_mounts' / 'blobs')

    async def blob_exists(self, user_id: str, blob_id: str) -> bool:
        target = self.get_target_dir(user_id, blob_id)
        if target.is_dir():
            await asyncio.to_thread(os.utime, target)
            return True
        return False

    @contextlib.asynccontextmanager
    async def acquire_upload_lock(self, user_id: str, blob_id: str):

        locks_dir = self.blobs_dir(user_id) / '.locks'
        await anyio.Path(locks_dir).mkdir(parents=True, exist_ok=True)
        lock = filelock.AsyncFileLock(
            lock_file=str(locks_dir / f'{blob_id}.lock'),
            executor=executor.get_request_thread_executor(),
        )
        async with lock:
            yield

    async def store_blob(self, user_id: str, blob_id: str,
                         staging_dir: pathlib.Path) -> None:

        target = self.get_target_dir(user_id, blob_id)
        await asyncio.to_thread(os.rename, str(staging_dir), str(target))

    def resolve_blob_to_dir(self, user_id: str, blob_id: str) -> str:
        target = self.get_target_dir(user_id, blob_id)
        if not target.is_dir():
            raise FileNotFoundError(f'Blob not found: {target}.')
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
        grace_cutoff = time.time() - bs.GC_GRACE_SECONDS
        for entry in staging_base.iterdir():
            if entry.is_dir():
                try:
                    if entry.stat().st_mtime < grace_cutoff:
                        shutil.rmtree(entry, ignore_errors=True)
                except OSError:
                    pass

    def list_users(self) -> List[str]:

        clients_dir = server_common.API_SERVER_CLIENT_DIR.expanduser().resolve()
        if not clients_dir.exists():
            return []
        users = []
        for entry in clients_dir.iterdir():
            if entry.is_dir() and (entry / 'file_mounts' / 'blobs').is_dir():
                users.append(entry.name)
        return users

    def reset_on_startup(self) -> None:
        """Reclaim ephemeral client state on server startup.

        Historically this wiped the entire client directory. That is unsafe:
        a file-mounts blob referenced by a non-terminal managed job must
        survive a server restart, because the jobs controller re-resolves the
        blob to stage file mounts when it recovers the job. Wiping it breaks
        recovery with a ``Blob not found`` error.

        Instead, preserve blobs that are still referenced -- reusing the same
        ref-count as the periodic GC (``cleanup_unreferenced_file_mounts``):
        the union of blobs referenced by active requests and by non-terminal
        managed jobs -- and remove everything else (unreferenced blobs, stale
        uploads, and per-user log/download staging).
        """
        client_dir = server_common.API_SERVER_CLIENT_DIR.expanduser().resolve()
        logger.debug(
            f'resetting local API server client directory at {client_dir}')
        if not client_dir.exists():
            return

        # Imported here rather than at module scope to avoid an import cycle
        # during server bootstrap; reset_on_startup runs once at startup, so
        # the import cost is irrelevant.
        # pylint: disable=import-outside-toplevel
        from sky.jobs import state as managed_job_state
        from sky.server.requests import requests as requests_lib
        active_blob_ids: Optional[Set[str]]
        try:
            active_blob_ids = (
                requests_lib.get_active_file_mounts_blob_ids() |
                managed_job_state.get_active_file_mounts_blob_ids())
        except Exception as e:  # pylint: disable=broad-except
            # If we cannot determine which blobs are still referenced, preserve
            # all of them: a leaked blob is reclaimed later by the periodic GC,
            # but a wrongly-deleted blob breaks managed-job recovery.
            logger.warning(
                'Failed to query active file-mount blobs on startup; '
                f'preserving all blobs. Error: {e}')
            active_blob_ids = None

        preserved: Set[pathlib.Path] = set()
        for user_id in self.list_users():
            for blob_id, _ in self.list_blob_ids(user_id):
                if active_blob_ids is None or blob_id in active_blob_ids:
                    preserved.add(
                        self.get_target_dir(user_id, blob_id).resolve())

        if not preserved:
            # Nothing to preserve: keep the cheap blanket wipe.
            shutil.rmtree(client_dir, ignore_errors=True)
            return

        _remove_dir_contents_except(client_dir, preserved)
