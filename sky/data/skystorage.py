"""Skystorage module"""
import multiprocessing
import os
import subprocess
import time
from typing import Optional
import urllib.parse

import click
import psutil

_CSYNC_BASE_PATH = '~/.skystorage'
_CSYNC_PID_PATH = '~/.skystorage/csync_pid'
_SYNC_PID_PATH = '~/.skystorage/sync_pid'


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
             max_retries: int = 10):
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
                #TODO: display the error with remaining # of retries
                wait_time = interval / 2
                fout.write('Encountered an error while syncing '
                           f'{src_to_bucket}. Retrying'
                           f' in {wait_time}s. {max_retries} more reattempts '
                           f'remaining. Check {log_path} for details.')
                time.sleep(wait_time)
                return run_sync(src, storetype, dst, num_threads, interval,
                                delete, no_follow_symlinks, max_retries - 1)
            else:
                raise RuntimeError(f'Failed to sync {src_to_bucket} after '
                                   f'number of retries. Check {log_path} for'
                                   'details') from None

    #run necessary post-processes
    if storetype == 's3':
        # set number of threads back to its default value
        config_cmd = 'aws configure set default.s3.max_concurrent_requests 10'
        subprocess.check_output(config_cmd, shell=True)


def _register_process_id(base_path,
                         parent_pid: int,
                         child_pid: Optional[int] = -1) -> str:
    base_dir = os.path.expanduser(base_path)
    os.makedirs(base_dir, exist_ok=True)
    pid = str(parent_pid)
    if child_pid:
        pid += f'_{child_pid}'
    pid_path = os.path.expanduser(os.path.join(base_dir, pid))
    open(pid_path, 'a').close()
    return pid_path


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
    """Syncs the source to the bucket every INTERVAL seconds. Creates a lock
    file while sync command is runninng and removes it when completed.
    """
    csync_pid = os.getpid()
    _register_process_id(_CSYNC_PID_PATH, csync_pid)
    while True:
        p = multiprocessing.Process(target=run_sync,
                                    args=(src, storetype, dst, num_threads,
                                          interval, delete, no_follow_symlinks))
        start_time = time.time()
        p.start()
        sync_pid_path = _register_process_id(_SYNC_PID_PATH, csync_pid, p.pid)
        p.join()
        end_time = time.time()
        # the time took to sync gets reflected to the INTERVAL
        elapsed_time = int(end_time - start_time)
        remaining_interval = update_interval(interval, elapsed_time)
        if os.path.exists(sync_pid_path):
            os.remove(sync_pid_path)
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
    csync_process_set = set(os.listdir(_CSYNC_PID_PATH))
    while True:
        if not csync_process_set:
            break
        sync_running_csync_set = set()
        for csync_pid in csync_process_set:
            for csync_sync_pid in os.listdir(_SYNC_PID_PATH):
                if csync_pid == csync_sync_pid.split('_')[0]:
                    sync_running_csync_set.add(csync_pid)
        remove_process_set = csync_process_set.difference(
            sync_running_csync_set)
        for csync_pid in remove_process_set:
            psutil.Process(int(csync_pid)).terminate()
            csync_process_set.remove(csync_pid)
        time.sleep(5)


if __name__ == '__main__':
    main()
