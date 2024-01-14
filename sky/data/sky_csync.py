"""CSYNC module"""
import functools
import multiprocessing
import os
import shutil
import subprocess
import threading
import time
from typing import Any, List, Optional, Tuple

import click
from fuse import FUSE
from fuse import FuseOSError
from fuse import Operations
import psutil

from sky import sky_logging
from sky.data import mounting_utils
from sky.data import storage_utils
from sky.utils import common_utils
from sky.utils import db_utils

logger = sky_logging.init_logger(__name__)

_CSYNC_PATH = '~/.sky/csync'
_CSYNC_DB_PATH = os.path.join(_CSYNC_PATH, 'sky_csync.db')
_CSYNC_READ_PATH = os.path.join(_CSYNC_PATH, 'read_{pid}')
_CSYNC_WRITE_PATH = os.path.join(_CSYNC_PATH, 'write_{pid}')

_BOOT_TIME = None
_CURSOR = None
_CONN = None
_DB = None
_MAX_SYNC_RETRIES = 10
# Allowing minimal seconds of interval between SYNC calls.
_MIN_SYNC_CALL_INTERVAL = 3
_DEFAULT_S3_NUM_THREAD = 10
CSYNC_DEFAULT_INTERVAL_SECONDS = 600


def connect_db(func):

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        global _DB, _CURSOR, _CONN, _BOOT_TIME

        def create_table(cursor, conn):
            cursor.execute("""\
                CREATE TABLE IF NOT EXISTS running_csync (
                csync_pid INTEGER PRIMARY KEY,
                sync_pid INTEGER DEFAULT -1,
                storage_mount_pid INTEGER,
                fuse_pid INTEGER,
                mountpoint_path TEXT,
                boot_time FLOAT)""")

            conn.commit()

        if _DB is None:
            db_path = os.path.expanduser(_CSYNC_DB_PATH)
            os.makedirs(os.path.abspath(os.path.expanduser(_CSYNC_PATH)),
                        exist_ok=True)
            _DB = db_utils.SQLiteConn(db_path, create_table)

        _CURSOR = _DB.cursor
        _CONN = _DB.conn
        _BOOT_TIME = psutil.boot_time()

        return func(*args, **kwargs)

    return wrapper


@connect_db
def _add_running_csync(csync_pid: int, storage_mount_pid: int, fuse_pid: int,
                       mountpoint_path: str):
    """Given the ids processes necessary to CSYNC, it creates a row with it"""
    assert _CURSOR is not None
    assert _CONN is not None
    _CURSOR.execute(
        'INSERT INTO running_csync '
        '(csync_pid, storage_mount_pid, fuse_pid, mountpoint_path, boot_time) '
        'VALUES (?, ?, ?, ?, ?)',
        (csync_pid, storage_mount_pid, fuse_pid, mountpoint_path, _BOOT_TIME))
    _CONN.commit()


@connect_db
def _get_all_running_csync_pid() -> List[Any]:
    """Returns all the registered pid of CSYNC processes"""
    assert _CURSOR is not None
    _CURSOR.execute('SELECT csync_pid FROM running_csync WHERE boot_time=(?)',
                    (_BOOT_TIME,))
    rows = _CURSOR.fetchall()
    csync_pids = [row[0] for row in rows]
    return csync_pids


@connect_db
def _set_running_csync_sync_pid(csync_pid: int, sync_pid: Optional[int]):
    """Given the process id of CSYNC, sets the sync_pid column value"""
    assert _CURSOR is not None
    assert _CONN is not None
    _CURSOR.execute(
        'UPDATE running_csync '
        'SET sync_pid=(?) WHERE csync_pid=(?)', (sync_pid, csync_pid))
    _CONN.commit()


@connect_db
def _get_running_csync_sync_pid(csync_pid: int) -> Optional[int]:
    """Given the process id of CSYNC, returns the sync_pid column value"""
    assert _CURSOR is not None
    _CURSOR.execute(
        'SELECT sync_pid FROM running_csync '
        'WHERE csync_pid=(?) AND boot_time=(?)', (csync_pid, _BOOT_TIME))
    row = _CURSOR.fetchone()
    if row:
        return row[0]
    raise ValueError(f'CSYNC PID {csync_pid} not found.')


