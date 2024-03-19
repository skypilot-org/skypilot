"""Miscellaneous Utils for Sky Data
"""
import concurrent.futures
from enum import Enum
from multiprocessing import pool
import os
import re
import subprocess
import textwrap
from typing import Any, Callable, Dict, List, Optional, Tuple
import urllib.parse

from filelock import FileLock

from sky import exceptions
from sky import sky_logging
from sky.adaptors import aws
from sky.adaptors import cloudflare
from sky.adaptors import gcp
from sky.adaptors import ibm
from sky.utils import ux_utils

Client = Any

logger = sky_logging.init_logger(__name__)


def split_s3_path(s3_path: str) -> Tuple[str, str]:
    """Splits S3 Path into Bucket name and Relative Path to Bucket

    Args:
      s3_path: str; S3 Path, e.g. s3://imagenet/train/
    """
    path_parts = s3_path.replace('s3://', '').split('/')
    bucket = path_parts.pop(0)
    key = '/'.join(path_parts)
    return bucket, key


def split_gcs_path(gcs_path: str) -> Tuple[str, str]:
    """Splits GCS Path into Bucket name and Relative Path to Bucket

    Args:
      gcs_path: str; GCS Path, e.g. gcs://imagenet/train/
    """
    path_parts = gcs_path.replace('gs://', '').split('/')
    bucket = path_parts.pop(0)
    key = '/'.join(path_parts)
    return bucket, key


def split_r2_path(r2_path: str) -> Tuple[str, str]:
    """Splits R2 Path into Bucket name and Relative Path to Bucket

    Args:
      r2_path: str; R2 Path, e.g. r2://imagenet/train/
    """
    path_parts = r2_path.replace('r2://', '').split('/')
    bucket = path_parts.pop(0)
    key = '/'.join(path_parts)
    return bucket, key


def split_cos_path(s3_path: str) -> Tuple[str, str, str]:
    """returns extracted region, bucket name and bucket path to data
        from the specified cos bucket's url.
        url expected format: "cos://region/bucket_name/optional_data_path"

        Raises:
          ValueError if s3_path isn't a valid IBM COS bucket URI."""

    # regex pattern: 3rd group differs from 2nd by accepting '/'
    pattern_region_bucket_data = r'cos://([-\w]+)/([-\w]+)(.*)'
    try:
        match = re.match(pattern_region_bucket_data, s3_path)
    except TypeError:
        # URI provided isn't a string
        raise ValueError(f'URI received: {s3_path} is not valid.')  # pylint: disable=raise-missing-from
    if match:
        region, bucket_name, data_path = match.group(1), match.group(
            2), match.group(3)
    else:
        raise ValueError(
            f'Bucket URI received: {s3_path} does not match expected pattern '
            'of "cos://region/bucket_name/optional_data_path"')

    if region not in get_cos_regions():
        raise ValueError('region missing/invalid in IBM COS URI.')

    return bucket_name, data_path, region


def create_s3_client(region: str = 'us-east-2') -> Client:
    """Helper method that connects to Boto3 client for S3 Bucket

    Args:
      region: str; Region name, e.g. us-west-1, us-east-2
    """
    return aws.client('s3', region_name=region)


def verify_s3_bucket(name: str) -> bool:
    """Helper method that checks if the S3 bucket exists

    Args:
      name: str; Name of S3 Bucket (without s3:// prefix)
    """
    s3 = aws.resource('s3')
    bucket = s3.Bucket(name)
    return bucket in s3.buckets.all()


def verify_gcs_bucket(name: str) -> bool:
    """Helper method that checks if the GCS bucket exists

    Args:
      name: str; Name of GCS Bucket (without gs:// prefix)
    """
    try:
        gcp.storage_client().get_bucket(name)
        return True
    except gcp.not_found_exception():
        return False


def create_r2_client(region: str = 'auto') -> Client:
    """Helper method that connects to Boto3 client for R2 Bucket

    Args:
      region: str; Region for CLOUDFLARE R2 is set to auto
    """
    return cloudflare.client('s3', region)


