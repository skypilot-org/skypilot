"""Skystorage module"""
import multiprocessing
import os
import pathlib
import subprocess
import time
from typing import Any, List, Optional
import urllib.parse

import click
import psutil

from sky.utils import common_utils
from sky.utils import db_utils

_CSYNC_BASE_PATH = '~/.skystorage'

_DB_PATH = os.path.expanduser('~/.skystorage/state.db')
pathlib.Path(_DB_PATH).parents[0].mkdir(parents=True, exist_ok=True)


def create_table(cursor, conn):
    cursor.execute("""\
        CREATE TABLE IF NOT EXISTS running_csyncs (
        csync_pid INTEGER PRIMARY KEY,
        sync_pid INTEGER DEFAULT 0)""")

    conn.commit()


_DB = db_utils.SQLiteConn(_DB_PATH, create_table)
_CURSOR = _DB.cursor
_CONN = _DB.conn


def _set_running_csyncs_csync_pid(csync_pid: int):
    """Given the process id of CSYNC, it should create a row with it"""
    _CURSOR.execute(
        'INSERT OR REPLACE INTO running_csyncs (csync_pid) VALUES (?)',
        (csync_pid,))
    _CONN.commit()


def _get_running_csyncs_csync_pid() -> List[Any]:
    """Returns all the registerd pid of CSYNC processes"""
    _CURSOR.execute('SELECT csync_pid FROM running_csyncs')
    rows = _CURSOR.fetchall()
    csync_pids = [row[0] for row in rows]
    return csync_pids


def _set_running_csyncs_sync_pid(csync_pid: int, sync_pid: Optional[int]):
    """Given the process id of CSYNC, sets the sync_pid column value"""
    _CURSOR.execute(
        'UPDATE running_csyncs SET sync_pid=(?) WHERE csync_pid=(?)',
        (sync_pid, csync_pid))
    _CONN.commit()


def _get_running_csyncs_sync_pid(csync_pid: int) -> int:
    """Given the process id of CSYNC, returns the sync_pid column value"""
    _CURSOR.execute('SELECT sync_pid FROM running_csyncs WHERE csync_pid=(?)',
                    (csync_pid,))
    row = _CURSOR.fetchone()
    if row:
        return row[0]
    raise ValueError(f'CSYNC PID {csync_pid} not found.')


def _delete_running_csyncs(csync_pid: int):
    """Deletes the row with the given process id of CSYNC from running_csyncs
    table"""
    _CURSOR.execute('DELETE FROM running_csyncs WHERE csync_pid=(?)',
                    (csync_pid,))
    _CONN.commit()


@click.group()
def main():
    pass


def update_interval(interval: int, elapsed_time: int):
    diff = interval - elapsed_time
    if diff <= 0:
        return 0
    else:
        return diff


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
             interval: int,
             delete: bool,
             no_follow_symlinks: bool,
             max_retries: int = 10,
             backoff: Optional[common_utils.Backoff] = None):
    """Runs the sync command to from SRC to STORETYPE bucket"""
    #TODO: add enum type class to handle storetypes
    storetype = storetype.lower()
    if storetype == 's3':
        sync_cmd = get_s3_upload_cmd(src, dst, num_threads, delete,
                                     no_follow_symlinks)
    elif storetype == 'gcs':
        sync_cmd = get_gcs_upload_cmd(src, dst, num_threads, delete,
                                      no_follow_symlinks)
    else:
        raise ValueError(f'Unknown store type: {storetype}')

    result = urllib.parse.urlsplit(dst)
    # the exact mounting point being either the bucket or subdirectory in it
    sync_point = result.path.split('/')[-1]
    log_file_name = f'csync_{storetype}_{sync_point}.log'
    base_dir = os.path.expanduser(_CSYNC_BASE_PATH)
    log_path = os.path.expanduser(os.path.join(base_dir, log_file_name))

    with open(log_path, 'a') as fout:
        try:
            subprocess.run(sync_cmd,
                           shell=True,
                           check=True,
                           stdout=fout,
                           stderr=fout)
        except subprocess.CalledProcessError:
            src_to_bucket = (f'\'{src}\' to \'{dst}\' '
                             f'at \'{storetype}\'')
            if max_retries > 0:
                if backoff is None:
                    # interval/2 is heuristically determined as initial backoff
                    backoff = common_utils.Backoff(int(interval / 2))
                wait_time = backoff.current_backoff()
                fout.write('Encountered an error while syncing '
                           f'{src_to_bucket}. Retrying'
                           f' in {wait_time}s. {max_retries} more reattempts '
                           f'remaining. Check {log_path} for details.')
                time.sleep(wait_time)
                run_sync(src, storetype, dst, num_threads, interval, delete,
                         no_follow_symlinks, max_retries - 1, backoff)
            else:
                raise RuntimeError(f'Failed to sync {src_to_bucket} after '
                                   f'number of retries. Check {log_path} for'
                                   'details') from None

    #run necessary post-processes
    if storetype == 's3':
        # set number of threads back to its default value
        config_cmd = 'aws configure set default.s3.max_concurrent_requests 10'
        subprocess.check_output(config_cmd, shell=True)


@main.command()
@click.argument('src', required=True, type=str)
@click.argument('storetype', required=True, type=str)
@click.argument('dst', required=True, type=str)
@click.option('--num-threads', required=False, default=10, type=int, help='')
@click.option('--interval', required=False, default=600, type=int, help='')
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
def csync(src: str, storetype: str, dst: str, num_threads: int, interval: int,
          delete: bool, no_follow_symlinks: bool):
    """Syncs the source to the bucket every INTERVAL seconds. Creates an entry
    of pid of the sync process in local database while sync command is runninng
    and removes it when completed.
    """
    csync_pid = os.getpid()
    _set_running_csyncs_csync_pid(csync_pid)
    while True:
        sync_process = multiprocessing.Process(
            target=run_sync,
            args=(src, storetype, dst, num_threads, interval, delete,
                  no_follow_symlinks))
        start_time = time.time()
        sync_process.start()
        _set_running_csyncs_sync_pid(csync_pid, sync_process.pid)
        sync_process.join()
        end_time = time.time()
        # the time took to sync gets reflected to the INTERVAL
        elapsed_time = int(end_time - start_time)
        remaining_interval = update_interval(interval, elapsed_time)
        # sync_pid column is set to 0 when sync is not running
        _set_running_csyncs_sync_pid(csync_pid, 0)
        time.sleep(remaining_interval)


@main.command()
def terminate() -> None:
    """Terminates all the CSYNC daemon running after checking if all the
    sync process has completed.
    """
    # TODO: Currently, this terminates all the CSYNC daemon by default.
    # Make an option of --all to terminate all and make the default
    # behavior to take a source name to terminate only one daemon.
    # Call the function to terminate the csync processes here
    csync_pid_set = set(_get_running_csyncs_csync_pid())
    while True:
        if not csync_pid_set:
            break
        sync_running_csync_set = set()
        for csync_pid in csync_pid_set:
            if _get_running_csyncs_sync_pid(csync_pid):
                sync_running_csync_set.add(csync_pid)
        remove_process_set = csync_pid_set.difference(sync_running_csync_set)
        for csync_pid in remove_process_set:
            psutil.Process(int(csync_pid)).terminate()
            _delete_running_csyncs(csync_pid)
            csync_pid_set.remove(csync_pid)
        time.sleep(5)


if __name__ == '__main__':
    main()
