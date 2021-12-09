"""Storage and Object Classes for Sky Data.
"""
import enum
import os
import glob
from multiprocessing import pool
from typing import Any, Dict, Tuple

import boto3
from botocore import exceptions as S3Exception
from google.api_core import exceptions as GSException

from sky.data import data_utils, data_transfer
from sky import logging

logger = logging.init_logger(__name__)

Path = str
StorageHandle = Any


class StorageType(enum.Enum):
    S3 = 0
    GCS = 1
    AZURE = 2


class AbstractStore:
    """AbstractStore abstracts away the different storage types exposed by
    different clouds.

    AbstractStore returns a StorageHandle that must be handled by the
    ExecutionBackend to download onto VMs or containers.

    TODO: Mounting AbstractStore onto VMs
    """

    def __init__(self, name: str, path: str):
        self.name = name
        self.path = path
        self.is_initialized = False

    def delete(self) -> None:
        """
        Removes the storage object from the cloud
        """
        raise NotImplementedError

    def get_handle(self) -> StorageHandle:
        """
        Returns the storage handle for use by the execution backend to attach
        to VM/containers
        """
        raise NotImplementedError

    def upload_local_dir(self, local_path: str, num_threads: int = 32) -> None:
        """Uploads directory specified by local_path to the remote bucket

        Args:
          local_path: Local path on user's device
          num_threads: Number of threads to upload individual files
        """
        assert local_path is not None
        local_path = os.path.expanduser(local_path)
        all_paths = glob.glob(local_path + '/**', recursive=True)
        del all_paths[0]

        def _upload_thread(local_file):
            remote_path = local_file.replace(local_path, '')
            logger.info(f'Uploading {local_file} to {remote_path}')
            if os.path.isfile(local_file):
                self.upload_file(local_file, remote_path)

        pp = pool.ThreadPool(processes=num_threads)
        pp.map(_upload_thread, all_paths)

    def download_remote_dir(self, local_path: str) -> None:
        """Downloads directory from remote bucket to the specified
        local_path

        Args:
          local_path: Local path on user's device
        """
        assert local_path is not None
        local_path = os.path.expanduser(local_path)
        iterator = self.remote_filepath_iterator()
        for remote_path in iterator:
            remote_path = next(iterator)
            if remote_path[-1] == '/':
                continue
            path = os.path.join(local_path, remote_path)
            if not os.path.exists(os.path.dirname(path)):
                os.makedirs(os.path.dirname(path))
            logger.info(f'Downloading {remote_path} to {path}')
            self.download_file(remote_path, path)


class Storage(object):
    """Storage objects handle persistent and large volume storage in the sky.

    Users create Storage objects by defining the storage object name and the
    source, where the data originally comes from. Power users can specify their 
    pre-initialized stores if their data is already on the cloud.

      Typical Usage: (See prototype/examples/playground/storage_playground.py)
        storage = Storage(name='imagenet-bucket', source='~/Documents/imagenet')

        # Move data to S3
        storage.get_or_copy_to_s3()

        # Move data to Google Cloud Storage
        storage.get_or_copy_to_gcs()

        # Delete Storage for both S3 and GCS
        storage.delete()
    """

    def __init__(self,
                 name: str,
                 source: Path,
                 stores: Dict[str, AbstractStore] = None,
                 persistent: bool = True):
        """Initializes a Storage object

        Three fields are required: the name of the storage, the source path where
        the data is initially located, and the default mount path where the data
        will be mounted to on the cloud.

        Args:
          name: str; Name of the storage object. Typically used as the bucket name
            in backing object stores.
          source: str; File path where the data is initially stored. Can be
            on local machine or on cloud (s3://, gs://, etc.). Paths do not need
            to be absolute.
          stores: Optional; - specify pre-initialized stores (S3Store, GsStore)
          persistent: bool; Whether to persist across sky runs.
        """
        self.name = name
        self.source = source
        self.persistent = persistent

        # Sky optimizer either adds a storage object instance or selects
        # from existing ones
        if stores is None:
            self.stores = {}
        else:
            self.stores = stores

    def get_or_copy_to_s3(self):
        """Adds AWS S3 Store to Storage
        """
        s3_store = self.add_store('AWS')
        return "s3://" + s3_store.name

    def get_or_copy_to_gcs(self):
        """Adds GCS Store to Storage
        """
        gs_store = self.add_store('GCP')
        return "gs://" + gs_store.name

    def get_or_copy_to_azure_blob(self):
        """Adds Azure Blob Store to Storage

        TODO: Finish Azure Blob Backend class
        """
        raise NotImplementedError

    def add_store(self, cloud_type: StorageType) -> AbstractStore:
        """Invoked by the optimizer after it has selected a store to
        add it to Storage.

        Args:
          cloud_type: StorageType; Type of the storage [S3, GS, AZURE]
        """
        store = None

        if cloud_type == 'AWS':
            store = S3Store(name=self.name,
                            path=self.source,
                            stores=self.stores)
        elif cloud_type == 'GCP':
            store = GsStore(name=self.name,
                            path=self.source,
                            stores=self.stores)
        else:
            raise ValueError(f'{cloud_type} not supported as a Store!')

        assert store.is_initialized
        assert cloud_type not in self.stores, f'Storage type \
                                                    {cloud_type} \
                                                    already exists, \
                                                    why do you want to \
                                                    add another of \
                                                    the same type? '

        self.stores[cloud_type] = store
        return store

    def delete(self) -> None:
        """Deletes data for all storage objects.
        """
        for _, store in self.stores.items():
            store.delete()