@connect_db
def _delete_running_csync(csync_pid: int):
    """Deletes the row with process id of CSYNC from running_csync table"""
    assert _CURSOR is not None
    assert _CONN is not None
    _CURSOR.execute('DELETE FROM running_csync WHERE csync_pid=(?)',
                    (csync_pid,))
    _CONN.commit()


@connect_db
def _get_csync_pid_from_mountpoint_path(path: str) -> Optional[int]:
    """Given the path, returns process ID of csync running on it"""
    assert _CURSOR is not None
    _CURSOR.execute(
        'SELECT csync_pid FROM running_csync '
        'WHERE mountpoint_path=(?) AND boot_time=(?)', (path, _BOOT_TIME))
    row = _CURSOR.fetchone()
    if row:
        return row[0]
    return None


@connect_db
def _get_mountpoint_path_from_csync_pid(csync_pid: int) -> Optional[str]:
    """Given the process ID of csync, returns mountpoint"""
    assert _CURSOR is not None
    _CURSOR.execute(
        'SELECT mountpoint_path FROM running_csync '
        'WHERE csync_pid=(?) AND boot_time=(?)', (csync_pid, _BOOT_TIME))
    row = _CURSOR.fetchone()
    if row:
        return row[0]
    return None


def _get_storage_mount_and_fuse_pid(
        csync_pid: int) -> Tuple[Optional[int], Optional[int]]:
    """Given the process id of CSYNC, returns storage_mount_pid and fuse_pid.
    """
    assert _CURSOR is not None
    _CURSOR.execute(
        'SELECT storage_mount_pid, fuse_pid FROM running_csync '
        'WHERE csync_pid=(?) AND boot_time=(?)', (csync_pid, _BOOT_TIME))
    row = _CURSOR.fetchone()
    if row:
        return row[0], row[1]  # storage_mount_pid, fuse_pid
    return None, None


