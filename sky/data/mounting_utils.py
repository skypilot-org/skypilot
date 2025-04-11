"""Helper functions for object store mounting in Sky Storage"""
import hashlib
import os
import random
import shlex
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
# Creates a fusermount3 soft link on older (<22) Ubuntu systems to utilize
# Rclone's mounting utility.
FUSERMOUNT3_SOFT_LINK_CMD = ('[ ! -f /bin/fusermount3 ] && '
                             'sudo ln -s /bin/fusermount /bin/fusermount3 || '
                             'true')
# https://github.com/Azure/azure-storage-fuse/releases
BLOBFUSE2_VERSION = '2.2.0'
_BLOBFUSE_CACHE_ROOT_DIR = '~/.sky/blobfuse2_cache'
_BLOBFUSE_CACHE_DIR = ('~/.sky/blobfuse2_cache/'
                       '{storage_account_name}_{container_name}')
# https://github.com/rclone/rclone/releases
RCLONE_VERSION = 'v1.68.2'

# A wrapper for goofys to choose the logging mechanism based on environment.
_GOOFYS_WRAPPER = ('$(if [ -S /dev/log ] ; then '
                   'echo "goofys"; '
                   'else '
                   'echo "goofys --log-file $(mktemp -t goofys.XXXX.log)"; '
                   'fi)')


def get_s3_mount_install_cmd() -> str:
    """Returns a command to install S3 mount utility goofys."""
    # TODO(aylei): maintain our goofys fork under skypilot-org
    install_cmd = ('ARCH=$(uname -m) && '
                   'if [ "$ARCH" = "aarch64" ] || [ "$ARCH" = "arm64" ]; then '
                   '  echo "goofys is not supported on $ARCH" && '
                   f'  exit {exceptions.ARCH_NOT_SUPPORTED_EXIT_CODE}; '
                   'else '
                   '  ARCH_SUFFIX="amd64"; '
                   'fi && '
                   'sudo wget -nc https://github.com/aylei/goofys/'
                   'releases/download/0.24.0-aylei-upstream/goofys '
                   '-O /usr/local/bin/goofys && '
                   'sudo chmod 755 /usr/local/bin/goofys')
    return install_cmd


# pylint: disable=invalid-name
def get_s3_mount_cmd(bucket_name: str,
                     mount_path: str,
                     _bucket_sub_path: Optional[str] = None) -> str:
    """Returns a command to mount an S3 bucket using goofys."""
    if _bucket_sub_path is None:
        _bucket_sub_path = ''
    else:
        _bucket_sub_path = f':{_bucket_sub_path}'
    mount_cmd = (f'{_GOOFYS_WRAPPER} -o allow_other '
                 f'--stat-cache-ttl {_STAT_CACHE_TTL} '
                 f'--type-cache-ttl {_TYPE_CACHE_TTL} '
                 f'{bucket_name}{_bucket_sub_path} {mount_path}')
    return mount_cmd


def get_nebius_mount_cmd(nebius_profile_name: str,
                         bucket_name: str,
                         endpoint_url: str,
                         mount_path: str,
                         _bucket_sub_path: Optional[str] = None) -> str:
    """Returns a command to install Nebius mount utility goofys."""
    if _bucket_sub_path is None:
        _bucket_sub_path = ''
    else:
        _bucket_sub_path = f':{_bucket_sub_path}'
    mount_cmd = (f'AWS_PROFILE={nebius_profile_name} {_GOOFYS_WRAPPER} '
                 '-o allow_other '
                 f'--stat-cache-ttl {_STAT_CACHE_TTL} '
                 f'--type-cache-ttl {_TYPE_CACHE_TTL} '
                 f'--endpoint {endpoint_url} '
                 f'{bucket_name}{_bucket_sub_path} {mount_path}')
    return mount_cmd


def get_gcs_mount_install_cmd() -> str:
    """Returns a command to install GCS mount utility gcsfuse."""
    install_cmd = ('ARCH=$(uname -m) && '
                   'if [ "$ARCH" = "aarch64" ] || [ "$ARCH" = "arm64" ]; then '
                   '  ARCH_SUFFIX="arm64"; '
                   'else '
                   '  ARCH_SUFFIX="amd64"; '
                   'fi && '
                   'wget -nc https://github.com/GoogleCloudPlatform/gcsfuse'
                   f'/releases/download/v{GCSFUSE_VERSION}/'
                   f'gcsfuse_{GCSFUSE_VERSION}_${{ARCH_SUFFIX}}.deb '
                   '-O /tmp/gcsfuse.deb && '
                   'sudo dpkg --install /tmp/gcsfuse.deb')
    return install_cmd


