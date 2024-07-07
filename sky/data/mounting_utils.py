"""Helper functions for object store mounting in Sky Storage"""
import os
import random
import textwrap
from typing import Optional

from sky import exceptions
from sky.skylet import constants
from sky.utils import command_runner

# Values used to construct mounting commands
_STAT_CACHE_TTL = '5s'
_STAT_CACHE_CAPACITY = 4096
_TYPE_CACHE_TTL = '5s'
_RENAME_DIR_LIMIT = 10000
# https://github.com/GoogleCloudPlatform/gcsfuse/releases
GCSFUSE_VERSION = '2.2.0'
# https://github.com/rclone/rclone/releases
RCLONE_VERSION = '1.67.0'
# Creates a fusermount3 soft link on older (<22) Ubuntu systems to utilize
# Rclone's mounting utility.
FUSERMOUNT3_SOFT_LINK_CMD = ('[ ! -f /bin/fusermount3 ] && '
                             'sudo ln -s /bin/fusermount /bin/fusermount3 || '
                             'true')


def get_s3_mount_install_cmd() -> str:
    """Returns a command to install S3 mount utility goofys."""
    install_cmd = ('sudo wget -nc https://github.com/romilbhardwaj/goofys/'
                   'releases/download/0.24.0-romilb-upstream/goofys '
                   '-O /usr/local/bin/goofys && '
                   'sudo chmod 755 /usr/local/bin/goofys')
    return install_cmd


def get_s3_mount_cmd(bucket_name: str, mount_path: str) -> str:
    """Returns a command to mount an S3 bucket using goofys."""
    mount_cmd = ('goofys -o allow_other '
                 f'--stat-cache-ttl {_STAT_CACHE_TTL} '
                 f'--type-cache-ttl {_TYPE_CACHE_TTL} '
                 f'{bucket_name} {mount_path}')
    return mount_cmd


def get_gcs_mount_install_cmd() -> str:
    """Returns a command to install GCS mount utility gcsfuse."""
    install_cmd = ('wget -nc https://github.com/GoogleCloudPlatform/gcsfuse'
                   f'/releases/download/v{GCSFUSE_VERSION}/'
                   f'gcsfuse_{GCSFUSE_VERSION}_amd64.deb '
                   '-O /tmp/gcsfuse.deb && '
                   'sudo dpkg --install /tmp/gcsfuse.deb')
    return install_cmd


def get_gcs_mount_cmd(bucket_name: str, mount_path: str) -> str:
    """Returns a command to mount a GCS bucket using gcsfuse."""
    mount_cmd = ('gcsfuse -o allow_other '
                 '--implicit-dirs '
                 f'--stat-cache-capacity {_STAT_CACHE_CAPACITY} '
                 f'--stat-cache-ttl {_STAT_CACHE_TTL} '
                 f'--type-cache-ttl {_TYPE_CACHE_TTL} '
                 f'--rename-dir-limit {_RENAME_DIR_LIMIT} '
                 f'{bucket_name} {mount_path}')
    return mount_cmd


def get_r2_mount_cmd(r2_credentials_path: str, r2_profile_name: str,
                     endpoint_url: str, bucket_name: str,
                     mount_path: str) -> str:
    """Returns a command to install R2 mount utility goofys."""
    mount_cmd = (f'AWS_SHARED_CREDENTIALS_FILE={r2_credentials_path} '
                 f'AWS_PROFILE={r2_profile_name} goofys -o allow_other '
                 f'--stat-cache-ttl {_STAT_CACHE_TTL} '
                 f'--type-cache-ttl {_TYPE_CACHE_TTL} '
                 f'--endpoint {endpoint_url} '
                 f'{bucket_name} {mount_path}')
    return mount_cmd


def get_rclone_install_cmd() -> str:
    """Returns a command to install Rclone."""
    install_cmd = ('wget -nc https://github.com/rclone/rclone/releases'
                   f'/download/v{RCLONE_VERSION}/rclone-v{RCLONE_VERSION}'
                   '-linux-amd64.deb -O /tmp/rclone.deb && '
                   'sudo dpkg --install /tmp/rclone.deb')
    return install_cmd


def get_cos_mount_cmd(rclone_config: str, rclone_profile_name: str,
                      bucket_name: str, mount_path: str) -> str:
    """Returns a command to mount an IBM COS bucket using rclone."""
    # stores bucket profile in rclone config file at the cluster's nodes.
    configure_rclone_profile = (f'{FUSERMOUNT3_SOFT_LINK_CMD}; '
                                f'mkdir -p {constants.RCLONE_CONFIG_DIR} && '
                                f'echo "{rclone_config}" >> '
                                f'{constants.RCLONE_CONFIG_PATH}')
    # --daemon will keep the mounting process running in the background.
    mount_cmd = (f'{configure_rclone_profile} && '
                 'rclone mount '
                 f'{rclone_profile_name}:{bucket_name} {mount_path} '
                 '--daemon')
    return mount_cmd


