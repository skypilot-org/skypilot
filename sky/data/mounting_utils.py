"""Helper functions for object store mounting in Sky Storage"""
import random
import shlex
import textwrap
from typing import Optional

from sky import exceptions
from sky.utils import command_runner

# Values used to construct mounting commands
_STAT_CACHE_TTL = '5s'
_STAT_CACHE_CAPACITY = 4096
_TYPE_CACHE_TTL = '5s'
_RENAME_DIR_LIMIT = 10000
# https://github.com/GoogleCloudPlatform/gcsfuse/releases
GCSFUSE_VERSION = '2.2.0'
# https://github.com/Azure/azure-storage-fuse/releases
BLOBFUSE2_VERSION = '2.2.0'
_BLOBFUSE_CACHE_ROOT_DIR = '~/.sky/blobfuse2_cache'
_BLOBFUSE_CACHE_DIR = ('~/.sky/blobfuse2_cache/'
                       '{storage_account_name}_{container_name}')


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


def get_az_mount_install_cmd() -> str:
    """Returns a command to install AZ Container mount utility blobfuse2."""
    install_cmd = ('sudo apt-get update; '
                   'sudo apt-get install -y '
                   '-o Dpkg::Options::="--force-confdef" '
                   'fuse3 libfuse3-dev && '
                   'wget -nc https://github.com/Azure/azure-storage-fuse'
                   f'/releases/download/blobfuse2-{BLOBFUSE2_VERSION}'
                   f'/blobfuse2-{BLOBFUSE2_VERSION}-Debian-11.0.x86_64.deb '
                   '-O /tmp/blobfuse2.deb && '
                   'sudo dpkg --install /tmp/blobfuse2.deb && '
                   f'mkdir -p {_BLOBFUSE_CACHE_ROOT_DIR};')

    return install_cmd


def get_az_mount_cmd(container_name: str,
                     storage_account_name: str,
                     mount_path: str,
                     storage_account_key: Optional[str] = None) -> str:
    """Returns a command to mount an AZ Container using blobfuse2.

    Args:
        container_name: Name of the mounting container.
        storage_account_name: Name of the storage account the given container
            belongs to.
        mount_path: Path where the container will be mounting.
        storage_account_key: Access key for the given storage account.

    Returns:
        str: Command used to mount AZ container with blobfuse2.
    """
    # Storage_account_key is set to None when mounting public container, and
    # mounting public containers are not officially supported by blobfuse2 yet.
    # Setting an empty SAS token value is a suggested workaround.
    # https://github.com/Azure/azure-storage-fuse/issues/1338
    if storage_account_key is None:
        key_env_var = f'AZURE_STORAGE_SAS_TOKEN={shlex.quote(" ")}'
    else:
        key_env_var = f'AZURE_STORAGE_ACCESS_KEY={storage_account_key}'

    cache_path = _BLOBFUSE_CACHE_DIR.format(
        storage_account_name=storage_account_name,
        container_name=container_name)
    mount_cmd = (f'AZURE_STORAGE_ACCOUNT={storage_account_name} '
                 f'{key_env_var} '
                 f'blobfuse2 {mount_path} --allow-other --no-symlinks '
                 '-o umask=022 -o default_permissions '
                 f'--tmp-path {cache_path} '
                 f'--container-name {container_name}')
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


def get_cos_mount_install_cmd() -> str:
    """Returns a command to install IBM COS mount utility rclone."""
    install_cmd = ('rclone version >/dev/null 2>&1 || '
                   '(curl https://rclone.org/install.sh | '
                   'sudo bash)')
    return install_cmd


def get_cos_mount_cmd(rclone_config_data: str, rclone_config_path: str,
                      bucket_rclone_profile: str, bucket_name: str,
                      mount_path: str) -> str:
    """Returns a command to mount an IBM COS bucket using rclone."""
    # creates a fusermount soft link on older (<22) Ubuntu systems for
    # rclone's mount utility.
    set_fuser3_soft_link = ('[ ! -f /bin/fusermount3 ] && '
                            'sudo ln -s /bin/fusermount /bin/fusermount3 || '
                            'true')
    # stores bucket profile in rclone config file at the cluster's nodes.
    configure_rclone_profile = (f'{set_fuser3_soft_link}; '
                                'mkdir -p ~/.config/rclone/ && '
                                f'echo "{rclone_config_data}" >> '
                                f'{rclone_config_path}')
    # --daemon will keep the mounting process running in the background.
    mount_cmd = (f'{configure_rclone_profile} && '
                 'rclone mount '
                 f'{bucket_rclone_profile}:{bucket_name} {mount_path} '
                 '--daemon')
    return mount_cmd


def _get_mount_binary(mount_cmd: str) -> str:
    """Returns mounting binary in string given as the mount command.

    Args:
        mount_cmd: Command used to mount a cloud storage.

    Returns:
        str: Name of the binary used to mount a cloud storage.
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

    # While these commands are run sequentially for each storage object,
    # we add random int to be on the safer side and avoid collisions.
    script_path = f'~/.sky/mount_{random.randint(0, 1000000)}.sh'
    command = (f'echo {shlex.quote(script)} > {script_path} && '
               f'chmod +x {script_path} && '
               f'bash {script_path} && '
               f'rm {script_path}')
    return command