class Passthrough(Operations):

    def __init__(self, root_read, root_write):
        self.root_read = root_read
        self.root_write = root_write

    # Helpers
    # =======
    # _full_path to the mount point
    def _full_path(self, partial, write: bool = False, read: bool = False):
        if partial.startswith("/"):
            partial = partial[1:]

        # Unless specified with write boolean, we default to the
        # read directory where the bucket is mounted.
        if write:
            full_path = self._full_write_path(partial)
        elif read:
            full_path = self._full_read_path(partial)
        elif self.is_in_write_dir(partial):
            full_path = self._full_write_path(partial)
        else:
            full_path = self._full_read_path(partial)
        return full_path

    def _full_read_path(self, partial):
        if partial.startswith("/"):
            partial = partial[1:]
        path = os.path.join(self.root_read, partial)
        return path

    def _full_write_path(self, partial):
        if partial.startswith("/"):
            partial = partial[1:]
        path = os.path.join(self.root_write, partial)
        return path

    def is_in_read_dir(self, filename):
        # Construct the full file path
        if filename.startswith("/"):
            filename = filename[1:]
        file_path = os.path.join(self.root_read, filename)
        return os.path.isfile(file_path) or os.path.isdir(file_path)

    def is_in_write_dir(self, filename):
        # Construct the full file path
        if filename.startswith("/"):
            filename = filename[1:]
        file_path = os.path.join(self.root_write, filename)
        return os.path.isfile(file_path) or os.path.isdir(file_path)

    # Filesystem methods
    # ==================

    def access(self, path, mode):
        if not os.access(self._full_path(path), mode):
            raise FuseOSError(errno.EACCES)

    def chmod(self, path, mode):
        full_path = self._full_path(path)
        return os.chmod(full_path, mode)

    def chown(self, path, uid, gid):
        full_path = self._full_path(path)
        return os.chown(full_path, uid, gid)

    def getattr(self, path, fh=None):
        # a path is passed to this function
        st = os.lstat(self._full_path(path))
        ret_dict = dict(
            (key, getattr(st, key))
            for key in ('st_atime', 'st_ctime', 'st_gid', 'st_mode', 'st_mtime',
                        'st_nlink', 'st_size', 'st_uid', 'st_blocks'))

        return ret_dict

    def readdir(self, path, fh):
        full_read_path = self._full_path(path, read=True)
        full_write_path = self._full_path(path, write=True)
        read_path_dir_set = set()
        write_path_dir_set = set()

        if os.path.isdir(full_read_path):
            read_path_dir_set = set(os.listdir(full_read_path))

        if os.path.isdir(full_write_path):
            write_path_dir_set = set(os.listdir(full_write_path))

        dirents = read_path_dir_set.union(write_path_dir_set)
        dirents.add('.')
        dirents.add('..')
        for r in dirents:
            yield r

    def readlink(self, path):
        pathname = os.readlink(self._full_path(path))
        if pathname.startswith("/"):
            # Path name is absolute, sanitize it.
            return os.path.relpath(pathname, self.root)
        else:
            return pathname

    def mknod(self, path, mode, dev):
        return os.mknod(self._full_path(path), mode, dev)

    def rmdir(self, path):
        if self.is_in_read_dir(path):
            os.rmdir(self._full_path(path, read=True))
        if self.is_in_write_dir(path):
            os.rmdir(self._full_path(path, write=True))

    def mkdir(self, path, mode):
        return os.mkdir(self._full_path(path, write=True), mode)

    def statfs(self, path):
        full_path = self._full_path(path)
        stv = os.statvfs(full_path)
        return dict((key, getattr(stv, key))
                    for key in ('f_bavail', 'f_bfree', 'f_blocks', 'f_bsize',
                                'f_favail', 'f_ffree', 'f_files', 'f_flag',
                                'f_frsize', 'f_namemax'))

    def unlink(self, path):
        if self.is_in_read_dir(path):
            os.unlink(self._full_path(path, read=True))
        if self.is_in_write_dir(path):
            os.unlink(self._full_path(path, write=True))

    def symlink(self, name, target):
        return os.symlink(name, self._full_path(target))

    def rename(self, old, new):
        return os.rename(self._full_path(old), self._full_path(new))

    def link(self, target, name):
        return os.link(self._full_path(target), self._full_path(name))

    def utimens(self, path, times=None):
        return os.utime(self._full_path(path), times)

    # File methods
    # ============

    def open(self, path, flags):
        return os.open(self._full_path(path), flags)

    def create(self, path, mode, fi=None):
        return os.open(self._full_path(path, write=True),
                       os.O_WRONLY | os.O_CREAT, mode)

    def read(self, path, length, offset, fh):
        os.lseek(fh, offset, os.SEEK_SET)
        return os.read(fh, length)

    def write(self, path, buf, offset, fh):
        os.lseek(fh, offset, os.SEEK_SET)
        return os.write(fh, buf)

    def truncate(self, path, length, fh=None):
        with open(self._full_path(path), 'r+') as f:
            f.truncate(length)

    def flush(self, path, fh):
        return os.fsync(fh)

    def release(self, path, fh):
        return os.close(fh)

    def fsync(self, path, fdatasync, fh):
        return self.flush(path, fh)


@click.group()
def main():
    pass