def verify_r2_bucket(name: str) -> bool:
    """Helper method that checks if the R2 bucket exists

    Args:
      name: str; Name of R2 Bucket (without r2:// prefix)
    """
    r2 = cloudflare.resource('s3')
    bucket = r2.Bucket(name)
    return bucket in r2.buckets.all()


def verify_ibm_cos_bucket(name: str) -> bool:
    """Helper method that checks if the cos bucket exists

    Args:
      name: str; Name of a COS Bucket (without cos://region/ prefix)
    """
    return get_ibm_cos_bucket_region(name) != ''


def _get_ibm_cos_bucket_region(region, bucket_name):
    """helper function of get_ibm_cos_bucket_region

    returns region of bucket if exists in a region,
    else returns an empty string.
    located outside of get_ibm_cos_bucket_region since
    inner function aren't pickable for processes.

    Args:
        region (str): region to search the bucket in.
        bucket_name (str): name of bucket in search.

    Returns:
        str: region or ''
    """
    try:
        # reinitialize a client to search in different regions
        tmp_client = ibm.get_cos_client(region)
        tmp_client.head_bucket(Bucket=bucket_name)
        return region
    except ibm.ibm_botocore.exceptions.ClientError as e:  # type: ignore[union-attr] # pylint: disable=line-too-long
        if e.response['Error']['Code'] == '404':
            logger.debug(f'bucket {bucket_name} was not found '
                         f'in {region}')
        # Failed to connect to bucket, e.g. owned by different user
        elif e.response['Error']['Code'] == '403':
            raise exceptions.StorageBucketGetError()
        else:
            raise e
    return ''


def get_ibm_cos_bucket_region(bucket_name: str) -> str:
    """Returns the bucket's region if exists, otherwise returns empty string.

    Args:
        bucket_name (str): name of IBM COS bucket.

    Raises:
        exceptions.StorageBucketGetError if failed to connect to bucket.

    Returns:
        str: region of bucket if bucket exists, else empty string.
    """

    # parallel lookup on bucket storage region
    # Using threads would be more efficient, but raises a leaked semaphore
    # warning on Mac machines when guarding cos client with a thread lock
    # nested within the process lock. Source:
    # https://github.com/skypilot-org/skypilot/pull/1966#issuecomment-1646992938
    with concurrent.futures.ProcessPoolExecutor() as executor:
        futures = []
        for region_scanned in get_cos_regions():
            future = executor.submit(_get_ibm_cos_bucket_region, region_scanned,
                                     bucket_name)
            futures.append(future)

        for future in concurrent.futures.as_completed(futures):
            region = future.result()
            if region:
                logger.debug(f'bucket {bucket_name} was found in {region}')
                return region
    return ''


def is_cloud_store_url(url):
    result = urllib.parse.urlsplit(url)
    # '' means non-cloud URLs.
    return result.netloc


def _group_files_by_dir(
        source_list: List[str]) -> Tuple[Dict[str, List[str]], List[str]]:
    """Groups a list of paths based on their directory

    Given a list of paths, generates a dict of {dir_name: List[file_name]}
    which groups files with same dir, and a list of dirs in the source_list.

    This is used to optimize uploads by reducing the number of calls to rsync.
    E.g., ['a/b/c.txt', 'a/b/d.txt', 'a/e.txt'] will be grouped into
    {'a/b': ['c.txt', 'd.txt'], 'a': ['e.txt']}, and these three files can be
    uploaded in two rsync calls instead of three.

    Args:
        source_list: List[str]; List of paths to group
    """
    grouped_files: Dict[str, List[str]] = {}
    dirs = []
    for source in source_list:
        source = os.path.abspath(os.path.expanduser(source))
        if os.path.isdir(source):
            dirs.append(source)
        else:
            base_path = os.path.dirname(source)
            file_name = os.path.basename(source)
            if base_path not in grouped_files:
                grouped_files[base_path] = []
            grouped_files[base_path].append(file_name)
    return grouped_files, dirs


