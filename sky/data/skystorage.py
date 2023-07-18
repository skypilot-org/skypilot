"""Skystorage module"""
import click
import contextlib
import filelock
import os
import psutil
import subprocess
import time
from typing import Dict, Tuple

C_SYNC_FILE_PATH = '~/.skystorage'


@click.group()
def main():
    pass


def update_interval(interval, elapsed_time):
    diff = interval - elapsed_time
    if diff <= 0:
        return 0
    else:
        return diff


def is_locked(file_path):
    locked = False
    if os.path.exists(file_path):
        lock = filelock.FileLock(file_path)
        try:
            lock.acquire(timeout=0)
        except filelock.Timeout:
            locked = True
        finally:
            if not locked:
                lock.release()
    return locked


def set_s3_sync_cmd(src_path, bucketname, num_threads, delete):
    config_cmd = ('aws configure set default.s3.max_concurrent_requests '
                  f'{num_threads}')
    subprocess.check_output(config_cmd, shell=True)
    sync_cmd = f'aws s3 sync {src_path} s3://{bucketname}'
    if delete:
        sync_cmd += ' --delete'
    return sync_cmd


def set_gcs_sync_cmd(src_path, bucketname, num_threads, delete):
    sync_cmd = (f'gsutil -m -o \'GSUtil:parallel_thread_count={num_threads}\' '
                'rsync -r')
    if delete:
        sync_cmd += ' -d'
    sync_cmd += f' {src_path} gs://{bucketname}'
    return sync_cmd


def run_sync(src, storetype, bucketname, num_threads, delete):
    #TODO: add enum type class to handle storetypes
    storetype = storetype.lower()
    if storetype == 's3':
        sync_cmd = set_s3_sync_cmd(src, bucketname, num_threads, delete)
    elif storetype == 'gcs':
        sync_cmd = set_gcs_sync_cmd(src, bucketname, num_threads, delete)
    else:
        raise ValueError(f'Unknown store type: {storetype}')
    #run the sync command
    #TODO: add try-except block
    subprocess.run(sync_cmd, shell=True, check=True)

    #run necessary post-processes
    if storetype == 's3':
        # set number of threads back to its default value
        config_cmd = 'aws configure set default.s3.max_concurrent_requests 10'
        subprocess.check_output(config_cmd, shell=True)


@main.command()
@click.argument('src', required=True, type=str)
@click.argument('storetype', required=True, type=str)
@click.argument('bucketname', required=True, type=str)
@click.option('--num-threads', required=False, default=10, type=int, help='')
@click.option('--interval', required=False, default=600, type=int, help='')
@click.option('--delete',
              required=False,
              default=False,
              type=bool,
              is_flag=True,
              help='')
@click.option('--lock',
              required=False,
              default=False,
              type=bool,
              is_flag=True,
              help='')
def csync(src, storetype, bucketname, num_threads, interval, delete, lock):
    base_dir = os.path.expanduser('~/.skystorage')
    os.makedirs(base_dir, exist_ok=True)
    lock_file_name = f'csync_{storetype}_{bucketname}.lock'
    lock_path = os.path.expanduser(os.path.join(base_dir, lock_file_name))

    # When the csync daemon was previously terminated abnormally,
    # it may still be holding the lock. This lock will be reset after removal.
    if os.path.exists(lock_path):
        os.remove(lock_path)

    while True:
        with contextlib.ExitStack() as stack:
            if lock:
                stack.enter_context(filelock.FileLock(lock_path))
            start_time = time.time()
            # TODO: add try-except block
            run_sync(src, storetype, bucketname, num_threads, delete)
            end_time = time.time()
        elapsed_time = start_time - end_time
        updated_interval = update_interval(interval, elapsed_time)
        if lock and os.path.exists(lock_path):
            os.remove(lock_path)
        time.sleep(updated_interval)


@main.command()
def terminate() -> None:
    # Call the function to terminate the csync processes here
    process_dict: Dict[int, Tuple[str, str]] = {}
    for proc in psutil.process_iter(['cmdline']):
        cmd = proc.info['cmdline']
        if 'csync' in cmd:
            # cmd[5] is store type and cmd[6] is the bucket name
            process_dict[proc.pid] = (cmd[5], cmd[6])

    if len(process_dict) > 0:
        while True:
            running_sync = dict(process_dict)
            if not running_sync:
                break
            for pid in list(running_sync):
                store_type = running_sync[pid][0]
                bucket_name = running_sync[pid][1]
                lock_file_name = f'csync_{store_type}_{bucket_name}.lock'
                lock_file_path = os.path.expanduser(
                    os.path.join(C_SYNC_FILE_PATH, lock_file_name))
                if not os.path.exists(lock_file_path):
                    # kill c_sync process
                    psutil.Process(pid).terminate()
                    # remove from c_sync_locks
                    del process_dict[pid]
            time.sleep(5)


if __name__ == '__main__':
    main()
