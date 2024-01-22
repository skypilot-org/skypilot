"""CSYNC module"""
import functools
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any, List, Optional, Tuple

import click
import psutil

from sky import exceptions
from sky import sky_logging
from sky.data import mounting_utils
from sky.data.storage_utils import StorageMode
from sky.skylet import constants
from sky.utils import common_utils
from sky.utils import db_utils

logger = sky_logging.init_logger(__name__)

_CSYNC_DB_PATH = os.path.join(constants.CSYNC_DIR, 'sky_csync.db')
_CSYNC_READ_PATH = os.path.join(constants.CSYNC_DIR, 'read_{pid}')
_CSYNC_WRITE_PATH = os.path.join(constants.CSYNC_DIR, 'write_{pid}')

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
                redirect_mount_pid INTEGER,
                mountpoint_path TEXT,
                boot_time FLOAT)""")

            conn.commit()

        if _DB is None:
            db_path = os.path.expanduser(_CSYNC_DB_PATH)
            os.makedirs(os.path.abspath(os.path.expanduser(
                constants.CSYNC_DIR)),
                        exist_ok=True)
            _DB = db_utils.SQLiteConn(db_path, create_table)

        _CURSOR = _DB.cursor
        _CONN = _DB.conn
        _BOOT_TIME = psutil.boot_time()

        return func(*args, **kwargs)

    return wrapper


@connect_db
def _add_running_csync(csync_pid: int, storage_mount_pid: int,
                       redirect_mount_pid: int, mountpoint_path: str):
    """Given the ids processes necessary to CSYNC, it creates a row with it"""
    assert _CURSOR is not None
    assert _CONN is not None
    _CURSOR.execute(
        'INSERT INTO running_csync '
        '(csync_pid, storage_mount_pid, redirect_mount_pid, '
        'mountpoint_path, boot_time) '
        'VALUES (?, ?, ?, ?, ?)',
        (csync_pid, storage_mount_pid, redirect_mount_pid, mountpoint_path,
         _BOOT_TIME))
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


def _get_storage_and_redirect_mount_pid(
        csync_pid: int) -> Tuple[Optional[int], Optional[int]]:
    """Given the pid of CSYNC, returns pid of storage and redirect mount."""
    assert _CURSOR is not None
    _CURSOR.execute(
        'SELECT storage_mount_pid, redirect_mount_pid FROM running_csync '
        'WHERE csync_pid=(?) AND boot_time=(?)', (csync_pid, _BOOT_TIME))
    row = _CURSOR.fetchone()
    if row:
        return row[0], row[1]  # storage_mount_pid, redirect_mount_pid
    return None, None


@click.group()
def main():
    pass


def get_upload_cmd(storetype: str, source: str, destination: str,
                   num_threads: int, delete: bool,
                   no_follow_symlinks: bool) -> str:
    """Builds sync command given storetype"""
    source = shlex.quote(source)
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
        excluded_list = ['.git/*', '.*.swp']
        excludes = ' '.join([
            f'--exclude {shlex.quote(file_name)}'
            for file_name in excluded_list
        ])
        base_sync_cmd.append(excludes)
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
        excluded_list = [r'^\.git/.*$', r'\..*\.swp$']
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


def get_storage_mount_script(storetype: str, destination: str,
                             mount_path: str) -> str:
    if storetype == 's3':
        install_cmd = mounting_utils.get_s3_mount_install_cmd()
        mount_cmd = mounting_utils.get_s3_mount_cmd(destination, mount_path)
    else:  # storetype == 'gcs':
        install_cmd = mounting_utils.get_gcs_mount_install_cmd()
        mount_cmd = mounting_utils.get_gcs_mount_cmd(destination, mount_path)

    storage_mount_script = mounting_utils.get_mounting_script(
        StorageMode.MOUNT, mount_path, mount_cmd, install_cmd)
    return storage_mount_script

def _handle_fuse_process(fuse_cmd: str) -> Tuple[int, int, str]:
    with subprocess.Popen(fuse_cmd,
                            shell=True,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE,
                            text=True) as fuse_process:
        fuse_pid = fuse_process.pid
        rc = fuse_process.wait()
        stdout, stderr = fuse_process.communicate()
        stderr = stdout + stderr
    if rc != 0:
        sys.stderr.write(stderr)
        sys.stderr.flush()
        sys.exit(exceptions.CSYNC_TERMINATE_FAILURE_CODE)
    return fuse_pid, rc, stderr


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
    # if the given source is already mounted with CSYNC, terminate it.
    if _get_csync_pid_from_mountpoint_path(mountpoint_path):
        _terminate([mountpoint_path])
    csync_pid = os.getpid()

    # create directories which mountpoint_path redirects
    # writing and reading to.
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

    error_msg = 'failed to run {process}'
    # mounting cloud object storage on the read path
    with tempfile.NamedTemporaryFile(mode='w+', delete=False) as script_file:
        storage_mount_script = get_storage_mount_script(storetype, destination,
                                                        csync_read_path)
        script_file.write(storage_mount_script)
        # ensure all data is written to the file
        script_file.flush()
        storage_fuse_mount_cmd = f'bash {script_file.name}'
        storage_fuse_mount_pid, rc, stderr = _handle_fuse_process(
            storage_fuse_mount_cmd)

    # redirect read/write of mountpoint_path by mounting redirection FUSE
    redirect_mount_cmd = mounting_utils.get_redirect_mount_cmd(
        mountpoint_path, csync_read_path, csync_write_path)
    redirect_fuse_mount_pid, rc, stderr = _handle_fuse_process(
        redirect_mount_cmd)

    _add_running_csync(csync_pid, storage_fuse_mount_pid,
                       redirect_fuse_mount_pid, mountpoint_path)

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
        storage_mount_pid, redirect_mount_pid = (
            _get_storage_and_redirect_mount_pid(csync_pid))
        pid_set.add((csync_pid, storage_mount_pid, redirect_mount_pid))

    failed_to_terminate: bool = False
    failed_to_terminate_list: List[Tuple[int, str, str]] = []
    unmount_cmd = 'sudo fusermount -uz {unmount_path}'
    for csync_pid, storage_mount_pid, redirect_mount_pid in pid_set:
        # 1.unmount cloud storage from read path
        csync_read_path = os.path.abspath(
            os.path.expanduser(_CSYNC_READ_PATH.format(pid=csync_pid)))
        storage_fuse_unmount_cmd = unmount_cmd.format(unmount_path=csync_read_path)
        _, rc, stderr = _handle_fuse_process(storage_fuse_unmount_cmd)
        if rc != 0:
            failed_to_terminate = True
            failed_to_terminate_list.append(
                (csync_pid, stderr, 'Storage Mount FUSE'))
            continue

        # 2.unmount redirection FUSE
        mountpoint_path = _get_mountpoint_path_from_csync_pid(csync_pid)
        redirection_fuse_unmount_cmd = unmount_cmd.format(
            unmount_path=mountpoint_path)
        _, rc, stderr = _handle_fuse_process(redirection_fuse_unmount_cmd)
        if rc != 0:
            failed_to_terminate = True
            failed_to_terminate_list.append(
                (csync_pid, stderr, 'Redirection FUSE'))
            continue
        # If there's any process obtaining the file descriptor of a file in
        # mountpoint, i.e. writing a checkpoint, the mountpoint will remain
        # mounted until the file descriptor is released after running
        # fusermount -uz MOUNT_PATH. We can confirm if the mountpoint is
        # completely unmounted by checking the termination
        # of the mount process.
        while True:
            try:
                process = psutil.Process(redirect_mount_pid)
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
                    # The daemon is externally terminated.
                    logger.info(f'Sync daemon process, {csync_pid}, was not '
                                'found.')
                break
            time.sleep(5)

        _delete_running_csync(csync_pid)
        print(f'deleted CSYNC mounted on {mountpoint_path!r}')

    if failed_to_terminate:
        for csync_pid, stderr, unmount_target in failed_to_terminate:
            err_msg = (
                f'{unmount_target} failed to terminate for CSYNC process '
                f'with pid {csync_pid}. '
                f'Detailed error message: {stderr}')
            sys.stderr.write(err_msg)
            sys.stderr.flush()
        sys.exit(exceptions.CSYNC_TERMINATE_FAILURE_CODE)
    return 0


if __name__ == '__main__':
    main()
