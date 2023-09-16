"""Skystorage module"""
import functools
import os
import pathlib
import subprocess
import time
from typing import Any, List, Optional
import urllib.parse

import click
import psutil

from sky import sky_logging
from sky.utils import common_utils
from sky.utils import db_utils

logger = sky_logging.init_logger(__name__)

_CSYNC_BASE_PATH = '~/.skystorage'

_DB = None
_CURSOR = None
_CONN = None
_BOOT_TIME = None


def db(func):

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        global _DB, _CURSOR, _CONN, _BOOT_TIME

        def create_table(cursor, conn):
            cursor.execute("""\
                CREATE TABLE IF NOT EXISTS running_csync (
                csync_pid INTEGER PRIMARY KEY,
                sync_pid INTEGER DEFAULT -1,
                source_path TEXT,
                boot_time FLOAT)""")

            conn.commit()

        if _DB is None:
            db_path = os.path.expanduser('~/.sky/skystorage.db')
            pathlib.Path(db_path).parents[0].mkdir(parents=True, exist_ok=True)
            _DB = db_utils.SQLiteConn(db_path, create_table)

        _CURSOR = _DB.cursor
        _CONN = _DB.conn
        _BOOT_TIME = psutil.boot_time()

        return func(*args, **kwargs)

    return wrapper


@db
def _add_running_csync(csync_pid: int, source_path: str):
    """Given the process id of CSYNC, it should create a row with it"""
    assert _CURSOR is not None
    assert _CONN is not None
    _CURSOR.execute(
        'INSERT INTO running_csync '
        '(csync_pid, source_path, boot_time) '
        'VALUES (?, ?, ?)', (csync_pid, source_path, _BOOT_TIME))
    _CONN.commit()


@db
def _get_all_running_csync_pid() -> List[Any]:
    """Returns all the registerd pid of CSYNC processes"""
    assert _CURSOR is not None
    _CURSOR.execute('SELECT csync_pid FROM running_csync WHERE boot_time=(?)',
                    (_BOOT_TIME,))
    rows = _CURSOR.fetchall()
    csync_pids = [row[0] for row in rows]
    return csync_pids


@db
def _get_running_csync_source_path() -> List[Any]:
    """Returns all the registerd source path of CSYNC processes"""
    assert _CURSOR is not None
    _CURSOR.execute(
        'SELECT source_path FROM running_csync '
        'WHERE boot_time=(?)', (_BOOT_TIME,))
    rows = _CURSOR.fetchall()
    source_paths = [row[0] for row in rows]
    return source_paths


@db
def _set_running_csync_sync_pid(csync_pid: int, sync_pid: Optional[int]):
    """Given the process id of CSYNC, sets the sync_pid column value"""
    assert _CURSOR is not None
    assert _CONN is not None
    _CURSOR.execute(
        'UPDATE running_csync '
        'SET sync_pid=(?) WHERE csync_pid=(?)', (sync_pid, csync_pid))
    _CONN.commit()


@db
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


@db
def _delete_running_csync(csync_pid: int):
    """Deletes the row with process id of CSYNC from running_csync table"""
    assert _CURSOR is not None
    assert _CONN is not None
    _CURSOR.execute('DELETE FROM running_csync WHERE csync_pid=(?)',
                    (csync_pid,))
    _CONN.commit()


@db
def _get_csync_pid_from_source_path(path: str) -> Optional[int]:
    """Given the path, returns process ID of csync running on it"""
    assert _CURSOR is not None
    _CURSOR.execute(
        'SELECT csync_pid FROM running_csync '
        'WHERE source_path=(?) AND boot_time=(?)', (path, _BOOT_TIME))
    row = _CURSOR.fetchone()
    if row:
        return row[0]
    raise ValueError(f'{path} is not mounted with CSYNC.')


@click.group()
def main():
    pass


def update_interval(interval_seconds: int, elapsed_time: int):
    """Updates the time interval for the next sync operation.

    Given the originally set interval_seconds and the time elapsed during the
    sync operation, this function computes and returns the remaining time to
    wait before the next sync operation.
    """
    diff = interval_seconds - elapsed_time
    return max(0, diff)


def get_s3_upload_cmd(src_path: str, dst: str, num_threads: int, delete: bool,
                      no_follow_symlinks: bool):
    """Builds sync command for aws s3"""
    config_cmd = ('aws configure set default.s3.max_concurrent_requests '
                  f'{num_threads}')
    subprocess.check_output(config_cmd, shell=True)
    sync_cmd = f'aws s3 sync {src_path} s3://{dst}'
    if delete:
        sync_cmd += ' --delete'
    if no_follow_symlinks:
        sync_cmd += ' --no-follow-symlinks'
    return sync_cmd


def get_gcs_upload_cmd(src_path: str, dst: str, num_threads: int, delete: bool,
                       no_follow_symlinks: bool):
    """Builds sync command for gcp gcs"""
    sync_cmd = (f'gsutil -m -o \'GSUtil:parallel_thread_count={num_threads}\' '
                'rsync -r')
    if delete:
        sync_cmd += ' -d'
    if no_follow_symlinks:
        sync_cmd += ' -e'
    sync_cmd += f' {src_path} gs://{dst}'
    return sync_cmd