# pylint: disable=invalid-name
def get_gcs_mount_cmd(bucket_name: str,
                      mount_path: str,
                      _bucket_sub_path: Optional[str] = None) -> str:
    """Returns a command to mount a GCS bucket using gcsfuse."""
    bucket_sub_path_arg = f'--only-dir {_bucket_sub_path} '\
        if _bucket_sub_path else ''
    mount_cmd = ('gcsfuse -o allow_other '
                 '--implicit-dirs '
                 f'--stat-cache-capacity {_STAT_CACHE_CAPACITY} '
                 f'--stat-cache-ttl {_STAT_CACHE_TTL} '
                 f'--type-cache-ttl {_TYPE_CACHE_TTL} '
                 f'--rename-dir-limit {_RENAME_DIR_LIMIT} '
                 f'{bucket_sub_path_arg}'
                 f'{bucket_name} {mount_path}')
    return mount_cmd


def get_az_mount_install_cmd() -> str:
    """Returns a command to install AZ Container mount utility blobfuse2."""
    install_cmd = (
        'sudo apt-get update; '
        'sudo apt-get install -y '
        '-o Dpkg::Options::="--force-confdef" '
        'fuse3 libfuse3-dev || { '
        '  echo "fuse3 not available, falling back to fuse"; '
        '  sudo apt-get install -y '
        '  -o Dpkg::Options::="--force-confdef" '
        '  fuse libfuse-dev; '
        '} && '
        'ARCH=$(uname -m) && '
        'if [ "$ARCH" = "aarch64" ] || [ "$ARCH" = "arm64" ]; then '
        '  echo "blobfuse2 is not supported on $ARCH" && '
        f'  exit {exceptions.ARCH_NOT_SUPPORTED_EXIT_CODE}; '
        'else '
        '  ARCH_SUFFIX="x86_64"; '
        'fi && '
        'wget -nc https://github.com/Azure/azure-storage-fuse'
        f'/releases/download/blobfuse2-{BLOBFUSE2_VERSION}'
        f'/blobfuse2-{BLOBFUSE2_VERSION}-Debian-11.0.${{ARCH_SUFFIX}}.deb '
        '-O /tmp/blobfuse2.deb && '
        'sudo dpkg --install /tmp/blobfuse2.deb && '
        f'mkdir -p {_BLOBFUSE_CACHE_ROOT_DIR};')

    return install_cmd


