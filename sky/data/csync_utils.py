"""Helper functions for running continous syncing for Sky Storage"""
import os
import psutil
import random
import textwrap
import time
from typing import Dict, Tuple

C_SYNC_FILE_PATH = '~/.skystorage'

def get_csync_command(csync_cmd: str, sync_path: str):

    script = textwrap.dedent(f"""
        #!/usr/bin/env bash
        set -e               

        SYNC_PATH={sync_path}

        # Check if sync path exists
        if [ ! -d "$SYNC_PATH" ]; then
          echo "Sync path $SYNC_PATH does not exist. Creating..."
          sudo mkdir -p $SYNC_PATH
          sudo chmod 777 $SYNC_PATH
        fi

        setsid {csync_cmd} >/dev/null 2>&1 &
    """)

    script_path = f'~/.sky/sync_{random.randint(0, 1000000)}.sh'
    first_line = r'(cat <<-\EOF > {}'.format(script_path)
        
    command = (f'{first_line}'
               f'{script}'
               f') && chmod +x {script_path}'
               f' && bash {script_path}'
               f' && rm {script_path}')
    return command

def wait_and_terminate_csyncs():
    process_dict: Dict[int, Tuple[str,str]] = {}
    for proc in psutil.process_iter(['cmdline']):
        cmd = proc.info['cmdline']
        if 'sky.data.skystorage' in cmd:
            # cmd[4] is store type and cmd[5] is the bucket name
            process_dict[proc.pid] = (cmd[4], cmd[5])

    while True:
        running_sync = dict(process_dict)
        if not running_sync:
            break
        for pid in list(running_sync):
            store_type = running_sync[pid][0]
            bucket_name = running_sync[pid][1]
            lock_file_name = f'sync_{store_type}_{bucket_name}.lock'
            lock_file_path = os.path.join(C_SYNC_FILE_PATH, lock_file_name)
            if not os.path.exists(lock_file_path):
                # kill c_sync process
                psutil.Process(pid).terminate()
                # remove from c_sync_locks
                del process_dict[pid]
        time.sleep(10)   