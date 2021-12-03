"""Storage and StorageBackend Classes for Sky Data
"""
# pylint: disable=maybe-no-member
import os
from typing import Dict, Tuple

import boto3
from botocore.exceptions import ClientError
from google.api_core.exceptions import NotFound

from sky.backends import data_utils, data_transfer

Path = str
StorageHandle = str


class StorageBackend:
    """
    StorageBackends abstract away the different storage types exposed by
    different clouds. They return a StorageHandle that must be handled by the
    ExecutionBackend to mount onto VMs or containers.
    """

    def __init__(self, name: str, path: str):
        self.name = name
        self.path = path
        self.is_initialized = False
        self.storage_handle = None

    def cleanup(self) -> None:
        """
        Removes the storage object from the cloud
        """
        raise NotImplementedError

    def get_handle(self) -> StorageHandle:
        """
        Returns the storage handle for use by the execution backend to attach
        to VM/containers :return: StorageHandle for the storage backend
        """
        return self.storage_handle


class AWSStorageBackend(StorageBackend):
    """
    AWSStorageBackend inherits from StorageBackend and represents the backend
    for S3 buckets.
    """

    def __init__(self, name: str, path: str, region='us-east-2', backends=None):
        super().__init__(name, path)
        self.backends = backends
        if 's3://' in self.path:
            assert name == data_utils.split_s3_path(path)[
                0], 'S3 Bucket is specified as path, the name should be the \
             same as S3 bucket!'

        self.client = data_utils.create_s3_client(region)
        self.region = region
        self.bucket, is_new_bucket = self.get_bucket()
        assert not is_new_bucket or self.path
        if 's3://' not in self.path:
            if is_new_bucket:
                print('Uploading Local to S3')
                self.upload_from_local(self.path)
            else:
                print('Syncing Local to S3')
                self.sync_from_local()
        self.is_initialized = True

    def cleanup(self) -> None:
        print(f'Deleting S3 Bucket {self.name}')
        return self.delete_s3_bucket(self.name)

    def get_handle(self) -> StorageHandle:
        return boto3.resource('s3').Bucket(self.name)

    def sync_from_local(self) -> None:
        """Syncs Local folder with S3 Bucket. This method is called after
        the folder is already uploaded onto the S3 bucket.
        """
        sync_command = f'aws s3 sync {self.path} s3://{self.name}/'
        os.system(sync_command)

    def get_bucket(self) -> Tuple[StorageHandle, bool]:
        """Obtains the S3 bucket. If the S3 bucket does not exist, this
        method will create the S3 bucket
        """
        s3 = boto3.resource('s3')
        bucket = s3.Bucket(self.name)
        if bucket in s3.buckets.all():
            return bucket, False
        return self.create_s3_bucket(self.name), True

    def upload_from_local(self, local_path) -> None:
        """Uploads folder from local_path to S3 bucket

        Args:
          local_path: str; Local path on user's device
        """
        data_transfer.local_to_s3(local_path, self)

    def download_to_local(self, local_path) -> None:
        """Uploads folder from local_path to S3 bucket

        Args:
          local_path: str; Local path on user's device
        """
        data_transfer.s3_to_local(self, local_path)

    def create_s3_bucket(self, bucket_name, region='us-east-2'):
        """Creates S3 bucket with specific name in specific region

        Args:
          bucket_name: str; Name of bucket
          region: str; Region name, e.g. us-west-1, us-east-2
        """
        s3_client = self.client
        try:
            if region is None:
                s3_client.create_bucket(Bucket=bucket_name)
            else:
                location = {'LocationConstraint': region}
                s3_client.create_bucket(Bucket=bucket_name,
                                        CreateBucketConfiguration=location)
        except ClientError as e:
            print(e)
            return None
        return boto3.resource('s3').Bucket(bucket_name)

    def delete_s3_bucket(self, bucket_name):
        """Deletes S3 bucket, including all objects in bucket

        Args:
          bucket_name: str; Name of bucket
        """
        s3 = boto3.resource('s3')
        bucket = s3.Bucket(bucket_name)
        bucket.objects.all().delete()
        bucket.delete()


