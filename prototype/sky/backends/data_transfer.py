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
# pylint: disable=maybe-no-member
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
    import pdb
    pdb.set_trace()
    # Update cloud bucket IAM role to allow for data transfer
    project_id = 'intercloud-320520'
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
    logging.info(f'AWS -> GCS Transfer Job: {json.dumps(result, indent=4)}')


def local_to_gcs(local_path: str, gcs_backend: GCSStorageBackend) -> None:
    """Creates a one-time transfer from Local to GCS.

    Args:
      local_path: str; Local path on user's device
      gcs_backend: StorageBackend; GCS Backend that contains a
      corresponding GCS bucket
    """
    assert local_path is not None
    local_path = os.path.expanduser(local_path)
    all_paths = glob.glob(local_path + '/**', recursive=True)
    del all_paths[0]

    def _upload_thread(local_file):
        remote_path = local_file.replace(local_path, '')
        logging.info(f'Uploading {local_file} to {remote_path}')
        if os.path.isfile(local_file):
            blob = gcs_backend.bucket.blob(remote_path)
            blob.upload_from_filename(local_file)

    pool = ThreadPool(processes=32)
    pool.map(_upload_thread, all_paths)


def local_to_s3(local_path: str, aws_backend: AWSStorageBackend) -> None:
    """Creates a one-time transfer from Local to S3.

    Args:
      local_path: str; Local path on user's device
      aws_backend: StorageBackend; AWS Backend that contains a
      corresponding S3 bucket
    """
    assert local_path is not None
    local_path = os.path.expanduser(local_path)
    all_paths = glob.glob(local_path + '/**', recursive=True)
    del all_paths[0]

    def _upload_thread(local_file):
        remote_path = local_file.replace(local_path, '')
        logging.info(f'Uploading {local_file} to {remote_path}')
        if os.path.isfile(local_file):
            aws_backend.client.upload_file(local_file, aws_backend.name,
                                           remote_path)

    pool = ThreadPool(processes=32)
    pool.map(_upload_thread, all_paths)


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
        logging.info(f'Downloading {obj.key} to {path}')
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
        logging.info(f'Downloading {obj.name} to {path}')
        obj.download_to_filename(path)


def _add_bucket_iam_member(bucket_name: str, role: str, member: str) -> None:
    storage_client = storage.Client()
    bucket = storage_client.bucket(bucket_name)

    policy = bucket.get_iam_policy(requested_policy_version=3)
    policy.bindings.append({'role': role, 'members': {member}})

    bucket.set_iam_policy(policy)

    logging.info(f'Added {member} with role {role} to {bucket_name}.')
