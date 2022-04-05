"""Storage and Store Classes for Sky Data."""
import enum
import os
import random
import subprocess
import textwrap
from typing import Any, Dict, Optional, Tuple, Union
import urllib.parse

from sky.adaptors import aws
from sky.adaptors import gcp
from sky.backends import backend_utils
from sky.data import data_transfer
from sky.data import data_utils
from sky import exceptions
from sky import global_user_state
from sky import sky_logging

logger = sky_logging.init_logger(__name__)

Path = str
StorageHandle = Any
StorageStatus = global_user_state.StorageStatus


class StoreType(enum.Enum):
    S3 = 'S3'
    GCS = 'GCS'
    AZURE = 'AZURE'


class StorageMode(enum.Enum):
    MOUNT = 'MOUNT'
    COPY = 'COPY'


def _get_storetype_from_store(store: 'Storage') -> StoreType:
    if isinstance(store, S3Store):
        return StoreType.S3
    elif isinstance(store, GcsStore):
        return StoreType.GCS
    else:
        raise ValueError(f'Unknown store type: {store}')


class AbstractStore:
    """AbstractStore abstracts away the different storage types exposed by
    different clouds.

    Storage objects are backed by AbstractStores, each representing a store
    present in a cloud.
    """

    class StoreMetadata:
        """A pickle-able representation of Store

        Allows store objects to be written to and reconstructed from
        global_user_state.
        """

        def __init__(self,
                     *,
                     name: str,
                     source: str,
                     region: Optional[str] = None,
                     is_sky_managed: Optional[bool] = None):
            self.name = name
            self.source = source
            self.region = region
            self.is_sky_managed = is_sky_managed

        def __repr__(self):
            return (f'StoreMetadata('
                    f'\n\tname={self.name},'
                    f'\n\tsource={self.source},'
                    f'\n\tregion={self.region},'
                    f'\n\tis_sky_managed={self.is_sky_managed})')

    def __init__(self,
                 name: str,
                 source: str,
                 region: Optional[str] = None,
                 is_sky_managed: Optional[bool] = None):
        """Initialize AbstractStore

        Args:
            name: Store name
            source: Data source for the store
            region: Region to place the bucket in
            is_sky_managed: Whether the store is managed by Sky. If None, it
              must be populated by the implementing class during initialization.

        Raises:
            StorageBucketCreateError: If bucket creation fails
            StorageBucketGetError: If fetching existing bucket fails
            StorageInitError: If general initialization fails
        """
        self.name = name
        self.source = source
        self.region = region
        self.is_sky_managed = is_sky_managed
        # Whether sky is responsible for the lifecycle of the Store.
        self._validate()
        self.initialize()

    @classmethod
    def from_metadata(cls, metadata: StoreMetadata, **override_args):
        """Create a Store from a StoreMetadata object.

        Used when reconstructing Storage and Store objects from
        global_user_state.
        """
        return cls(name=override_args.get('name', metadata.name),
                   source=override_args.get('source', metadata.source),
                   region=override_args.get('region', metadata.region),
                   is_sky_managed=override_args.get('is_sky_managed',
                                                    metadata.is_sky_managed))

    def get_metadata(self) -> StoreMetadata:
        return self.StoreMetadata(name=self.name,
                                  source=self.source,
                                  region=self.region,
                                  is_sky_managed=self.is_sky_managed)

    def initialize(self):
        """Initializes the Store object on the cloud.

        Initialization involves fetching bucket if exists, or creating it if
        it does not.

        Raises:
          StorageBucketCreateError: If bucket creation fails
          StorageBucketGetError: If fetching existing bucket fails
          StorageInitError: If general initialization fails.
        """
        pass

    def _validate(self) -> None:
        """Runs validation checks on class args"""
        pass

    def upload(self) -> None:
        """Uploads source to the store bucket

        Upload must be called by the Storage handler - it is not called on
        Store initialization.
        """
        raise NotImplementedError

    def delete(self) -> None:
        """Removes the Storage object from the cloud."""
        raise NotImplementedError

    def get_handle(self) -> StorageHandle:
        """Returns the storage handle for use by the execution backend to attach
        to VM/containers
        """
        raise NotImplementedError

    def sync_local_dir(self) -> None:
        """Syncs a local directory to a Store bucket."""
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

    def mount_command(self, mount_path: str) -> str:
        """Returns the command to mount the Store to the specified mount_path.

        Includes the setup commands to install mounting tools.

        Args:
          mount_path: str; Mount path on remote server
        """
        raise NotImplementedError

    def __deepcopy__(self, memo):
        # S3 Client and GCS Client cannot be deep copied, hence the
        # original Store object is returned
        return self


