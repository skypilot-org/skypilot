"""Storage and Store Classes for Sky Data."""
import enum
import os
import subprocess
import time
from typing import Any, Dict, Optional, Tuple, Union, List
import urllib.parse

import colorama

from sky import clouds
from sky.adaptors import aws
from sky.adaptors import gcp
from sky.backends import backend_utils
from sky.utils import schemas
from sky.data import data_transfer
from sky.data import data_utils
from sky.data import mounting_utils
from sky import exceptions
from sky import global_user_state
from sky import sky_logging
from sky.utils import ux_utils

logger = sky_logging.init_logger(__name__)

Path = str
StorageHandle = Any
StorageStatus = global_user_state.StorageStatus

# Clouds with object storage implemented in this module. Azure Blob
# Storage isn't supported yet (even though Azure is).
STORE_ENABLED_CLOUDS = [clouds.AWS(), clouds.GCP()]

# Max number of objects a GCS bucket can be directly deleted with
_GCS_RM_MAX_OBJS = 256

# Maximum number of concurrent rsync upload processes
_MAX_CONCURRENT_UPLOADS = 32

_BUCKET_FAIL_TO_CONNECT_MESSAGE = (
    'Failed to connect to an existing bucket {name!r}.\n'
    'Please check if:\n  1. the bucket name is taken and/or '
    '\n  2. the bucket permissions are not setup correctly.')


class StoreType(enum.Enum):
    """Enum for the different types of stores."""
    S3 = 'S3'
    GCS = 'GCS'
    AZURE = 'AZURE'

    @classmethod
    def from_cloud(cls, cloud: clouds.Cloud) -> 'StoreType':
        if isinstance(cloud, clouds.AWS):
            return StoreType.S3
        elif isinstance(cloud, clouds.GCP):
            return StoreType.GCS
        elif isinstance(cloud, clouds.Azure):
            return StoreType.AZURE

        raise ValueError(f'Unsupported cloud for StoreType: {cloud}')

    @classmethod
    def from_store(cls, store: 'AbstractStore') -> 'StoreType':
        if isinstance(store, S3Store):
            return StoreType.S3
        elif isinstance(store, GcsStore):
            return StoreType.GCS
        else:
            with ux_utils.print_exception_no_traceback():
                raise ValueError(f'Unknown store type: {store}')


class StorageMode(enum.Enum):
    MOUNT = 'MOUNT'
    COPY = 'COPY'


def get_storetype_from_cloud(cloud: clouds.Cloud) -> StoreType:
    if isinstance(cloud, clouds.AWS):
        return StoreType.S3
    elif isinstance(cloud, clouds.GCP):
        return StoreType.GCS
    elif isinstance(cloud, clouds.Azure):
        with ux_utils.print_exception_no_traceback():
            raise ValueError('Azure Blob Storage is not supported yet.')
    else:
        with ux_utils.print_exception_no_traceback():
            raise ValueError(f'Unknown cloud type: {cloud}')


def get_store_prefix(storetype: StoreType) -> str:
    if storetype == StoreType.S3:
        return 's3://'
    elif storetype == StoreType.GCS:
        return 'gs://'
    elif storetype == StoreType.AZURE:
        with ux_utils.print_exception_no_traceback():
            raise ValueError('Azure Blob Storage is not supported yet.')
    else:
        with ux_utils.print_exception_no_traceback():
            raise ValueError(f'Unknown store type: {storetype}')