def get_upload_cmd(storetype: str, source: str, destination: str,
                   num_threads: int, delete: bool,
                   no_follow_symlinks: bool) -> str:
    """Builds sync command given storetype"""

    if storetype == 's3':
        # aws s3 sync does not support options to set number of threads
        # so we need a separate command for configuration
        thread_configure_cmd = (
            'aws configure set default.s3.max_concurrent_requests '
            '{num_threads}')
        user_configured_thread_cmd = thread_configure_cmd.format(
            num_threads=num_threads)

        # Construct the main sync command
        base_sync_cmd = ['aws', 's3', 'sync', source, f's3://{destination}']
        if delete:
            base_sync_cmd.append('--delete')
        if no_follow_symlinks:
            base_sync_cmd.append('--no-follow-symlinks')
        main_sync_cmd = ' '.join(base_sync_cmd)

        # Reset the number of threads back to its default value after the sync
        reset_thread_cmd = thread_configure_cmd.format(
            num_threads=_DEFAULT_S3_NUM_THREAD)

        full_cmd = '; '.join(
            [user_configured_thread_cmd, main_sync_cmd, reset_thread_cmd])

    elif storetype == 'gcs':
        base_sync_cmd = [
            'gsutil', '-m', f'-o "GSUtil:parallel_thread_count={num_threads}"',
            'rsync', '-r'
        ]
        # Conditionally add flags based on the function arguments
        if delete:
            base_sync_cmd.append('-d')
        if no_follow_symlinks:
            base_sync_cmd.append('-e')

        base_sync_cmd.append('-x')
        excluded_list = [r'\..*\.swp$']
        excludes = '|'.join(excluded_list)
        base_sync_cmd.append(excludes)

        base_sync_cmd.extend([source, f'gs://{destination}'])
        full_cmd = ' '.join(base_sync_cmd)
    else:
        raise ValueError(f'Unsupported store type: {storetype}')

    return full_cmd


def run_sync(source: str, storetype: str, destination: str, num_threads: int,
             interval_seconds: int, delete: bool, no_follow_symlinks: bool,
             csync_pid: int):
    """Runs the sync command from source to storetype bucket"""
    # TODO(Doyoung): Add a removal sync logic between write path and read path.
    # If any files/dirs exists in both directory, the one from write path must
    # be removed.

    # TODO(Doyoung): add enum type class to handle storetypes
    storetype = storetype.lower()
    sync_cmd = get_upload_cmd(storetype, source, destination, num_threads,
                              delete, no_follow_symlinks)

    source_to_bucket = (f'{source!r} to {destination!r} '
                        f'at {storetype!r}')
    # interval_seconds/2 is heuristically determined
    # as initial backoff
    backoff = common_utils.Backoff(int(interval_seconds / 2))
    for i in range(_MAX_SYNC_RETRIES):
        try:
            with subprocess.Popen(sync_cmd, start_new_session=True,
                                  shell=True) as proc:
                _set_running_csync_sync_pid(csync_pid, proc.pid)
                proc.wait()
                _set_running_csync_sync_pid(csync_pid, -1)
        except subprocess.CalledProcessError:
            # reset sync pid as the sync process is terminated
            _set_running_csync_sync_pid(csync_pid, -1)
            wait_time = backoff.current_backoff()
            logger.warning('Encountered an error while syncing '
                           f'{source_to_bucket}. Retrying sync '
                           f'in {wait_time}s. {_MAX_SYNC_RETRIES-i-1} more '
                           'reattempts remaining. Check the log file '
                           'in ~/.sky/ for more details.')
            time.sleep(wait_time)
        else:
            # successfully completed sync process
            return
    raise RuntimeError(f'Failed to sync {source_to_bucket} after '
                       f'{_MAX_SYNC_RETRIES} number of retries. Check '
                       'the log file in ~/.sky/ for more'
                       'details') from None


def run_fuse_operation(read_path, write_path, full_src):
    try:
        FUSE(Passthrough(read_path, write_path),
             full_src,
             nothreads=True,
             foreground=False)
    except Exception as e:
        print(f"Error in FUSE operation: {e}")


@main.command()
@click.argument('source', required=True, type=str)
@click.argument('storetype', required=True, type=str)
@click.argument('destination', required=True, type=str)
@click.option('--num-threads', required=False, default=10, type=int, help='')
@click.option('--interval-seconds',
              required=False,
              default=CSYNC_DEFAULT_INTERVAL_SECONDS,
              type=int,
              help='')
@click.option('--delete',
              required=False,
              default=False,
              type=bool,
              is_flag=True,
              help='')
@click.option('--no-follow-symlinks',
              required=False,
              default=False,
              type=bool,
              is_flag=True,
              help='')