def parallel_upload(source_path_list: List[str],
                    filesync_command_generator: Callable[[str, List[str]], str],
                    dirsync_command_generator: Callable[[str, str], str],
                    bucket_name: str,
                    access_denied_message: str,
                    create_dirs: bool = False,
                    max_concurrent_uploads: Optional[int] = None) -> None:
    """Helper function to run parallel uploads for a list of paths.

    Used by S3Store, GCSStore, and R2Store to run rsync commands in parallel by
    providing appropriate command generators.

    Args:
        source_path_list: List of paths to local files or directories
        filesync_command_generator: Callable that generates rsync command
            for a list of files belonging to the same dir.
        dirsync_command_generator: Callable that generates rsync command
            for a directory.
        access_denied_message: Message to intercept from the underlying
            upload utility when permissions are insufficient. Used in
            exception handling.
        create_dirs: If the local_path is a directory and this is set to
            False, the contents of the directory are directly uploaded to
            root of the bucket. If the local_path is a directory and this is
            set to True, the directory is created in the bucket root and
            contents are uploaded to it.
        max_concurrent_uploads: Maximum number of concurrent threads to use
            to upload files.
    """
    # Generate gsutil rsync command for files and dirs
    commands = []
    grouped_files, dirs = _group_files_by_dir(source_path_list)
    # Generate file upload commands
    for dir_path, file_names in grouped_files.items():
        sync_command = filesync_command_generator(dir_path, file_names)
        commands.append(sync_command)
    # Generate dir upload commands
    for dir_path in dirs:
        if create_dirs:
            dest_dir_name = os.path.basename(dir_path)
        else:
            dest_dir_name = ''
        sync_command = dirsync_command_generator(dir_path, dest_dir_name)
        commands.append(sync_command)

    # Run commands in parallel
    with pool.ThreadPool(processes=max_concurrent_uploads) as p:
        p.starmap(
            run_upload_cli,
            zip(commands, [access_denied_message] * len(commands),
                [bucket_name] * len(commands)))


def get_gsutil_command() -> Tuple[str, str]:
    """Gets the alias'd command for gsutil and a command to define the alias.

    This is required for applying platform-specific flags to gsutil.

    In particular, we disable multiprocessing on Mac using
    `-o "GSUtil:parallel_process_count=1"`. Multithreading is still enabled.
    gsutil on Mac has a bug with multiprocessing that causes it to crash
    when uploading files. Related issues:
    https://bugs.python.org/issue33725
    https://github.com/GoogleCloudPlatform/gsutil/issues/464

    The flags are added by checking the platform using bash in a one-liner.
    The platform check is done inline to have the flags match where the command
    is executed, rather than where the code is run. This is important when
    the command is run in a remote VM.

    Returns:
        Tuple[str, str] : (gsutil_alias, command to generate the alias)
        The command to generate alias must be run before using the alias. E.g.,
        ```
        gsutil_alias, alias_gen = get_gsutil_command()
        cmd_to_run = f'{alias_gen}; {gsutil_alias} cp ...'
        ```
    """
    gsutil_alias = 'skypilot_gsutil'
    disable_multiprocessing_flag = '-o "GSUtil:parallel_process_count=1"'

    # Define skypilot_gsutil as a shell function instead of an alias.
    # This function will behave just like alias, but can be called immediately
    # after its definition on the same line
    alias_gen = (f'[[ "$(uname)" == "Darwin" ]] && {gsutil_alias}() {{ '
                 f'gsutil -m {disable_multiprocessing_flag} "$@"; }} '
                 f'|| {gsutil_alias}() {{ gsutil -m "$@"; }}')

    return gsutil_alias, alias_gen