class GCSStorageBackend(StorageBackend):
    """
    GCSStorageBackend inherits from StorageBackend and represents the backend
    for GCS buckets.
    """

    def __init__(self,
                 name: str,
                 path: str,
                 region='us-central1',
                 backends=None):
        super().__init__(name, path)
        self.backends = backends
        if 'gcs://' in self.path:
            assert name == data_utils.split_gcs_path(path)[
                0], 'GCS Bucket is specified as path, the name should be the \
             same as GCS bucket!'

        self.client = data_utils.create_gcs_client()
        self.name = name
        self.region = region
        self.bucket, is_new_bucket = self.get_bucket()
        self.path = path
        assert not is_new_bucket or self.path
        if 'gcs://' not in self.path:
            if 's3://' in self.path:
                print('Initating GCS Data Transfer Service from S3->GCS')
                aws_backend = backends['AWS']
                self.transfer_to_gcs(aws_backend)
            elif is_new_bucket and 's3://' not in self.path:
                print('Uploading Local to GCS')
                self.upload_from_local(self.path)
            else:
                print('Syncing Local to GCS')
                self.sync_from_local()

        self.is_initialized = True

    def cleanup(self):
        print(f'Deleting GCS Bucket {self.name}')
        return self.delete_gcs_bucket(self.name)

    def get_handle(self):
        return self.client.get_bucket(self.name)

    def sync_from_local(self):
        """Syncs Local folder with GCS Bucket. This method is called after
        the folder is already uploaded onto the GCS bucket.
        """
        sync_command = f'gsutil -m rsync -r {self.path} gs://{self.name}/'
        os.system(sync_command)

    def transfer_to_gcs(self, aws_backend: StorageBackend):
        """Transfer data from S3 to GCS bucket using Google's Data Transfer
        service

        Args:
          aws_backend: StorageBackend; S3 Backend, see AWSStorageBackend
        """
        data_transfer.s3_to_gcs(aws_backend, self)

    def get_bucket(self):
        """Obtains the GCS bucket. If the GCS bucket does not exist, this
        method will create the GCS bucket
        """
        try:
            bucket = self.client.get_bucket(self.name)
            return bucket, False
        except NotFound:
            return self.create_gcs_bucket(self.name), True

    def upload_from_local(self, local_path):
        """Uploads folder from local_path to GCS bucket

        Args:
          local_path: str; Local path on user's device
        """
        data_transfer.local_to_gcs(local_path, self)

    def download_to_local(self, local_path):
        """Uploads folder from local_path to GCS bucket

        Args:
          local_path: str; Local path on user's device
        """
        data_transfer.gcs_to_local(self, local_path)

    def create_gcs_bucket(self, bucket_name, region='us-central1'):
        """Creates GCS bucket with specific name in specific region

        Args:
          bucket_name: str; Name of bucket
          region: str; Region name, e.g. us-central1, us-west1
        """
        bucket = self.client.bucket(bucket_name)
        bucket.storage_class = 'STANDARD'
        new_bucket = self.client.create_bucket(bucket, location=region)
        print('Created bucket {} in {} with storage class {}'.format(
            new_bucket.name, new_bucket.location, new_bucket.storage_class))
        return new_bucket

    def delete_gcs_bucket(self, bucket_name):
        """Deletes GCS bucket, including all objects in bucket

        Args:
          bucket_name: str; Name of bucket
        """
        bucket = self.client.get_bucket(bucket_name)
        bucket.delete(force=True)


class Storage(object):
    """
    Storage objects handle persistent and large volume storage in the sky.
    Users create Storage objects with an initialize_fn and a default mount poth.
    Power users can specify their pre-initialized backends if their data is
    already on the cloud.
    """

    def __init__(self,
                 name: str,
                 source_path: str,
                 default_mount_path: Path,
                 storage_backends: Dict[str, StorageBackend] = None,
                 persistent: bool = True):
        """
        :param name: Name of the storage object. Used as the unique id for
        persistence.
        :param initialize_fn: Shell commands to run to initialize storage.
        All paths must be absolute (using the default_mount_path)
        :param default_mount_path: Default path to mount this storage at.
        :param storage_backends: Optional - specify  pre-initialized
        storage backends
        :param persistent: Whether to persist across sky runs.
        """
        self.name = name
        self.source_path = source_path
        self.default_mount_path = default_mount_path
        self.persistent = persistent

        # Sky optimizer either adds a storage backend instance or selects
        # from existing ones
        if storage_backends is None:
            self.storage_backends = {}
        else:
            self.storage_backends = storage_backends

    def add_backend(self, cloud_type: str) -> None:
        """Invoked by the optimizer after it has created a storage backend to
        add it to Storage.

        Args:
          cloud_type: str; Type of the storage [AWS, GCP, Azure]
        """
        backend = None

        if cloud_type == 'AWS':
            backend = AWSStorageBackend(self.name,
                                        self.source_path,
                                        backends=self.storage_backends)
        elif cloud_type == 'GCP':
            backend = GCSStorageBackend(self.name,
                                        self.source_path,
                                        backends=self.storage_backends)
        else:
            raise ValueError(f'{cloud_type} not supported as Storage Backend!')

        assert backend.is_initialized
        assert cloud_type not in self.storage_backends, f'Storage type \
                                                    {cloud_type} \
                                                    already exists, \
                                                    why do you want to \
                                                    add another of \
                                                    the same type? '

        self.storage_backends[cloud_type] = backend

    def cleanup(self):
        """
        If not persistent, deletes data from all storage backends.
        :return:
        """
        for _, backend in self.storage_backends.items():
            backend.cleanup()