def csync(source: str, storetype: str, destination: str, num_threads: int,
          interval_seconds: int, delete: bool, no_follow_symlinks: bool):
    """Runs daemon to sync the source to the bucket every INTERVAL seconds.

    Creates an entry of pid of the sync process in local database while sync
    command is running and removes it when completed.

    Args:
        source (str): The local path to the directory that you want to sync.
        storetype (str): The type of cloud storage to sync to.
        destination (str): The bucket or subdirectory in the bucket where the
            files should be synced.
        num_threads (int): The number of threads to use for the sync operation.
        interval_seconds (int): The time interval, in seconds, at which to run
            the sync operation.
        delete (bool): Whether or not to delete files in the destination that
            are not present in the source.
        no_follow_symlinks (bool): Whether or not to follow symbolic links in
            the source directory.
    """
    mountpoint_path = os.path.abspath(os.path.expanduser(source))
    # If the given source is already mounted with CSYNC, terminate it.
    if _get_csync_pid_from_mountpoint_path(mountpoint_path):
        _terminate([mountpoint_path])
    csync_pid = os.getpid()

    # create redirected directories for writing and reading
    csync_write_path = os.path.abspath(
        os.path.expanduser(_CSYNC_WRITE_PATH.format(pid=csync_pid)))
    csync_read_path = os.path.abspath(
        os.path.expanduser(_CSYNC_READ_PATH.format(pid=csync_pid)))
    if os.path.isdir(csync_write_path):
        shutil.rmtree(csync_write_path)
    if os.path.isdir(csync_read_path):
        shutil.rmtree(csync_read_path)
    os.makedirs(csync_write_path, exist_ok=True)
    os.makedirs(csync_read_path, exist_ok=True)
    # mount destination bucket to /.tmp_mount
    # TODO(doyoung): Currently, manually getting the mount_command, but
    # later, we need to refactor the mount_command perhaps making some
    # part of it a static method so it can be called from here by specifying
    # the bucket name and or whatever. Need to try to think of a way to reuse the code.
    #install_cmd = ('wget -nc https://github.com/GoogleCloudPlatform/gcsfuse'
    #                f'/releases/download/v1.0.1/'
    #                f'gcsfuse_1.0.1_amd64.deb '
    #                '-O /tmp/gcsfuse.deb && '
    #                'sudo dpkg --install /tmp/gcsfuse.deb')
    mount_cmd = ('gcsfuse -o allow_other '
                 '--implicit-dirs '
                 f'--stat-cache-capacity 4096 '
                 f'--stat-cache-ttl 5s '
                 f'--type-cache-ttl 5s '
                 f'--rename-dir-limit 10000 '
                 f'{destination} {csync_read_path}')
    # output = subprocess.run(mount_cmd,
    #                         shell=True,
    #                         stdout=subprocess.PIPE,
    #                         stderr=subprocess.PIPE,
    #                         check=True,
    #                         text=True)
    storage_mount_process = subprocess.Popen(mount_cmd,
                                             shell=True,
                                             stdout=subprocess.PIPE,
                                             stderr=subprocess.PIPE,
                                             text=True)
    storage_mount_pid = storage_mount_process.pid
    # Use FUSE to mount both write path and read path to the mountpoint
    fuse_process = multiprocessing.Process(target=run_fuse_operation,
                                           args=(csync_read_path,
                                                 csync_write_path,
                                                 mountpoint_path))
    fuse_process.start()
    fuse_pid = fuse_process.pid
    _add_running_csync(csync_pid, storage_mount_pid, fuse_pid, mountpoint_path)
    while True:
        start_time = time.time()
        delete = False
        run_sync(csync_write_path, storetype, destination, num_threads,
                 interval_seconds, delete, no_follow_symlinks, csync_pid)
        end_time = time.time()
        # Given the interval_seconds and the time elapsed during the sync
        # operation, we compute remaining time to wait before the next
        # sync operation.
        elapsed_time = int(end_time - start_time)
        remaining_interval = max(_MIN_SYNC_CALL_INTERVAL,
                                 interval_seconds - elapsed_time)
        # sync_pid column is set to 0 when sync is not running
        time.sleep(remaining_interval)


