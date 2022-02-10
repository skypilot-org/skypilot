"""Storage and Store Classes for Sky Data."""
import enum
import os
from typing import Any, Dict, Optional, Tuple

from sky.data import data_utils, data_transfer
from sky import sky_logging
from sky.cloud_adaptors import aws, gcp

logger = sky_logging.init_logger(__name__)

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

    def __init__(self, name: str, source: str):
        self.name = name
        self.source = source
        self.is_initialized = False

    def delete(self) -> None:
        """
        Removes the storage object from the cloud
        """
        raise NotImplementedError

    def get_handle(self) -> StorageHandle:
        """Returns the storage handle for use by the execution backend to attach
        to VM/containers
        """
        raise NotImplementedError

    def sync_local_dir(self) -> None:
        """Syncs local directory with Store bucket
        """
        raise NotImplementedError

    def download_remote_dir(self, local_path: str) -> None:
        """Downloads directory from remote bucket to the specified
        local_path

        Args:
          local_path: Local path on user's device
        """
        assert local_path is not None
        local_path = os.path.expanduser(local_path)
        iterator = self._remote_filepath_iterator()
        for remote_path in iterator:
            remote_path = next(iterator)
            if remote_path[-1] == '/':
                continue
            path = os.path.join(local_path, remote_path)
            if not os.path.exists(os.path.dirname(path)):
                os.makedirs(os.path.dirname(path))
            logger.info(f'Downloading {remote_path} to {path}')
            self._download_file(remote_path, path)

    def _download_file(self, remote_path: str, local_path: str) -> None:
        """Downloads file from remote to local on Store

        Args:
          remote_path: str; Remote file path on Store
          local_path: str; Local file path on user's device
        """
        raise NotImplementedError

    def __deepcopy__(self, memo):
        # S3 Client and GCS Client cannot be deep copied, hence the
        # original Store object is returned
        return self


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
                 stores: Optional[Dict[StorageType, AbstractStore]] = None,
                 persistent: bool = True):
        """Initializes a Storage object

        Three fields are required: the name of the storage, the source
        path where the data is initially located, and the default mount
        path where the data will be mounted to on the cloud.

        Args:
          name: str; Name of the storage object. Typically used as the
            bucket name in backing object stores.
          source: str; File path where the data is initially stored. Can be
            on local machine or on cloud (s3://, gs://, etc.). Paths do not need
            to be absolute.
          stores: Optional; - specify pre-initialized stores (S3Store, GcsStore)
          persistent: bool; Whether to persist across sky launches.
        """
        self.name = name
        self.source = source
        self.persistent = persistent

        # Sky optimizer either adds a storage object instance or selects
        # from existing ones
        self.stores = {} if stores is None else stores

        # If source is a pre-existing bucket, connect to the bucket
        # If the bucket does not exist, this will error out
        if 's3://' in self.source:
            self.get_or_copy_to_s3()
        elif 'gs://' in self.source:
            self.get_or_copy_to_gcs()

    def get_or_copy_to_s3(self):
        """Adds AWS S3 Store to Storage
        """
        s3_store = self.add_store(StorageType.S3)
        return 's3://' + s3_store.name

    def get_or_copy_to_gcs(self):
        """Adds GCS Store to Storage
        """
        gs_store = self.add_store(StorageType.GCS)
        return 'gs://' + gs_store.name

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

        if cloud_type in self.stores:
            logger.info(f'Storage type {cloud_type} already exists!')
            return self.stores[cloud_type]

        if cloud_type == StorageType.S3:
            store = S3Store(name=self.name, source=self.source)
        elif cloud_type == StorageType.GCS:
            store = GcsStore(name=self.name, source=self.source)
        else:
            raise ValueError(f'{cloud_type} not supported as a Store!')

        assert store.is_initialized

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
    """

    def __init__(self, name: str, source: str, region='us-east-2'):
        super().__init__(name, source)
        if self.source.startswith('s3://'):
            assert name == data_utils.split_s3_path(source)[0], (
                'S3 Bucket is specified as path, the name should be the '
                'same as S3 bucket!')

        elif self.source.startswith('gs://'):
            assert name == data_utils.split_gcs_path(source)[0], (
                'GCS Bucket is specified as path, the name should be the '
                'same as GCS bucket!')
            assert data_utils.verify_gcs_bucket(name), (
                f'Source specified as {source}, a GCS bucket. ',
                'GCS Bucket should exist!')

        self.client = data_utils.create_s3_client(region)
        self.region = region
        self.bucket, is_new_bucket = self._get_bucket()
        assert not is_new_bucket or self.source

        if self.source.startswith('s3://'):
            pass
        elif self.source.startswith('gs://'):
            self._transfer_to_s3()
        else:
            logger.info('Syncing Local to S3')
            self.sync_local_dir()

        self.is_initialized = True

    def delete(self) -> None:
        logger.info(f'Deleting S3 Bucket {self.name}')
        return self._delete_s3_bucket(self.name)

    def get_handle(self) -> StorageHandle:
        return aws.resource('s3').Bucket(self.name)

    def sync_local_dir(self) -> None:
        """Syncs Local folder with S3 Bucket. This method is called after
        the folder is already uploaded onto the S3 bucket.

        AWS Sync by default uses 10 threads to upload files to the bucket.
        To increase parallelism, modify max_concurrent_requests in your
        aws config file (Default path: ~/.aws/config).
        """
        sync_command = f'aws s3 sync {self.source} s3://{self.name}/ --delete'
        os.system(sync_command)

    def _transfer_to_s3(self) -> None:
        if self.source.startswith('gs://'):
            data_transfer.gcs_to_s3(self.name, self.name)

    def _get_bucket(self) -> Tuple[StorageHandle, bool]:
        """Obtains the S3 bucket.

        If the bucket exists, this method will connect to the bucket.
        If the bucket does not exist, there are two cases:
          1) Raise an error if the bucket source starts with s3://
          2) Create a new bucket otherwise
        """
        s3 = aws.resource('s3')
        bucket = s3.Bucket(self.name)
        if bucket in s3.buckets.all():
            return bucket, False
        if self.source.startswith('s3://'):
            raise ValueError('Attempted to connect to a non-existent bucket.')
        return self._create_s3_bucket(self.name), True

    def _download_file(self, remote_path: str, local_path: str) -> None:
        """Downloads file from remote to local on s3 bucket
        using the boto3 API

        Args:
          remote_path: str; Remote path on S3 bucket
          local_path: str; Local path on user's device
        """
        self.bucket.download_file(remote_path, local_path)

    def _create_s3_bucket(self,
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
        except aws.client_exception() as e:
            logger.info(e)
            return None
        return aws.resource('s3').Bucket(bucket_name)

    def _delete_s3_bucket(self, bucket_name: str) -> None:
        """Deletes S3 bucket, including all objects in bucket

        Args:
          bucket_name: str; Name of bucket
        """
        s3 = aws.resource('s3')
        bucket = s3.Bucket(bucket_name)
        bucket.objects.all().delete()
        bucket.delete()


class GcsStore(AbstractStore):
    """GcsStore inherits from Storage Object and represents the backend
    for GCS buckets.
    """

    def __init__(self, name: str, source: str, region='us-central1'):
        super().__init__(name, source)
        if self.source.startswith('s3://'):
            assert name == data_utils.split_s3_path(source)[0], (
                'S3 Bucket is specified as path, the name should be the '
                'same as S3 bucket!')
            assert data_utils.verify_s3_bucket(name), (
                f'Source specified as {source}, an S3 bucket. ',
                'S3 Bucket should exist!')

        elif self.source.startswith('gs://'):
            assert name == data_utils.split_gcs_path(source)[0], (
                'GCS Bucket is specified as path, the name should be the '
                'same as GCS bucket!')

        self.client = gcp.storage_client()
        self.region = region
        self.bucket, is_new_bucket = self._get_bucket()
        assert not is_new_bucket or self.source

        if self.source.startswith('gs://'):
            pass
        elif self.source.startswith('s3://'):
            self._transfer_to_gcs()
        else:
            logger.info('Syncing Local to GCS')
            self.sync_local_dir()

        self.is_initialized = True

    def delete(self) -> None:
        logger.info(f'Deleting GCS Bucket {self.name}')
        return self._delete_gcs_bucket(self.name)

    def get_handle(self) -> StorageHandle:
        return self.client.get_bucket(self.name)

    def sync_local_dir(self) -> None:
        """Syncs Local folder with GCS Bucket. This method is called after
        the folder is already uploaded onto the GCS bucket.
        """
        sync_command = f'gsutil -m rsync -d -r {self.source} gs://{self.name}/'
        os.system(sync_command)

    def _transfer_to_gcs(self) -> None:
        if self.source.startswith('s3://'):
            data_transfer.s3_to_gcs(self.name, self.name)

    def _get_bucket(self) -> Tuple[StorageHandle, bool]:
        """Obtains the GCS bucket.

        If the bucket exists, this method will connect to the bucket.
        If the bucket does not exist, there are two cases:
          1) Raise an error if the bucket source starts with gs://
          2) Create a new bucket otherwise
        """
        try:
            bucket = self.client.get_bucket(self.name)
            return bucket, False
        except gcp.not_found_exception() as e:
            if self.source.startswith('gs://'):
                raise ValueError(
                    'Attempted to connect to a non-existent bucket.') from e
            logger.info(e)
            return self._create_gcs_bucket(self.name), True

    def _download_file(self, remote_path: str, local_path: str) -> None:
        """Downloads file from remote to local on GS bucket

        Args:
          remote_path: str; Remote path on GS bucket
          local_path: str; Local path on user's device
        """
        blob = self.bucket.blob(remote_path)
        blob.download_to_filename(local_path, timeout=None)

    def _create_gcs_bucket(self,
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

    def _delete_gcs_bucket(self, bucket_name: str) -> None:
        """Deletes GCS bucket, including all objects in bucket

        Args:
          bucket_name: str; Name of bucket
        """
        bucket = self.client.get_bucket(bucket_name)
        bucket.delete(force=True)