def get_mount_cached_cmd(rclone_config: str, rclone_profile_name: str,
                         bucket_name: str, mount_path: str) -> str:
    """Returns a command to mount a GCP/AWS bucket using rclone."""
    # stores bucket profile in rclone config file at the remote nodes.
    configure_rclone_profile = (f'{FUSERMOUNT3_SOFT_LINK_CMD}; '
                                f'mkdir -p {constants.RCLONE_CONFIG_DIR} && '
                                f'echo "{rclone_config}" >> '
                                f'{constants.RCLONE_CONFIG_PATH}')
    # --daemon will keep the mounting process running in the background.
    # TODO(Doyoung): remove rclone log related scripts and options when done with implementation.
    log_dir_path = os.path.expanduser('~/.sky/rclone_log')
    log_file_path = os.path.join(log_dir_path, f'{bucket_name}.log')
    create_log_cmd = f'mkdir -p {log_dir_path} && touch {log_file_path}'
    # when mounting multiple directories with vfs cache mode, it's handled by
    # rclone to create separate cache directories at ~/.cache/rclone/vfs. It is
    # not necessary to specify separate cache directories.
    mount_cmd = (
        #f'{create_log_cmd}; '
        f'{configure_rclone_profile} && '
        'rclone mount '
        f'{rclone_profile_name}:{bucket_name} {mount_path} '
        '--daemon --daemon-wait 0 '
        # need to update the log fiel so it grabs the home directory from the remote instance.
        #f'--log-file {log_file_path} --log-level DEBUG ' #log related flags
        '--allow-other --vfs-cache-mode full --dir-cache-time 30s '
        '--transfers 1 --vfs-cache-poll-interval 5s')
    return mount_cmd


def _get_mount_binary(mount_cmd: str) -> str:
    """Returns mounting binary in string given as the mount command.

    Args:
        mount_cmd: str; command used to mount a cloud storage.

    Returns:
        str: name of the binary used to mount a cloud storage.
    """
    if 'goofys' in mount_cmd:
        return 'goofys'
    elif 'gcsfuse' in mount_cmd:
        return 'gcsfuse'
    elif 'blobfuse2' in mount_cmd:
        return 'blobfuse2'
    else:
        assert 'rclone' in mount_cmd
        return 'rclone'


def get_mounting_script(
    mount_path: str,
    mount_cmd: str,
    install_cmd: str,
    version_check_cmd: Optional[str] = None,
) -> str:
    """Generates the mounting script.

    Generated script first unmounts any existing mount at the mount path,
    checks and installs the mounting utility if required, creates the mount
    path and finally mounts the bucket.

    Args:
        mount_path: Path to mount the bucket at.
        install_cmd: Command to install the mounting utility. Should be
          single line.
        mount_cmd: Command to mount the bucket. Should be single line.
        version_check_cmd: Command to check the version of already installed
          mounting util.

    Returns:
        str: Mounting script as a str.
    """

    mount_binary = _get_mount_binary(mount_cmd)
    installed_check = f'[ -x "$(command -v {mount_binary})" ]'
    if version_check_cmd is not None:
        installed_check += f' && {version_check_cmd}'

    script = textwrap.dedent(f"""
        #!/usr/bin/env bash
        set -e
                             
        {command_runner.ALIAS_SUDO_TO_EMPTY_FOR_ROOT_CMD}

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

    return script


def get_mounting_command(
    mount_path: str,
    install_cmd: str,
    mount_cmd: str,
    version_check_cmd: Optional[str] = None,
) -> str:
    """Generates the mounting command for a given bucket.

    The generated mounting script is written to a temporary file, which is then
    executed and subsequently deleted, ensuring that these operations are
    encapsulated within a single, executable command sequence.

    Args:
        mount_path: Path to mount the bucket at.
        install_cmd: Command to install the mounting utility. Should be
          single line.
        mount_cmd: Command to mount the bucket. Should be single line.
        version_check_cmd: Command to check the version of already installed
          mounting util.

    Returns:
        str: Mounting command with the mounting script as a heredoc.
    """
    script = get_mounting_script(mount_path, mount_cmd, install_cmd,
                                 version_check_cmd)

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