class Storage(object):
    """Storage objects handle persistent and large volume storage in the sky.

    Storage represents an abstract data store containing large data files
    required by the task. Compared to file_mounts, storage is faster and
    can persist across runs, requiring fewer uploads from your local machine.

    A storage object can be used in either MOUNT mode or COPY mode. In MOUNT
    mode (the default), the backing store is directly "mounted" to the remote
    VM, and files are fetched when accessed by the task and files written to the
    mount path are also written to the remote store. In COPY mode, the files are
    pre-fetched and cached on the local disk and writes are not replicated on
    the remote store.

    Behind the scenes, storage automatically uploads all data in the source
    to a backing object store in a particular cloud (S3/GCS/Azure Blob).

      Typical Usage: (See examples/playground/storage_playground.py)
        storage = Storage(name='imagenet-bucket', source='~/Documents/imagenet')

        # Move data to S3
        storage.add_store('S3')

        # Move data to Google Cloud Storage
        storage.add_store('GCS')

        # Delete Storage for both S3 and GCS
        storage.delete()
    """

    class StorageMetadata(object):
        """A pickle-able tuple of:

        - (required) Storage name.
        - (required) Source
        - (optional) Set of stores managed by sky added to the Storage object
        """

        def __init__(
            self,
            *,
            storage_name: str,
            source: str,
            sky_stores: Optional[Dict[StoreType,
                                      AbstractStore.StoreMetadata]] = None):
            assert storage_name is not None or source is not None
            self.__version__ = 1
            self.storage_name = storage_name
            self.source = source
            # Only stores managed by sky are stored here in the
            # global_user_state
            self.sky_stores = {} if sky_stores is None else sky_stores

        def __repr__(self):
            return (f'StorageMetadata('
                    f'\n\tstorage_name={self.storage_name},'
                    f'\n\tsource={self.source},'
                    f'\n\tstores={self.sky_stores})')

        def add_store(self, store: AbstractStore) -> None:
            storetype = _get_storetype_from_store(store)
            self.sky_stores[storetype] = store.get_metadata()

        def remove_store(self, store: AbstractStore) -> None:
            storetype = _get_storetype_from_store(store)
            if storetype in self.sky_stores:
                del self.sky_stores[storetype]

        def __setstate__(self, state):
            """Used by pickle.loads for backwards compatibility"""
            version = state.get('__version__', None)
            del version
            self.__dict__.update(state)

    def __init__(self,
                 name: Optional[str] = None,
                 source: Optional[Path] = None,
                 stores: Optional[Dict[StoreType, AbstractStore]] = None,
                 persistent: Optional[bool] = True,
                 mode: Optional[StorageMode] = StorageMode.MOUNT,
                 sync_on_reconstruction: Optional[bool] = True):
        """Initializes a Storage object.

        Three fields are required: the name of the storage, the source
        path where the data is initially located, and the default mount
        path where the data will be mounted to on the cloud.

        Storage object validation depends on the name, source and mount mode.
        There are four combinations possible for name and source inputs:

        - name is None, source is None: Underspecified storage object.
        - name is not None, source is None: If MOUNT mode, provision an empty
            bucket with name <name>. If COPY mode, raise error since source is
            required.
        - name is None, source is not None: If source is local, raise error
            since name is required to create destination bucket. If source is
            a bucket URL, use the source bucket as the backing store (if
            permissions allow, else raise error).
        - name is not None, source is not None: If source is local, upload the
            contents of the source path to <name> bucket. Create new bucket if
            required. If source is bucket url - raise error. Name should not be
            specified if the source is a URL; name will be inferred from source.

        Args:
          name: str; Name of the storage object. Typically used as the
            bucket name in backing object stores.
          source: str; File path where the data is initially stored. Can be a
            local path or a cloud URI (s3://, gs://, etc.). Local paths do not
            need to be absolute.
          stores: Optional; Specify pre-initialized stores (S3Store, GcsStore).
          persistent: bool; Whether to persist across sky launches.
          mode: StorageMode; Specify how the storage object is manifested on
            the remote VM. Can be either MOUNT or COPY. Defaults to MOUNT.
          sync_on_reconstruction: bool; Whether to sync the data if the storage
            object is found in the global_user_state and reconstructed from
            there. This is set to false when the Storage object is created not
            for direct use, e.g. for sky storage delete.
        """
        self.name = name
        self.source = source
        self.persistent = persistent
        self.mode = mode
        self.sync_on_reconstruction = sync_on_reconstruction

        # Validate and correct inputs if necessary
        self._validate_storage_spec()

        # Sky optimizer either adds a storage object instance or selects
        # from existing ones
        self.stores = {} if stores is None else stores

        # Logic to rebuild Storage if it is in global user state
        self.handle = global_user_state.get_handle_from_storage_name(self.name)
        if self.handle:
            # Reconstruct the Storage object from the global_user_state
            logger.info('Detected existing storage object, '
                        f'loading Storage: {self.name}')
            for s_type, s_metadata in self.handle.sky_stores.items():
                # When initializing from global_user_state, we override the
                # source from the YAML
                if s_type == StoreType.S3:
                    store = S3Store.from_metadata(s_metadata,
                                                  source=self.source)
                elif s_type == StoreType.GCS:
                    store = GcsStore.from_metadata(s_metadata,
                                                   source=self.source)
                else:
                    raise ValueError(f'Unknown store type: {s_type}')

                self._add_store(store, is_reconstructed=True)

            # TODO(romilb): This logic should likely be in add_store to move
            # syncing to file_mount stage..
            if self.sync_on_reconstruction:
                msg = ''
                if self.source and \
                        not data_utils.is_cloud_store_url(self.source):
                    msg = ' and uploading from source'
                logger.info(f'Verifying bucket{msg} for storage {self.name}')
                self.sync_all_stores()

        else:
            # Storage does not exist in global_user_state, create new stores
            sky_managed_stores = {
                t: s.get_metadata()
                for t, s in self.stores.items()
                if s.is_sky_managed()
            }
            self.handle = self.StorageMetadata(storage_name=self.name,
                                               source=self.source,
                                               sky_stores=sky_managed_stores)

            if self.source is not None:
                # If source is a pre-existing bucket, connect to the bucket
                # If the bucket does not exist, this will error out
                if self.source.startswith('s3://'):
                    self.add_store(StoreType.S3)
                elif self.source.startswith('gs://'):
                    self.add_store(StoreType.GCS)

    @staticmethod
    def _validate_source(source: str, mode: StorageMode) -> [str, bool]:
        """Validates the source path.

        Args:
          source: str; File path where the data is initially stored. Can be a
            local path or a cloud URI (s3://, gs://, etc.). Local paths do not
            need to be absolute.
          mode: StorageMode; StorageMode of the storage object

        Returns:
          Tuple[source, is_local_source]
          source: str; The source path.
          is_local_path: bool; Whether the source is a local path. False if URI.
        """
        # Check if source is a valid local/remote URL
        split_path = urllib.parse.urlsplit(source)
        if split_path.scheme == '':
            if source.endswith('/'):
                raise exceptions.StorageSourceError(
                    'Storage source paths cannot end with a slash '
                    '(try "/mydir: /mydir" or "/myfile: /myfile"). '
                    f'Found source={source}')
            # Local path, check if it exists
            source = os.path.abspath(os.path.expanduser(source))
            if not os.path.exists(source):
                raise exceptions.StorageSourceError('Local source path does not'
                                                    f' exist: {source}')
            # Raise warning if user's path is a symlink
            elif os.path.islink(source):
                logger.warning(f'Source path {source} is a symlink. '
                               'Referenced contents are uploaded, matching '
                               'the default behavior for S3 and GCS syncing.')
            is_local_source = True
        elif split_path.scheme in ['s3', 'gs']:
            is_local_source = False
            # Storage mounting does not support mounting specific files from
            # cloud store - ensure path points to only a directory
            if mode == StorageMode.MOUNT:
                if split_path.path.strip('/') != '':
                    raise exceptions.StorageModeError(
                        'MOUNT mode does not support'
                        ' mounting specific files from cloud'
                        ' storage. Please use COPY mode or'
                        ' specify only the bucket name as'
                        ' the source.')
        else:
            raise exceptions.StorageSourceError(
                f'Supported paths: local, s3://, gs://. Got: {source}')
        return source, is_local_source

    def _validate_storage_spec(self) -> None:
        """
        Validates the storage spec and updates local fields if necessary.
        """
        if self.source is None:
            # If the mode is COPY, the source must be specified
            if self.mode == StorageMode.COPY:
                # TODO(romilb): What about when a Storage object without source
                #  already exists in global_user_state (e.g. used for scratch)
                #  and now the user wants to mount it in COPY mode? We should
                #  perhaps check for existence in global_user_state here.
                raise exceptions.StorageSourceError(
                    'Storage source must be specified when using COPY mode.')
            else:
                # If source is not specified in mount mode, the intent is to
                # create a bucket and use it as scratch disk. Name must be
                # specified to create bucket.
                if not self.name:
                    raise exceptions.StorageSpecError(
                        'Storage source or storage name must be specified.')
                else:
                    # Create bucket and mount
                    return
        elif self.source is not None:
            source, is_local_source = Storage._validate_source(
                self.source, self.mode)
            if is_local_source:
                # Expand user in source path
                self.source = os.path.abspath(os.path.expanduser(self.source))
            if not self.name:
                if is_local_source:
                    raise exceptions.StorageNameError(
                        'Storage name must be specified if the source is local.'
                    )
                else:
                    # Set name to source bucket name and continue
                    self.name = urllib.parse.urlsplit(source).netloc
                    return
            else:
                if is_local_source:
                    # If name is specified and source is local, upload to bucket
                    return
                else:
                    # Both name and source should not be specified if the source
                    # is a URI. Name will be inferred from the URI.
                    raise exceptions.StorageSpecError(
                        'Storage name should not be specified if the source is '
                        'a remote URI.')
        raise exceptions.StorageSpecError(
            f'Validation failed for storage source {self.source}, name '
            f'{self.name} and mode {self.mode}. Please check the arguments.')

    def add_store(self, store_type: Union[str, StoreType]) -> AbstractStore:
        """Initializes and adds a new store to the storage.

        Invoked by the optimizer after it has selected a store to
        add it to Storage.

        Args:
          store_type: StoreType; Type of the storage [S3, GCS, AZURE]
        """
        if isinstance(store_type, str):
            store_type = StoreType(store_type)

        if store_type in self.stores:
            logger.info(f'Storage type {store_type} already exists.')
            return self.stores[store_type]

        if store_type == StoreType.S3:
            store_cls = S3Store
        elif store_type == StoreType.GCS:
            store_cls = GcsStore
        else:
            raise exceptions.StorageSpecError(
                f'{store_type} not supported as a Store.')

        # Initialize store object and get/create bucket
        try:
            store = store_cls(name=self.name, source=self.source)
        except exceptions.StorageBucketCreateError:
            # Creation failed, so this must be sky managed store. Add failure
            # to state.
            logger.error(f'Sky could not create {store_type} store '
                         f'with name {self.name}.')
            global_user_state.set_storage_status(self.name,
                                                 StorageStatus.INIT_FAILED)
            raise
        except exceptions.StorageBucketGetError:
            # Bucket get failed, so this is not sky managed. Do not update state
            logger.error(f'Sky could not get {store_type} store '
                         f'with name {self.name}.')
            raise
        except exceptions.StorageInitError:
            logger.error(f'Sky could not initialize {store_type} store with '
                         f'name {self.name}. General initialization error.')
            raise

        # Add store to storage
        self._add_store(store)

        # Upload source to store
        self._sync_store(store)

        return store

    def _add_store(self, store: AbstractStore, is_reconstructed: bool = False):
        # Adds a store object to the storage
        store_type = _get_storetype_from_store(store)
        self.stores[store_type] = store
        # If store initialized and is sky managed, add to state
        if store.is_sky_managed:
            self.handle.add_store(store)
            if not is_reconstructed:
                global_user_state.add_or_update_storage(self.name, self.handle,
                                                        StorageStatus.INIT)

    def delete(self, store_type: Optional[StoreType] = None) -> None:
        """Deletes data for all sky-managed storage objects.

        If a storage is not managed by sky, it is not deleted from the cloud.
        User must manually delete any object stores created outside of sky.

        Args:
            store_type: StoreType; Specific cloud store to remove from the list
              of backing stores.
        """
        if not self.stores:
            logger.info('No backing stores found. Deleting storage.')
            global_user_state.remove_storage(self.name)
        if store_type:
            store = self.stores[store_type]
            is_sky_managed = store.is_sky_managed
            # We delete a store from the cloud if it's sky managed. Else just
            # remove handle and return
            if is_sky_managed:
                self.handle.remove_store(store)
                store.delete()
                # Check remaining stores - if none is sky managed, remove
                # the storage from global_user_state.
                delete = all(s.is_sky_managed is False for s in self.stores)
                if delete:
                    global_user_state.remove_storage(self.name)
                else:
                    global_user_state.set_storage_handle(self.name, self.handle)
            # Remove store from bookkeeping
            del self.stores[store_type]
        else:
            for _, store in self.stores.items():
                if store.is_sky_managed:
                    self.handle.remove_store(store)
                    store.delete()
            self.stores = {}
            # Remove storage from global_user_state if present
            global_user_state.remove_storage(self.name)

    def sync_all_stores(self):
        """Syncs the source and destinations of all stores in the Storage"""
        for _, store in self.stores.items():
            self._sync_store(store)

    def _sync_store(self, store: AbstractStore):
        """Runs the upload routine for the store and handles failures"""
        try:
            store.upload()
        except exceptions.StorageUploadError:
            logger.error(f'Sky could not upload {self.source} to store '
                         f'name {store.name}.')
            if store.is_sky_managed:
                global_user_state.set_storage_status(
                    self.name, StorageStatus.UPLOAD_FAILED)
            raise

        # Upload succeeded - update state
        if store.is_sky_managed:
            global_user_state.set_storage_status(self.name, StorageStatus.READY)


