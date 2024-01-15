"""Helper functions for object store mounting in Sky Storage"""
import random
import textwrap
from typing import Optional

from sky import exceptions
from sky.data import storage_utils


def get_mounting_script(
    mount_mode: storage_utils.StorageMode,
    mount_path: str,
    mount_cmd: str,
    install_cmd: Optional[str] = None,
    version_check_cmd: Optional[str] = None,
    csync_log_path: Optional[str] = None,
) -> str:
    mount_binary = mount_cmd.split()[0]
    installed_check = f'[ -x "$(command -v {mount_binary})" ]'
    if mount_mode == storage_utils.StorageMode.MOUNT:
        assert csync_log_path is None, ('CSYNC log path should '
                                        'not be defined for MOUNT mode.')
        if version_check_cmd is not None:
            installed_check += f' && {version_check_cmd}'
    else:
        assert install_cmd is None, ('Installing commands should '
                                     'not be defined for CSYNC mode.')

    script = textwrap.dedent(f"""
        #!/usr/bin/env bash
        set -e

        MOUNT_MODE={mount_mode.value}
        MOUNT_PATH='{mount_path}'
        MOUNT_BINARY={mount_binary}
        echo "MOUNT_MODE is: $MOUNT_MODE"

        if [ "$MOUNT_MODE" = "MOUNT" ]; then
            # Check if path is already mounted
            if grep -q $MOUNT_PATH /proc/mounts ; then
              echo "Path already mounted - unmounting..."
              fusermount -uz "$MOUNT_PATH"
              echo "Successfully unmounted $MOUNT_PATH."
            fi

            # Install MOUNT_BINARY if not already installed
            if {installed_check}; then
              echo "$MOUNT_BINARY already installed. Proceeding..."
            else
              echo "Installing $MOUNT_BINARY..."
              {install_cmd}
            fi
        fi

        # Check if mount path exists
        if [ ! -d "$MOUNT_PATH" ]; then
          echo "Mount/CSYNC path $MOUNT_PATH does not exist. Creating..."
          sudo mkdir -p $MOUNT_PATH
          sudo chmod 777 $MOUNT_PATH
        else
          # Check if mount path contains files for MOUNT mode only
          if [ "$MOUNT_MODE" = "MOUNT" ]; then
            if [ "$(ls -A $MOUNT_PATH)" ]; then
              echo "Mount path $MOUNT_PATH is not empty. Please mount to another path or remove it first."
              exit {exceptions.MOUNT_PATH_NON_EMPTY_CODE}
            fi
          fi
        fi

        if [ "$MOUNT_MODE" = "MOUNT" ]; then
          echo "Mounting source bucket to $MOUNT_PATH with $MOUNT_BINARY..."
          {mount_cmd}
          echo "Mounting done."
        else
          # running CSYNC cmd
          echo "Setting up CSYNC on $MOUNT_PATH to source bucket..."
          setsid {mount_cmd} >> {csync_log_path} 2>&1 &
          echo "CSYNC is set."
        fi
    """)
    return script


def get_mounting_command(
    mount_mode: storage_utils.StorageMode,
    mount_path: str,
    mount_cmd: str,
    install_cmd: Optional[str] = None,
    version_check_cmd: Optional[str] = None,
    csync_log_path: Optional[str] = None,
) -> str:
    """Generates the mounting command for a given bucket.

    There are two types of mounting supported in Skypilot, a traditional MOUNT
    and CSYNC.

    For traditional mounting, generated script first unmounts any
    existing mount at the mount path, checks and installs the mounting utility
    if required, creates the mount path and finally mounts the bucket.

    For CSYNC, generated script first creates the CSYNC_PATH if it does not
    exist, and finally runs CSYNC daemon on CSYNC_PATH to the bucket.

    Args:
        mount_mode: Defines which mounting mode is used between traditional
          MOUNT and CSYNC
        mount_path: Path to mount the bucket at.
        mount_cmd: Command to mount the bucket. Should be single line.
        install_cmd: Command to install the mounting utility for MOUNT mode.
          Should be single line.

    Returns:
        str: Mounting command with the mounting script as a heredoc.
    """
    if mount_mode == storage_utils.StorageMode.MOUNT:
        script_path = f'~/.sky/mount_{random.randint(0, 1000000)}.sh'
    else:  # script path for CSYNC mode
        script_path = f'~/.sky/csync_{random.randint(0, 1000000)}.sh'

    script = get_mounting_script(mount_mode, mount_path, mount_cmd, install_cmd,
                                 version_check_cmd, csync_log_path)

    # TODO(romilb): Get direct bash script to work like so:
    # command = f'bash <<-\EOL' \
    #           f'{script}' \
    #           'EOL'

    # TODO(romilb): This heredoc should have EOF after script, but it
    #  fails with sky's ssh pipeline. Instead, we don't use EOF and use )
    #  as the end of heredoc. This raises a warning (here-document delimited
    #  by end-of-file) that can be safely ignored.

    # While these commands are run sequentially for each storage object,
    # we add random int to be on the safer side and avoid collisions.
    first_line = r'(cat <<-\EOF > {}'.format(script_path)
    command = (f'{first_line}'
               f'{script}'
               f') && chmod +x {script_path}'
               f' && bash {script_path}'
               f' && rm {script_path}')
    return command
