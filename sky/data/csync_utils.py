"""Helper functions for running continous syncing for Sky Storage"""
import random
import textwrap

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

        nohup {csync_cmd} >/dev/null 2>&1 &
    """)

    script_path = f'~/.sky/sync_{random.randint(0, 1000000)}.sh'
    first_line = r'(cat <<-\EOF > {}'.format(script_path)
        
    command = (f'{first_line}'
               f'{script}'
               f') && chmod +x {script_path}'
               f' && bash {script_path}'
               f' && rm {script_path}')
    return command

def csync_is_running():
    # get a list of sync_*.lock files
    # go thorugh the list to check if any are 
    return

