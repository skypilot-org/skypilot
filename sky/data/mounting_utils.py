"""Helper functions for object store mounting in Sky Storage"""
import random
import textwrap
from typing import Optional

from sky import exceptions


def get_mounting_command(
    mount_path: str,
    install_cmd: str,
    mount_cmd: str,
    version_check_cmd: Optional[str] = None,
) -> str:
    """
    Generates the mounting command for a given bucket. Generated script first
    unmounts any existing mount at the mount path, checks and installs the
    mounting utility if required, creates the mount path and finally mounts
    the bucket.

    Args:
        mount_path: Path to mount the bucket at.
        install_cmd: Command to install the mounting utility. Should be
          single line.
        mount_cmd: Command to mount the bucket. Should be single line.

    Returns:
        str: Mounting command with the mounting script as a heredoc.
    """
    mount_binary = mount_cmd.split()[0]
    installed_check = f'[ -x "$(command -v {mount_binary})" ]'
    if version_check_cmd is not None:
        installed_check += f' && {version_check_cmd}'
    script = textwrap.dedent(f"""
        #!/usr/bin/env bash
        set -e

        MOUNT_PATH={mount_path}
        MOUNT_BINARY={mount_binary}

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

        # Check if mount path exists
        if [ ! -d "$MOUNT_PATH" ]; then
          echo "Mount path $MOUNT_PATH does not exist. Creating..."
          sudo mkdir -p $MOUNT_PATH
          sudo chmod 777 $MOUNT_PATH
        else
          # Check if mount path contains files
          if [ "$(ls -A $MOUNT_PATH)" ]; then
            echo "Mount path $MOUNT_PATH is not empty. Please mount to another path or remove it first."
            exit {exceptions.MOUNT_PATH_NON_EMPTY_CODE}
          fi
        fi
        echo "Mounting $SOURCE_BUCKET to $MOUNT_PATH with $MOUNT_BINARY..."
        {mount_cmd}
        echo "Mounting done."
    """)

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
    script_path = f'~/.sky/mount_{random.randint(0, 1000000)}.sh'
    first_line = r'(cat <<-\EOF > {}'.format(script_path)
    command = (f'{first_line}'
               f'{script}'
               f') && chmod +x {script_path}'
               f' && bash {script_path}'
               f' && rm {script_path}')
    return command
