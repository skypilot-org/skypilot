"""Data Transfer between 4 Sources:
- Local (User's laptop or lab machine)
- AWS - S3 Bucket
- GCP - GCS Bucket
- Azure - Azure blob bucket

Currently implemented:
- Local -> S3
- S3 -> Local
- Local -> GCS
- GCS -> Local
- S3 -> GCS

TODO:
- All combinations of Azure Transfer
- GCS -> S3
"""
from datetime import datetime
import glob
import json
from multiprocessing.pool import ThreadPool
import os
from typing import Any

import boto3
from googleapiclient import discovery
from google.cloud import storage
from oauth2client.client import GoogleCredentials

from sky import logging

logger = logging.init_logger(__name__)

AWSStorageBackend = Any
GCSStorageBackend = Any


def s3_to_gcs(aws_backend: AWSStorageBackend,
              gcs_backend: GCSStorageBackend) -> None:
    """Creates a one-time transfer from Amazon S3 to Google Cloud Storage.
    Can be viewed from: https://console.cloud.google.com/transfer/cloud

    Args:
      aws_backend: StorageBackend; AWS Backend that contains a
      corresponding S3 bucket
      gcs_backend: StorageBackend; GCS Backend that contains a
      corresponding GCS bucket
    """
    credentials = GoogleCredentials.get_application_default()
    storagetransfer = discovery.build(serviceName='storagetransfer',
                                      version='v1',
                                      credentials=credentials)

    session = boto3.Session()
    aws_credentials = session.get_credentials().get_frozen_credentials()

    with open(os.environ["GOOGLE_APPLICATION_CREDENTIALS"], "r") as fp:
        credentials = json.load(fp)
    project_id = credentials["project_id"]
    # Update cloud bucket IAM role to allow for data transfer
    storage_account = storagetransfer.googleServiceAccounts().get(
        projectId=project_id).execute()
    _add_bucket_iam_member(gcs_backend.name, 'roles/storage.admin',
                           'serviceAccount:' + storage_account['accountEmail'])

    starttime = datetime.utcnow()
    transfer_job = {
        'description': f'Transferring data from S3 Bucket \
         {aws_backend.name} to GCS Bucket {gcs_backend.name}',
        'status': 'ENABLED',
        'projectId': project_id,
        'schedule': {
            'scheduleStartDate': {
                'day': starttime.day,
                'month': starttime.month,
                'year': starttime.year,
            },
            'scheduleEndDate': {
                'day': starttime.day,
                'month': starttime.month,
                'year': starttime.year,
            },
        },
        'transferSpec': {
            'awsS3DataSource': {
                'bucketName': aws_backend.name,
                'awsAccessKey': {
                    'accessKeyId': aws_credentials.access_key,
                    'secretAccessKey': aws_credentials.secret_key,
                }
            },
            'gcsDataSink': {
                'bucketName': gcs_backend.name,
            }
        }
    }

    result = storagetransfer.transferJobs().create(body=transfer_job).execute()
    logger.info(f'AWS -> GCS Transfer Job: {json.dumps(result, indent=4)}')


def s3_to_local(aws_backend: AWSStorageBackend, local_path: str) -> None:
    """Creates a one-time transfer from S3 to Local.

    Args:
      aws_backend: StorageBackend; AWS Backend that contains a
      corresponding S3 bucket
      local_path: str; Local path on user's device
    """
    assert local_path is not None
    local_path = os.path.expanduser(local_path)
    for obj in aws_backend.bucket.objects.filter():
        if obj.key[-1] == '/':
            continue
        path = os.path.join(local_path, obj.key)
        if not os.path.exists(os.path.dirname(path)):
            os.makedirs(os.path.dirname(path))
        logger.info(f'Downloading {obj.key} to {path}')
        aws_backend.bucket.download_file(obj.key, path)


def gcs_to_local(gcs_backend: GCSStorageBackend, local_path: str) -> None:
    """Creates a one-time transfer from GCS to Local.

    Args:
      gcs_backend: StorageBackend; GCS Backend that contains a
      corresponding GCS bucket
      local_path: str; Local path on user's device
    """
    assert local_path is not None
    local_path = os.path.expanduser(local_path)
    for obj in gcs_backend.bucket.list_blobs():
        if obj.name[-1] == '/':
            continue
        path = os.path.join(local_path, obj.name)
        if not os.path.exists(os.path.dirname(path)):
            os.makedirs(os.path.dirname(path))
        logger.info(f'Downloading {obj.name} to {path}')
        obj.download_to_filename(path)


def _add_bucket_iam_member(bucket_name: str, role: str, member: str) -> None:
    storage_client = storage.Client()
    bucket = storage_client.bucket(bucket_name)

    policy = bucket.get_iam_policy(requested_policy_version=3)
    policy.bindings.append({'role': role, 'members': {member}})

    bucket.set_iam_policy(policy)

    logger.info(f'Added {member} with role {role} to {bucket_name}.')
