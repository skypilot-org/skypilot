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

import boto3
from googleapiclient import discovery
from google.cloud import storage
from oauth2client.client import GoogleCredentials

from sky import logging

logger = logging.init_logger(__name__)

S3Store = Any
GcsStore = Any


def s3_to_gcs(s3_store: S3Store, gs_store: GcsStore) -> None:
    """Creates a one-time transfer from Amazon S3 to Google Cloud Storage.
    Can be viewed from: https://console.cloud.google.com/transfer/cloud

    Args:
      s3_store: S3Store; AWS S3 Store that contains a
      corresponding S3 bucket
      gs_store: GcsStore; GCP Gs Store that contains a
      corresponding GCS bucket
    """
    credentials = GoogleCredentials.get_application_default()
    storagetransfer = discovery.build(serviceName='storagetransfer',
                                      version='v1',
                                      credentials=credentials)

    session = boto3.Session()
    aws_credentials = session.get_credentials().get_frozen_credentials()

    with open(os.environ['GOOGLE_APPLICATION_CREDENTIALS'], 'r') as fp:
        gcp_credentials = json.load(fp)
    project_id = gcp_credentials['project_id']

    # Update cloud bucket IAM role to allow for data transfer
    storage_account = storagetransfer.googleServiceAccounts().get(
        projectId=project_id).execute()
    _add_bucket_iam_member(gs_store.name, 'roles/storage.admin',
                           'serviceAccount:' + storage_account['accountEmail'])

    starttime = datetime.utcnow()
    transfer_job = {
        'description': f'Transferring data from S3 Bucket \
        {s3_store.name} to GCS Bucket {gs_store.name}',
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
                'bucketName': s3_store.name,
                'awsAccessKey': {
                    'accessKeyId': aws_credentials.access_key,
                    'secretAccessKey': aws_credentials.secret_key,
                }
            },
            'gcsDataSink': {
                'bucketName': gs_store.name,
            }
        }
    }

    result = storagetransfer.transferJobs().create(body=transfer_job).execute()
    logger.info(f'AWS -> GCS Transfer Job: {json.dumps(result, indent=4)}')


def _add_bucket_iam_member(bucket_name: str, role: str, member: str) -> None:
    storage_client = storage.Client()
    bucket = storage_client.bucket(bucket_name)

    policy = bucket.get_iam_policy(requested_policy_version=3)
    policy.bindings.append({'role': role, 'members': {member}})

    bucket.set_iam_policy(policy)

    logger.info(f'Added {member} with role {role} to {bucket_name}.')
