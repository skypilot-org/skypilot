"""Local-file-system blob storage"""

import asyncio
import contextlib
import os
import pathlib
import shutil
import time
from typing import List, Tuple

import anyio
import filelock

from sky import sky_logging
from sky.server import common as server_common
from sky.server.blob import blob_storage as bs
from sky.server.requests import executor

logger = sky_logging.init_logger(__name__)


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
        """Called on server startup to clean up ephemeral client state.

        We clear the transient per-request state (uploaded task YAMLs and the
        legacy non-blob file_mounts upload dirs) so that a freshly started
        server begins from a clean slate, matching the request DB reset.

        We deliberately preserve ``file_mounts/blobs/``: blobs are
        content-addressed, atomically committed and may still be referenced by
        a non-terminal managed job that outlived the server restart. Wiping
        them here would break job recovery (the controller resolves the blob on
        relaunch) even though the dir is on persistent storage. Their lifecycle
        is owned by the reference-aware GC in
        ``server.cleanup_unreferenced_file_mounts`` instead.
        """
        clients_dir = server_common.API_SERVER_CLIENT_DIR.expanduser()
        logger.debug('clearing transient local API server client state at '
                     f'{clients_dir} (preserving file_mounts/blobs)')
        if not clients_dir.exists():
            return
        for user_dir in clients_dir.iterdir():
            if not user_dir.is_dir():
                continue
            # Uploaded task YAMLs are tied to request ids that do not survive
            # the restart.
            shutil.rmtree(user_dir / 'tasks', ignore_errors=True)
            # Under file_mounts/, keep only the content-addressed blobs dir;
            # everything else is legacy per-request upload state.
            file_mounts_dir = user_dir / 'file_mounts'
            if not file_mounts_dir.is_dir():
                continue
            for entry in file_mounts_dir.iterdir():
                if entry.name == 'blobs':
                    continue
                if entry.is_dir():
                    shutil.rmtree(entry, ignore_errors=True)
                else:
                    try:
                        entry.unlink()
                    except OSError:
                        pass