class S3Store(AbstractStore):
    """S3Store inherits from Storage Object and represents the backend
    for S3 buckets.

        Typical Usage Example:
          # To initialize an S3Store implicitly, do this: 
          storage = Storage(name='imagenet-bucket', source='~/Documents/imagenet')

          # Move data to S3, and creates an S3Store
          storage.get_or_copy_to_s3()
    """

    def __init__(self, name: str, path: str, region='us-east-2', stores=None):
        super().__init__(name, path)
        self.stores = stores
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
                logger.info('Uploading Local to S3')
                self.upload_local_dir(self.path)
            else:
                logger.info('Syncing Local to S3')
                self.sync_from_local()
        self.is_initialized = True

    def delete(self) -> None:
        logger.info(f'Deleting S3 Bucket {self.name}')
        return self.delete_s3_bucket(self.name)

    def get_handle(self) -> StorageHandle:
        return boto3.resource('s3').Bucket(self.name)

    def sync_from_local(self) -> None:
        """Syncs Local folder with S3 Bucket. This method is called after
        the folder is already uploaded onto the S3 bucket.

        AWS Sync by default uses 10 threads to upload files to the bucket.
        To increase parallelism, modify max_concurrent_requests in your
        aws config file (Default path: ~/.aws/config).
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

    def upload_file(self, local_path: str, remote_path: str) -> None:
        """Uploads file from local path to remote path on s3 bucket
        using the boto3 API

        Args:
          local_path: str; Local path on user's device
          remote_path: str; Remote path on S3 bucket
        """
        self.client.upload_file(local_path, self.name, remote_path)

    def download_file(self, remote_path: str, local_path: str) -> None:
        """Downloads file from remote to local on s3 bucket
        using the boto3 API

        Args:
          remote_path: str; Remote path on S3 bucket
          local_path: str; Local path on user's device
        """
        self.bucket.download_file(remote_path, local_path)

    def remote_filepath_iterator(self) -> str:
        """Generator that yields the remote file paths from the S3 bucket
        """
        for obj in self.bucket.objects.filter():
            yield obj.key

    def create_s3_bucket(self,
                         bucket_name: str,
                         region='us-east-2') -> StorageHandle:
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
                logger.info(f'Created S3 bucket {bucket_name} in {region}')
        except S3Exception.ClientError as e:
            logger.info(e)
            return None
        return boto3.resource('s3').Bucket(bucket_name)

    def delete_s3_bucket(self, bucket_name: str) -> None:
        """Deletes S3 bucket, including all objects in bucket

        Args:
          bucket_name: str; Name of bucket
        """
        s3 = boto3.resource('s3')
        bucket = s3.Bucket(bucket_name)
        bucket.objects.all().delete()
        bucket.delete()


class GsStore(AbstractStore):
    """GsStore inherits from Storage Object and represents the backend
    for GCS buckets.

        Typical Usage Example:
          # To initialize an GsStore implicitly, do this: 
          storage = Storage(name='imagenet-bucket', source='~/Documents/imagenet')

          # Move data to Gcs, and creates an GsStore
          storage.get_or_copy_to_gcs()
    """

    def __init__(self, name: str, path: str, region='us-central1', stores=None):
        super().__init__(name, path)
        self.stores = stores
        if 'gs://' in self.path:
            assert name == data_utils.split_gcs_path(path)[
                0], 'GCS Bucket is specified as path, the name should be the \
                same as GCS bucket!'

        self.client = data_utils.create_gcs_client()
        self.name = name
        self.region = region
        self.bucket, is_new_bucket = self.get_bucket()
        self.path = path
        assert not is_new_bucket or self.path
        if 'gs://' not in self.path:
            if 's3://' in self.path:
                logger.info('Initating GCS Data Transfer Service from S3->GCS')
                s3_store = stores['AWS']
                self.transfer_to_gcs(s3_store)
            elif is_new_bucket and 's3://' not in self.path:
                logger.info('Uploading Local to GCS')
                self.upload_local_dir(self.path)
            else:
                logger.info('Syncing Local to GCS')
                self.sync_from_local()

        self.is_initialized = True

    def delete(self) -> None:
        logger.info(f'Deleting GCS Bucket {self.name}')
        return self.delete_gcs_bucket(self.name)

    def get_handle(self) -> StorageHandle:
        return self.client.get_bucket(self.name)

    def sync_from_local(self) -> None:
        """Syncs Local folder with GCS Bucket. This method is called after
        the folder is already uploaded onto the GCS bucket.
        """
        sync_command = f'gsutil -m rsync -r {self.path} gs://{self.name}/'
        os.system(sync_command)

    def transfer_to_gcs(self, s3_store: AbstractStore) -> None:
        """Transfer data from S3 to GCS bucket using Google's Data Transfer
        service

        Args:
          s3_store: Object; S3 Backend, see S3Store
        """
        data_transfer.s3_to_gcs(s3_store, self)

    def get_bucket(self) -> Tuple[StorageHandle, bool]:
        """Obtains the GCS bucket. If the GCS bucket does not exist, this
        method will create the GCS bucket
        """
        try:
            bucket = self.client.get_bucket(self.name)
            return bucket, False
        except GSException.NotFound as e:
            logger.info(e)
            return self.create_gcs_bucket(self.name), True

    def upload_file(self, local_file: str, remote_path: str) -> None:
        """Uploads file from local path to remote path on GCS bucket

        Args:
          local_path: str; Local path on user's device
          remote_path: str; Remote path on GCS bucket
        """
        blob = self.bucket.blob(remote_path)
        blob.upload_from_filename(local_file)

    def download_file(self, remote_path: str, local_path: str) -> None:
        """Downloads file from remote to local on GS bucket

        Args:
          remote_path: str; Remote path on GS bucket
          local_path: str; Local path on user's device
        """
        blob = self.bucket.blob(remote_path)
        blob.download_to_filename(local_path)

    def remote_filepath_iterator(self) -> str:
        """Generator that yields the remote file paths from the S3 bucket
        """
        iterator = self.bucket.list_blobs()
        while True:
            try:
                obj = next(iterator)
                yield obj.name
            except StopIteration:
                break

    def create_gcs_bucket(self,
                          bucket_name: str,
                          region='us-central1') -> StorageHandle:
        """Creates GCS bucket with specific name in specific region

        Args:
          bucket_name: str; Name of bucket
          region: str; Region name, e.g. us-central1, us-west1
        """
        bucket = self.client.bucket(bucket_name)
        bucket.storage_class = 'STANDARD'
        new_bucket = self.client.create_bucket(bucket, location=region)
        logger.info(
            f'Created GCS bucket {new_bucket.name} in {new_bucket.location} \
            with storage class {new_bucket.storage_class}')
        return new_bucket

    def delete_gcs_bucket(self, bucket_name: str) -> None:
        """Deletes GCS bucket, including all objects in bucket

        Args:
          bucket_name: str; Name of bucket
        """
        bucket = self.client.get_bucket(bucket_name)
        bucket.delete(force=True)
