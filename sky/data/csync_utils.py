"""Helper functions for running continous syncing for Sky Storage"""
import random
import textwrap


def get_csync_command(csync_cmd: str, csync_path: str) -> str:
    """
    Generates the CSYNC command for a given bucket. Generated script first
    creates the CSYNC_PATH if it does not exist, and finally runs CSYNC
    daemon on CSYNC_PATH to the bucket.

    Args:
        csync_path: Path to run CSYNC daemon to the bucket at.
        mount_cmd: Command to run CSYNC daemon to the bucket.
            Should be single line.

    Returns:
        str: CSYNC command with the script as a heredoc.
    """

    script = textwrap.dedent(f"""
        #!/usr/bin/env bash
        set -e               

        CSYNC_PATH={csync_path}

        # Check if csync path exists
        if [ ! -d "$CSYNC_PATH" ]; then
          echo "CSYNC path $CSYNC_PATH does not exist. Creating..."
          sudo mkdir -p $CSYNC_PATH
          sudo chmod 777 $CSYNC_PATH
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
