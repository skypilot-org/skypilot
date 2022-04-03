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
import json
import os
from typing import Any

from sky import clouds
from sky import sky_logging
from sky.adaptors import aws, gcp

logger = sky_logging.init_logger(__name__)

S3Store = Any
GcsStore = Any


def s3_to_gcs(s3_bucket_name: str, gs_bucket_name: str) -> None:
    """Creates a one-time transfer from Amazon S3 to Google Cloud Storage.
    Can be viewed from: https://console.cloud.google.com/transfer/cloud

    Args:
      s3_bucket_name: str; Name of the Amazon S3 Bucket
      gs_bucket_name: str; Name of the Google Cloud Storage Bucket
    """
    from oauth2client.client import GoogleCredentials  # pylint: disable=import-outside-toplevel

    oauth_credentials = GoogleCredentials.get_application_default()
    storagetransfer = gcp.build('storagetransfer',
                                'v1',
                                credentials=oauth_credentials)

    session = aws.session()
    aws_credentials = session.get_credentials().get_frozen_credentials()

    project_id = clouds.GCP.get_project_id()

    # Update cloud bucket IAM role to allow for data transfer
    storage_account = storagetransfer.googleServiceAccounts().get(
        projectId=project_id).execute()
    _add_bucket_iam_member(gs_bucket_name, 'roles/storage.admin',
                           'serviceAccount:' + storage_account['accountEmail'])

    starttime = datetime.utcnow()
    transfer_job = {
        'description': f'Transferring data from S3 Bucket \
        {s3_bucket_name} to GCS Bucket {gs_bucket_name}',
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
                'bucketName': s3_bucket_name,
                'awsAccessKey': {
                    'accessKeyId': aws_credentials.access_key,
                    'secretAccessKey': aws_credentials.secret_key,
                }
            },
            'gcsDataSink': {
                'bucketName': gs_bucket_name,
            }
        }
    }

    result = storagetransfer.transferJobs().create(body=transfer_job).execute()
    logger.info(f'AWS -> GCS Transfer Job: {json.dumps(result, indent=4)}')


def gcs_to_s3(gs_bucket_name: str, s3_bucket_name: str) -> None:
    """Creates a one-time transfer from Google Cloud Storage to Amazon S3.

     Args:
      gs_bucket_name: str; Name of the Google Cloud Storage Bucket
      s3_bucket_name: str; Name of the Amazon S3 Bucket
    """
    sync_command = f'gsutil -m rsync -rd gs://{gs_bucket_name} \
        s3://{s3_bucket_name}'

    os.system(sync_command)


def _add_bucket_iam_member(bucket_name: str, role: str, member: str) -> None:
    storage_client = gcp.storage_client()
    bucket = storage_client.bucket(bucket_name)

    policy = bucket.get_iam_policy(requested_policy_version=3)
    policy.bindings.append({'role': role, 'members': {member}})

    bucket.set_iam_policy(policy)

    logger.info(f'Added {member} with role {role} to {bucket_name}.')