def run_upload_cli(command: str, access_denied_message: str, bucket_name: str):
    # TODO(zhwu): Use log_lib.run_with_log() and redirect the output
    # to a log file.
    with subprocess.Popen(command,
                          stderr=subprocess.PIPE,
                          stdout=subprocess.DEVNULL,
                          shell=True) as process:
        stderr = []
        assert process.stderr is not None  # for mypy
        while True:
            line = process.stderr.readline()
            if not line:
                break
            str_line = line.decode('utf-8')
            stderr.append(str_line)
            if access_denied_message in str_line:
                process.kill()
                with ux_utils.print_exception_no_traceback():
                    raise PermissionError(
                        'Failed to upload files to '
                        'the remote bucket. The bucket does not have '
                        'write permissions. It is possible that '
                        'the bucket is public.')
        returncode = process.wait()
        if returncode != 0:
            stderr_str = '\n'.join(stderr)
            with ux_utils.print_exception_no_traceback():
                logger.error(stderr_str)
                raise exceptions.StorageUploadError(
                    f'Upload to bucket failed for store {bucket_name}. '
                    'Please check the logs.')


def get_cos_regions() -> List[str]:
    return [
        'us-south', 'us-east', 'eu-de', 'eu-gb', 'eu-es', 'ca-tor', 'au-syd',
        'br-sao', 'jp-osa', 'jp-tok'
    ]