class S3Store(AbstractStore):
    """S3Store inherits from Storage Object and represents the backend
    for S3 buckets.
    """

    _STAT_CACHE_TTL = '5s'
    _TYPE_CACHE_TTL = '5s'

    def __init__(self,
                 name: str,
                 source: str,
                 region: Optional[str] = 'us-east-2',
                 is_sky_managed: Optional[bool] = None):
        self.client = None
        self.bucket = None
        super().__init__(name, source, region, is_sky_managed)

    def _validate(self):
        if self.source is not None:
            if self.source.startswith('s3://'):
                assert self.name == data_utils.split_s3_path(self.source)[0], (
                    'S3 Bucket is specified as path, the name should be the '
                    'same as S3 bucket.')
            elif self.source.startswith('gs://'):
                assert self.name == data_utils.split_gcs_path(self.source)[0], (
                    'GCS Bucket is specified as path, the name should be the '
                    'same as GCS bucket.')
                assert data_utils.verify_gcs_bucket(self.name), (
                    f'Source specified as {self.source}, a GCS bucket. ',
                    'GCS Bucket should exist.')

    def initialize(self):
        """Initializes the S3 store object on the cloud.

        Initialization involves fetching bucket if exists, or creating it if
        it does not.

        Raises:
          StorageBucketCreateError: If bucket creation fails
          StorageBucketGetError: If fetching existing bucket fails
          StorageInitError: If general initialization fails.
        """
        self.client = data_utils.create_s3_client(self.region)
        self.bucket, is_new_bucket = self._get_bucket()
        if self.is_sky_managed is None:
            # If is_sky_managed is not specified, then this is a new storage
            # object (i.e., did not exist in global_user_state) and we should
            # set the is_sky_managed property.
            # If is_sky_managed is specified, then we take no action.
            self.is_sky_managed = is_new_bucket

    def upload(self):
        """Uploads source to store bucket.

        Upload must be called by the Storage handler - it is not called on
        Store initialization.

        Raises:
            StorageUploadError: if upload fails.
        """
        try:
            if self.source is not None:
                if self.source.startswith('s3://'):
                    pass
                elif self.source.startswith('gs://'):
                    self._transfer_to_s3()
                else:
                    self.sync_local_dir()
        except exceptions.StorageUploadError:
            raise
        except Exception as e:
            raise exceptions.StorageUploadError(
                f'Upload failed for store {self.name}') from e

    def delete(self) -> None:
        logger.info(f'Deleting S3 Bucket {self.name}')
        return self._delete_s3_bucket(self.name)

    def get_handle(self) -> StorageHandle:
        return aws.resource('s3').Bucket(self.name)

    def sync_local_dir(self) -> None:
        """Syncs a local directory to a S3 bucket.

        AWS Sync by default uses 10 threads to upload files to the bucket.  To
        increase parallelism, modify max_concurrent_requests in your aws config
        file (Default path: ~/.aws/config).
        """
        sync_command = f'aws s3 sync {self.source} s3://{self.name}/'
        with backend_utils.safe_console_status(
                f'[bold cyan]Syncing '
                f'[green]{self.source} to s3://{self.name}/'):
            with subprocess.Popen(sync_command.split(' '),
                                  stderr=subprocess.PIPE) as process:
                while True:
                    line = process.stderr.readline()
                    if not line:
                        break
                    str_line = line.decode('utf-8')
                    logger.info(str_line)
                    if 'Access Denied' in str_line:
                        process.kill()
                        logger.error('Sky Storage failed to upload files to '
                                     'the S3 bucket. The bucket does not have '
                                     'write permissions. It is possible that '
                                     'the bucket is public.')
                        e = PermissionError('Can\'t write to bucket!')
                        logger.error(e)
                        raise e
                retcode = process.wait()
                if retcode != 0:
                    raise exceptions.StorageUploadError(
                        f'Upload to S3 failed for store {self.name} and source '
                        f'{self.source}. Please check the logs.')

    def _transfer_to_s3(self) -> None:
        if self.source.startswith('gs://'):
            data_transfer.gcs_to_s3(self.name, self.name)

    def _get_bucket(self) -> Tuple[StorageHandle, bool]:
        """Obtains the S3 bucket.

        If the bucket exists, this method will connect to the bucket.
        If the bucket does not exist, there are two cases:
          1) Raise an error if the bucket source starts with s3://
          2) Create a new bucket otherwise

        Raises:
            StorageBucketCreateError: If creating the bucket fails
            StorageBucketGetError: If fetching a bucket fails
        """
        s3 = aws.resource('s3')
        bucket = s3.Bucket(self.name)
        # Checks if bucket exists (both public and private buckets)
        try:
            s3.meta.client.head_bucket(Bucket=self.name)
            return bucket, False
        except aws.client_exception() as e:
            # If it was a 404 error, then the bucket does not exist.
            error_code = e.response['Error']['Code']
            if error_code == '404':
                if self.source is not None:
                    if self.source.startswith('s3://'):
                        raise exceptions.StorageBucketGetError(
                            'Attempted to connect to a non-existent bucket: '
                            f'{self.source}. Consider using `aws s3 ls '
                            f'{self.source}` to debug.') from e
                    else:
                        bucket = self._create_s3_bucket(self.name)
                        return bucket, True
                # TODO(romilb): Fix this logic repetition here
                else:
                    bucket = self._create_s3_bucket(self.name)
                    return bucket, True
            else:
                ex = exceptions.StorageBucketGetError(
                    'Failed to connect to an existing bucket. \n'
                    'Check if the 1) the bucket name is taken and/or '
                    '2) the bucket permissions are not setup correctly. '
                    f'Consider using `aws s3 ls {self.name}` to debug.')
                logger.error(ex)
                raise ex from e

    def _download_file(self, remote_path: str, local_path: str) -> None:
        """Downloads file from remote to local on s3 bucket
        using the boto3 API

        Args:
          remote_path: str; Remote path on S3 bucket
          local_path: str; Local path on user's device
        """
        self.bucket.download_file(remote_path, local_path)

    def mount_command(self, mount_path: str) -> str:
        """Returns the command to mount the bucket to the mount_path.

        Uses goofys to mount the bucket.

        Args:
          mount_path: str; Path to mount the bucket to.
        """
        # TODO(romilb): Move the inline script to a separate file
        script = textwrap.dedent(f"""
            #!/usr/bin/env bash
            set -e

            S3_SOURCE={self.bucket.name}
            MOUNT_PATH={mount_path}
            STAT_CACHE_TTL={self._STAT_CACHE_TTL}
            TYPE_CACHE_TTL={self._TYPE_CACHE_TTL}

            # Check if path is already mounted
            if ! [ "$(grep -q $MOUNT_PATH /proc/mounts)" ] ; then
                echo "Path already mounted - unmounting..."
                fusermount -u "$MOUNT_PATH"
                echo "Successfully unmounted $MOUNT_PATH."
            fi

            # Install goofys if not already installed
            if ! [ -x "$(command -v goofys)" ]; then
              echo "Installing goofys..."
              sudo wget -nc https://github.com/kahing/goofys/releases/latest/download/goofys -O /usr/local/bin/goofys
              sudo chmod +x /usr/local/bin/goofys
            else
              echo "Goofys already installed. Proceeding..."
            fi

            # Check if mount path exists
            if [ ! -d "$MOUNT_PATH" ]; then
              echo "Mount path $MOUNT_PATH does not exist. Creating..."
              sudo mkdir -p $MOUNT_PATH
              sudo chmod 777 $MOUNT_PATH
            else
              # Check if mount path contains files
              if [ "$(ls -A $MOUNT_PATH)" ]; then
                echo "Mount path $MOUNT_PATH is not empty. Please make sure its empty."
                exit 1
              fi
            fi
            echo "Mounting $S3_SOURCE to $MOUNT_PATH with goofys..."
            goofys -o allow_other --stat-cache-ttl $STAT_CACHE_TTL --type-cache-ttl $TYPE_CACHE_TTL $S3_SOURCE $MOUNT_PATH
            echo "Mounting done."
        """)

        # TODO(romilb): Get direct bash script to work like so:
        # command = f'bash <<-\EOL' \
        #           f'{script}' \
        #           'EOL'

        # TODO(romilb): This heredoc should have EOF after script, but it
        #  fails with sky's ssh pipeline. Instead, we don't use EOF and use )
        #  as the end of heredoc. This raises a warning (here-document delimited
        #  by end-of-file) that can be safely ignored.

        # While these commands are run sequentially for each storage object,
        # we add random int to be on the safer side and avoid collisions.
        script_path = f'~/.sky/mount_{random.randint(0,1000000)}.sh'
        first_line = r'(cat <<-\EOF > {}'.format(script_path)
        command = (f'{first_line}'
                   f'{script}'
                   f') && chmod +x {script_path}'
                   f' && bash {script_path}'
                   f' && rm {script_path}')
        return command

    def _create_s3_bucket(self,
                          bucket_name: str,
                          region='us-east-2') -> StorageHandle:
        """Creates S3 bucket with specific name in specific region

        Args:
          bucket_name: str; Name of bucket
          region: str; Region name, e.g. us-west-1, us-east-2
        Raises:
          StorageBucketCreateError: If bucket creation fails.
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
            logger.error(e)
            raise exceptions.StorageBucketCreateError(
                f'Attempted to create a bucket '
                f'{self.name} but failed.') from e
        return aws.resource('s3').Bucket(bucket_name)

    def _delete_s3_bucket(self, bucket_name: str) -> None:
        """Deletes S3 bucket, including all objects in bucket

        Args:
          bucket_name: str; Name of bucket
        """
        try:
            s3 = aws.resource('s3')
            bucket = s3.Bucket(bucket_name)
            bucket.objects.all().delete()
            bucket.delete()
        except aws.client_exception() as e:
            logger.error(f'Unable to delete S3 bucket {self.name}')
            logger.error(e)
            raise e


class GcsStore(AbstractStore):
    """GcsStore inherits from Storage Object and represents the backend
    for GCS buckets.
    """

    def __init__(self,
                 name: str,
                 source: str,
                 region: Optional[str] = 'us-central1',
                 is_sky_managed: Optional[bool] = None):
        self.client = None
        self.bucket = None
        super().__init__(name, source, region, is_sky_managed)

    def _validate(self):
        if self.source is not None:
            if self.source.startswith('s3://'):
                assert self.name == data_utils.split_s3_path(self.source)[0], (
                    'S3 Bucket is specified as path, the name should be the '
                    'same as S3 bucket.')
                assert data_utils.verify_s3_bucket(self.name), (
                    f'Source specified as {self.source}, an S3 bucket. ',
                    'S3 Bucket should exist.')
            elif self.source.startswith('gs://'):
                assert self.name == data_utils.split_gcs_path(self.source)[0], (
                    'GCS Bucket is specified as path, the name should be the '
                    'same as GCS bucket.')

    def initialize(self):
        """Initializes the GCS store object on the cloud.

        Initialization involves fetching bucket if exists, or creating it if
        it does not.

        Raises:
          StorageBucketCreateError: If bucket creation fails
          StorageBucketGetError: If fetching existing bucket fails
          StorageInitError: If general initialization fails.
        """
        self.client = gcp.storage_client()
        self.bucket, is_new_bucket = self._get_bucket()
        if self.is_sky_managed is None:
            # If is_sky_managed is not specified, then this is a new storage
            # object (i.e., did not exist in global_user_state) and we should
            # set the is_sky_managed property.
            # If is_sky_managed is specified, then we take no action.
            self.is_sky_managed = is_new_bucket

    def upload(self):
        """Uploads source to store bucket.

        Upload must be called by the Storage handler - it is not called on
        Store initialization.

        Raises:
            StorageUploadError: if upload fails.
        """
        try:
            if self.source.startswith('gs://'):
                pass
            elif self.source.startswith('s3://'):
                self._transfer_to_gcs()
            else:
                logger.info('Syncing Local to GCS')
                self.sync_local_dir()
        except Exception as e:
            raise exceptions.StorageUploadError(
                f'Upload failed for store {self.name}') from e

    def delete(self) -> None:
        logger.info(f'Deleting GCS Bucket {self.name}')
        return self._delete_gcs_bucket(self.name)

    def get_handle(self) -> StorageHandle:
        return self.client.get_bucket(self.name)

    def sync_local_dir(self) -> None:
        """Syncs a local directory to a GCS bucket."""
        sync_command = f'gsutil -m rsync -d -r {self.source} gs://{self.name}/'
        logger.info(f'Executing: {sync_command}')
        with subprocess.Popen(sync_command.split(' '),
                              stderr=subprocess.PIPE) as process:
            while True:
                line = process.stderr.readline()
                if not line:
                    break
                str_line = line.decode('utf-8')
                logger.info(str_line)
                if 'AccessDeniedException' in str_line:
                    process.kill()
                    logger.error('Sky Storage failed to upload files to '
                                 'GCS. The bucket does not have '
                                 'write permissions. It is possible that '
                                 'the bucket is public.')
                    e = PermissionError('Can\'t write to bucket!')
                    logger.error(e)
                    raise e
            retcode = process.wait()
            if retcode != 0:
                raise exceptions.StorageUploadError(
                    f'Upload to S3 failed for store {self.name} and source '
                    f'{self.source}. Please check logs.')
            logger.info('Done Syncing Local to GCS')

    def _transfer_to_gcs(self) -> None:
        if self.source.startswith('s3://'):
            data_transfer.s3_to_gcs(self.name, self.name)

    def _get_bucket(self) -> Tuple[StorageHandle, bool]:
        """Obtains the GCS bucket.
        If the bucket exists, this method will connect to the bucket.

        If the bucket does not exist, there are two cases:
          1) Raise an error if the bucket source starts with gs://
          2) Create a new bucket otherwise

        Raises:
            StorageBucketCreateError: If creating the bucket fails
            StorageBucketGetError: If fetching a bucket fails
        """
        try:
            bucket = self.client.get_bucket(self.name)
            return bucket, False
        except gcp.not_found_exception() as e:
            if self.source.startswith('gs://'):
                raise exceptions.StorageBucketGetError(
                    'Attempted to connect to a non-existent bucket: '
                    f'{self.source}') from e
            else:
                bucket = self._create_gcs_bucket(self.name)
                return bucket, True
        except gcp.forbidden_exception():
            # Try public bucket to see if bucket exists
            logger.info(
                'External Bucket detected; Connecting to external bucket...')
            try:
                a_client = gcp.anonymous_storage_client()
                bucket = a_client.bucket(self.name)
                # Check if bucket can be listed/read from
                next(bucket.list_blobs())
                return bucket, False
            except gcp.not_found_exception() as e:
                ex = exceptions.StorageBucketGetError(
                    f'Failed to connect to external bucket {self.name} \n'
                    'Check if the 1) the bucket name is taken and/or '
                    '2) the bucket permissions are not setup correctly. '
                    f'Consider using `gsutil ls gs://{self.name}` to debug.')
                logger.error(ex)
                raise ex from e
            except ValueError as e:
                ex = exceptions.StorageBucketGetError(
                    f'Attempted to access a private external bucket {self.name}'
                    '\nCheck if the 1) the bucket name is taken and/or '
                    '2) the bucket permissions are not setup correctly. '
                    f'Consider using `gsutil ls gs://{self.name}` to debug.')
                logger.error(ex)
                raise ex from e

    def mount_command(self, mount_path: str) -> str:
        """Returns the command to mount the bucket to the mount_path.

        Uses gcsfuse to mount the bucket.

        Args:
          mount_path: str; Path to mount the bucket to.
        """
        # TODO(romilb, michaelzhiluo): Experiment with s3api for GCS buckets
        raise NotImplementedError

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
        try:
            bucket = self.client.bucket(bucket_name)
            bucket.storage_class = 'STANDARD'
            new_bucket = self.client.create_bucket(bucket, location=region)
        except Exception as e:
            logger.error(e)
            raise exceptions.StorageBucketCreateError(
                f'Attempted to create a bucket {self.name} but failed.') from e
        logger.info(
            f'Created GCS bucket {new_bucket.name} in {new_bucket.location} '
            f'with storage class {new_bucket.storage_class}')
        return new_bucket

    def _delete_gcs_bucket(self, bucket_name: str) -> None:
        """Deletes GCS bucket, including all objects in bucket

        Args:
          bucket_name: str; Name of bucket
        """
        try:
            bucket = self.client.get_bucket(bucket_name)
            bucket.delete(force=True)
        except gcp.forbidden_exception() as e:
            # Try public bucket to see if bucket exists
            logger.error('External Bucket detected; User not allowed to delete '
                         'external bucket!')
            logger.error(e)
            raise e
