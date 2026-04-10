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

from sky.server import common as server_common
from sky.server.blob import blob_storage as bs
from sky.server.requests import executor


class LocalFilesystemBlobStorage(bs.BlobStorage):
    """Local-filesystem blob storage"""

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
