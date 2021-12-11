"""Miscellaneous Utils for Sky Data
"""
from typing import Any, Tuple

import boto3
from google.cloud import storage

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
    return boto3.client('s3', region_name=region)


def create_gcs_client() -> Client:
    """Helper method that connects to GCS Storage Client for
    GCS Bucket

    Args:
      region: str; Region name, e.g. us-central1, us-west1
    """
    return storage.Client()