def run_sync(src: str,
             storetype: str,
             dst: str,
             num_threads: int,
             interval_seconds: int,
             delete: bool,
             no_follow_symlinks: bool,
             csync_pid: int,
             max_retries: int = 10,
             backoff: Optional[common_utils.Backoff] = None):
    """Runs the sync command to from src to storetype bucket"""
    #TODO: add enum type class to handle storetypes
    storetype = storetype.lower()
    if storetype == 's3':
        sync_cmd = get_s3_upload_cmd(src, dst, num_threads, delete,
                                     no_follow_symlinks)
    elif storetype == 'gcs':
        sync_cmd = get_gcs_upload_cmd(src, dst, num_threads, delete,
                                      no_follow_symlinks)
    else:
        raise ValueError(f'Unsupported store type: {storetype}')

    result = urllib.parse.urlsplit(dst)
    # the exact mounting point being either the bucket or subdirectory in it
    sync_point = result.path.split('/')[-1]
    log_file_name = f'csync_{storetype}_{sync_point}.log'
    base_dir = os.path.expanduser(_CSYNC_BASE_PATH)
    os.makedirs(base_dir, exist_ok=True)
    log_path = os.path.expanduser(os.path.join(base_dir, log_file_name))

    with open(log_path, 'a') as fout:
        try:
            with subprocess.Popen(sync_cmd,
                                  stdout=fout,
                                  stderr=fout,
                                  start_new_session=True,
                                  shell=True) as proc:
                _set_running_csync_sync_pid(csync_pid, proc.pid)
                proc.wait()
        except subprocess.CalledProcessError:
            src_to_bucket = (f'\'{src}\' to \'{dst}\' '
                             f'at \'{storetype}\'')
            if max_retries > 0:
                if backoff is None:
                    # interval_seconds/2 is heuristically determined
                    # as initial backoff
                    backoff = common_utils.Backoff(int(interval_seconds / 2))
                wait_time = backoff.current_backoff()
                fout.write('Encountered an error while syncing '
                           f'{src_to_bucket}. Retrying'
                           f' in {wait_time}s. {max_retries} more reattempts '
                           f'remaining. Check {log_path} for details.')
                time.sleep(wait_time)
                # reset sync pid as the sync process is terminated
                _set_running_csync_sync_pid(csync_pid, -1)
                run_sync(src, storetype, dst, num_threads, interval_seconds,
                         delete, no_follow_symlinks, csync_pid, max_retries - 1,
                         backoff)
            else:
                raise RuntimeError(f'Failed to sync {src_to_bucket} after '
                                   f'number of retries. Check {log_path} for'
                                   'details') from None

        #run necessary post-processes
        _set_running_csync_sync_pid(csync_pid, -1)
        if storetype == 's3':
            # set number of threads back to its default value
            config_cmd = \
                'aws configure set default.s3.max_concurrent_requests 10'
            subprocess.run(config_cmd,
                           shell=True,
                           check=True,
                           stdout=fout,
                           stderr=fout)


@main.command()
@click.argument('source', required=True, type=str)
@click.argument('storetype', required=True, type=str)
@click.argument('destination', required=True, type=str)
@click.option('--num-threads', required=False, default=10, type=int, help='')
@click.option('--interval-seconds',
              required=False,
              default=600,
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
    """Syncs the source to the bucket every INTERVAL seconds. Creates an entry
    of pid of the sync process in local database while sync command is runninng
    and removes it when completed.
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
    full_src = os.path.abspath(os.path.expanduser(source))
    csync_mounted_source_paths = _get_running_csync_source_path()
    # If the given source is already mounted with CSYNC, terminate it.
    for source_path in csync_mounted_source_paths:
        if os.path.samefile(full_src, source_path):
            _terminate([full_src])
            break
    csync_pid = os.getpid()
    _add_running_csync(csync_pid, full_src)
    while True:
        start_time = time.time()
        run_sync(full_src, storetype, destination, num_threads,
                 interval_seconds, delete, no_follow_symlinks, csync_pid)
        end_time = time.time()
        # the time took to sync gets reflected to the interval_seconds
        elapsed_time = int(end_time - start_time)
        remaining_interval = update_interval(interval_seconds, elapsed_time)
        # sync_pid column is set to 0 when sync is not running
        time.sleep(remaining_interval)


@main.command()
@click.argument('paths', nargs=-1, required=False, type=str)
@click.option('--all',
              '-a',
              default=False,
              is_flag=True,
              required=False,
              help='Terminates all CSYNC processes.')
def terminate(paths: List[str], all: bool = False) -> None:  # pylint: disable=redefined-builtin
    """Terminates all the CSYNC daemon running after checking if all the
    sync process has completed.

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


def _terminate(paths: List[str], all: bool = False) -> None:  # pylint: disable=redefined-builtin
    """Terminates all the CSYNC daemon running after checking if all the
    sync process has completed.
    """
    # TODO: Currently, this terminates all the CSYNC daemon by default.
    # Make an option of --all to terminate all and make the default
    # behavior to take a source name to terminate only one daemon.
    # Call the function to terminate the csync processes here
    if all:
        csync_pid_set = set(_get_all_running_csync_pid())
    else:
        csync_pid_set = set()
        for path in paths:
            full_path = os.path.abspath(os.path.expanduser(path))
            csync_pid_set.add(_get_csync_pid_from_source_path(full_path))
    while True:
        if not csync_pid_set:
            break
        sync_running_csync_set = set()
        for csync_pid in csync_pid_set:
            # sync_pid is set to -1 when sync is not running
            if _get_running_csync_sync_pid(csync_pid) != -1:
                sync_running_csync_set.add(csync_pid)
        remove_process_set = csync_pid_set.difference(sync_running_csync_set)
        for csync_pid in remove_process_set:
            try:
                psutil.Process(int(csync_pid)).terminate()
            except psutil.NoSuchProcess as e:
                if 'process no longer exists' in str(e):
                    _delete_running_csync(csync_pid)
                    continue
            _delete_running_csync(csync_pid)
            print(f'deleted {csync_pid}')
            csync_pid_set.remove(csync_pid)
        time.sleep(5)


if __name__ == '__main__':
    main()