class AbstractStore:
    """AbstractStore abstracts away the different storage types exposed by
    different clouds.

    Storage objects are backed by AbstractStores, each representing a store
    present in a cloud.
    """

    _STAT_CACHE_TTL = '5s'
    _STAT_CACHE_CAPACITY = 4096
    _TYPE_CACHE_TTL = '5s'
    _RENAME_DIR_LIMIT = 10000

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
            storage_name: Optional[str],
            source: Optional[str],
            sky_stores: Optional[Dict[StoreType,
                                      AbstractStore.StoreMetadata]] = None):
            assert storage_name is not None or source is not None
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
            storetype = StoreType.from_store(store)
            self.sky_stores[storetype] = store.get_metadata()

        def remove_store(self, store: AbstractStore) -> None:
            storetype = StoreType.from_store(store)
            if storetype in self.sky_stores:
                del self.sky_stores[storetype]

    def __init__(self,
                 name: Optional[str] = None,
                 source: Union[Path, List[Path], None] = None,
                 stores: Optional[Dict[StoreType, AbstractStore]] = None,
                 persistent: Optional[bool] = True,
                 mode: StorageMode = StorageMode.MOUNT,
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
          source: str, List[str]; File path where the data is initially stored.
            Can be a single local path, a list of local paths, or a cloud URI
            (s3://, gs://, etc.). Local paths do not need to be absolute.
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
        assert mode in StorageMode
        self.sync_on_reconstruction = sync_on_reconstruction

        # TODO(romilb, zhwu): This is a workaround to support storage deletion
        # for spot. Once sky storage supports forced management for external
        # buckets, this can be deprecated.
        self.force_delete = False

        # Validate and correct inputs if necessary
        self._validate_storage_spec()

        # Sky optimizer either adds a storage object instance or selects
        # from existing ones
        self.stores = {} if stores is None else stores

        # Logic to rebuild Storage if it is in global user state
        self.handle = global_user_state.get_handle_from_storage_name(self.name)
        if self.handle:
            # Reconstruct the Storage object from the global_user_state
            logger.debug('Detected existing storage object, '
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
                    with ux_utils.print_exception_no_traceback():
                        raise ValueError(f'Unknown store type: {s_type}')

                self._add_store(store, is_reconstructed=True)

            # TODO(romilb): This logic should likely be in add_store to move
            # syncing to file_mount stage..
            if self.sync_on_reconstruction:
                msg = ''
                if (self.source and
                    (isinstance(self.source, list) or
                     not data_utils.is_cloud_store_url(self.source))):
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
                if isinstance(self.source, str):
                    if self.source.startswith('s3://'):
                        self.add_store(StoreType.S3)
                    elif self.source.startswith('gs://'):
                        self.add_store(StoreType.GCS)

    @staticmethod
    def _validate_source(source: str, mode: StorageMode,
                         sync_on_reconstruction: bool) -> Tuple[str, bool]:
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

        def _check_basename_conflicts(source_list: List[str]) -> None:
            """Checks if two paths in source_list have the same basename."""
            basenames = [os.path.basename(s) for s in source_list]
            conflicts = {x for x in basenames if basenames.count(x) > 1}
            if conflicts:
                with ux_utils.print_exception_no_traceback():
                    raise exceptions.StorageSourceError(
                        'Cannot have multiple files or directories with the '
                        'same name in source. Conflicts found for: '
                        f'{", ".join(conflicts)}')

        def _validate_local_source(local_source):
            if local_source.endswith('/'):
                with ux_utils.print_exception_no_traceback():
                    raise exceptions.StorageSourceError(
                        'Storage source paths cannot end with a slash '
                        '(try "/mydir: /mydir" or "/myfile: /myfile"). '
                        f'Found source={local_source}')
            # Local path, check if it exists
            full_src = os.path.abspath(os.path.expanduser(local_source))
            # Only check if local source exists if it is synced to the bucket
            if not os.path.exists(full_src) and sync_on_reconstruction:
                with ux_utils.print_exception_no_traceback():
                    raise exceptions.StorageSourceError(
                        'Local source path does not'
                        f' exist: {local_source}')
            # Raise warning if user's path is a symlink
            elif os.path.islink(full_src):
                logger.warning(f'Source path {source} is a symlink. '
                               'Referenced contents are uploaded, matching '
                               'the default behavior for S3 and GCS syncing.')

        # Check if source is a list of paths
        if isinstance(source, list):
            # Check for conflicts in basenames
            _check_basename_conflicts(source)
            # Validate each path
            for local_source in source:
                _validate_local_source(local_source)
            is_local_source = True
        else:
            # Check if str source is a valid local/remote URL
            split_path = urllib.parse.urlsplit(source)
            if split_path.scheme == '':
                _validate_local_source(source)
                # Check if source is a file - throw error if it is
                full_src = os.path.abspath(os.path.expanduser(source))
                if os.path.isfile(full_src):
                    with ux_utils.print_exception_no_traceback():
                        raise exceptions.StorageSourceError(
                            'Storage source path cannot be a file - only'
                            ' directories are supported as a source. '
                            'To upload a single file, specify it in a list '
                            f'by writing source: [{source}]. Note '
                            'that the file will be uploaded to the root of the '
                            'bucket and will appear at <destination_path>/'
                            f'{os.path.basename(source)}. Alternatively, you '
                            'can directly upload the file to the VM without '
                            'using a bucket by writing <destination_path>: '
                            f'{source} in the file_mounts section of your YAML')
                is_local_source = True
            elif split_path.scheme in ['s3', 'gs']:
                is_local_source = False
                # Storage mounting does not support mounting specific files from
                # cloud store - ensure path points to only a directory
                if mode == StorageMode.MOUNT:
                    if split_path.path.strip('/') != '':
                        with ux_utils.print_exception_no_traceback():
                            raise exceptions.StorageModeError(
                                'MOUNT mode does not support'
                                ' mounting specific files from cloud'
                                ' storage. Please use COPY mode or'
                                ' specify only the bucket name as'
                                ' the source.')
            else:
                with ux_utils.print_exception_no_traceback():
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
                # Check if a Storage object already exists in global_user_state
                # (e.g. used as scratch previously). Such storage objects can be
                # mounted in copy mode even though they have no source in the
                # yaml spec (the name is the source).
                handle = global_user_state.get_handle_from_storage_name(
                    self.name)
                if handle is not None:
                    return
                else:
                    with ux_utils.print_exception_no_traceback():
                        raise exceptions.StorageSourceError(
                            'New storage object: source must be specified when '
                            'using COPY mode.')
            else:
                # If source is not specified in COPY mode, the intent is to
                # create a bucket and use it as scratch disk. Name must be
                # specified to create bucket.
                if not self.name:
                    with ux_utils.print_exception_no_traceback():
                        raise exceptions.StorageSpecError(
                            'Storage source or storage name must be specified.')
                else:
                    # Create bucket and mount
                    return
        elif self.source is not None:
            source, is_local_source = Storage._validate_source(
                self.source, self.mode, self.sync_on_reconstruction)

            if not self.name:
                if is_local_source:
                    with ux_utils.print_exception_no_traceback():
                        raise exceptions.StorageNameError(
                            'Storage name must be specified if the source is '
                            'local.')
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
                    with ux_utils.print_exception_no_traceback():
                        raise exceptions.StorageSpecError(
                            'Storage name should not be specified if the '
                            'source is a remote URI.')
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
            with ux_utils.print_exception_no_traceback():
                raise exceptions.StorageSpecError(
                    f'{store_type} not supported as a Store.')

        # Initialize store object and get/create bucket
        try:
            store = store_cls(name=self.name, source=self.source)
        except exceptions.StorageBucketCreateError:
            # Creation failed, so this must be sky managed store. Add failure
            # to state.
            logger.error(f'Could not create {store_type} store '
                         f'with name {self.name}.')
            global_user_state.set_storage_status(self.name,
                                                 StorageStatus.INIT_FAILED)
            raise
        except exceptions.StorageBucketGetError:
            # Bucket get failed, so this is not sky managed. Do not update state
            logger.error(f'Could not get {store_type} store '
                         f'with name {self.name}.')
            raise
        except exceptions.StorageInitError:
            logger.error(f'Could not initialize {store_type} store with '
                         f'name {self.name}. General initialization error.')
            raise

        # Add store to storage
        self._add_store(store)

        # Upload source to store
        self._sync_store(store)

        return store

    def _add_store(self, store: AbstractStore, is_reconstructed: bool = False):
        # Adds a store object to the storage
        store_type = StoreType.from_store(store)
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
            elif self.force_delete:
                store.delete()
            # Remove store from bookkeeping
            del self.stores[store_type]
        else:
            for _, store in self.stores.items():
                if store.is_sky_managed:
                    self.handle.remove_store(store)
                    store.delete()
                elif self.force_delete:
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
            logger.error(f'Could not upload {self.source} to store '
                         f'name {store.name}.')
            if store.is_sky_managed:
                global_user_state.set_storage_status(
                    self.name, StorageStatus.UPLOAD_FAILED)
            raise

        # Upload succeeded - update state
        if store.is_sky_managed:
            global_user_state.set_storage_status(self.name, StorageStatus.READY)

    @classmethod
    def from_yaml_config(cls, config: Dict[str, str]) -> 'Storage':
        backend_utils.validate_schema(config, schemas.get_storage_schema(),
                                      'Invalid storage YAML: ')

        name = config.pop('name', None)
        source = config.pop('source', None)
        store = config.pop('store', None)
        mode_str = config.pop('mode', None)
        force_delete = config.pop('_force_delete', False)

        if isinstance(mode_str, str):
            # Make mode case insensitive, if specified
            mode = StorageMode(mode_str.upper())
        else:
            # Make sure this keeps the same as the default mode in __init__
            mode = StorageMode.MOUNT
        persistent = config.pop('persistent', True)

        assert not config, f'Invalid storage args: {config.keys()}'

        # Validation of the config object happens on instantiation.
        storage_obj = cls(name=name,
                          source=source,
                          persistent=persistent,
                          mode=mode)
        if store is not None:
            storage_obj.add_store(StoreType(store.upper()))

        # Add force deletion flag
        storage_obj.force_delete = force_delete
        return storage_obj

    def to_yaml_config(self) -> Dict[str, str]:
        config = dict()

        def add_if_not_none(key, value):
            if value is not None:
                config[key] = value

        name = self.name
        if (self.source is not None and isinstance(self.source, str) and
                data_utils.is_cloud_store_url(self.source)):
            # Remove name if source is a cloud store URL
            name = None
        add_if_not_none('name', name)
        add_if_not_none('source', self.source)

        stores = None
        if len(self.stores) > 0:
            stores = ','.join([store.value for store in self.stores])
        add_if_not_none('store', stores)
        add_if_not_none('persistent', self.persistent)
        add_if_not_none('mode', self.mode.value)
        if self.force_delete:
            config['_force_delete'] = True
        return config


class S3Store(AbstractStore):
    """S3Store inherits from Storage Object and represents the backend
    for S3 buckets.
    """

    ACCESS_DENIED_MESSAGE = 'Access Denied'

    def __init__(self,
                 name: str,
                 source: str,
                 region: Optional[str] = 'us-east-2',
                 is_sky_managed: Optional[bool] = None):
        self.client = None
        self.bucket = None
        super().__init__(name, source, region, is_sky_managed)

    def _validate(self):
        if self.source is not None and isinstance(self.source, str):
            if self.source.startswith('s3://'):
                assert self.name == data_utils.split_s3_path(self.source)[0], (
                    'S3 Bucket is specified as path, the name should be the'
                    ' same as S3 bucket.')
            elif self.source.startswith('gs://'):
                assert self.name == data_utils.split_gcs_path(self.source)[0], (
                    'GCS Bucket is specified as path, the name should be '
                    'the same as GCS bucket.')
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
            if isinstance(self.source, list):
                self.batch_aws_rsync(self.source, create_dirs=True)
            elif self.source is not None:
                if self.source.startswith('s3://'):
                    pass
                elif self.source.startswith('gs://'):
                    self._transfer_to_s3()
                else:
                    self.batch_aws_rsync([self.source])
        except exceptions.StorageUploadError:
            raise
        except Exception as e:
            raise exceptions.StorageUploadError(
                f'Upload failed for store {self.name}') from e

    def delete(self) -> None:
        self._delete_s3_bucket(self.name)
        logger.info(f'{colorama.Fore.GREEN}Deleted S3 bucket {self.name}.'
                    f'{colorama.Style.RESET_ALL}')

    def get_handle(self) -> StorageHandle:
        return aws.resource('s3').Bucket(self.name)

    def batch_aws_rsync(self,
                        source_path_list: List[Path],
                        create_dirs: bool = False) -> None:
        """Invokes aws s3 sync to batch upload a list of local paths to S3

        AWS Sync by default uses 10 threads to upload files to the bucket.  To
        increase parallelism, modify max_concurrent_requests in your aws config
        file (Default path: ~/.aws/config).

        Since aws s3 sync does not support batch operations, we construct
        multiple commands to be run in parallel.

        Args:
            source_path_list: List of paths to local files or directories
            create_dirs: If the local_path is a directory and this is set to
                False, the contents of the directory are directly uploaded to
                root of the bucket. If the local_path is a directory and this is
                set to True, the directory is created in the bucket root and
                contents are uploaded to it.
        """

        def get_file_sync_command(base_dir_path, file_names):
            includes = ' '.join(
                [f'--include "{file_name}"' for file_name in file_names])
            sync_command = ('aws s3 sync --no-follow-symlinks --exclude="*" '
                            f'{includes} {base_dir_path} '
                            f's3://{self.name}')
            return sync_command

        def get_dir_sync_command(src_dir_path, dest_dir_name):
            sync_command = ('aws s3 sync --no-follow-symlinks '
                            f'{src_dir_path} '
                            f's3://{self.name}/{dest_dir_name}')
            return sync_command

        # Generate message for upload
        if len(source_path_list) > 1:
            source_message = f'{len(source_path_list)} paths'
        else:
            source_message = source_path_list[0]

        with backend_utils.safe_console_status(
                f'[bold cyan]Syncing '
                f'[green]{source_message}[/] to [green]s3://{self.name}/[/]'):
            data_utils.parallel_upload(
                source_path_list,
                get_file_sync_command,
                get_dir_sync_command,
                self.name,
                self.ACCESS_DENIED_MESSAGE,
                create_dirs=create_dirs,
                max_concurrent_uploads=_MAX_CONCURRENT_UPLOADS)

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

        try:
            # Try Public bucket case.
            # This line does not error out if the bucket is an external public
            # bucket or if it is a user's bucket that is publicly
            # accessible.
            self.client.head_bucket(Bucket=self.name)
            return bucket, False
        except aws.client_exception() as e:
            error_code = e.response['Error']['Code']
            # AccessDenied error for buckets that are private and not owned by
            # user.
            if error_code == '403':
                command = f'aws s3 ls {self.name}'
                with ux_utils.print_exception_no_traceback():
                    raise exceptions.StorageBucketGetError(
                        _BUCKET_FAIL_TO_CONNECT_MESSAGE.format(name=self.name) +
                        f' To debug, consider using {command}.') from e

        if isinstance(self.source, str) and self.source.startswith('s3://'):
            with ux_utils.print_exception_no_traceback():
                raise exceptions.StorageBucketGetError(
                    'Attempted to connect to a non-existent bucket: '
                    f'{self.source}. Consider using `aws s3 ls '
                    f'{self.source}` to debug.')

        # If bucket cannot be found in both private and public settings,
        # the bucket is created by Sky.
        bucket = self._create_s3_bucket(self.name)
        return bucket, True

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
        install_cmd = ('sudo wget -nc https://github.com/romilbhardwaj/goofys/'
                       'releases/download/0.24.0-romilb-upstream/goofys '
                       '-O /usr/local/bin/goofys && '
                       'sudo chmod +x /usr/local/bin/goofys')
        mount_cmd = ('goofys -o allow_other '
                     f'--stat-cache-ttl {self._STAT_CACHE_TTL} '
                     f'--type-cache-ttl {self._TYPE_CACHE_TTL} '
                     f'{self.bucket.name} {mount_path}')
        return mounting_utils.get_mounting_command(mount_path, install_cmd,
                                                   mount_cmd)

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
            with ux_utils.print_exception_no_traceback():
                raise exceptions.StorageBucketCreateError(
                    f'Attempted to create a bucket '
                    f'{self.name} but failed.') from e
        return aws.resource('s3').Bucket(bucket_name)

    def _delete_s3_bucket(self, bucket_name: str) -> None:
        """Deletes S3 bucket, including all objects in bucket

        Args:
          bucket_name: str; Name of bucket
        """
        # Deleting objects is very slow programatically
        # (i.e. bucket.objects.all().delete() is slow).
        # In addition, standard delete operations (i.e. via `aws s3 rm`)
        # are slow, since AWS puts deletion markers.
        # https://stackoverflow.com/questions/49239351/why-is-it-so-much-slower-to-delete-objects-in-aws-s3-than-it-is-to-create-them
        # The fastest way to delete is to run `aws s3 rb --force`,
        # which removes the bucket by force.
        remove_command = f'aws s3 rb s3://{bucket_name} --force'
        try:
            with backend_utils.safe_console_status(
                    f'[bold cyan]Deleting S3 bucket {bucket_name}[/]'):
                subprocess.check_output(remove_command.split(' '))
        except subprocess.CalledProcessError as e:
            logger.error(e.output)
            with ux_utils.print_exception_no_traceback():
                raise exceptions.StorageBucketDeleteError(
                    f'Failed to delete S3 bucket {bucket_name}.')

        # Wait until bucket deletion propagates on AWS servers
        while data_utils.verify_s3_bucket(bucket_name):
            time.sleep(0.1)


class GcsStore(AbstractStore):
    """GcsStore inherits from Storage Object and represents the backend
    for GCS buckets.
    """

    ACCESS_DENIED_MESSAGE = 'AccessDeniedException'

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
            if isinstance(self.source, str):
                if self.source.startswith('s3://'):
                    assert self.name == data_utils.split_s3_path(
                        self.source
                    )[0], (
                        'S3 Bucket is specified as path, the name should be the'
                        ' same as S3 bucket.')
                    assert data_utils.verify_s3_bucket(self.name), (
                        f'Source specified as {self.source}, an S3 bucket. ',
                        'S3 Bucket should exist.')
                elif self.source.startswith('gs://'):
                    assert self.name == data_utils.split_gcs_path(
                        self.source
                    )[0], (
                        'GCS Bucket is specified as path, the name should be '
                        'the same as GCS bucket.')

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
            if isinstance(self.source, list):
                self.batch_gsutil_rsync(self.source, create_dirs=True)
            elif self.source is not None:
                if self.source.startswith('gs://'):
                    pass
                elif self.source.startswith('s3://'):
                    self._transfer_to_gcs()
                else:
                    # If a single directory is specified in source, upload
                    # contents to root of bucket by suffixing /*.
                    self.batch_gsutil_rsync([self.source])
        except exceptions.StorageUploadError:
            raise
        except Exception as e:
            raise exceptions.StorageUploadError(
                f'Upload failed for store {self.name}') from e

    def delete(self) -> None:
        self._delete_gcs_bucket(self.name)
        logger.info(f'{colorama.Fore.GREEN}Deleted GCS bucket {self.name}.'
                    f'{colorama.Style.RESET_ALL}')

    def get_handle(self) -> StorageHandle:
        return self.client.get_bucket(self.name)

    def batch_gsutil_cp(self,
                        source_path_list: List[Path],
                        create_dirs: bool = False) -> None:
        """Invokes gsutil cp -n to batch upload a list of local paths

        -n flag to gsutil cp checks the existence of an object before uploading,
        making it similar to gsutil rsync. Since it allows specification of a
        list of files, it is faster than calling gsutil rsync on each file.
        However, unlike rsync, files are compared based on just their filename,
        and any updates to a file would not be copied to the bucket.
        """
        # Generate message for upload
        if len(source_path_list) > 1:
            source_message = f'{len(source_path_list)} paths'
        else:
            source_message = source_path_list

        # If the source_path list contains a directory, then gsutil cp -n
        # copies the dir as is to the root of the bucket. To copy the
        # contents of directory to the root, add /* to the directory path
        # e.g., /mydir/*
        source_path_list = [
            str(path) + '/*' if
            (os.path.isdir(path) and not create_dirs) else str(path)
            for path in source_path_list
        ]
        copy_list = '\n'.join(
            os.path.abspath(os.path.expanduser(p)) for p in source_path_list)
        sync_command = (f'echo "{copy_list}" | '
                        f'gsutil -m cp -e -n -r -I gs://{self.name}')

        with backend_utils.safe_console_status(
                f'[bold cyan]Syncing '
                f'[green]{source_message}[/] to [green]gs://{self.name}/[/]'):
            data_utils.run_upload_cli(sync_command,
                                      self.ACCESS_DENIED_MESSAGE,
                                      bucket_name=self.name)

    def batch_gsutil_rsync(self,
                           source_path_list: List[Path],
                           create_dirs: bool = False) -> None:
        """Invokes gsutil rsync to batch upload a list of local paths

        Since gsutil rsync does not support include commands, We use negative
        look-ahead regex to exclude everything else than the path(s) we want to
        upload.

        Since gsutil rsync does not support batch operations, we construct
        multiple commands to be run in parallel.

        Args:
            source_path_list: List of paths to local files or directories
            create_dirs: If the local_path is a directory and this is set to
                False, the contents of the directory are directly uploaded to
                root of the bucket. If the local_path is a directory and this is
                set to True, the directory is created in the bucket root and
                contents are uploaded to it.
        """

        def get_file_sync_command(base_dir_path, file_names):
            sync_format = '|'.join(file_names)
            sync_command = (f'gsutil -m rsync -x \'^(?!{sync_format}$).*\' '
                            f'{base_dir_path} gs://{self.name}')
            return sync_command

        def get_dir_sync_command(src_dir_path, dest_dir_name):
            sync_command = (f'gsutil -m rsync -r {src_dir_path} '
                            f'gs://{self.name}/{dest_dir_name}')
            return sync_command

        # Generate message for upload
        if len(source_path_list) > 1:
            source_message = f'{len(source_path_list)} paths'
        else:
            source_message = source_path_list[0]

        with backend_utils.safe_console_status(
                f'[bold cyan]Syncing '
                f'[green]{source_message}[/] to [green]gs://{self.name}/[/]'):
            data_utils.parallel_upload(
                source_path_list,
                get_file_sync_command,
                get_dir_sync_command,
                self.name,
                self.ACCESS_DENIED_MESSAGE,
                create_dirs=create_dirs,
                max_concurrent_uploads=_MAX_CONCURRENT_UPLOADS)

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
            if isinstance(self.source, str) and self.source.startswith('gs://'):
                with ux_utils.print_exception_no_traceback():
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
            except (gcp.not_found_exception(), ValueError) as e:
                command = f'gsutil ls gs://{self.name}'
                with ux_utils.print_exception_no_traceback():
                    raise exceptions.StorageBucketGetError(
                        _BUCKET_FAIL_TO_CONNECT_MESSAGE.format(name=self.name) +
                        f' To debug, consider using {command}.') from e

    def mount_command(self, mount_path: str) -> str:
        """Returns the command to mount the bucket to the mount_path.

        Uses gcsfuse to mount the bucket.

        Args:
          mount_path: str; Path to mount the bucket to.
        """
        install_cmd = ('wget -nc https://github.com/GoogleCloudPlatform/gcsfuse'
                       '/releases/download/v0.41.2/gcsfuse_0.41.2_amd64.deb '
                       '-O /tmp/gcsfuse.deb && '
                       'sudo dpkg --install /tmp/gcsfuse.deb')
        mount_cmd = ('gcsfuse -o allow_other '
                     '--implicit-dirs '
                     f'--stat-cache-capacity {self._STAT_CACHE_CAPACITY} '
                     f'--stat-cache-ttl {self._STAT_CACHE_TTL} '
                     f'--type-cache-ttl {self._TYPE_CACHE_TTL} '
                     f'--rename-dir-limit {self._RENAME_DIR_LIMIT} '
                     f'{self.bucket.name} {mount_path}')
        return mounting_utils.get_mounting_command(mount_path, install_cmd,
                                                   mount_cmd)

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
        except Exception as e:  # pylint: disable=broad-except
            with ux_utils.print_exception_no_traceback():
                raise exceptions.StorageBucketCreateError(
                    f'Attempted to create a bucket {self.name} but failed.'
                ) from e
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
            self.client.get_bucket(bucket_name)
        except gcp.forbidden_exception() as e:
            # Try public bucket to see if bucket exists
            with ux_utils.print_exception_no_traceback():
                raise PermissionError(
                    'External Bucket detected. User not allowed to delete '
                    'external bucket.') from e

        try:
            with backend_utils.safe_console_status(
                    f'[bold cyan]Deleting GCS bucket {bucket_name}[/]'):
                remove_obj_command = ('gsutil -m rm -r'
                                      f' gs://{bucket_name}')
                subprocess.check_output(remove_obj_command.split(' '),
                                        stderr=subprocess.STDOUT)
        except subprocess.CalledProcessError as e:
            logger.error(e.output)
            with ux_utils.print_exception_no_traceback():
                raise exceptions.StorageBucketDeleteError(
                    f'Failed to delete GCS bucket {bucket_name}.')