# pylint: disable=invalid-name
def get_az_mount_cmd(container_name: str,
                     storage_account_name: str,
                     mount_path: str,
                     storage_account_key: Optional[str] = None,
                     _bucket_sub_path: Optional[str] = None) -> str:
    """Returns a command to mount an AZ Container using blobfuse2.

    Args:
        container_name: Name of the mounting container.
        storage_account_name: Name of the storage account the given container
            belongs to.
        mount_path: Path where the container will be mounting.
        storage_account_key: Access key for the given storage account.
        _bucket_sub_path: Sub path of the mounting container.

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
        key_env_var = ('AZURE_STORAGE_ACCESS_KEY='
                       f'{shlex.quote(storage_account_key)}')

    cache_path = _BLOBFUSE_CACHE_DIR.format(
        storage_account_name=storage_account_name,
        container_name=container_name)
    # The line below ensures the cache directory is new before mounting to
    # avoid "config error in file_cache [temp directory not empty]" error, which
    # can occur after stopping and starting the same cluster on Azure.
    # This helps ensure a clean state for blobfuse2 operations.
    remote_boot_time_cmd = 'date +%s -d "$(uptime -s)"'
    if _bucket_sub_path is None:
        bucket_sub_path_arg = ''
    else:
        bucket_sub_path_arg = f'--subdirectory={_bucket_sub_path}/ '
    mount_options = '-o allow_other -o default_permissions'
    # TODO(zpoint): clear old cache that has been created in the previous boot.
    blobfuse2_cmd = ('blobfuse2 --no-symlinks -o umask=022 '
                     f'--tmp-path {cache_path}_$({remote_boot_time_cmd}) '
                     f'{bucket_sub_path_arg}'
                     f'--container-name {container_name}')
    # 1. Set -o nonempty to bypass empty directory check of blobfuse2 when using
    # fusermount-wrapper, since the mount is delegated to fusermount and
    # blobfuse2 only get the mounted fd.
    # 2. {} is the mount point placeholder that will be replaced with the
    # mounted fd by fusermount-wrapper.
    wrapped = (f'fusermount-wrapper -m {mount_path} {mount_options} '
               f'-- {blobfuse2_cmd} -o nonempty {{}}')
    original = f'{blobfuse2_cmd} {mount_options} {mount_path}'
    # If fusermount-wrapper is available, use it to wrap the blobfuse2 command
    # to avoid requiring root privilege.
    # TODO(aylei): feeling hacky, refactor this.
    get_mount_cmd = ('command -v fusermount-wrapper >/dev/null 2>&1 && '
                     f'echo "{wrapped}" || echo "{original}"')
    mount_cmd = (f'AZURE_STORAGE_ACCOUNT={storage_account_name} '
                 f'{key_env_var} '
                 f'$({get_mount_cmd})')
    return mount_cmd


# pylint: disable=invalid-name
def get_r2_mount_cmd(r2_credentials_path: str,
                     r2_profile_name: str,
                     endpoint_url: str,
                     bucket_name: str,
                     mount_path: str,
                     _bucket_sub_path: Optional[str] = None) -> str:
    """Returns a command to install R2 mount utility goofys."""
    if _bucket_sub_path is None:
        _bucket_sub_path = ''
    else:
        _bucket_sub_path = f':{_bucket_sub_path}'
    mount_cmd = (f'AWS_SHARED_CREDENTIALS_FILE={r2_credentials_path} '
                 f'AWS_PROFILE={r2_profile_name} {_GOOFYS_WRAPPER} '
                 '-o allow_other '
                 f'--stat-cache-ttl {_STAT_CACHE_TTL} '
                 f'--type-cache-ttl {_TYPE_CACHE_TTL} '
                 f'--endpoint {endpoint_url} '
                 f'{bucket_name}{_bucket_sub_path} {mount_path}')
    return mount_cmd


def get_cos_mount_cmd(rclone_config: str,
                      rclone_profile_name: str,
                      bucket_name: str,
                      mount_path: str,
                      _bucket_sub_path: Optional[str] = None) -> str:
    """Returns a command to mount an IBM COS bucket using rclone."""
    # stores bucket profile in rclone config file at the cluster's nodes.
    configure_rclone_profile = (f'{FUSERMOUNT3_SOFT_LINK_CMD}; '
                                f'mkdir -p {constants.RCLONE_CONFIG_DIR} && '
                                f'echo "{rclone_config}" >> '
                                f'{constants.RCLONE_CONFIG_PATH}')
    if _bucket_sub_path is None:
        sub_path_arg = f'{bucket_name}/{_bucket_sub_path}'
    else:
        sub_path_arg = f'/{bucket_name}'
    # --daemon will keep the mounting process running in the background.
    mount_cmd = (f'{configure_rclone_profile} && '
                 'rclone mount '
                 f'{rclone_profile_name}:{sub_path_arg} {mount_path} '
                 '--daemon')
    return mount_cmd


def get_mount_cached_cmd(rclone_config: str, rclone_profile_name: str,
                         bucket_name: str, mount_path: str) -> str:
    """Returns a command to mount a bucket using rclone with vfs cache."""
    # stores bucket profile in rclone config file at the remote nodes.
    configure_rclone_profile = (f'{FUSERMOUNT3_SOFT_LINK_CMD}; '
                                f'mkdir -p {constants.RCLONE_CONFIG_DIR} && '
                                f'echo {shlex.quote(rclone_config)} >> '
                                f'{constants.RCLONE_CONFIG_PATH}')
    # Assume mount path is unique. We use a hash of mount path as
    # various filenames related to the mount.
    # This is because the full path may be longer than
    # the filename length limit.
    # The hash is a non-negative integer in string form.
    hashed_mount_path = hashlib.md5(mount_path.encode()).hexdigest()
    log_file_path = os.path.join(constants.RCLONE_LOG_DIR,
                                 f'{hashed_mount_path}.log')
    create_log_cmd = (f'mkdir -p {constants.RCLONE_LOG_DIR} && '
                      f'touch {log_file_path}')
    # when mounting multiple directories with vfs cache mode, it's handled by
    # rclone to create separate cache directories at ~/.cache/rclone/vfs. It is
    # not necessary to specify separate cache directories.
    mount_cmd = (
        f'{create_log_cmd} && '
        f'{configure_rclone_profile} && '
        'rclone mount '
        f'{rclone_profile_name}:{bucket_name} {mount_path} '
        # '--daemon' keeps the mounting process running in the background.
        # fail in 10 seconds if mount cannot complete by then,
        # which should be plenty of time.
        '--daemon --daemon-wait 10 '
        f'--log-file {log_file_path} --log-level INFO '
        # '--dir-cache-time' sets how long directory listings are cached before
        # rclone checks the remote storage for changes again. A shorter
        # interval allows for faster detection of new or updated files on the
        # remote, but increases the frequency of metadata lookups.
        '--allow-other --vfs-cache-mode full --dir-cache-time 10s '
        # '--transfers 1' guarantees the files written at the local mount point
        # to be uploaded to the backend storage in the order of creation.
        # '--vfs-cache-poll-interval' specifies the frequency of how often
        # rclone checks the local mount point for stale objects in cache.
        # '--vfs-write-back' defines the time to write files on remote storage
        # after last use of the file in local mountpoint.
        '--transfers 1 --vfs-cache-poll-interval 10s --vfs-write-back 1s '
        # Have rclone evict files if the cache size exceeds 10G.
        # This is to prevent cache from growing too large and
        # using up all the disk space. Note that files that opened
        # by a process is not evicted from the cache.
        '--vfs-cache-max-size 10G '
        # give each mount its own cache directory
        f'--cache-dir {constants.RCLONE_CACHE_DIR}/{hashed_mount_path} '
        # This command produces children processes, which need to be
        # detached from the current process's terminal. The command doesn't
        # produce any output, so we aren't dropping any logs.
        '> /dev/null 2>&1')
    return mount_cmd


def get_rclone_install_cmd() -> str:
    """ RClone installation for both apt-get and rpm.
    This would be common command.
    """
    # pylint: disable=line-too-long
    install_cmd = (
        'ARCH=$(uname -m) && '
        'if [ "$ARCH" = "aarch64" ] || [ "$ARCH" = "arm64" ]; then '
        '  ARCH_SUFFIX="arm"; '
        'else '
        '  ARCH_SUFFIX="amd64"; '
        'fi && '
        f'(which dpkg > /dev/null 2>&1 && (which rclone > /dev/null || (cd ~ > /dev/null'
        f' && curl -O https://downloads.rclone.org/{RCLONE_VERSION}/rclone-{RCLONE_VERSION}-linux-${{ARCH_SUFFIX}}.deb'
        f' && sudo dpkg -i rclone-{RCLONE_VERSION}-linux-${{ARCH_SUFFIX}}.deb'
        f' && rm -f rclone-{RCLONE_VERSION}-linux-${{ARCH_SUFFIX}}.deb)))'
        f' || (which rclone > /dev/null || (cd ~ > /dev/null'
        f' && curl -O https://downloads.rclone.org/{RCLONE_VERSION}/rclone-{RCLONE_VERSION}-linux-${{ARCH_SUFFIX}}.rpm'
        f' && sudo yum --nogpgcheck install rclone-{RCLONE_VERSION}-linux-${{ARCH_SUFFIX}}.rpm -y'
        f' && rm -f rclone-{RCLONE_VERSION}-linux-${{ARCH_SUFFIX}}.rpm))')
    return install_cmd


def get_oci_mount_cmd(mount_path: str, store_name: str, region: str,
                      namespace: str, compartment: str, config_file: str,
                      config_profile: str) -> str:
    """ OCI specific RClone mount command for oci object storage. """
    # pylint: disable=line-too-long
    mount_cmd = (
        f'sudo chown -R `whoami` {mount_path}'
        f' && rclone config create oos_{store_name} oracleobjectstorage'
        f' provider user_principal_auth namespace {namespace}'
        f' compartment {compartment} region {region}'
        f' oci-config-file {config_file}'
        f' oci-config-profile {config_profile}'
        f' && sed -i "s/oci-config-file/config_file/g;'
        f' s/oci-config-profile/config_profile/g" ~/.config/rclone/rclone.conf'
        f' && ([ ! -f /bin/fusermount3 ] && sudo ln -s /bin/fusermount /bin/fusermount3 || true)'
        f' && (grep -q {mount_path} /proc/mounts || rclone mount oos_{store_name}:{store_name} {mount_path} --daemon --allow-non-empty)'
    )
    return mount_cmd


def get_rclone_version_check_cmd() -> str:
    """ RClone version check. This would be common command. """
    return f'rclone --version | grep -q {RCLONE_VERSION}'


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
