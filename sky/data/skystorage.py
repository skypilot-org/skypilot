import click
import contextlib
import filelock
import os
import subprocess
import time

from sky.data import csync_utils

def update_interval(interval, elapsed_time):
    diff = interval - elapsed_time
    if diff <= 0:
        return 0
    else:
        return diff

def is_locked(file_path):
    locked=False
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

def set_s3_sync_cmd(src_path,bucketname,num_threads,delete):
    config_cmd = f'aws configure set default.s3.max_concurrent_requests {num_threads}'
    subprocess.check_output(config_cmd, shell=True)
    sync_cmd = f"aws s3 sync {src_path} s3://{bucketname}"
    if delete:
        sync_cmd += " --delete"
    return sync_cmd

def set_gcs_sync_cmd(src_path,bucketname,num_threads,delete):
    sync_cmd = f'gsutil -m -o "GSUtil:parallel_thread_count={num_threads}" rsync -r'
    if delete:
        sync_cmd += ' -d'
    sync_cmd += f' {src_path} gs://{bucketname}'

def run_sync(src,storetype,bucketname,num_threads,delete):
    #TODO: add enum type class to handle storetypes
    storetype = storetype.lower()
    if storetype == 's3':
        sync_cmd = set_s3_sync_cmd(src,bucketname,num_threads,delete)
    elif storetype == 'gcs':
        sync_cmd = set_gcs_sync_cmd(src,bucketname,num_threads,delete)
    else:
        raise ValueError(f'Unknown store type: {storetype}')
    #run the sync command
    #TODO: add try-except block
    subprocess.run(sync_cmd, shell=True)

    #run necessary post-processes
    if storetype == 's3':
        # set number of threads back to its default value
        config_cmd = f'aws configure set default.s3.max_concurrent_requests 10'
        subprocess.check_output(config_cmd, shell=True)


def csync(src, storetype,bucketname,num_threads,interval,delete,lock):
    base_dir = os.path.expanduser('~/.skystorage')
    os.makedirs(base_dir, exist_ok=True)
    lock_file_name =  f'csync_{storetype}_{bucketname}.lock'
    lock_path = os.path.join(base_dir, lock_file_name)

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
            run_sync(src,storetype,bucketname,num_threads,delete)
            end_time = time.time()
        elapsed_time = start_time - end_time
        updated_interval = update_interval(interval, elapsed_time)
        time.sleep(updated_interval)

@click.command()
@click.argument('src', required=True,type=str)
@click.argument('storetype', required=True, type=str)
@click.argument('bucketname', required=True,type=str)
@click.option('--num-threads', required=False, default=10, type=int, help='')
@click.option('--interval', required=False, default=600, type=int, help='')
@click.option('--delete', required=False, default=False, type=bool, is_flag=True, help='')
@click.option('--lock', required=False, default=False, type=bool, is_flag=True, help='')
def main(src, storetype, bucketname, num_threads, interval, delete, lock):
    csync(src,storetype,bucketname,num_threads,interval,delete,lock)
    

if __name__ == '__main__':
    main()