@main.command()
@click.argument('paths', nargs=-1, required=False)
@click.option('--all',
              '-a',
              default=False,
              is_flag=True,
              required=False,
              help='Terminates all CSYNC processes.')
def terminate(paths: List[str], all: bool = False) -> None:  # pylint: disable=redefined-builtin
    """Terminates the CSYNC daemon running.

    Args:
        paths (List[str]): list of CSYNC-mounted paths
        all (bool): determine either or not to unmount every CSYNC-mounted
            paths

    Raises:
        click.UsageError: when the paths are not specified
    """
    if not paths and not all:
        raise click.UsageError('Please provide the CSYNC-mounted path to '
                               'terminate the CSYNC process.')
    _terminate(paths, all)


def _terminate(paths: List[str], all: bool = False) -> int:  # pylint: disable=redefined-builtin
    """Terminates the CSYNC daemon running.

    Before terminating the running CSYNC daemon, it checks if the sync process
    spawned by the daemon is running. If it is, we wait until the sync gets
    completed, and then proceed to terminate the daemon.
    """
    csync_pid_set = set()
    if all:
        for csync_pid in _get_all_running_csync_pid():
            csync_pid_set.add(csync_pid)
    # when terminating specified CSYNC processes given the paths where
    # the processes are running on.
    else:
        for path in paths:
            full_path = os.path.abspath(os.path.expanduser(path))
            csync_pid = _get_csync_pid_from_mountpoint_path(full_path)
            if csync_pid is not None:
                csync_pid_set.add(csync_pid)

    pid_set = set()
    for csync_pid in csync_pid_set:
        storage_mount_pid, fuse_pid = _get_storage_mount_and_fuse_pid(csync_pid)
        pid_set.add((csync_pid, storage_mount_pid, fuse_pid))

    for csync_pid, _, fuse_pid in pid_set:
        # 1.unmount cloud storage from read path
        csync_read_path = os.path.abspath(
            os.path.expanduser(_CSYNC_READ_PATH.format(pid=csync_pid)))
        storage_unmount_cmd = f'fusermount -uz {csync_read_path}'
        with subprocess.Popen(storage_unmount_cmd,
                              shell=True,
                              stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE,
                              text=True) as unmount_process:
            return_code = unmount_process.wait()
            if return_code != 0:
                # TODO(Doyoung): implement logic to handle this error
                pass

        # 2.unmount redirection FUSE
        mountpoint_path = _get_mountpoint_path_from_csync_pid(csync_pid)
        storage_unmount_cmd = f'fusermount -uz {mountpoint_path}'
        with subprocess.Popen(storage_unmount_cmd,
                              shell=True,
                              stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE,
                              text=True) as unmount_process:

            return_code = unmount_process.wait()
            if return_code != 0:
                # TODO(Doyoung): implement logic to handle this error
                pass
        # If there's any process obtaining the file descriptor of a file in
        # mountpoint, i.e. writing a checkpoint, the mountpoint will remain
        # mounted until the file descriptor is released. We can confirm if
        # the mountpoint is completely unmounted by checking the termination
        # of the mount process.
        while True:
            try:
                process = psutil.Process(fuse_pid)
                # Check if process is still running
                if (process.status() == psutil.STATUS_ZOMBIE or
                        process.status() == psutil.STATUS_DEAD):
                    break
            # raised if process doesn't exist/terminated
            except psutil.NoSuchProcess:
                break
            time.sleep(5)

        # 3.terminate sync daemon
        while True:
            # when the sync process is not running, then the sync_pid
            # is stored as -1 in the state. We want to make sure to only
            # terminate the sync daemon when the sync is not running.
            if _get_running_csync_sync_pid(csync_pid) == -1:
                try:
                    psutil.Process(int(csync_pid)).terminate()
                except psutil.NoSuchProcess:
                    logger.info(f'Sync daemon process, {csync_pid}, was not '
                                'found.')
                break
            time.sleep(5)

        _delete_running_csync(csync_pid)
        print(f'deleted CSYNC mounted on {mountpoint_path!r}')

    return 0


if __name__ == '__main__':
    main()
