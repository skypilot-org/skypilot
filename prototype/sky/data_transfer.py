import boto3
from googleapiclient import discovery
from google.cloud import storage
import json
from oauth2client.client import GoogleCredentials
from datetime import datetime
import os
import glob
from multiprocessing.pool import ThreadPool


def _s3_to_gcs(aws_backend, gcs_backend):
    """Create a one-time transfer from Amazon S3 to Google Cloud Storage."""
    credentials = GoogleCredentials.get_application_default()
    storagetransfer = discovery.build('storagetransfer',
                                      'v1',
                                      credentials=credentials)

    session = boto3.Session()
    aws_credentials = session.get_credentials().get_frozen_credentials()

    # Update cloud bucket IAM role to allow for data transfer
    project_id = 'intercloud-320520'
    storage_account = storagetransfer.googleServiceAccounts().get(
        projectId=project_id).execute()
    _add_bucket_iam_member(gcs_backend.name, "roles/storage.admin",
                           "serviceAccount:" + storage_account["accountEmail"])

    starttime = datetime.utcnow()
    transfer_job = {
        'description': f"Transferring data from S3 Bucket {aws_backend.name} to GCS Bucket {gcs_backend.name}",
        'status': 'ENABLED',
        'projectId': project_id,
        "schedule": {
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
    print('AWS -> GCS Transfer Job: {}'.format(json.dumps(result, indent=4)))


def _local_to_gcs(local_path, gcs_backend):
    assert local_path is not None
    local_path = os.path.expanduser(local_path)
    all_paths = glob.glob(local_path + '/**', recursive=True)
    del all_paths[0]

    def _upload_thread(local_file):
        remote_path = local_file.replace(local_path, "")
        print(f"Uploading {local_file} to {remote_path}")
        if os.path.isfile(local_file):
            blob = gcs_backend.bucket.blob(remote_path)
            blob.upload_from_filename(local_file)

    pool = ThreadPool(processes=32)
    pool.map(_upload_thread, all_paths)


def _local_to_s3(local_path, aws_backend):
    assert local_path is not None
    local_path = os.path.expanduser(local_path)
    all_paths = glob.glob(local_path + '/**', recursive=True)
    del all_paths[0]

    def _upload_thread(local_file):
        remote_path = local_file.replace(local_path, "")
        print(f"Uploading {local_file} to {remote_path}")
        if os.path.isfile(local_file):
            aws_backend.client.upload_file(local_file, aws_backend.name,
                                           remote_path)

    pool = ThreadPool(processes=32)
    pool.map(_upload_thread, all_paths)


def _s3_to_local(aws_backend, local_path):
    assert local_path is not None
    local_path = os.path.expanduser(local_path)
    for obj in aws_backend.bucket.objects.filter():
        if obj.key[-1] == '/':
            continue
        path = os.path.join(local_path, obj.key)
        if not os.path.exists(os.path.dirname(path)):
            os.makedirs(os.path.dirname(path))
        print(f"Downloading {obj.key} to {path}")
        aws_backend.bucket.download_file(obj.key, path)


def _gcs_to_local(gcs_backend, local_path):
    assert local_path is not None
    local_path = os.path.expanduser(local_path)
    for obj in gcs_backend.bucket.list_blobs():
        if obj.name[-1] == '/':
            continue
        path = os.path.join(local_path, obj.name)
        if not os.path.exists(os.path.dirname(path)):
            os.makedirs(os.path.dirname(path))
        print(f"Downloading {obj.name} to {path}")
        obj.download_to_filename(path)


# From Google Tutorial
def _add_bucket_iam_member(bucket_name, role, member):
    storage_client = storage.Client()
    bucket = storage_client.bucket(bucket_name)

    policy = bucket.get_iam_policy(requested_policy_version=3)
    policy.bindings.append({"role": role, "members": {member}})

    bucket.set_iam_policy(policy)

    print("Added {} with role {} to {}.".format(member, role, bucket_name))
