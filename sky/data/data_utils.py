"""Miscellaneous Utils for Sky Data
"""
import os
from typing import Any, Dict, List, Tuple
import urllib.parse

from sky.adaptors import aws, gcp

Client = Any


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


def is_cloud_store_url(url):
    result = urllib.parse.urlsplit(url)
    # '' means non-cloud URLs.
    return result.netloc


def group_files_by_dir(
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
    grouped_files = {}
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