class Rclone():
    """Static class implementing common utilities of rclone without rclone sdk.

    Storage providers supported by rclone are required to:
    - list their rclone profile prefix in RcloneClouds
    - implement configuration in get_rclone_config()
    """

    RCLONE_CONFIG_PATH = '~/.config/rclone/rclone.conf'
    _RCLONE_ABS_CONFIG_PATH = os.path.expanduser(RCLONE_CONFIG_PATH)

    # Mapping of storage providers using rclone
    # to their respective profile prefix
    class RcloneClouds(Enum):
        IBM = 'sky-ibm-'

    @staticmethod
    def generate_rclone_bucket_profile_name(bucket_name: str,
                                            cloud: RcloneClouds) -> str:
        """Returns rclone profile name for specified bucket

        Args:
            bucket_name (str): name of bucket
            cloud (RcloneClouds): enum object of storage provider
                supported via rclone
        """
        try:
            return cloud.value + bucket_name
        except AttributeError as e:
            with ux_utils.print_exception_no_traceback():
                raise ValueError(f'Value: {cloud} isn\'t a member of '
                                 'Rclone.RcloneClouds') from e

    @staticmethod
    def get_rclone_config(bucket_name: str, cloud: RcloneClouds,
                          region: str) -> str:
        bucket_rclone_profile = Rclone.generate_rclone_bucket_profile_name(
            bucket_name, cloud)
        if cloud is Rclone.RcloneClouds.IBM:
            access_key_id, secret_access_key = ibm.get_hmac_keys()
            config_data = textwrap.dedent(f"""\
                [{bucket_rclone_profile}]
                type = s3
                provider = IBMCOS
                access_key_id = {access_key_id}
                secret_access_key = {secret_access_key}
                region = {region}
                endpoint = s3.{region}.cloud-object-storage.appdomain.cloud
                location_constraint = {region}-smart
                acl = private
                """)
        else:
            with ux_utils.print_exception_no_traceback():
                raise NotImplementedError('No rclone configuration builder was '
                                          f'implemented for cloud: {cloud}.')
        return config_data

    @staticmethod
    def store_rclone_config(bucket_name: str, cloud: RcloneClouds,
                            region: str) -> str:
        """Creates a configuration files for rclone - used for
        bucket syncing and mounting """

        rclone_config_path = Rclone._RCLONE_ABS_CONFIG_PATH
        config_data = Rclone.get_rclone_config(bucket_name, cloud, region)

        # Raise exception if rclone isn't installed
        try:
            subprocess.run('rclone version',
                           shell=True,
                           check=True,
                           stdout=subprocess.PIPE)
        except subprocess.CalledProcessError:
            with ux_utils.print_exception_no_traceback():
                raise exceptions.StorageError(
                    'rclone wasn\'t detected. '
                    'Consider installing via: '
                    '"curl https://rclone.org/install.sh '
                    '| sudo bash" ') from None

        # create ~/.config/rclone/ if doesn't exist
        os.makedirs(os.path.dirname(rclone_config_path), exist_ok=True)
        # create rclone.conf if doesn't exist
        if not os.path.isfile(rclone_config_path):
            open(rclone_config_path, 'w', encoding='utf-8').close()

        # write back file without profile: [bucket_name]
        # to which the new bucket profile is appended
        with FileLock(rclone_config_path + '.lock'):
            profiles_to_keep = Rclone._remove_bucket_profile_rclone(
                bucket_name, cloud)
            with open(f'{rclone_config_path}', 'w', encoding='utf-8') as file:
                if profiles_to_keep:
                    file.writelines(profiles_to_keep)
                    if profiles_to_keep[-1].strip():
                        # add a new line to config_data
                        # if last file line contains data
                        config_data += '\n'
                file.write(config_data)

        return config_data

    @staticmethod
    def get_region_from_rclone(bucket_name: str, cloud: RcloneClouds) -> str:
        """Returns region field of the specified bucket in rclone.conf
         if bucket exists, else empty string"""
        bucket_rclone_profile = Rclone.generate_rclone_bucket_profile_name(
            bucket_name, cloud)
        with open(Rclone._RCLONE_ABS_CONFIG_PATH, 'r',
                  encoding='utf-8') as file:
            bucket_profile_found = False
            for line in file:
                if line.lstrip().startswith('#'):  # skip user's comments.
                    continue
                if line.strip() == f'[{bucket_rclone_profile}]':
                    bucket_profile_found = True
                elif bucket_profile_found and line.startswith('region'):
                    return line.split('=')[1].strip()
                elif bucket_profile_found and line.startswith('['):
                    # for efficiency stop if we've searched past the
                    # requested bucket profile with no match
                    return ''
        # segment for bucket and/or region field for bucket wasn't found
        return ''

    @staticmethod
    def delete_rclone_bucket_profile(bucket_name: str, cloud: RcloneClouds):
        """Deletes specified bucket profile for rclone.conf"""
        bucket_rclone_profile = Rclone.generate_rclone_bucket_profile_name(
            bucket_name, cloud)
        rclone_config_path = Rclone._RCLONE_ABS_CONFIG_PATH

        if not os.path.isfile(rclone_config_path):
            logger.warning(
                'Failed to locate "rclone.conf" while '
                f'trying to delete rclone profile: {bucket_rclone_profile}')
            return

        with FileLock(rclone_config_path + '.lock'):
            profiles_to_keep = Rclone._remove_bucket_profile_rclone(
                bucket_name, cloud)

            # write back file without profile: [bucket_rclone_profile]
            with open(f'{rclone_config_path}', 'w', encoding='utf-8') as file:
                file.writelines(profiles_to_keep)

    @staticmethod
    def _remove_bucket_profile_rclone(bucket_name: str,
                                      cloud: RcloneClouds) -> List[str]:
        """Returns rclone profiles without profiles matching
          [profile_prefix+bucket_name]"""
        bucket_rclone_profile = Rclone.generate_rclone_bucket_profile_name(
            bucket_name, cloud)
        rclone_config_path = Rclone._RCLONE_ABS_CONFIG_PATH

        with open(f'{rclone_config_path}', 'r', encoding='utf-8') as file:
            lines = file.readlines()  # returns a list of the file's lines
            # delete existing bucket profile matching:
            # '[profile_prefix+bucket_name]'
            lines_to_keep = []  # lines to write back to file
            # while skip_lines is True avoid adding lines to lines_to_keep
            skip_lines = False

        for line in lines:
            if line.lstrip().startswith('#') and not skip_lines:
                # keep user comments only if they aren't under
                # a profile we are discarding
                lines_to_keep.append(line)
            elif f'[{bucket_rclone_profile}]' in line:
                skip_lines = True
            elif skip_lines:
                if '[' in line:
                    # former profile segment ended, new one begins
                    skip_lines = False
                    lines_to_keep.append(line)
            else:
                lines_to_keep.append(line)

        return lines_to_keep
