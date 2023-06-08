"""Miscellaneous Utils for Sky Data
"""
from multiprocessing import pool
import os
import subprocess
from typing import Any, Callable, Dict, List, Optional, Tuple
import urllib.parse

from sky import exceptions
from sky import sky_logging
from sky.adaptors import aws, gcp, cloudflare
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
