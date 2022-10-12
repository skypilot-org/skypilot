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
import json
import subprocess
import time

import colorama

from sky import clouds
from sky import sky_logging
from sky.adaptors import aws, gcp
from sky.backends import backend_utils
from sky.utils import ux_utils

logger = sky_logging.init_logger(__name__)

MAX_POLLS = 120000
POLL_INTERVAL = 1


def s3_to_gcs(s3_bucket_name: str, gs_bucket_name: str) -> None:
    """Creates a one-time transfer from Amazon S3 to Google Cloud Storage.

    Can be viewed from: https://console.cloud.google.com/transfer/cloud
    it will block until the transfer is complete.

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

    transfer_job = {
        'description': f'Transferring data from S3 Bucket \
        {s3_bucket_name} to GCS Bucket {gs_bucket_name}',
        'status': 'ENABLED',
        'projectId': project_id,
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

    response = storagetransfer.transferJobs().create(
        body=transfer_job).execute()
    operation = storagetransfer.transferJobs().run(jobName=response['name'],
                                                   body={
                                                       'projectId': project_id
                                                   }).execute()

    logger.info(f'{colorama.Fore.GREEN}Transfer job scheduled: '
                f'{colorama.Style.RESET_ALL}'
                f'AWS s3://{s3_bucket_name} -> GCS gs://{gs_bucket_name} ')
    logger.debug(json.dumps(operation, indent=4))
    logger.info('Waiting for the transfer to finish')
    start = time.time()
    with backend_utils.safe_console_status('Transferring'):
        for _ in range(MAX_POLLS):
            result = (storagetransfer.transferOperations().get(
                name=operation['name']).execute())
            if 'error' in result:
                with ux_utils.print_exception_no_traceback():
                    raise RuntimeError(result['error'])

            if 'done' in result and result['done']:
                logger.info('Operation done.')
                break
            time.sleep(POLL_INTERVAL)
    logger.info(f'Transfer finished in {time.time() - start:.2f} seconds')


def gcs_to_s3(gs_bucket_name: str, s3_bucket_name: str) -> None:
    """Creates a one-time transfer from Google Cloud Storage to Amazon S3.

     Args:
      gs_bucket_name: str; Name of the Google Cloud Storage Bucket
      s3_bucket_name: str; Name of the Amazon S3 Bucket
    """
    sync_command = (f'gsutil -m rsync -rd gs://{gs_bucket_name} '
                    f's3://{s3_bucket_name}')

    subprocess.call(sync_command, shell=True)


def _add_bucket_iam_member(bucket_name: str, role: str, member: str) -> None:
    storage_client = gcp.storage_client()
    bucket = storage_client.bucket(bucket_name)

    policy = bucket.get_iam_policy(requested_policy_version=3)
    policy.bindings.append({'role': role, 'members': {member}})

    bucket.set_iam_policy(policy)

    logger.info(f'Added {member} with role {role} to {bucket_name}.')
