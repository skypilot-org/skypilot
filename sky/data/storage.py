"""Storage and Store Classes for Sky Data."""
from abc import abstractmethod
from dataclasses import dataclass
import enum
import hashlib
import os
import re
import shlex
import subprocess
import time
import typing
from typing import Any, Callable, Dict, List, Optional, Tuple, Type, Union
import urllib.parse

import colorama

from sky import check as sky_check
from sky import clouds
from sky import exceptions
from sky import global_user_state
from sky import sky_logging
from sky import skypilot_config
from sky.adaptors import aws
from sky.adaptors import azure
from sky.adaptors import cloudflare
from sky.adaptors import gcp
from sky.adaptors import ibm
from sky.adaptors import nebius
from sky.adaptors import oci
from sky.clouds import cloud as sky_cloud
from sky.data import data_transfer
from sky.data import data_utils
from sky.data import mounting_utils
from sky.data import storage_utils
from sky.skylet import constants
from sky.utils import common_utils
from sky.utils import rich_utils
from sky.utils import schemas
from sky.utils import status_lib
from sky.utils import ux_utils

if typing.TYPE_CHECKING:
    from google.cloud import storage  # type: ignore
    import mypy_boto3_s3

logger = sky_logging.init_logger(__name__)

StorageHandle = Any
StorageStatus = status_lib.StorageStatus
Path = str
SourceType = Union[Path, List[Path]]

# Clouds with object storage implemented in this module. Azure Blob
# Storage isn't supported yet (even though Azure is).
# TODO(Doyoung): need to add clouds.CLOUDFLARE() to support
# R2 to be an option as preferred store type
STORE_ENABLED_CLOUDS: List[str] = [
    str(clouds.AWS()),
    str(clouds.GCP()),
    str(clouds.Azure()),
    str(clouds.IBM()),
    str(clouds.OCI()),
    str(clouds.Nebius()),
    cloudflare.NAME,
]

# Maximum number of concurrent rsync upload processes
_MAX_CONCURRENT_UPLOADS = 32

_BUCKET_FAIL_TO_CONNECT_MESSAGE = (
    'Failed to access existing bucket {name!r}. '
    'This is likely because it is a private bucket you do not have access to.\n'
    'To fix: \n'
    '  1. If you are trying to create a new bucket: use a different name.\n'
    '  2. If you are trying to connect to an existing bucket: make sure '
    'your cloud credentials have access to it.')

_BUCKET_EXTERNALLY_DELETED_DEBUG_MESSAGE = (
    'Bucket {bucket_name!r} does not exist. '
    'It may have been deleted externally.')

_STORAGE_LOG_FILE_NAME = 'storage_sync.log'


def get_cached_enabled_storage_cloud_names_or_refresh(
        raise_if_no_cloud_access: bool = False) -> List[str]:
    # This is a temporary solution until https://github.com/skypilot-org/skypilot/issues/1943 # pylint: disable=line-too-long
    # is resolved by implementing separate 'enabled_storage_clouds'
    enabled_clouds = sky_check.get_cached_enabled_clouds_or_refresh(
        sky_cloud.CloudCapability.STORAGE)
    enabled_clouds = [str(cloud) for cloud in enabled_clouds]

    r2_is_enabled, _ = cloudflare.check_storage_credentials()
    if r2_is_enabled:
        enabled_clouds.append(cloudflare.NAME)
    if raise_if_no_cloud_access and not enabled_clouds:
        raise exceptions.NoCloudAccessError(
            'No cloud access available for storage. '
            'Please check your cloud credentials.')
    return enabled_clouds


def _is_storage_cloud_enabled(cloud_name: str,
                              try_fix_with_sky_check: bool = True) -> bool:
    enabled_storage_cloud_names = (
        get_cached_enabled_storage_cloud_names_or_refresh())
    if cloud_name in enabled_storage_cloud_names:
        return True
    if try_fix_with_sky_check:
        # TODO(zhwu): Only check the specified cloud to speed up.
        sky_check.check_capability(
            sky_cloud.CloudCapability.STORAGE,
            quiet=True,
            workspace=skypilot_config.get_active_workspace())
        return _is_storage_cloud_enabled(cloud_name,
                                         try_fix_with_sky_check=False)
    return False


class StoreType(enum.Enum):
    """Enum for the different types of stores."""
    S3 = 'S3'
    GCS = 'GCS'
    AZURE = 'AZURE'
    R2 = 'R2'
    IBM = 'IBM'
    OCI = 'OCI'
    NEBIUS = 'NEBIUS'
    VOLUME = 'VOLUME'

    @classmethod
    def _get_s3_compatible_store_by_cloud(cls,
                                          cloud_name: str) -> Optional[str]:
        """Get S3-compatible store type by cloud name."""
        for store_type, store_class in _S3_COMPATIBLE_STORES.items():
            config = store_class.get_config()
            if config.cloud_name.lower() == cloud_name:
                return store_type
        return None

    @classmethod
    def _get_s3_compatible_config(
            cls, store_type: str) -> Optional['S3CompatibleConfig']:
        """Get S3-compatible store configuration by store type."""
        store_class = _S3_COMPATIBLE_STORES.get(store_type)
        if store_class:
            return store_class.get_config()
        return None

    @classmethod
    def find_s3_compatible_config_by_prefix(
            cls, source: str) -> Optional['StoreType']:
        """Get S3-compatible store type by URL prefix."""
        for store_type, store_class in _S3_COMPATIBLE_STORES.items():
            config = store_class.get_config()
            if source.startswith(config.url_prefix):
                return StoreType(store_type)
        return None

    @classmethod
    def from_cloud(cls, cloud: str) -> 'StoreType':
        cloud_lower = cloud.lower()
        if cloud_lower == str(clouds.GCP()).lower():
            return StoreType.GCS
        elif cloud_lower == str(clouds.IBM()).lower():
            return StoreType.IBM
        elif cloud_lower == str(clouds.Azure()).lower():
            return StoreType.AZURE
        elif cloud_lower == str(clouds.OCI()).lower():
            return StoreType.OCI
        elif cloud_lower == str(clouds.Lambda()).lower():
            with ux_utils.print_exception_no_traceback():
                raise ValueError('Lambda Cloud does not provide cloud storage.')
        elif cloud_lower == str(clouds.SCP()).lower():
            with ux_utils.print_exception_no_traceback():
                raise ValueError('SCP does not provide cloud storage.')
        else:
            s3_store_type = cls._get_s3_compatible_store_by_cloud(cloud_lower)
            if s3_store_type:
                return cls(s3_store_type)

        raise ValueError(f'Unsupported cloud for StoreType: {cloud}')

    def to_cloud(self) -> str:
        config = self._get_s3_compatible_config(self.value)
        if config:
            return config.cloud_name

        if self == StoreType.GCS:
            return str(clouds.GCP())
        elif self == StoreType.AZURE:
            return str(clouds.Azure())
        elif self == StoreType.IBM:
            return str(clouds.IBM())
        elif self == StoreType.OCI:
            return str(clouds.OCI())
        else:
            raise ValueError(f'Unknown store type: {self}')

    @classmethod
    def from_store(cls, store: 'AbstractStore') -> 'StoreType':
        if isinstance(store, S3CompatibleStore):
            return cls(store.get_store_type())

        if isinstance(store, GcsStore):
            return StoreType.GCS
        elif isinstance(store, AzureBlobStore):
            return StoreType.AZURE
        elif isinstance(store, IBMCosStore):
            return StoreType.IBM
        elif isinstance(store, OciStore):
            return StoreType.OCI
        else:
            with ux_utils.print_exception_no_traceback():
                raise ValueError(f'Unknown store type: {store}')

    def store_prefix(self) -> str:
        config = self._get_s3_compatible_config(self.value)
        if config:
            return config.url_prefix

        if self == StoreType.GCS:
            return 'gs://'
        elif self == StoreType.AZURE:
            return 'https://'
        elif self == StoreType.IBM:
            return 'cos://'
        elif self == StoreType.OCI:
            return 'oci://'
        else:
            with ux_utils.print_exception_no_traceback():
                raise ValueError(f'Unknown store type: {self}')

    @classmethod
    def get_endpoint_url(cls, store: 'AbstractStore', path: str) -> str:
        """Generates the endpoint URL for a given store and path.

        Args:
            store: Store object implementing AbstractStore.
            path: Path within the store.

        Returns:
            Endpoint URL of the bucket as a string.
        """
        store_type = cls.from_store(store)
        if store_type == StoreType.AZURE:
            assert isinstance(store, AzureBlobStore)
            storage_account_name = store.storage_account_name
            bucket_endpoint_url = data_utils.AZURE_CONTAINER_URL.format(
                storage_account_name=storage_account_name, container_name=path)
        else:
            bucket_endpoint_url = f'{store_type.store_prefix()}{path}'
        return bucket_endpoint_url

    @classmethod
    def get_fields_from_store_url(
        cls, store_url: str
    ) -> Tuple['StoreType', str, str, Optional[str], Optional[str]]:
        """Returns the store type, bucket name, and sub path from
        a store URL, and the storage account name and region if applicable.

        Args:
            store_url: str; The store URL.
        """
        # The full path from the user config of IBM COS contains the region,
        # and Azure Blob Storage contains the storage account name, we need to
        # pass these information to the store constructor.
        storage_account_name = None
        region = None
        for store_type in StoreType:
            if store_url.startswith(store_type.store_prefix()):
                if store_type == StoreType.AZURE:
                    storage_account_name, bucket_name, sub_path = \
                        data_utils.split_az_path(store_url)
                elif store_type == StoreType.IBM:
                    bucket_name, sub_path, region = data_utils.split_cos_path(
                        store_url)
                elif store_type == StoreType.GCS:
                    bucket_name, sub_path = data_utils.split_gcs_path(store_url)
                else:
                    # Check compatible stores
                    for compatible_store_type, store_class in \
                        _S3_COMPATIBLE_STORES.items():
                        if store_type.value == compatible_store_type:
                            config = store_class.get_config()
                            bucket_name, sub_path = config.split_path(store_url)
                            break
                    else:
                        # If we get here, it's an unknown S3-compatible store
                        raise ValueError(
                            f'Unknown S3-compatible store type: {store_type}')
                return store_type, bucket_name, \
                    sub_path, storage_account_name, region
        raise ValueError(f'Unknown store URL: {store_url}')


class StorageMode(enum.Enum):
    MOUNT = 'MOUNT'
    COPY = 'COPY'
    MOUNT_CACHED = 'MOUNT_CACHED'


MOUNTABLE_STORAGE_MODES = [
    StorageMode.MOUNT,
    StorageMode.MOUNT_CACHED,
]

DEFAULT_STORAGE_MODE = StorageMode.MOUNT


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
                     source: Optional[SourceType],
                     region: Optional[str] = None,
                     is_sky_managed: Optional[bool] = None,
                     _bucket_sub_path: Optional[str] = None):
            self.name = name
            self.source = source
            self.region = region
            self.is_sky_managed = is_sky_managed
            self._bucket_sub_path = _bucket_sub_path

        def __repr__(self):
            return (f'StoreMetadata('
                    f'\n\tname={self.name},'
                    f'\n\tsource={self.source},'
                    f'\n\tregion={self.region},'
                    f'\n\tis_sky_managed={self.is_sky_managed},'
                    f'\n\t_bucket_sub_path={self._bucket_sub_path})')

    def __init__(self,
                 name: str,
                 source: Optional[SourceType],
                 region: Optional[str] = None,
                 is_sky_managed: Optional[bool] = None,
                 sync_on_reconstruction: Optional[bool] = True,
                 _bucket_sub_path: Optional[str] = None):  # pylint: disable=invalid-name
        """Initialize AbstractStore

        Args:
            name: Store name
            source: Data source for the store
            region: Region to place the bucket in
            is_sky_managed: Whether the store is managed by Sky. If None, it
              must be populated by the implementing class during initialization.
            sync_on_reconstruction: bool; Whether to sync data if the storage
              object is found in the global_user_state and reconstructed from
              there. This is set to false when the Storage object is created not
              for direct use, e.g. for 'sky storage delete', or the storage is
              being re-used, e.g., for `sky start` on a stopped cluster.
            _bucket_sub_path: str; The prefix of the bucket directory to be
              created in the store, e.g. if _bucket_sub_path=my-dir, the files
              will be uploaded to s3://<bucket>/my-dir/.
              This only works if source is a local directory.
              # TODO(zpoint): Add support for non-local source.
        Raises:
            StorageBucketCreateError: If bucket creation fails
            StorageBucketGetError: If fetching existing bucket fails
            StorageInitError: If general initialization fails
        """
        self.name = name
        self.source = source
        self.region = region
        self.is_sky_managed = is_sky_managed
        self.sync_on_reconstruction = sync_on_reconstruction

        # To avoid mypy error
        self._bucket_sub_path: Optional[str] = None
        # Trigger the setter to strip any leading/trailing slashes.
        self.bucket_sub_path = _bucket_sub_path
        # Whether sky is responsible for the lifecycle of the Store.
        self._validate()
        self.initialize()

    @property
    def bucket_sub_path(self) -> Optional[str]:
        """Get the bucket_sub_path."""
        return self._bucket_sub_path

    @bucket_sub_path.setter
    # pylint: disable=invalid-name
    def bucket_sub_path(self, bucket_sub_path: Optional[str]) -> None:
        """Set the bucket_sub_path, stripping any leading/trailing slashes."""
        if bucket_sub_path is not None:
            self._bucket_sub_path = bucket_sub_path.strip('/')
        else:
            self._bucket_sub_path = None

    @classmethod
    def from_metadata(cls, metadata: StoreMetadata, **override_args):
        """Create a Store from a StoreMetadata object.

        Used when reconstructing Storage and Store objects from
        global_user_state.
        """
        return cls(
            name=override_args.get('name', metadata.name),
            source=override_args.get('source', metadata.source),
            region=override_args.get('region', metadata.region),
            is_sky_managed=override_args.get('is_sky_managed',
                                             metadata.is_sky_managed),
            sync_on_reconstruction=override_args.get('sync_on_reconstruction',
                                                     True),
            # Backward compatibility
            # TODO: remove the hasattr check after v0.11.0
            _bucket_sub_path=override_args.get(
                '_bucket_sub_path',
                metadata._bucket_sub_path  # pylint: disable=protected-access
            ) if hasattr(metadata, '_bucket_sub_path') else None)

    def get_metadata(self) -> StoreMetadata:
        return self.StoreMetadata(name=self.name,
                                  source=self.source,
                                  region=self.region,
                                  is_sky_managed=self.is_sky_managed,
                                  _bucket_sub_path=self._bucket_sub_path)

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
        """Removes the Storage from the cloud."""
        raise NotImplementedError

    def _delete_sub_path(self) -> None:
        """Removes objects from the sub path in the bucket."""
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
        raise NotImplementedError

    def _download_file(self, remote_path: str, local_path: str) -> None:
        """Downloads file from remote to local on Store

        Args:
          remote_path: str; Remote file path on Store
          local_path: str; Local file path on user's device
        """
        raise NotImplementedError

    def mount_command(self, mount_path: str) -> str:
        """Returns the command to mount the Store to the specified mount_path.

        This command is used for MOUNT mode. Includes the setup commands to
        install mounting tools.

        Args:
          mount_path: str; Mount path on remote server
        """
        raise NotImplementedError

    def mount_cached_command(self, mount_path: str) -> str:
        """Returns the command to mount the Store to the specified mount_path.

        This command is used for MOUNT_CACHED mode. Includes the setup commands
        to install mounting tools.

        Args:
          mount_path: str; Mount path on remote server
        """
        raise exceptions.NotSupportedError(
            f'{StorageMode.MOUNT_CACHED.value} is '
            f'not supported for {self.name}.')

    def __deepcopy__(self, memo):
        # S3 Client and GCS Client cannot be deep copied, hence the
        # original Store object is returned
        return self

    def _validate_existing_bucket(self):
        """Validates the storage fields for existing buckets."""
        # Check if 'source' is None, this is only allowed when Storage is in
        # either MOUNT mode or COPY mode with sky-managed storage.
        # Note: In COPY mode, a 'source' being None with non-sky-managed
        # storage is already handled as an error in _validate_storage_spec.
        if self.source is None:
            # Retrieve a handle associated with the storage name.
            # This handle links to sky managed storage if it exists.
            handle = global_user_state.get_handle_from_storage_name(self.name)
            # If handle is None, it implies the bucket is created
            # externally and not managed by Skypilot. For mounting such
            # externally created buckets, users must provide the
            # bucket's URL as 'source'.
            if handle is None:
                source_endpoint = StoreType.get_endpoint_url(store=self,
                                                             path=self.name)
                with ux_utils.print_exception_no_traceback():
                    raise exceptions.StorageSpecError(
                        'Attempted to mount a non-sky managed bucket '
                        f'{self.name!r} without specifying the storage source.'
                        f' Bucket {self.name!r} already exists. \n'
                        '    • To create a new bucket, specify a unique name.\n'
                        '    • To mount an externally created bucket (e.g., '
                        'created through cloud console or cloud cli), '
                        'specify the bucket URL in the source field '
                        'instead of its name. I.e., replace '
                        f'`name: {self.name}` with '
                        f'`source: {source_endpoint}`.')


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
        - (optional) Storage mode.
        - (optional) Set of stores managed by sky added to the Storage object
        """

        def __init__(
            self,
            *,
            storage_name: Optional[str],
            source: Optional[SourceType],
            mode: Optional[StorageMode] = None,
            sky_stores: Optional[Dict[StoreType,
                                      AbstractStore.StoreMetadata]] = None):
            assert storage_name is not None or source is not None
            self.storage_name = storage_name
            self.source = source
            self.mode = mode
            # Only stores managed by sky are stored here in the
            # global_user_state
            self.sky_stores = {} if sky_stores is None else sky_stores

        def __repr__(self):
            return (f'StorageMetadata('
                    f'\n\tstorage_name={self.storage_name},'
                    f'\n\tsource={self.source},'
                    f'\n\tmode={self.mode},'
                    f'\n\tstores={self.sky_stores})')

        def add_store(self, store: AbstractStore) -> None:
            storetype = StoreType.from_store(store)
            self.sky_stores[storetype] = store.get_metadata()

        def remove_store(self, store: AbstractStore) -> None:
            storetype = StoreType.from_store(store)
            if storetype in self.sky_stores:
                del self.sky_stores[storetype]

    def __init__(
        self,
        name: Optional[str] = None,
        source: Optional[SourceType] = None,
        stores: Optional[List[StoreType]] = None,
        persistent: Optional[bool] = True,
        mode: StorageMode = DEFAULT_STORAGE_MODE,
        sync_on_reconstruction: bool = True,
        # pylint: disable=invalid-name
        _is_sky_managed: Optional[bool] = None,
        # pylint: disable=invalid-name
        _bucket_sub_path: Optional[str] = None
    ) -> None:
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
            for direct use, e.g. for 'sky storage delete', or the storage is
            being re-used, e.g., for `sky start` on a stopped cluster.
          _is_sky_managed: Optional[bool]; Indicates if the storage is managed
            by Sky. Without this argument, the controller's behavior differs
            from the local machine. For example, if a bucket does not exist:
            Local Machine (is_sky_managed=True) →
            Controller (is_sky_managed=False).
            With this argument, the controller aligns with the local machine,
            ensuring it retains the is_sky_managed information from the YAML.
            During teardown, if is_sky_managed is True, the controller should
            delete the bucket. Otherwise, it might mistakenly delete only the
            sub-path, assuming is_sky_managed is False.
          _bucket_sub_path: Optional[str]; The subdirectory to use for the
            storage object.
        """
        self.name = name
        self.source = source
        self.persistent = persistent
        self.mode = mode
        assert mode in StorageMode
        self.stores: Dict[StoreType, Optional[AbstractStore]] = {}
        if stores is not None:
            for store in stores:
                self.stores[store] = None
        self.sync_on_reconstruction = sync_on_reconstruction
        self._is_sky_managed = _is_sky_managed
        self._bucket_sub_path = _bucket_sub_path

        self._constructed = False
        # TODO(romilb, zhwu): This is a workaround to support storage deletion
        # for managed jobs. Once sky storage supports forced management for
        # external buckets, this can be deprecated.
        self.force_delete = False

    def construct(self):
        """Constructs the storage object.

        The Storage object is lazily initialized, so that when a user
        initializes a Storage object on client side, it does not trigger the
        actual storage creation on the client side.

        Instead, once the specification of the storage object is uploaded to the
        SkyPilot API server side, the server side should use this construct()
        method to actually create the storage object. The construct() method
        will:

        1. Set the stores field if not specified
        2. Create the bucket or check the existence of the bucket
        3. Sync the data from the source to the bucket if necessary
        """
        if self._constructed:
            return
        self._constructed = True

        # Validate and correct inputs if necessary
        self._validate_storage_spec(self.name)

        # Logic to rebuild Storage if it is in global user state
        handle = global_user_state.get_handle_from_storage_name(self.name)
        if handle is not None:
            self.handle = handle
            # Reconstruct the Storage object from the global_user_state
            logger.debug('Detected existing storage object, '
                         f'loading Storage: {self.name}')
            self._add_store_from_metadata(self.handle.sky_stores)

            # When a storage object is reconstructed from global_user_state,
            # the user may have specified a new store type in the yaml file that
            # was not used with the storage object. We should error out in this
            # case, as we don't support having multiple stores for the same
            # storage object.
            if any(s is None for s in self.stores.values()):
                new_store_type = None
                previous_store_type = None
                for store_type, store in self.stores.items():
                    if store is not None:
                        previous_store_type = store_type
                    else:
                        new_store_type = store_type
                with ux_utils.print_exception_no_traceback():
                    raise exceptions.StorageBucketCreateError(
                        f'Bucket {self.name} was previously created for '
                        f'{previous_store_type.value.lower()!r}, but a new '
                        f'store type {new_store_type.value.lower()!r} is '
                        'requested. This is not supported yet. Please specify '
                        'the same store type: '
                        f'{previous_store_type.value.lower()!r}.')

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
            # Sky optimizer either adds a storage object instance or selects
            # from existing ones
            input_stores = self.stores
            self.stores = {}
            self.handle = self.StorageMetadata(storage_name=self.name,
                                               source=self.source,
                                               mode=self.mode)

            for store in input_stores:
                self.add_store(store)

            if self.source is not None:
                # If source is a pre-existing bucket, connect to the bucket
                # If the bucket does not exist, this will error out
                if isinstance(self.source, str):
                    if self.source.startswith('gs://'):
                        self.add_store(StoreType.GCS)
                    elif data_utils.is_az_container_endpoint(self.source):
                        self.add_store(StoreType.AZURE)
                    elif self.source.startswith('cos://'):
                        self.add_store(StoreType.IBM)
                    elif self.source.startswith('oci://'):
                        self.add_store(StoreType.OCI)

                    store_type = StoreType.find_s3_compatible_config_by_prefix(
                        self.source)
                    if store_type:
                        self.add_store(store_type)

    def get_bucket_sub_path_prefix(self, blob_path: str) -> str:
        """Adds the bucket sub path prefix to the blob path."""
        if self._bucket_sub_path is not None:
            return f'{blob_path}/{self._bucket_sub_path}'
        return blob_path

    @staticmethod
    def _validate_source(
            source: SourceType, mode: StorageMode,
            sync_on_reconstruction: bool) -> Tuple[SourceType, bool]:
        """Validates the source path.

        Args:
          source: str; File path where the data is initially stored. Can be a
            local path or a cloud URI (s3://, gs://, r2:// etc.).
            Local paths do not need to be absolute.
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
            elif split_path.scheme in [
                    's3', 'gs', 'https', 'r2', 'cos', 'oci', 'nebius'
            ]:
                is_local_source = False
                # Storage mounting does not support mounting specific files from
                # cloud store - ensure path points to only a directory
                if mode in MOUNTABLE_STORAGE_MODES:
                    if (split_path.scheme != 'https' and
                        ((split_path.scheme != 'cos' and
                          split_path.path.strip('/') != '') or
                         (split_path.scheme == 'cos' and
                          not re.match(r'^/[-\w]+(/\s*)?$', split_path.path)))):
                        # regex allows split_path.path to include /bucket
                        # or /bucket/optional_whitespaces while considering
                        # cos URI's regions (cos://region/bucket_name)
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
                        f'Supported paths: local, s3://, gs://, https://, '
                        f'r2://, cos://, oci://, nebius://. Got: {source}')
        return source, is_local_source

    def _validate_storage_spec(self, name: Optional[str]) -> None:
        """Validates the storage spec and updates local fields if necessary."""

        def validate_name(name):
            """ Checks for validating the storage name.

            Checks if the name starts the s3, gcs or r2 prefix and raise error
            if it does. Store specific validation checks (e.g., S3 specific
            rules) happen in the corresponding store class.
            """
            prefix = name.split('://')[0]
            prefix = prefix.lower()
            if prefix in ['s3', 'gs', 'https', 'r2', 'cos', 'oci', 'nebius']:
                with ux_utils.print_exception_no_traceback():
                    raise exceptions.StorageNameError(
                        'Prefix detected: `name` cannot start with '
                        f'{prefix}://. If you are trying to use an existing '
                        'bucket created outside of SkyPilot, please specify it '
                        'using the `source` field (e.g. '
                        '`source: s3://mybucket/`). If you are trying to '
                        'create a new bucket, please use the `store` field to '
                        'specify the store type (e.g. `store: s3`).')

        if self.source is None:
            # If the mode is COPY, the source must be specified
            if self.mode == StorageMode.COPY:
                # Check if a Storage object already exists in global_user_state
                # (e.g. used as scratch previously). Such storage objects can be
                # mounted in copy mode even though they have no source in the
                # yaml spec (the name is the source).
                handle = global_user_state.get_handle_from_storage_name(name)
                if handle is None:
                    with ux_utils.print_exception_no_traceback():
                        raise exceptions.StorageSourceError(
                            'New storage object: source must be specified when '
                            'using COPY mode.')
            else:
                # If source is not specified in COPY mode, the intent is to
                # create a bucket and use it as scratch disk. Name must be
                # specified to create bucket.
                if not name:
                    with ux_utils.print_exception_no_traceback():
                        raise exceptions.StorageSpecError(
                            'Storage source or storage name must be specified.')
            assert name is not None, handle
            validate_name(name)
            self.name = name
            return
        elif self.source is not None:
            source, is_local_source = Storage._validate_source(
                self.source, self.mode, self.sync_on_reconstruction)
            if not name:
                if is_local_source:
                    with ux_utils.print_exception_no_traceback():
                        raise exceptions.StorageNameError(
                            'Storage name must be specified if the source is '
                            'local.')
                else:
                    assert isinstance(source, str)
                    # Set name to source bucket name and continue
                    if source.startswith('cos://'):
                        # cos url requires custom parsing
                        name = data_utils.split_cos_path(source)[0]
                    elif data_utils.is_az_container_endpoint(source):
                        _, name, _ = data_utils.split_az_path(source)
                    else:
                        name = urllib.parse.urlsplit(source).netloc
                    assert name is not None, source
                    self.name = name
                    return
            else:
                if is_local_source:
                    # If name is specified and source is local, upload to bucket
                    assert name is not None, source
                    validate_name(name)
                    self.name = name
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

    def _add_store_from_metadata(
            self, sky_stores: Dict[StoreType,
                                   AbstractStore.StoreMetadata]) -> None:
        """Reconstructs Storage.stores from sky_stores.

        Reconstruct AbstractStore objects from sky_store's metadata and
        adds them into Storage.stores
        """
        for s_type, s_metadata in sky_stores.items():
            # When initializing from global_user_state, we override the
            # source from the YAML
            try:
                if s_type.value in _S3_COMPATIBLE_STORES:
                    store_class = _S3_COMPATIBLE_STORES[s_type.value]
                    store = store_class.from_metadata(
                        s_metadata,
                        source=self.source,
                        sync_on_reconstruction=self.sync_on_reconstruction,
                        _bucket_sub_path=self._bucket_sub_path)
                elif s_type == StoreType.S3:
                    store = S3Store.from_metadata(
                        s_metadata,
                        source=self.source,
                        sync_on_reconstruction=self.sync_on_reconstruction,
                        _bucket_sub_path=self._bucket_sub_path)
                elif s_type == StoreType.R2:
                    store = R2Store.from_metadata(
                        s_metadata,
                        source=self.source,
                        sync_on_reconstruction=self.sync_on_reconstruction,
                        _bucket_sub_path=self._bucket_sub_path)
                elif s_type == StoreType.GCS:
                    store = GcsStore.from_metadata(
                        s_metadata,
                        source=self.source,
                        sync_on_reconstruction=self.sync_on_reconstruction,
                        _bucket_sub_path=self._bucket_sub_path)
                elif s_type == StoreType.AZURE:
                    assert isinstance(s_metadata,
                                      AzureBlobStore.AzureBlobStoreMetadata)
                    store = AzureBlobStore.from_metadata(
                        s_metadata,
                        source=self.source,
                        sync_on_reconstruction=self.sync_on_reconstruction,
                        _bucket_sub_path=self._bucket_sub_path)
                elif s_type == StoreType.IBM:
                    store = IBMCosStore.from_metadata(
                        s_metadata,
                        source=self.source,
                        sync_on_reconstruction=self.sync_on_reconstruction,
                        _bucket_sub_path=self._bucket_sub_path)
                elif s_type == StoreType.OCI:
                    store = OciStore.from_metadata(
                        s_metadata,
                        source=self.source,
                        sync_on_reconstruction=self.sync_on_reconstruction,
                        _bucket_sub_path=self._bucket_sub_path)
                elif s_type == StoreType.NEBIUS:
                    store = NebiusStore.from_metadata(
                        s_metadata,
                        source=self.source,
                        sync_on_reconstruction=self.sync_on_reconstruction,
                        _bucket_sub_path=self._bucket_sub_path)
                else:
                    with ux_utils.print_exception_no_traceback():
                        raise ValueError(f'Unknown store type: {s_type}')
            # Following error is caught when an externally removed storage
            # is attempted to be fetched.
            except exceptions.StorageExternalDeletionError as e:
                if isinstance(e, exceptions.NonExistentStorageAccountError):
                    assert isinstance(s_metadata,
                                      AzureBlobStore.AzureBlobStoreMetadata)
                    logger.debug(f'Storage object {self.name!r} was attempted '
                                 'to be reconstructed while the corresponding '
                                 'storage account '
                                 f'{s_metadata.storage_account_name!r} does '
                                 'not exist.')
                else:
                    logger.debug(f'Storage object {self.name!r} was attempted '
                                 'to be reconstructed while the corresponding '
                                 'bucket was externally deleted.')
                continue
            self._add_store(store, is_reconstructed=True)

    @classmethod
    def from_metadata(cls, metadata: StorageMetadata,
                      **override_args) -> 'Storage':
        """Create Storage from StorageMetadata object.

        Used when reconstructing Storage object and AbstractStore objects from
        global_user_state.
        """
        # Name should not be specified if the source is a cloud store URL.
        source = override_args.get('source', metadata.source)
        name = override_args.get('name', metadata.storage_name)
        # If the source is a list, it consists of local paths
        if not isinstance(source,
                          list) and data_utils.is_cloud_store_url(source):
            name = None

        storage_obj = cls(name=name,
                          source=source,
                          sync_on_reconstruction=override_args.get(
                              'sync_on_reconstruction', True))

        # For backward compatibility
        if hasattr(metadata, 'mode'):
            if metadata.mode:
                storage_obj.mode = override_args.get('mode', metadata.mode)

        return storage_obj

    def add_store(self,
                  store_type: Union[str, StoreType],
                  region: Optional[str] = None) -> AbstractStore:
        """Initializes and adds a new store to the storage.

        Invoked by the optimizer after it has selected a store to
        add it to Storage.

        Args:
          store_type: StoreType; Type of the storage [S3, GCS, AZURE, R2, IBM]
          region: str; Region to place the bucket in. Caller must ensure that
            the region is valid for the chosen store_type.
        """
        assert self._constructed, self
        assert self.name is not None, self

        if isinstance(store_type, str):
            store_type = StoreType(store_type)

        if self.stores.get(store_type) is not None:
            if store_type == StoreType.AZURE:
                azure_store_obj = self.stores[store_type]
                assert isinstance(azure_store_obj, AzureBlobStore)
                storage_account_name = azure_store_obj.storage_account_name
                logger.info(f'Storage type {store_type} already exists under '
                            f'storage account {storage_account_name!r}.')
            else:
                logger.info(f'Storage type {store_type} already exists.')
            store = self.stores[store_type]
            assert store is not None, self
            return store

        store_cls: Type[AbstractStore]
        # First check if it's a registered S3-compatible store
        if store_type.value in _S3_COMPATIBLE_STORES:
            store_cls = _S3_COMPATIBLE_STORES[store_type.value]
        elif store_type == StoreType.GCS:
            store_cls = GcsStore
        elif store_type == StoreType.AZURE:
            store_cls = AzureBlobStore
        elif store_type == StoreType.IBM:
            store_cls = IBMCosStore
        elif store_type == StoreType.OCI:
            store_cls = OciStore
        else:
            with ux_utils.print_exception_no_traceback():
                raise exceptions.StorageSpecError(
                    f'{store_type} not supported as a Store.')
        try:
            store = store_cls(
                name=self.name,
                source=self.source,
                region=region,
                sync_on_reconstruction=self.sync_on_reconstruction,
                is_sky_managed=self._is_sky_managed,
                _bucket_sub_path=self._bucket_sub_path)
        except exceptions.StorageBucketCreateError:
            # Creation failed, so this must be sky managed store. Add failure
            # to state.
            logger.error(f'Could not create {store_type} store '
                         f'with name {self.name}.')
            try:
                global_user_state.set_storage_status(self.name,
                                                     StorageStatus.INIT_FAILED)
            except ValueError as e:
                logger.error(f'Error setting storage status: {e}')
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
        except exceptions.StorageSpecError:
            logger.error(f'Could not mount externally created {store_type} '
                         f'store with name {self.name!r}.')
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
                assert self.name is not None, self
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
            assert self.name is not None
            global_user_state.remove_storage(self.name)
        if store_type is not None:
            assert self.name is not None
            store = self.stores[store_type]
            assert store is not None, self
            is_sky_managed = store.is_sky_managed
            # We delete a store from the cloud if it's sky managed. Else just
            # remove handle and return
            if is_sky_managed:
                self.handle.remove_store(store)
                store.delete()
                # Check remaining stores - if none is sky managed, remove
                # the storage from global_user_state.
                delete = True
                for store in self.stores.values():
                    assert store is not None, self
                    if store.is_sky_managed:
                        delete = False
                        break
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
                assert store is not None, self
                if store.is_sky_managed:
                    self.handle.remove_store(store)
                    store.delete()
                elif self.force_delete:
                    store.delete()
            self.stores = {}
            # Remove storage from global_user_state if present
            if self.name is not None:
                global_user_state.remove_storage(self.name)

    def sync_all_stores(self):
        """Syncs the source and destinations of all stores in the Storage"""
        for _, store in self.stores.items():
            assert store is not None, self
            self._sync_store(store)

    def _sync_store(self, store: AbstractStore):
        """Runs the upload routine for the store and handles failures"""
        assert self._constructed, self
        assert self.name is not None, self

        def warn_for_git_dir(source: str):
            if os.path.isdir(os.path.join(source, '.git')):
                logger.warning(f'\'.git\' directory under \'{self.source}\' '
                               'is excluded during sync.')

        try:
            if self.source is not None:
                if isinstance(self.source, str):
                    warn_for_git_dir(self.source)
                else:
                    for source in self.source:
                        warn_for_git_dir(source)
            store.upload()
        except exceptions.StorageUploadError:
            logger.error(f'Could not upload {self.source!r} to store '
                         f'name {store.name!r}.')
            if store.is_sky_managed:
                global_user_state.set_storage_status(
                    self.name, StorageStatus.UPLOAD_FAILED)
            raise

        # Upload succeeded - update state
        if store.is_sky_managed:
            global_user_state.set_storage_status(self.name, StorageStatus.READY)

    @classmethod
    def from_yaml_config(cls, config: Dict[str, Any]) -> 'Storage':
        common_utils.validate_schema(config, schemas.get_storage_schema(),
                                     'Invalid storage YAML: ')
        name = config.pop('name', None)
        source = config.pop('source', None)
        store = config.pop('store', None)
        mode_str = config.pop('mode', None)
        force_delete = config.pop('_force_delete', None)
        # pylint: disable=invalid-name
        _is_sky_managed = config.pop('_is_sky_managed', None)
        # pylint: disable=invalid-name
        _bucket_sub_path = config.pop('_bucket_sub_path', None)
        if force_delete is None:
            force_delete = False

        if isinstance(mode_str, str):
            # Make mode case insensitive, if specified
            mode = StorageMode(mode_str.upper())
        else:
            mode = DEFAULT_STORAGE_MODE
        persistent = config.pop('persistent', None)
        if persistent is None:
            persistent = True

        assert not config, f'Invalid storage args: {config.keys()}'

        # Validation of the config object happens on instantiation.
        if store is not None:
            stores = [StoreType(store.upper())]
        else:
            stores = None
        storage_obj = cls(name=name,
                          source=source,
                          persistent=persistent,
                          mode=mode,
                          stores=stores,
                          _is_sky_managed=_is_sky_managed,
                          _bucket_sub_path=_bucket_sub_path)

        # Add force deletion flag
        storage_obj.force_delete = force_delete
        return storage_obj

    def to_yaml_config(self) -> Dict[str, Any]:
        config = {}

        def add_if_not_none(key: str, value: Optional[Any]):
            if value is not None:
                config[key] = value

        name = None
        if (self.source is None or not isinstance(self.source, str) or
                not data_utils.is_cloud_store_url(self.source)):
            # Remove name if source is a cloud store URL
            name = self.name
        add_if_not_none('name', name)
        add_if_not_none('source', self.source)

        stores = None
        is_sky_managed = self._is_sky_managed
        if self.stores:
            stores = ','.join([store.value for store in self.stores])
            store = list(self.stores.values())[0]
            if store is not None:
                is_sky_managed = store.is_sky_managed
        add_if_not_none('store', stores)
        add_if_not_none('_is_sky_managed', is_sky_managed)
        add_if_not_none('persistent', self.persistent)
        add_if_not_none('mode', self.mode.value)
        if self.force_delete:
            config['_force_delete'] = True
        if self._bucket_sub_path is not None:
            config['_bucket_sub_path'] = self._bucket_sub_path
        return config


# Registry for S3-compatible stores
_S3_COMPATIBLE_STORES = {}


def register_s3_compatible_store(store_class):
    """Decorator to automatically register S3-compatible stores."""
    store_type = store_class.get_store_type()
    _S3_COMPATIBLE_STORES[store_type] = store_class
    return store_class


@dataclass
class S3CompatibleConfig:
    """Configuration for S3-compatible storage providers."""
    # Provider identification
    store_type: str  # Store type identifier (e.g., "S3", "R2", "MINIO")
    url_prefix: str  # URL prefix (e.g., "s3://", "r2://", "minio://")

    # Client creation
    client_factory: Callable[[Optional[str]], Any]
    resource_factory: Callable[[str], StorageHandle]
    split_path: Callable[[str], Tuple[str, str]]
    verify_bucket: Callable[[str], bool]

    # CLI configuration
    aws_profile: Optional[str] = None
    get_endpoint_url: Optional[Callable[[], str]] = None
    credentials_file: Optional[str] = None
    extra_cli_args: Optional[List[str]] = None

    # Provider-specific settings
    cloud_name: str = ''
    default_region: Optional[str] = None
    access_denied_message: str = 'Access Denied'

    # Mounting
    mount_cmd_factory: Optional[Callable] = None
    mount_cached_cmd_factory: Optional[Callable] = None

    def __post_init__(self):
        if self.extra_cli_args is None:
            self.extra_cli_args = []


class S3CompatibleStore(AbstractStore):
    """Base class for S3-compatible object storage providers.

    This class provides a unified interface for all S3-compatible storage
    providers (AWS S3, Cloudflare R2, Nebius, MinIO, etc.) by leveraging
    a configuration-driven approach that eliminates code duplication.

    ## Adding a New S3-Compatible Store

    To add a new S3-compatible storage provider (e.g., MinIO),
    follow these steps:

    ### 1. Add Store Type to Enum
    First, add your store type to the StoreType enum:
    ```python
    class StoreType(enum.Enum):
        # ... existing entries ...
        MINIO = 'MINIO'
    ```

    ### 2. Create Store Class
    Create a new store class that inherits from S3CompatibleStore:
    ```python
    @register_s3_compatible_store
    class MinIOStore(S3CompatibleStore):
        '''MinIOStore for MinIO object storage.'''

        @classmethod
        def get_config(cls) -> S3CompatibleConfig:
            '''Return the configuration for MinIO.'''
            return S3CompatibleConfig(
                store_type='MINIO',
                url_prefix='minio://',
                client_factory=lambda region:\
                    data_utils.create_minio_client(region),
                resource_factory=lambda name:\
                    minio.resource('s3').Bucket(name),
                split_path=data_utils.split_minio_path,
                aws_profile='minio',
                get_endpoint_url=lambda: minio.get_endpoint_url(),
                cloud_name='minio',
                default_region='us-east-1',
                mount_cmd_factory=mounting_utils.get_minio_mount_cmd,
            )
    ```

    ### 3. Implement Required Utilities
    Create the necessary utility functions:

    #### In `sky/data/data_utils.py`:
    ```python
    def create_minio_client(region: Optional[str] = None):
        '''Create MinIO S3 client.'''
        return boto3.client('s3',
                          endpoint_url=minio.get_endpoint_url(),
                          aws_access_key_id=minio.get_access_key(),
                          aws_secret_access_key=minio.get_secret_key(),
                          region_name=region or 'us-east-1')

    def split_minio_path(minio_path: str) -> Tuple[str, str]:
        '''Split minio://bucket/key into (bucket, key).'''
        path_parts = minio_path.replace('minio://', '').split('/', 1)
        bucket = path_parts[0]
        key = path_parts[1] if len(path_parts) > 1 else ''
        return bucket, key
    ```

    #### In `sky/utils/mounting_utils.py`:
    ```python
    def get_minio_mount_cmd(profile: str, bucket_name: str, endpoint_url: str,
                           mount_path: str,
                           bucket_sub_path: Optional[str]) -> str:
        '''Generate MinIO mount command using s3fs.'''
        # Implementation similar to other S3-compatible mount commands
        pass
    ```

    ### 4. Create Adapter Module (if needed)
    Create `sky/adaptors/minio.py` for MinIO-specific configuration:
    ```python
    '''MinIO adapter for SkyPilot.'''

    MINIO_PROFILE_NAME = 'minio'

    def get_endpoint_url() -> str:
        '''Get MinIO endpoint URL from configuration.'''
        # Read from ~/.minio/config or environment variables
        pass

    def resource(resource_name: str):
        '''Get MinIO resource.'''
        # Implementation for creating MinIO resources
        pass
    ```

    """

    _ACCESS_DENIED_MESSAGE = 'Access Denied'

    def __init__(self,
                 name: str,
                 source: str,
                 region: Optional[str] = None,
                 is_sky_managed: Optional[bool] = None,
                 sync_on_reconstruction: bool = True,
                 _bucket_sub_path: Optional[str] = None):
        # Initialize configuration first to get defaults
        self.config = self.__class__.get_config()

        # Use provider's default region if not specified
        if region is None:
            region = self.config.default_region

        # Initialize S3CompatibleStore specific attributes
        self.client: 'mypy_boto3_s3.Client'
        self.bucket: 'StorageHandle'

        # Call parent constructor
        super().__init__(name, source, region, is_sky_managed,
                         sync_on_reconstruction, _bucket_sub_path)

    @classmethod
    @abstractmethod
    def get_config(cls) -> S3CompatibleConfig:
        """Return the configuration for this S3-compatible provider."""
        pass

    @classmethod
    def get_store_type(cls) -> str:
        """Return the store type identifier from configuration."""
        return cls.get_config().store_type

    @property
    def provider_prefixes(self) -> set:
        """Dynamically get all provider prefixes from registered stores."""
        prefixes = set()

        # Get prefixes from all registered S3-compatible stores
        for store_class in _S3_COMPATIBLE_STORES.values():
            config = store_class.get_config()
            prefixes.add(config.url_prefix)

        # Add hardcoded prefixes for non-S3-compatible stores
        prefixes.update({
            'gs://',  # GCS
            'https://',  # Azure
            'cos://',  # IBM COS
            'oci://',  # OCI
        })

        return prefixes

    def _validate(self):
        if self.source is not None and isinstance(self.source, str):
            if self.source.startswith(self.config.url_prefix):
                bucket_name, _ = self.config.split_path(self.source)
                assert self.name == bucket_name, (
                    f'{self.config.store_type} Bucket is specified as path, '
                    f'the name should be the same as {self.config.store_type} '
                    f'bucket.')
                # Only verify if this is NOT the same store type as the source
                if self.__class__.get_store_type() != self.config.store_type:
                    assert self.config.verify_bucket(self.name), (
                        f'Source specified as {self.source},'
                        f'a {self.config.store_type} '
                        f'bucket. {self.config.store_type} Bucket should exist.'
                    )
            elif self.source.startswith('gs://'):
                assert self.name == data_utils.split_gcs_path(self.source)[0], (
                    'GCS Bucket is specified as path, the name should be '
                    'the same as GCS bucket.')
                if not isinstance(self, GcsStore):
                    assert data_utils.verify_gcs_bucket(self.name), (
                        f'Source specified as {self.source}, a GCS bucket. ',
                        'GCS Bucket should exist.')
            elif data_utils.is_az_container_endpoint(self.source):
                storage_account_name, container_name, _ = (
                    data_utils.split_az_path(self.source))
                assert self.name == container_name, (
                    'Azure bucket is specified as path, the name should be '
                    'the same as Azure bucket.')
                if not isinstance(self, AzureBlobStore):
                    assert data_utils.verify_az_bucket(
                        storage_account_name, self.name
                    ), (f'Source specified as {self.source}, an Azure bucket. '
                        'Azure bucket should exist.')
            elif self.source.startswith('cos://'):
                assert self.name == data_utils.split_cos_path(self.source)[0], (
                    'COS Bucket is specified as path, the name should be '
                    'the same as COS bucket.')
                if not isinstance(self, IBMCosStore):
                    assert data_utils.verify_ibm_cos_bucket(self.name), (
                        f'Source specified as {self.source}, a COS bucket. ',
                        'COS Bucket should exist.')
            elif self.source.startswith('oci://'):
                raise NotImplementedError(
                    f'Moving data from OCI to {self.source} is ',
                    'currently not supported.')

        # Validate name
        self.name = self.validate_name(self.name)

        # Check if the storage is enabled
        if not _is_storage_cloud_enabled(self.config.cloud_name):
            with ux_utils.print_exception_no_traceback():
                raise exceptions.ResourcesUnavailableError(
                    f'Storage "store: {self.config.store_type.lower()}" '
                    f'specified, but '
                    f'{self.config.cloud_name} access is disabled. '
                    'To fix, enable '
                    f'{self.config.cloud_name} by running `sky check`.')

    @classmethod
    def validate_name(cls, name: str) -> str:
        """Validates the name of the S3 store.

        Source for rules: https://docs.aws.amazon.com/AmazonS3/latest/userguide/bucketnamingrules.html # pylint: disable=line-too-long
        """

        def _raise_no_traceback_name_error(err_str):
            with ux_utils.print_exception_no_traceback():
                raise exceptions.StorageNameError(err_str)

        if name is not None and isinstance(name, str):
            if not 3 <= len(name) <= 63:
                _raise_no_traceback_name_error(
                    f'Invalid store name: name {name} must be between 3 (min) '
                    'and 63 (max) characters long.')

            # Check for valid characters and start/end with a letter or number
            pattern = r'^[a-z0-9][-a-z0-9.]*[a-z0-9]$'
            if not re.match(pattern, name):
                _raise_no_traceback_name_error(
                    f'Invalid store name: name {name} can consist only of '
                    'lowercase letters, numbers, dots (.), and hyphens (-). '
                    'It must begin and end with a letter or number.')

            # Check for two adjacent periods
            if '..' in name:
                _raise_no_traceback_name_error(
                    f'Invalid store name: name {name} must not contain '
                    'two adjacent periods.')

            # Check for IP address format
            ip_pattern = r'^(?:\d{1,3}\.){3}\d{1,3}$'
            if re.match(ip_pattern, name):
                _raise_no_traceback_name_error(
                    f'Invalid store name: name {name} must not be formatted as '
                    'an IP address (for example, 192.168.5.4).')

            # Check for 'xn--' prefix
            if name.startswith('xn--'):
                _raise_no_traceback_name_error(
                    f'Invalid store name: name {name} must not start with the '
                    'prefix "xn--".')

            # Check for '-s3alias' suffix
            if name.endswith('-s3alias'):
                _raise_no_traceback_name_error(
                    f'Invalid store name: name {name} must not end with the '
                    'suffix "-s3alias".')

            # Check for '--ol-s3' suffix
            if name.endswith('--ol-s3'):
                _raise_no_traceback_name_error(
                    f'Invalid store name: name {name} must not end with the '
                    'suffix "--ol-s3".')
        else:
            _raise_no_traceback_name_error('Store name must be specified.')
        return name

    def initialize(self):
        """Initializes the S3 store object on the cloud.

        Initialization involves fetching bucket if exists, or creating it if
        it does not.

        Raises:
          StorageBucketCreateError: If bucket creation fails
          StorageBucketGetError: If fetching existing bucket fails
          StorageInitError: If general initialization fails.
        """
        self.client = self.config.client_factory(self.region)
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
                if self._is_same_provider_source():
                    pass  # No transfer needed
                elif self._needs_cross_provider_transfer():
                    self._transfer_from_other_provider()
                else:
                    self.batch_aws_rsync([self.source])
        except exceptions.StorageUploadError:
            raise
        except Exception as e:
            raise exceptions.StorageUploadError(
                f'Upload failed for store {self.name}') from e

    def _is_same_provider_source(self) -> bool:
        """Check if source is from the same provider."""
        return isinstance(self.source, str) and self.source.startswith(
            self.config.url_prefix)

    def _needs_cross_provider_transfer(self) -> bool:
        """Check if source needs cross-provider transfer."""
        if not isinstance(self.source, str):
            return False
        return any(
            self.source.startswith(prefix) for prefix in self.provider_prefixes)

    def _detect_source_type(self) -> str:
        """Detect the source provider type from URL."""
        if not isinstance(self.source, str):
            return 'unknown'

        for provider in self.provider_prefixes:
            if self.source.startswith(provider):
                return provider[:-len('://')]
        return ''

    def _transfer_from_other_provider(self):
        """Transfer data from another cloud to this S3-compatible store."""
        source_type = self._detect_source_type()
        target_type = self.config.store_type.lower()

        if hasattr(data_transfer, f'{source_type}_to_{target_type}'):
            transfer_func = getattr(data_transfer,
                                    f'{source_type}_to_{target_type}')
            transfer_func(self.name, self.name)
        else:
            with ux_utils.print_exception_no_traceback():
                raise NotImplementedError(
                    f'Transfer from {source_type} to {target_type} '
                    'is not yet supported.')

    def delete(self) -> None:
        """Delete the bucket or sub-path."""
        if self._bucket_sub_path is not None and not self.is_sky_managed:
            return self._delete_sub_path()

        deleted_by_skypilot = self._delete_bucket(self.name)
        provider = self.config.store_type
        if deleted_by_skypilot:
            msg_str = f'Deleted {provider} bucket {self.name}.'
        else:
            msg_str = f'{provider} bucket {self.name} may have been deleted ' \
                      f'externally. Removing from local state.'
        logger.info(f'{colorama.Fore.GREEN}{msg_str}{colorama.Style.RESET_ALL}')

    def get_handle(self) -> StorageHandle:
        """Get storage handle using provider's resource factory."""
        return self.config.resource_factory(self.name)

    def _download_file(self, remote_path: str, local_path: str) -> None:
        """Download file using S3 API."""
        self.bucket.download_file(remote_path, local_path)

    def mount_command(self, mount_path: str) -> str:
        """Get mount command using provider's mount factory."""
        if self.config.mount_cmd_factory is None:
            raise exceptions.NotSupportedError(
                f'Mounting not supported for {self.config.store_type}')

        install_cmd = mounting_utils.get_s3_mount_install_cmd()
        mount_cmd = self.config.mount_cmd_factory(self.bucket.name, mount_path,
                                                  self._bucket_sub_path)
        return mounting_utils.get_mounting_command(mount_path, install_cmd,
                                                   mount_cmd)

    def mount_cached_command(self, mount_path: str) -> str:
        """Get cached mount command. Can be overridden by subclasses."""
        if self.config.mount_cached_cmd_factory is None:
            raise exceptions.NotSupportedError(
                f'Cached mounting not supported for {self.config.store_type}')

        install_cmd = mounting_utils.get_rclone_install_cmd()
        mount_cmd = self.config.mount_cached_cmd_factory(
            self.bucket.name, mount_path, self._bucket_sub_path)
        return mounting_utils.get_mounting_command(mount_path, install_cmd,
                                                   mount_cmd)

    def batch_aws_rsync(self,
                        source_path_list: List[Path],
                        create_dirs: bool = False) -> None:
        """Generic S3-compatible rsync using AWS CLI."""
        sub_path = f'/{self._bucket_sub_path}' if self._bucket_sub_path else ''

        def get_file_sync_command(base_dir_path, file_names):
            includes = ' '.join([
                f'--include {shlex.quote(file_name)}'
                for file_name in file_names
            ])
            base_dir_path = shlex.quote(base_dir_path)

            # Build AWS CLI command with provider-specific configuration
            cmd_parts = ['aws s3 sync --no-follow-symlinks --exclude="*"']
            cmd_parts.append(f'{includes} {base_dir_path}')
            cmd_parts.append(f's3://{self.name}{sub_path}')

            # Add provider-specific arguments
            if self.config.get_endpoint_url:
                cmd_parts.append(
                    f'--endpoint-url {self.config.get_endpoint_url()}')
            if self.config.aws_profile:
                cmd_parts.append(f'--profile={self.config.aws_profile}')
            if self.config.extra_cli_args:
                cmd_parts.extend(self.config.extra_cli_args)

            # Handle credentials file via environment
            cmd = ' '.join(cmd_parts)
            if self.config.credentials_file:
                cmd = 'AWS_SHARED_CREDENTIALS_FILE=' + \
                f'{self.config.credentials_file} {cmd}'

            return cmd

        def get_dir_sync_command(src_dir_path, dest_dir_name):
            # we exclude .git directory from the sync
            excluded_list = storage_utils.get_excluded_files(src_dir_path)
            excluded_list.append('.git/*')

            # Process exclusion patterns to make them work correctly with aws
            # s3 sync - this logic is from S3Store2 to ensure compatibility
            processed_excludes = []
            for excluded_path in excluded_list:
                # Check if the path is a directory exclusion pattern
                # For AWS S3 sync, directory patterns need to end with "/*" to
                # exclude all contents
                if (excluded_path.endswith('/') or os.path.isdir(
                        os.path.join(src_dir_path, excluded_path.rstrip('/')))):
                    # Remove any trailing slash and add '/*' to exclude all
                    # contents
                    processed_excludes.append(f'{excluded_path.rstrip("/")}/*')
                else:
                    processed_excludes.append(excluded_path)

            excludes = ' '.join([
                f'--exclude {shlex.quote(file_name)}'
                for file_name in processed_excludes
            ])
            src_dir_path = shlex.quote(src_dir_path)

            cmd_parts = ['aws s3 sync --no-follow-symlinks']
            cmd_parts.append(f'{excludes} {src_dir_path}')
            cmd_parts.append(f's3://{self.name}{sub_path}/{dest_dir_name}')

            if self.config.get_endpoint_url:
                cmd_parts.append(
                    f'--endpoint-url {self.config.get_endpoint_url()}')
            if self.config.aws_profile:
                cmd_parts.append(f'--profile={self.config.aws_profile}')
            if self.config.extra_cli_args:
                cmd_parts.extend(self.config.extra_cli_args)

            cmd = ' '.join(cmd_parts)
            if self.config.credentials_file:
                cmd = 'AWS_SHARED_CREDENTIALS_FILE=' + \
                f'{self.config.credentials_file} {cmd}'

            return cmd

        # Generate message for upload
        if len(source_path_list) > 1:
            source_message = f'{len(source_path_list)} paths'
        else:
            source_message = source_path_list[0]

        provider_prefix = self.config.url_prefix
        log_path = sky_logging.generate_tmp_logging_file_path(
            _STORAGE_LOG_FILE_NAME)
        sync_path = (f'{source_message} -> '
                     f'{provider_prefix}{self.name}{sub_path}/')

        with rich_utils.safe_status(
                ux_utils.spinner_message(f'Syncing {sync_path}',
                                         log_path=log_path)):
            data_utils.parallel_upload(
                source_path_list,
                get_file_sync_command,
                get_dir_sync_command,
                log_path,
                self.name,
                self.config.access_denied_message,
                create_dirs=create_dirs,
                max_concurrent_uploads=_MAX_CONCURRENT_UPLOADS)

        logger.info(
            ux_utils.finishing_message(f'Storage synced: {sync_path}',
                                       log_path))

    def _get_bucket(self) -> Tuple[StorageHandle, bool]:
        """Get or create bucket using S3 API."""
        bucket = self.config.resource_factory(self.name)

        try:
            # Try Public bucket case.
            self.client.head_bucket(Bucket=self.name)
            self._validate_existing_bucket()
            return bucket, False
        except aws.botocore_exceptions().ClientError as e:
            error_code = e.response['Error']['Code']
            if error_code == '403':
                command = f'aws s3 ls s3://{self.name}'
                if self.config.aws_profile:
                    command += f' --profile={self.config.aws_profile}'
                if self.config.get_endpoint_url:
                    command += f' --endpoint-url '\
                        f'{self.config.get_endpoint_url()}'
                if self.config.credentials_file:
                    command = (f'AWS_SHARED_CREDENTIALS_FILE='
                               f'{self.config.credentials_file} {command}')
                with ux_utils.print_exception_no_traceback():
                    raise exceptions.StorageBucketGetError(
                        _BUCKET_FAIL_TO_CONNECT_MESSAGE.format(name=self.name) +
                        f' To debug, consider running `{command}`.') from e

        if isinstance(self.source, str) and self.source.startswith(
                self.config.url_prefix):
            with ux_utils.print_exception_no_traceback():
                raise exceptions.StorageBucketGetError(
                    'Attempted to use a non-existent bucket as a source: '
                    f'{self.source}.')

        # If bucket cannot be found, create it if needed
        if self.sync_on_reconstruction:
            bucket = self._create_bucket(self.name)
            return bucket, True
        else:
            raise exceptions.StorageExternalDeletionError(
                'Attempted to fetch a non-existent bucket: '
                f'{self.name}')

    def _create_bucket(self, bucket_name: str) -> StorageHandle:
        """Create bucket using S3 API."""
        try:
            create_bucket_config: Dict[str, Any] = {'Bucket': bucket_name}
            if self.region is not None and self.region != 'us-east-1':
                create_bucket_config['CreateBucketConfiguration'] = {
                    'LocationConstraint': self.region
                }
            self.client.create_bucket(**create_bucket_config)
            logger.info(
                f'  {colorama.Style.DIM}Created S3 bucket {bucket_name!r} in '
                f'{self.region or "us-east-1"}{colorama.Style.RESET_ALL}')

            # Add AWS tags configured in config.yaml to the bucket.
            # This is useful for cost tracking and external cleanup.
            bucket_tags = skypilot_config.get_effective_region_config(
                cloud=self.config.cloud_name,
                region=None,
                keys=('labels',),
                default_value={})
            if bucket_tags:
                self.client.put_bucket_tagging(
                    Bucket=bucket_name,
                    Tagging={
                        'TagSet': [{
                            'Key': k,
                            'Value': v
                        } for k, v in bucket_tags.items()]
                    })
        except aws.botocore_exceptions().ClientError as e:
            with ux_utils.print_exception_no_traceback():
                raise exceptions.StorageBucketCreateError(
                    f'Attempted to create a bucket {self.name} but failed.'
                ) from e
        return self.config.resource_factory(bucket_name)

    def _delete_bucket(self, bucket_name: str) -> bool:
        """Delete bucket using AWS CLI."""
        cmd_parts = [f'aws s3 rb s3://{bucket_name} --force']

        if self.config.aws_profile:
            cmd_parts.append(f'--profile={self.config.aws_profile}')
        if self.config.get_endpoint_url:
            cmd_parts.append(f'--endpoint-url {self.config.get_endpoint_url()}')

        remove_command = ' '.join(cmd_parts)

        if self.config.credentials_file:
            remove_command = (f'AWS_SHARED_CREDENTIALS_FILE='
                              f'{self.config.credentials_file} '
                              f'{remove_command}')

        return self._execute_remove_command(
            remove_command, bucket_name,
            f'Deleting {self.config.store_type} bucket {bucket_name}',
            (f'Failed to delete {self.config.store_type} bucket '
             f'{bucket_name}.'))

    def _execute_remove_command(self, command: str, bucket_name: str,
                                hint_operating: str, hint_failed: str) -> bool:
        """Execute bucket removal command."""
        try:
            with rich_utils.safe_status(
                    ux_utils.spinner_message(hint_operating)):
                subprocess.check_output(command.split(' '),
                                        stderr=subprocess.STDOUT)
        except subprocess.CalledProcessError as e:
            if 'NoSuchBucket' in e.output.decode('utf-8'):
                logger.debug(
                    _BUCKET_EXTERNALLY_DELETED_DEBUG_MESSAGE.format(
                        bucket_name=bucket_name))
                return False
            else:
                with ux_utils.print_exception_no_traceback():
                    raise exceptions.StorageBucketDeleteError(
                        f'{hint_failed}'
                        f'Detailed error: {e.output}')
        return True

    def _delete_sub_path(self) -> None:
        """Remove objects from the sub path in the bucket."""
        assert self._bucket_sub_path is not None, 'bucket_sub_path is not set'
        deleted_by_skypilot = self._delete_bucket_sub_path(
            self.name, self._bucket_sub_path)
        provider = self.config.store_type
        if deleted_by_skypilot:
            msg_str = (f'Removed objects from {provider} bucket '
                       f'{self.name}/{self._bucket_sub_path}.')
        else:
            msg_str = (f'Failed to remove objects from {provider} bucket '
                       f'{self.name}/{self._bucket_sub_path}.')
        logger.info(f'{colorama.Fore.GREEN}{msg_str}{colorama.Style.RESET_ALL}')

    def _delete_bucket_sub_path(self, bucket_name: str, sub_path: str) -> bool:
        """Delete objects in the sub path from the bucket."""
        cmd_parts = [f'aws s3 rm s3://{bucket_name}/{sub_path}/ --recursive']

        if self.config.aws_profile:
            cmd_parts.append(f'--profile={self.config.aws_profile}')
        if self.config.get_endpoint_url:
            cmd_parts.append(f'--endpoint-url {self.config.get_endpoint_url()}')

        remove_command = ' '.join(cmd_parts)

        if self.config.credentials_file:
            remove_command = (f'AWS_SHARED_CREDENTIALS_FILE='
                              f'{self.config.credentials_file} '
                              f'{remove_command}')

        return self._execute_remove_command(
            remove_command, bucket_name,
            (f'Removing objects from {self.config.store_type} bucket '
             f'{bucket_name}/{sub_path}'),
            (f'Failed to remove objects from {self.config.store_type} '
             f'bucket {bucket_name}/{sub_path}.'))


class GcsStore(AbstractStore):
    """GcsStore inherits from Storage Object and represents the backend
    for GCS buckets.
    """

    _ACCESS_DENIED_MESSAGE = 'AccessDeniedException'

    def __init__(self,
                 name: str,
                 source: str,
                 region: Optional[str] = 'us-central1',
                 is_sky_managed: Optional[bool] = None,
                 sync_on_reconstruction: Optional[bool] = True,
                 _bucket_sub_path: Optional[str] = None):
        self.client: 'storage.Client'
        self.bucket: StorageHandle
        super().__init__(name, source, region, is_sky_managed,
                         sync_on_reconstruction, _bucket_sub_path)

    def _validate(self):
        if self.source is not None and isinstance(self.source, str):
            if self.source.startswith('s3://'):
                assert self.name == data_utils.split_s3_path(self.source)[0], (
                    'S3 Bucket is specified as path, the name should be the'
                    ' same as S3 bucket.')
                assert data_utils.verify_s3_bucket(self.name), (
                    f'Source specified as {self.source}, an S3 bucket. ',
                    'S3 Bucket should exist.')
            elif self.source.startswith('gs://'):
                assert self.name == data_utils.split_gcs_path(self.source)[0], (
                    'GCS Bucket is specified as path, the name should be '
                    'the same as GCS bucket.')
            elif data_utils.is_az_container_endpoint(self.source):
                storage_account_name, container_name, _ = (
                    data_utils.split_az_path(self.source))
                assert self.name == container_name, (
                    'Azure bucket is specified as path, the name should be '
                    'the same as Azure bucket.')
                assert data_utils.verify_az_bucket(
                    storage_account_name, self.name), (
                        f'Source specified as {self.source}, an Azure bucket. '
                        'Azure bucket should exist.')
            elif self.source.startswith('r2://'):
                assert self.name == data_utils.split_r2_path(self.source)[0], (
                    'R2 Bucket is specified as path, the name should be '
                    'the same as R2 bucket.')
                assert data_utils.verify_r2_bucket(self.name), (
                    f'Source specified as {self.source}, a R2 bucket. ',
                    'R2 Bucket should exist.')
            elif self.source.startswith('nebius://'):
                assert self.name == data_utils.split_nebius_path(
                    self.source)[0], (
                        'Nebius Object Storage is specified as path, the name '
                        'should be the same as R2 bucket.')
                assert data_utils.verify_nebius_bucket(self.name), (
                    f'Source specified as {self.source}, a Nebius Object '
                    f'Storage bucket. Nebius Object Storage Bucket should '
                    f'exist.')
            elif self.source.startswith('cos://'):
                assert self.name == data_utils.split_cos_path(self.source)[0], (
                    'COS Bucket is specified as path, the name should be '
                    'the same as COS bucket.')
                assert data_utils.verify_ibm_cos_bucket(self.name), (
                    f'Source specified as {self.source}, a COS bucket. ',
                    'COS Bucket should exist.')
            elif self.source.startswith('oci://'):
                raise NotImplementedError(
                    'Moving data from OCI to GCS is currently not supported.')
        # Validate name
        self.name = self.validate_name(self.name)
        # Check if the storage is enabled
        if not _is_storage_cloud_enabled(str(clouds.GCP())):
            with ux_utils.print_exception_no_traceback():
                raise exceptions.ResourcesUnavailableError(
                    'Storage \'store: gcs\' specified, but '
                    'GCP access is disabled. To fix, enable '
                    'GCP by running `sky check`. '
                    'More info: https://docs.skypilot.co/en/latest/getting-started/installation.html.')  # pylint: disable=line-too-long

    @classmethod
    def validate_name(cls, name: str) -> str:
        """Validates the name of the GCS store.

        Source for rules: https://cloud.google.com/storage/docs/buckets#naming
        """

        def _raise_no_traceback_name_error(err_str):
            with ux_utils.print_exception_no_traceback():
                raise exceptions.StorageNameError(err_str)

        if name is not None and isinstance(name, str):
            # Check for overall length
            if not 3 <= len(name) <= 222:
                _raise_no_traceback_name_error(
                    f'Invalid store name: name {name} must contain 3-222 '
                    'characters.')

            # Check for valid characters and start/end with a number or letter
            pattern = r'^[a-z0-9][-a-z0-9._]*[a-z0-9]$'
            if not re.match(pattern, name):
                _raise_no_traceback_name_error(
                    f'Invalid store name: name {name} can only contain '
                    'lowercase letters, numeric characters, dashes (-), '
                    'underscores (_), and dots (.). Spaces are not allowed. '
                    'Names must start and end with a number or letter.')

            # Check for 'goog' prefix and 'google' in the name
            if name.startswith('goog') or any(
                    s in name
                    for s in ['google', 'g00gle', 'go0gle', 'g0ogle']):
                _raise_no_traceback_name_error(
                    f'Invalid store name: name {name} cannot begin with the '
                    '"goog" prefix or contain "google" in various forms.')

            # Check for dot-separated components length
            components = name.split('.')
            if any(len(component) > 63 for component in components):
                _raise_no_traceback_name_error(
                    'Invalid store name: Dot-separated components in name '
                    f'{name} can be no longer than 63 characters.')

            if '..' in name or '.-' in name or '-.' in name:
                _raise_no_traceback_name_error(
                    f'Invalid store name: name {name} must not contain two '
                    'adjacent periods or a dot next to a hyphen.')

            # Check for IP address format
            ip_pattern = r'^(?:\d{1,3}\.){3}\d{1,3}$'
            if re.match(ip_pattern, name):
                _raise_no_traceback_name_error(
                    f'Invalid store name: name {name} cannot be represented as '
                    'an IP address in dotted-decimal notation '
                    '(for example, 192.168.5.4).')
        else:
            _raise_no_traceback_name_error('Store name must be specified.')
        return name

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
                elif self.source.startswith('r2://'):
                    self._transfer_to_gcs()
                elif self.source.startswith('oci://'):
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
        if self._bucket_sub_path is not None and not self.is_sky_managed:
            return self._delete_sub_path()

        deleted_by_skypilot = self._delete_gcs_bucket(self.name)
        if deleted_by_skypilot:
            msg_str = f'Deleted GCS bucket {self.name}.'
        else:
            msg_str = f'GCS bucket {self.name} may have been deleted ' \
                      f'externally. Removing from local state.'
        logger.info(f'{colorama.Fore.GREEN}{msg_str}'
                    f'{colorama.Style.RESET_ALL}')

    def _delete_sub_path(self) -> None:
        assert self._bucket_sub_path is not None, 'bucket_sub_path is not set'
        deleted_by_skypilot = self._delete_gcs_bucket(self.name,
                                                      self._bucket_sub_path)
        if deleted_by_skypilot:
            msg_str = f'Deleted objects in GCS bucket ' \
                      f'{self.name}/{self._bucket_sub_path}.'
        else:
            msg_str = f'GCS bucket {self.name} may have ' \
                      'been deleted externally.'
        logger.info(f'{colorama.Fore.GREEN}{msg_str}'
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
            source_message = source_path_list[0]

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
        gsutil_alias, alias_gen = data_utils.get_gsutil_command()
        sub_path = (f'/{self._bucket_sub_path}'
                    if self._bucket_sub_path else '')
        sync_command = (f'{alias_gen}; echo "{copy_list}" | {gsutil_alias} '
                        f'cp -e -n -r -I gs://{self.name}{sub_path}')
        log_path = sky_logging.generate_tmp_logging_file_path(
            _STORAGE_LOG_FILE_NAME)
        sync_path = f'{source_message} -> gs://{self.name}{sub_path}/'
        with rich_utils.safe_status(
                ux_utils.spinner_message(f'Syncing {sync_path}',
                                         log_path=log_path)):
            data_utils.run_upload_cli(sync_command,
                                      self._ACCESS_DENIED_MESSAGE,
                                      bucket_name=self.name,
                                      log_path=log_path)
        logger.info(
            ux_utils.finishing_message(f'Storage synced: {sync_path}',
                                       log_path))

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
        sub_path = (f'/{self._bucket_sub_path}'
                    if self._bucket_sub_path else '')

        def get_file_sync_command(base_dir_path, file_names):
            sync_format = '|'.join(file_names)
            gsutil_alias, alias_gen = data_utils.get_gsutil_command()
            base_dir_path = shlex.quote(base_dir_path)
            sync_command = (f'{alias_gen}; {gsutil_alias} '
                            f'rsync -e -x \'^(?!{sync_format}$).*\' '
                            f'{base_dir_path} gs://{self.name}{sub_path}')
            return sync_command

        def get_dir_sync_command(src_dir_path, dest_dir_name):
            excluded_list = storage_utils.get_excluded_files(src_dir_path)
            # we exclude .git directory from the sync
            excluded_list.append(r'^\.git/.*$')
            excludes = '|'.join(excluded_list)
            gsutil_alias, alias_gen = data_utils.get_gsutil_command()
            src_dir_path = shlex.quote(src_dir_path)
            sync_command = (f'{alias_gen}; {gsutil_alias} '
                            f'rsync -e -r -x \'({excludes})\' {src_dir_path} '
                            f'gs://{self.name}{sub_path}/{dest_dir_name}')
            return sync_command

        # Generate message for upload
        if len(source_path_list) > 1:
            source_message = f'{len(source_path_list)} paths'
        else:
            source_message = source_path_list[0]

        log_path = sky_logging.generate_tmp_logging_file_path(
            _STORAGE_LOG_FILE_NAME)
        sync_path = f'{source_message} -> gs://{self.name}{sub_path}/'
        with rich_utils.safe_status(
                ux_utils.spinner_message(f'Syncing {sync_path}',
                                         log_path=log_path)):
            data_utils.parallel_upload(
                source_path_list,
                get_file_sync_command,
                get_dir_sync_command,
                log_path,
                self.name,
                self._ACCESS_DENIED_MESSAGE,
                create_dirs=create_dirs,
                max_concurrent_uploads=_MAX_CONCURRENT_UPLOADS)
        logger.info(
            ux_utils.finishing_message(f'Storage synced: {sync_path}',
                                       log_path))

    def _transfer_to_gcs(self) -> None:
        if isinstance(self.source, str) and self.source.startswith('s3://'):
            data_transfer.s3_to_gcs(self.name, self.name)
        elif isinstance(self.source, str) and self.source.startswith('r2://'):
            data_transfer.r2_to_gcs(self.name, self.name)

    def _get_bucket(self) -> Tuple[StorageHandle, bool]:
        """Obtains the GCS bucket.
        If the bucket exists, this method will connect to the bucket.

        If the bucket does not exist, there are three cases:
          1) Raise an error if the bucket source starts with gs://
          2) Return None if bucket has been externally deleted and
             sync_on_reconstruction is False
          3) Create and return a new bucket otherwise

        Raises:
            StorageSpecError: If externally created bucket is attempted to be
                mounted without specifying storage source.
            StorageBucketCreateError: If creating the bucket fails
            StorageBucketGetError: If fetching a bucket fails
            StorageExternalDeletionError: If externally deleted storage is
                attempted to be fetched while reconstructing the storage for
                'sky storage delete' or 'sky start'
        """
        try:
            bucket = self.client.get_bucket(self.name)
            self._validate_existing_bucket()
            return bucket, False
        except gcp.not_found_exception() as e:
            if isinstance(self.source, str) and self.source.startswith('gs://'):
                with ux_utils.print_exception_no_traceback():
                    raise exceptions.StorageBucketGetError(
                        'Attempted to use a non-existent bucket as a source: '
                        f'{self.source}') from e
            else:
                # If bucket cannot be found (i.e., does not exist), it is to be
                # created by Sky. However, creation is skipped if Store object
                # is being reconstructed for deletion or re-mount with
                # sky start, and error is raised instead.
                if self.sync_on_reconstruction:
                    bucket = self._create_gcs_bucket(self.name, self.region)
                    return bucket, True
                else:
                    # This is raised when Storage object is reconstructed for
                    # sky storage delete or to re-mount Storages with sky start
                    # but the storage is already removed externally.
                    raise exceptions.StorageExternalDeletionError(
                        'Attempted to fetch a non-existent bucket: '
                        f'{self.name}') from e
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
                        f' To debug, consider running `{command}`.') from e

    def mount_command(self, mount_path: str) -> str:
        """Returns the command to mount the bucket to the mount_path.

        Uses gcsfuse to mount the bucket.

        Args:
          mount_path: str; Path to mount the bucket to.
        """
        install_cmd = mounting_utils.get_gcs_mount_install_cmd()
        mount_cmd = mounting_utils.get_gcs_mount_cmd(self.bucket.name,
                                                     mount_path,
                                                     self._bucket_sub_path)
        version_check_cmd = (
            f'gcsfuse --version | grep -q {mounting_utils.GCSFUSE_VERSION}')
        return mounting_utils.get_mounting_command(mount_path, install_cmd,
                                                   mount_cmd, version_check_cmd)

    def mount_cached_command(self, mount_path: str) -> str:
        install_cmd = mounting_utils.get_rclone_install_cmd()
        rclone_profile_name = (
            data_utils.Rclone.RcloneStores.GCS.get_profile_name(self.name))
        rclone_config = data_utils.Rclone.RcloneStores.GCS.get_config(
            rclone_profile_name=rclone_profile_name)
        mount_cached_cmd = mounting_utils.get_mount_cached_cmd(
            rclone_config, rclone_profile_name, self.bucket.name, mount_path)
        return mounting_utils.get_mounting_command(mount_path, install_cmd,
                                                   mount_cached_cmd)

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
            f'  {colorama.Style.DIM}Created GCS bucket {new_bucket.name!r} in '
            f'{new_bucket.location} with storage class '
            f'{new_bucket.storage_class}{colorama.Style.RESET_ALL}')
        return new_bucket

    def _delete_gcs_bucket(
        self,
        bucket_name: str,
        # pylint: disable=invalid-name
        _bucket_sub_path: Optional[str] = None
    ) -> bool:
        """Deletes objects in GCS bucket

        Args:
          bucket_name: str; Name of bucket
          _bucket_sub_path: str; Sub path in the bucket, if provided only
            objects in the sub path will be deleted, else the whole bucket will
            be deleted

        Returns:
         bool; True if bucket was deleted, False if it was deleted externally.

        Raises:
            StorageBucketDeleteError: If deleting the bucket fails.
            PermissionError: If the bucket is external and the user is not
                allowed to delete it.
        """
        if _bucket_sub_path is not None:
            command_suffix = f'/{_bucket_sub_path}'
            hint_text = 'objects in '
        else:
            command_suffix = ''
            hint_text = ''
        with rich_utils.safe_status(
                ux_utils.spinner_message(
                    f'Deleting {hint_text}GCS bucket '
                    f'[green]{bucket_name}{command_suffix}[/]')):
            try:
                self.client.get_bucket(bucket_name)
            except gcp.forbidden_exception() as e:
                # Try public bucket to see if bucket exists
                with ux_utils.print_exception_no_traceback():
                    raise PermissionError(
                        'External Bucket detected. User not allowed to delete '
                        'external bucket.') from e
            except gcp.not_found_exception():
                # If bucket does not exist, it may have been deleted externally.
                # Do a no-op in that case.
                logger.debug(
                    _BUCKET_EXTERNALLY_DELETED_DEBUG_MESSAGE.format(
                        bucket_name=bucket_name))
                return False
            try:
                gsutil_alias, alias_gen = data_utils.get_gsutil_command()
                remove_obj_command = (
                    f'{alias_gen};{gsutil_alias} '
                    f'rm -r gs://{bucket_name}{command_suffix}')
                subprocess.check_output(remove_obj_command,
                                        stderr=subprocess.STDOUT,
                                        shell=True,
                                        executable='/bin/bash')
                return True
            except subprocess.CalledProcessError as e:
                with ux_utils.print_exception_no_traceback():
                    raise exceptions.StorageBucketDeleteError(
                        f'Failed to delete {hint_text}GCS bucket '
                        f'{bucket_name}{command_suffix}.'
                        f'Detailed error: {e.output}')


class AzureBlobStore(AbstractStore):
    """Represents the backend for Azure Blob Storage Container."""

    _ACCESS_DENIED_MESSAGE = 'Access Denied'
    DEFAULT_RESOURCE_GROUP_NAME = 'sky{user_hash}'
    # Unlike resource group names, which only need to be unique within the
    # subscription, storage account names must be globally unique across all of
    # Azure users. Hence, the storage account name includes the subscription
    # hash as well to ensure its uniqueness.
    DEFAULT_STORAGE_ACCOUNT_NAME = (
        'sky{region_hash}{user_hash}{subscription_hash}')
    _SUBSCRIPTION_HASH_LENGTH = 4
    _REGION_HASH_LENGTH = 4

    class AzureBlobStoreMetadata(AbstractStore.StoreMetadata):
        """A pickle-able representation of Azure Blob Store.

        Allows store objects to be written to and reconstructed from
        global_user_state.
        """

        def __init__(self,
                     *,
                     name: str,
                     storage_account_name: str,
                     source: Optional[SourceType],
                     region: Optional[str] = None,
                     is_sky_managed: Optional[bool] = None):
            self.storage_account_name = storage_account_name
            super().__init__(name=name,
                             source=source,
                             region=region,
                             is_sky_managed=is_sky_managed)

        def __repr__(self):
            return (f'AzureBlobStoreMetadata('
                    f'\n\tname={self.name},'
                    f'\n\tstorage_account_name={self.storage_account_name},'
                    f'\n\tsource={self.source},'
                    f'\n\tregion={self.region},'
                    f'\n\tis_sky_managed={self.is_sky_managed})')

    def __init__(self,
                 name: str,
                 source: str,
                 storage_account_name: str = '',
                 region: Optional[str] = 'eastus',
                 is_sky_managed: Optional[bool] = None,
                 sync_on_reconstruction: bool = True,
                 _bucket_sub_path: Optional[str] = None):
        self.storage_client: 'storage.Client'
        self.resource_client: 'storage.Client'
        self.container_name: str
        # storage_account_name is not None when initializing only
        # when it is being reconstructed from the handle(metadata).
        self.storage_account_name = storage_account_name
        self.storage_account_key: Optional[str] = None
        self.resource_group_name: Optional[str] = None
        if region is None:
            region = 'eastus'
        super().__init__(name, source, region, is_sky_managed,
                         sync_on_reconstruction, _bucket_sub_path)

    @classmethod
    def from_metadata(cls, metadata: AbstractStore.StoreMetadata,
                      **override_args) -> 'AzureBlobStore':
        """Creates AzureBlobStore from a AzureBlobStoreMetadata object.

        Used when reconstructing Storage and Store objects from
        global_user_state.

        Args:
            metadata: Metadata object containing AzureBlobStore information.

        Returns:
            An instance of AzureBlobStore.
        """
        assert isinstance(metadata, AzureBlobStore.AzureBlobStoreMetadata)
        # TODO: this needs to be kept in sync with the abstract
        # AbstractStore.from_metadata.
        return cls(
            name=override_args.get('name', metadata.name),
            storage_account_name=override_args.get(
                'storage_account', metadata.storage_account_name),
            source=override_args.get('source', metadata.source),
            region=override_args.get('region', metadata.region),
            is_sky_managed=override_args.get('is_sky_managed',
                                             metadata.is_sky_managed),
            sync_on_reconstruction=override_args.get('sync_on_reconstruction',
                                                     True),
            # Backward compatibility
            # TODO: remove the hasattr check after v0.11.0
            _bucket_sub_path=override_args.get(
                '_bucket_sub_path',
                metadata._bucket_sub_path  # pylint: disable=protected-access
            ) if hasattr(metadata, '_bucket_sub_path') else None)

    def get_metadata(self) -> AzureBlobStoreMetadata:
        return self.AzureBlobStoreMetadata(
            name=self.name,
            storage_account_name=self.storage_account_name,
            source=self.source,
            region=self.region,
            is_sky_managed=self.is_sky_managed)

    def _validate(self):
        if self.source is not None and isinstance(self.source, str):
            if self.source.startswith('s3://'):
                assert self.name == data_utils.split_s3_path(self.source)[0], (
                    'S3 Bucket is specified as path, the name should be the'
                    ' same as S3 bucket.')
                assert data_utils.verify_s3_bucket(self.name), (
                    f'Source specified as {self.source}, a S3 bucket. ',
                    'S3 Bucket should exist.')
            elif self.source.startswith('gs://'):
                assert self.name == data_utils.split_gcs_path(self.source)[0], (
                    'GCS Bucket is specified as path, the name should be '
                    'the same as GCS bucket.')
                assert data_utils.verify_gcs_bucket(self.name), (
                    f'Source specified as {self.source}, a GCS bucket. ',
                    'GCS Bucket should exist.')
            elif data_utils.is_az_container_endpoint(self.source):
                _, container_name, _ = data_utils.split_az_path(self.source)
                assert self.name == container_name, (
                    'Azure bucket is specified as path, the name should be '
                    'the same as Azure bucket.')
            elif self.source.startswith('r2://'):
                assert self.name == data_utils.split_r2_path(self.source)[0], (
                    'R2 Bucket is specified as path, the name should be '
                    'the same as R2 bucket.')
                assert data_utils.verify_r2_bucket(self.name), (
                    f'Source specified as {self.source}, a R2 bucket. ',
                    'R2 Bucket should exist.')
            elif self.source.startswith('nebius://'):
                assert self.name == data_utils.split_nebius_path(
                    self.source)[0], (
                        'Nebius Object Storage is specified as path, the name '
                        'should be the same as Nebius Object Storage bucket.')
                assert data_utils.verify_nebius_bucket(self.name), (
                    f'Source specified as {self.source}, a Nebius Object '
                    f'Storage bucket. Nebius Object Storage Bucket should '
                    f'exist.')
            elif self.source.startswith('cos://'):
                assert self.name == data_utils.split_cos_path(self.source)[0], (
                    'COS Bucket is specified as path, the name should be '
                    'the same as COS bucket.')
                assert data_utils.verify_ibm_cos_bucket(self.name), (
                    f'Source specified as {self.source}, a COS bucket. ',
                    'COS Bucket should exist.')
            elif self.source.startswith('oci://'):
                raise NotImplementedError(
                    'Moving data from OCI to AZureBlob is not supported.')
        # Validate name
        self.name = self.validate_name(self.name)

        # Check if the storage is enabled
        if not _is_storage_cloud_enabled(str(clouds.Azure())):
            with ux_utils.print_exception_no_traceback():
                raise exceptions.ResourcesUnavailableError(
                    'Storage "store: azure" specified, but '
                    'Azure access is disabled. To fix, enable '
                    'Azure by running `sky check`. More info: '
                    'https://docs.skypilot.co/en/latest/getting-started/installation.html.'  # pylint: disable=line-too-long
                )

    @classmethod
    def validate_name(cls, name: str) -> str:
        """Validates the name of the AZ Container.

        Source for rules: https://learn.microsoft.com/en-us/rest/api/storageservices/Naming-and-Referencing-Containers--Blobs--and-Metadata#container-names # pylint: disable=line-too-long

        Args:
            name: Name of the container

        Returns:
            Name of the container

        Raises:
            StorageNameError: if the given container name does not follow the
                naming convention
        """

        def _raise_no_traceback_name_error(err_str):
            with ux_utils.print_exception_no_traceback():
                raise exceptions.StorageNameError(err_str)

        if name is not None and isinstance(name, str):
            if not 3 <= len(name) <= 63:
                _raise_no_traceback_name_error(
                    f'Invalid store name: name {name} must be between 3 (min) '
                    'and 63 (max) characters long.')

            # Check for valid characters and start/end with a letter or number
            pattern = r'^[a-z0-9][-a-z0-9]*[a-z0-9]$'
            if not re.match(pattern, name):
                _raise_no_traceback_name_error(
                    f'Invalid store name: name {name} can consist only of '
                    'lowercase letters, numbers, and hyphens (-). '
                    'It must begin and end with a letter or number.')

            # Check for two adjacent hyphens
            if '--' in name:
                _raise_no_traceback_name_error(
                    f'Invalid store name: name {name} must not contain '
                    'two adjacent hyphens.')

        else:
            _raise_no_traceback_name_error('Store name must be specified.')
        return name

    def initialize(self):
        """Initializes the AZ Container object on the cloud.

        Initialization involves fetching container if exists, or creating it if
        it does not. Also, it checks for the existence of the storage account
        if provided by the user and the resource group is inferred from it.
        If not provided, both are created with a default naming conventions.

        Raises:
            StorageBucketCreateError: If container creation fails or storage
                account attempted to be created already exists.
            StorageBucketGetError: If fetching existing container fails.
            StorageInitError: If general initialization fails.
            NonExistentStorageAccountError: When storage account provided
                either through config.yaml or local db does not exist under
                user's subscription ID.
        """
        self.storage_client = data_utils.create_az_client('storage')
        self.resource_client = data_utils.create_az_client('resource')
        self._update_storage_account_name_and_resource()

        self.container_name, is_new_bucket = self._get_bucket()
        if self.is_sky_managed is None:
            # If is_sky_managed is not specified, then this is a new storage
            # object (i.e., did not exist in global_user_state) and we should
            # set the is_sky_managed property.
            # If is_sky_managed is specified, then we take no action.
            self.is_sky_managed = is_new_bucket

    def _update_storage_account_name_and_resource(self):
        self.storage_account_name, self.resource_group_name = (
            self._get_storage_account_and_resource_group())

        # resource_group_name is set to None when using non-sky-managed
        # public container or private container without authorization.
        if self.resource_group_name is not None:
            self.storage_account_key = data_utils.get_az_storage_account_key(
                self.storage_account_name, self.resource_group_name,
                self.storage_client, self.resource_client)

    def update_storage_attributes(self, **kwargs: Dict[str, Any]):
        assert 'storage_account_name' in kwargs, (
            'only storage_account_name supported')
        assert isinstance(kwargs['storage_account_name'],
                          str), ('storage_account_name must be a string')
        self.storage_account_name = kwargs['storage_account_name']
        self._update_storage_account_name_and_resource()

    @staticmethod
    def get_default_storage_account_name(region: Optional[str]) -> str:
        """Generates a unique default storage account name.

        The subscription ID is included to avoid conflicts when user switches
        subscriptions. The length of region_hash, user_hash, and
        subscription_hash are adjusted to ensure the storage account name
        adheres to the 24-character limit, as some region names can be very
        long. Using a 4-character hash for the region helps keep the name
        concise and prevents potential conflicts.
        Reference: https://learn.microsoft.com/en-us/azure/azure-resource-manager/management/resource-name-rules#microsoftstorage # pylint: disable=line-too-long

        Args:
            region: Name of the region to create the storage account/container.

        Returns:
            Name of the default storage account.
        """
        assert region is not None
        subscription_id = azure.get_subscription_id()
        subscription_hash_obj = hashlib.md5(subscription_id.encode('utf-8'))
        subscription_hash = subscription_hash_obj.hexdigest(
        )[:AzureBlobStore._SUBSCRIPTION_HASH_LENGTH]
        region_hash_obj = hashlib.md5(region.encode('utf-8'))
        region_hash = region_hash_obj.hexdigest()[:AzureBlobStore.
                                                  _REGION_HASH_LENGTH]

        storage_account_name = (
            AzureBlobStore.DEFAULT_STORAGE_ACCOUNT_NAME.format(
                region_hash=region_hash,
                user_hash=common_utils.get_user_hash(),
                subscription_hash=subscription_hash))

        return storage_account_name

    def _get_storage_account_and_resource_group(
            self) -> Tuple[str, Optional[str]]:
        """Get storage account and resource group to be used for AzureBlobStore

        Storage account name and resource group name of the container to be
        used for AzureBlobStore object is obtained from this function. These
        are determined by either through the metadata, source, config.yaml, or
        default name:

        1) If self.storage_account_name already has a set value, this means we
        are reconstructing the storage object using metadata from the local
        state.db to reuse sky managed storage.

        2) Users provide externally created non-sky managed storage endpoint
        as a source from task yaml. Then, storage account is read from it and
        the resource group is inferred from it.

        3) Users provide the storage account, which they want to create the
        sky managed storage, through config.yaml. Then, resource group is
        inferred from it.

        4) If none of the above are true, default naming conventions are used
        to create the resource group and storage account for the users.

        Returns:
            str: The storage account name.
            Optional[str]: The resource group name, or None if not found.

        Raises:
            StorageBucketCreateError: If storage account attempted to be
                created already exists.
            NonExistentStorageAccountError: When storage account provided
                either through config.yaml or local db does not exist under
                user's subscription ID.
        """
        # self.storage_account_name already has a value only when it is being
        # reconstructed with metadata from local db.
        if self.storage_account_name:
            resource_group_name = azure.get_az_resource_group(
                self.storage_account_name)
            if resource_group_name is None:
                # If the storage account does not exist, the containers under
                # the account does not exist as well.
                with ux_utils.print_exception_no_traceback():
                    raise exceptions.NonExistentStorageAccountError(
                        f'The storage account {self.storage_account_name!r} '
                        'read from local db does not exist under your '
                        'subscription ID. The account may have been externally'
                        ' deleted.')
            storage_account_name = self.storage_account_name
        # Using externally created container
        elif (isinstance(self.source, str) and
              data_utils.is_az_container_endpoint(self.source)):
            storage_account_name, container_name, _ = data_utils.split_az_path(
                self.source)
            assert self.name == container_name
            resource_group_name = azure.get_az_resource_group(
                storage_account_name)
        # Creates new resource group and storage account or use the
        # storage_account provided by the user through config.yaml
        else:
            config_storage_account = (
                skypilot_config.get_effective_region_config(
                    cloud='azure',
                    region=None,
                    keys=('storage_account',),
                    default_value=None))
            if config_storage_account is not None:
                # using user provided storage account from config.yaml
                storage_account_name = config_storage_account
                resource_group_name = azure.get_az_resource_group(
                    storage_account_name)
                # when the provided storage account does not exist under user's
                # subscription id.
                if resource_group_name is None:
                    with ux_utils.print_exception_no_traceback():
                        raise exceptions.NonExistentStorageAccountError(
                            'The storage account '
                            f'{storage_account_name!r} specified in '
                            'config.yaml does not exist under the user\'s '
                            'subscription ID. Provide a storage account '
                            'through config.yaml only when creating a '
                            'container under an already existing storage '
                            'account within your subscription ID.')
            else:
                # If storage account name is not provided from config, then
                # use default resource group and storage account names.
                storage_account_name = self.get_default_storage_account_name(
                    self.region)
                resource_group_name = (self.DEFAULT_RESOURCE_GROUP_NAME.format(
                    user_hash=common_utils.get_user_hash()))
                try:
                    # obtains detailed information about resource group under
                    # the user's subscription. Used to check if the name
                    # already exists
                    self.resource_client.resource_groups.get(
                        resource_group_name)
                except azure.exceptions().ResourceNotFoundError:
                    with rich_utils.safe_status(
                            ux_utils.spinner_message(
                                f'Setting up resource group: '
                                f'{resource_group_name}')):
                        self.resource_client.resource_groups.create_or_update(
                            resource_group_name, {'location': self.region})
                    logger.info('  Created Azure resource group '
                                f'{resource_group_name!r}.')
                # check if the storage account name already exists under the
                # given resource group name.
                try:
                    self.storage_client.storage_accounts.get_properties(
                        resource_group_name, storage_account_name)
                except azure.exceptions().ResourceNotFoundError:
                    with rich_utils.safe_status(
                            ux_utils.spinner_message(
                                f'Setting up storage account: '
                                f'{storage_account_name}')):
                        self._create_storage_account(resource_group_name,
                                                     storage_account_name)
                        # wait until new resource creation propagates to Azure.
                        time.sleep(1)
                    logger.info('  Created Azure storage account '
                                f'{storage_account_name!r}.')

        return storage_account_name, resource_group_name

    def _create_storage_account(self, resource_group_name: str,
                                storage_account_name: str) -> None:
        """Creates new storage account and assign Storage Blob Data Owner role.

        Args:
            resource_group_name: Name of the resource group which the storage
                account will be created under.
            storage_account_name: Name of the storage account to be created.

        Raises:
            StorageBucketCreateError: If storage account attempted to be
                created already exists or fails to assign role to the create
                storage account.
        """
        try:
            creation_response = (
                self.storage_client.storage_accounts.begin_create(
                    resource_group_name, storage_account_name, {
                        'sku': {
                            'name': 'Standard_GRS'
                        },
                        'kind': 'StorageV2',
                        'location': self.region,
                        'encryption': {
                            'services': {
                                'blob': {
                                    'key_type': 'Account',
                                    'enabled': True
                                }
                            },
                            'key_source': 'Microsoft.Storage'
                        },
                    }).result())
        except azure.exceptions().ResourceExistsError as error:
            with ux_utils.print_exception_no_traceback():
                raise exceptions.StorageBucketCreateError(
                    'Failed to create storage account '
                    f'{storage_account_name!r}. You may be '
                    'attempting to create a storage account '
                    'already being in use. Details: '
                    f'{common_utils.format_exception(error, use_bracket=True)}')

        # It may take some time for the created storage account to propagate
        # to Azure, we reattempt to assign the role for several times until
        # storage account creation fully propagates.
        role_assignment_start = time.time()
        retry = 0

        while (time.time() - role_assignment_start <
               constants.WAIT_FOR_STORAGE_ACCOUNT_CREATION):
            try:
                azure.assign_storage_account_iam_role(
                    storage_account_name=storage_account_name,
                    storage_account_id=creation_response.id)
                return
            except AttributeError as e:
                if 'signed_session' in str(e):
                    if retry % 5 == 0:
                        logger.info(
                            'Retrying role assignment due to propagation '
                            'delay of the newly created storage account. '
                            f'Retry count: {retry}.')
                    time.sleep(1)
                    retry += 1
                    continue
                with ux_utils.print_exception_no_traceback():
                    role_assignment_failure_error_msg = (
                        constants.ROLE_ASSIGNMENT_FAILURE_ERROR_MSG.format(
                            storage_account_name=storage_account_name))
                    raise exceptions.StorageBucketCreateError(
                        f'{role_assignment_failure_error_msg}'
                        'Details: '
                        f'{common_utils.format_exception(e, use_bracket=True)}')

    def upload(self):
        """Uploads source to store bucket.

        Upload must be called by the Storage handler - it is not called on
        Store initialization.

        Raises:
            StorageUploadError: if upload fails.
        """
        try:
            if isinstance(self.source, list):
                self.batch_az_blob_sync(self.source, create_dirs=True)
            elif self.source is not None:
                error_message = (
                    'Moving data directly from {cloud} to Azure is currently '
                    'not supported. Please specify a local source for the '
                    'storage object.')
                if data_utils.is_az_container_endpoint(self.source):
                    pass
                elif self.source.startswith('s3://'):
                    raise NotImplementedError(error_message.format('S3'))
                elif self.source.startswith('gs://'):
                    raise NotImplementedError(error_message.format('GCS'))
                elif self.source.startswith('r2://'):
                    raise NotImplementedError(error_message.format('R2'))
                elif self.source.startswith('cos://'):
                    raise NotImplementedError(error_message.format('IBM COS'))
                elif self.source.startswith('oci://'):
                    raise NotImplementedError(error_message.format('OCI'))
                elif self.source.startswith('nebius://'):
                    raise NotImplementedError(error_message.format('NEBIUS'))
                else:
                    self.batch_az_blob_sync([self.source])
        except exceptions.StorageUploadError:
            raise
        except Exception as e:
            raise exceptions.StorageUploadError(
                f'Upload failed for store {self.name}') from e

    def delete(self) -> None:
        """Deletes the storage."""
        if self._bucket_sub_path is not None and not self.is_sky_managed:
            return self._delete_sub_path()

        deleted_by_skypilot = self._delete_az_bucket(self.name)
        if deleted_by_skypilot:
            msg_str = (f'Deleted AZ Container {self.name!r} under storage '
                       f'account {self.storage_account_name!r}.')
        else:
            msg_str = (f'AZ Container {self.name} may have '
                       'been deleted externally. Removing from local state.')
        logger.info(f'{colorama.Fore.GREEN}{msg_str}'
                    f'{colorama.Style.RESET_ALL}')

    def _delete_sub_path(self) -> None:
        assert self._bucket_sub_path is not None, 'bucket_sub_path is not set'
        try:
            container_url = data_utils.AZURE_CONTAINER_URL.format(
                storage_account_name=self.storage_account_name,
                container_name=self.name)
            container_client = data_utils.create_az_client(
                client_type='container',
                container_url=container_url,
                storage_account_name=self.storage_account_name,
                resource_group_name=self.resource_group_name)
            # List and delete blobs in the specified directory
            blobs = container_client.list_blobs(
                name_starts_with=self._bucket_sub_path + '/')
            for blob in blobs:
                container_client.delete_blob(blob.name)
            logger.info(
                f'Deleted objects from sub path {self._bucket_sub_path} '
                f'in container {self.name}.')
        except Exception as e:  # pylint: disable=broad-except
            logger.error(
                f'Failed to delete objects from sub path '
                f'{self._bucket_sub_path} in container {self.name}. '
                f'Details: {common_utils.format_exception(e, use_bracket=True)}'
            )

    def get_handle(self) -> StorageHandle:
        """Returns the Storage Handle object."""
        return self.storage_client.blob_containers.get(
            self.resource_group_name, self.storage_account_name, self.name)

    def batch_az_blob_sync(self,
                           source_path_list: List[Path],
                           create_dirs: bool = False) -> None:
        """Invokes az storage blob sync to batch upload a list of local paths.

        Args:
            source_path_list: List of paths to local files or directories
            create_dirs: If the local_path is a directory and this is set to
                False, the contents of the directory are directly uploaded to
                root of the bucket. If the local_path is a directory and this is
                set to True, the directory is created in the bucket root and
                contents are uploaded to it.
        """
        container_path = (f'{self.container_name}/{self._bucket_sub_path}'
                          if self._bucket_sub_path else self.container_name)

        def get_file_sync_command(base_dir_path, file_names) -> str:
            # shlex.quote is not used for file_names as 'az storage blob sync'
            # already handles file names with empty spaces when used with
            # '--include-pattern' option.
            includes_list = ';'.join(file_names)
            includes = f'--include-pattern "{includes_list}"'
            base_dir_path = shlex.quote(base_dir_path)
            sync_command = (f'az storage blob sync '
                            f'--account-name {self.storage_account_name} '
                            f'--account-key {self.storage_account_key} '
                            f'{includes} '
                            '--delete-destination false '
                            f'--source {base_dir_path} '
                            f'--container {container_path}')
            return sync_command

        def get_dir_sync_command(src_dir_path, dest_dir_name) -> str:
            # we exclude .git directory from the sync
            excluded_list = storage_utils.get_excluded_files(src_dir_path)
            excluded_list.append('.git/')
            excludes_list = ';'.join(
                [file_name.rstrip('*') for file_name in excluded_list])
            excludes = f'--exclude-path "{excludes_list}"'
            src_dir_path = shlex.quote(src_dir_path)
            if dest_dir_name:
                dest_dir_name = f'/{dest_dir_name}'
            else:
                dest_dir_name = ''
            sync_command = (f'az storage blob sync '
                            f'--account-name {self.storage_account_name} '
                            f'--account-key {self.storage_account_key} '
                            f'{excludes} '
                            '--delete-destination false '
                            f'--source {src_dir_path} '
                            f'--container {container_path}{dest_dir_name}')
            return sync_command

        # Generate message for upload
        assert source_path_list
        if len(source_path_list) > 1:
            source_message = f'{len(source_path_list)} paths'
        else:
            source_message = source_path_list[0]
        container_endpoint = data_utils.AZURE_CONTAINER_URL.format(
            storage_account_name=self.storage_account_name,
            container_name=container_path)
        log_path = sky_logging.generate_tmp_logging_file_path(
            _STORAGE_LOG_FILE_NAME)
        sync_path = f'{source_message} -> {container_endpoint}/'
        with rich_utils.safe_status(
                ux_utils.spinner_message(f'Syncing {sync_path}',
                                         log_path=log_path)):
            data_utils.parallel_upload(
                source_path_list,
                get_file_sync_command,
                get_dir_sync_command,
                log_path,
                self.name,
                self._ACCESS_DENIED_MESSAGE,
                create_dirs=create_dirs,
                max_concurrent_uploads=_MAX_CONCURRENT_UPLOADS)
        logger.info(
            ux_utils.finishing_message(f'Storage synced: {sync_path}',
                                       log_path))

    def _get_bucket(self) -> Tuple[str, bool]:
        """Obtains the AZ Container.

        Buckets for Azure Blob Storage are referred as Containers.
        If the container exists, this method will return the container.
        If the container does not exist, there are three cases:
          1) Raise an error if the container source starts with https://
          2) Return None if container has been externally deleted and
             sync_on_reconstruction is False
          3) Create and return a new container otherwise

        Returns:
            str: name of the bucket(container)
            bool: represents either or not the bucket is managed by skypilot

        Raises:
            StorageBucketCreateError: If creating the container fails
            StorageBucketGetError: If fetching a container fails
            StorageExternalDeletionError: If externally deleted container is
                attempted to be fetched while reconstructing the Storage for
                'sky storage delete' or 'sky start'
        """
        try:
            container_url = data_utils.AZURE_CONTAINER_URL.format(
                storage_account_name=self.storage_account_name,
                container_name=self.name)
            try:
                container_client = data_utils.create_az_client(
                    client_type='container',
                    container_url=container_url,
                    storage_account_name=self.storage_account_name,
                    resource_group_name=self.resource_group_name)
            except azure.exceptions().ClientAuthenticationError as e:
                if 'ERROR: AADSTS50020' in str(e):
                    # Caught when failing to obtain container client due to
                    # lack of permission to passed given private container.
                    if self.resource_group_name is None:
                        with ux_utils.print_exception_no_traceback():
                            raise exceptions.StorageBucketGetError(
                                _BUCKET_FAIL_TO_CONNECT_MESSAGE.format(
                                    name=self.name))
                raise
            if container_client.exists():
                is_private = (True if
                              container_client.get_container_properties().get(
                                  'public_access', None) is None else False)
                # when user attempts to use private container without
                # access rights
                if self.resource_group_name is None and is_private:
                    with ux_utils.print_exception_no_traceback():
                        raise exceptions.StorageBucketGetError(
                            _BUCKET_FAIL_TO_CONNECT_MESSAGE.format(
                                name=self.name))
                self._validate_existing_bucket()
                return container_client.container_name, False
            # when the container name does not exist under the provided
            # storage account name and credentials, and user has the rights to
            # access the storage account.
            else:
                # when this if statement is not True, we let it to proceed
                # farther and create the container.
                if (isinstance(self.source, str) and
                        self.source.startswith('https://')):
                    with ux_utils.print_exception_no_traceback():
                        raise exceptions.StorageBucketGetError(
                            'Attempted to use a non-existent container as a '
                            f'source: {self.source}. Please check if the '
                            'container name is correct.')
        except azure.exceptions().ServiceRequestError as e:
            # raised when storage account name to be used does not exist.
            error_message = e.message
            if 'Name or service not known' in error_message:
                with ux_utils.print_exception_no_traceback():
                    raise exceptions.StorageBucketGetError(
                        'Attempted to fetch the container from non-existent '
                        'storage account '
                        f'name: {self.storage_account_name}. Please check '
                        'if the name is correct.')
            else:
                with ux_utils.print_exception_no_traceback():
                    raise exceptions.StorageBucketGetError(
                        'Failed to fetch the container from storage account '
                        f'{self.storage_account_name!r}.'
                        'Details: '
                        f'{common_utils.format_exception(e, use_bracket=True)}')

        # If the container cannot be found in both private and public settings,
        # the container is to be created by Sky. However, creation is skipped
        # if Store object is being reconstructed for deletion or re-mount with
        # sky start, and error is raised instead.
        if self.sync_on_reconstruction:
            container = self._create_az_bucket(self.name)
            return container.name, True

        # Raised when Storage object is reconstructed for sky storage
        # delete or to re-mount Storages with sky start but the storage
        # is already removed externally.
        with ux_utils.print_exception_no_traceback():
            raise exceptions.StorageExternalDeletionError(
                f'Attempted to fetch a non-existent container: {self.name}')

    def mount_command(self, mount_path: str) -> str:
        """Returns the command to mount the container to the mount_path.

        Uses blobfuse2 to mount the container.

        Args:
            mount_path: Path to mount the container to

        Returns:
            str: a heredoc used to setup the AZ Container mount
        """
        install_cmd = mounting_utils.get_az_mount_install_cmd()
        mount_cmd = mounting_utils.get_az_mount_cmd(self.container_name,
                                                    self.storage_account_name,
                                                    mount_path,
                                                    self.storage_account_key,
                                                    self._bucket_sub_path)
        return mounting_utils.get_mounting_command(mount_path, install_cmd,
                                                   mount_cmd)

    def mount_cached_command(self, mount_path: str) -> str:
        install_cmd = mounting_utils.get_rclone_install_cmd()
        rclone_profile_name = (
            data_utils.Rclone.RcloneStores.AZURE.get_profile_name(self.name))
        rclone_config = data_utils.Rclone.RcloneStores.AZURE.get_config(
            rclone_profile_name=rclone_profile_name,
            storage_account_name=self.storage_account_name,
            storage_account_key=self.storage_account_key)
        mount_cached_cmd = mounting_utils.get_mount_cached_cmd(
            rclone_config, rclone_profile_name, self.container_name, mount_path)
        return mounting_utils.get_mounting_command(mount_path, install_cmd,
                                                   mount_cached_cmd)

    def _create_az_bucket(self, container_name: str) -> StorageHandle:
        """Creates AZ Container.

        Args:
            container_name: Name of bucket(container)

        Returns:
            StorageHandle: Handle to interact with the container

        Raises:
            StorageBucketCreateError: If container creation fails.
        """
        try:
            # Container is created under the region which the storage account
            # belongs to.
            container = self.storage_client.blob_containers.create(
                self.resource_group_name,
                self.storage_account_name,
                container_name,
                blob_container={})
            logger.info(f'  {colorama.Style.DIM}Created AZ Container '
                        f'{container_name!r} in {self.region!r} under storage '
                        f'account {self.storage_account_name!r}.'
                        f'{colorama.Style.RESET_ALL}')
        except azure.exceptions().ResourceExistsError as e:
            if 'container is being deleted' in e.error.message:
                with ux_utils.print_exception_no_traceback():
                    raise exceptions.StorageBucketCreateError(
                        f'The container {self.name!r} is currently being '
                        'deleted. Please wait for the deletion to complete'
                        'before attempting to create a container with the '
                        'same name. This may take a few minutes.')
            else:
                with ux_utils.print_exception_no_traceback():
                    raise exceptions.StorageBucketCreateError(
                        f'Failed to create the container {self.name!r}. '
                        'Details: '
                        f'{common_utils.format_exception(e, use_bracket=True)}')
        return container

    def _delete_az_bucket(self, container_name: str) -> bool:
        """Deletes AZ Container, including all objects in Container.

        Args:
            container_name: Name of bucket(container).

        Returns:
            bool: True if container was deleted, False if it's deleted
                externally.

        Raises:
            StorageBucketDeleteError: If deletion fails for reasons other than
                the container not existing.
        """
        try:
            with rich_utils.safe_status(
                    ux_utils.spinner_message(
                        f'Deleting Azure container {container_name}')):
                # Check for the existence of the container before deletion.
                self.storage_client.blob_containers.get(
                    self.resource_group_name,
                    self.storage_account_name,
                    container_name,
                )
                self.storage_client.blob_containers.delete(
                    self.resource_group_name,
                    self.storage_account_name,
                    container_name,
                )
        except azure.exceptions().ResourceNotFoundError as e:
            if 'Code: ContainerNotFound' in str(e):
                logger.debug(
                    _BUCKET_EXTERNALLY_DELETED_DEBUG_MESSAGE.format(
                        bucket_name=container_name))
                return False
            else:
                with ux_utils.print_exception_no_traceback():
                    raise exceptions.StorageBucketDeleteError(
                        f'Failed to delete Azure container {container_name}. '
                        f'Detailed error: {e}')
        return True


class IBMCosStore(AbstractStore):
    """IBMCosStore inherits from Storage Object and represents the backend
    for COS buckets.
    """
    _ACCESS_DENIED_MESSAGE = 'Access Denied'

    def __init__(self,
                 name: str,
                 source: str,
                 region: Optional[str] = 'us-east',
                 is_sky_managed: Optional[bool] = None,
                 sync_on_reconstruction: bool = True,
                 _bucket_sub_path: Optional[str] = None):
        self.client: 'storage.Client'
        self.bucket: 'StorageHandle'
        self.rclone_profile_name = (
            data_utils.Rclone.RcloneStores.IBM.get_profile_name(self.name))
        super().__init__(name, source, region, is_sky_managed,
                         sync_on_reconstruction, _bucket_sub_path)

    def _validate(self):
        if self.source is not None and isinstance(self.source, str):
            if self.source.startswith('s3://'):
                assert self.name == data_utils.split_s3_path(self.source)[0], (
                    'S3 Bucket is specified as path, the name should be the'
                    ' same as S3 bucket.')
                assert data_utils.verify_s3_bucket(self.name), (
                    f'Source specified as {self.source}, a S3 bucket. ',
                    'S3 Bucket should exist.')
            elif self.source.startswith('gs://'):
                assert self.name == data_utils.split_gcs_path(self.source)[0], (
                    'GCS Bucket is specified as path, the name should be '
                    'the same as GCS bucket.')
                assert data_utils.verify_gcs_bucket(self.name), (
                    f'Source specified as {self.source}, a GCS bucket. ',
                    'GCS Bucket should exist.')
            elif data_utils.is_az_container_endpoint(self.source):
                storage_account_name, container_name, _ = (
                    data_utils.split_az_path(self.source))
                assert self.name == container_name, (
                    'Azure bucket is specified as path, the name should be '
                    'the same as Azure bucket.')
                assert data_utils.verify_az_bucket(
                    storage_account_name, self.name), (
                        f'Source specified as {self.source}, an Azure bucket. '
                        'Azure bucket should exist.')
            elif self.source.startswith('r2://'):
                assert self.name == data_utils.split_r2_path(self.source)[0], (
                    'R2 Bucket is specified as path, the name should be '
                    'the same as R2 bucket.')
                assert data_utils.verify_r2_bucket(self.name), (
                    f'Source specified as {self.source}, a R2 bucket. ',
                    'R2 Bucket should exist.')
            elif self.source.startswith('nebius://'):
                assert self.name == data_utils.split_nebius_path(
                    self.source)[0], (
                        'Nebius Object Storage is specified as path, the name '
                        'should be the same as Nebius Object Storage bucket.')
                assert data_utils.verify_nebius_bucket(self.name), (
                    f'Source specified as {self.source}, a Nebius Object '
                    f'Storage  bucket. Nebius Object Storage Bucket should '
                    f'exist.')
            elif self.source.startswith('cos://'):
                assert self.name == data_utils.split_cos_path(self.source)[0], (
                    'COS Bucket is specified as path, the name should be '
                    'the same as COS bucket.')
        # Validate name
        self.name = IBMCosStore.validate_name(self.name)

    @classmethod
    def validate_name(cls, name: str) -> str:
        """Validates the name of a COS bucket.

        Rules source: https://ibm.github.io/ibm-cos-sdk-java/com/ibm/cloud/objectstorage/services/s3/model/Bucket.html  # pylint: disable=line-too-long
        """

        def _raise_no_traceback_name_error(err_str):
            with ux_utils.print_exception_no_traceback():
                raise exceptions.StorageNameError(err_str)

        if name is not None and isinstance(name, str):
            if not 3 <= len(name) <= 63:
                _raise_no_traceback_name_error(
                    f'Invalid store name: {name} must be between 3 (min) '
                    'and 63 (max) characters long.')

            # Check for valid characters and start/end with a letter or number
            pattern = r'^[a-z0-9][-a-z0-9.]*[a-z0-9]$'
            if not re.match(pattern, name):
                _raise_no_traceback_name_error(
                    f'Invalid store name: {name} can consist only of '
                    'lowercase letters, numbers, dots (.), and dashes (-). '
                    'It must begin and end with a letter or number.')

            # Check for two adjacent periods or dashes
            if any(substring in name for substring in ['..', '--']):
                _raise_no_traceback_name_error(
                    f'Invalid store name: {name} must not contain '
                    'two adjacent periods/dashes')

            # Check for IP address format
            ip_pattern = r'^(?:\d{1,3}\.){3}\d{1,3}$'
            if re.match(ip_pattern, name):
                _raise_no_traceback_name_error(
                    f'Invalid store name: {name} must not be formatted as '
                    'an IP address (for example, 192.168.5.4).')

            if any(substring in name for substring in ['.-', '-.']):
                _raise_no_traceback_name_error(
                    f'Invalid store name: {name} must '
                    'not allow substrings: ".-", "-." .')
        else:
            _raise_no_traceback_name_error('Store name must be specified.')
        return name

    def initialize(self):
        """Initializes the cos store object on the cloud.

        Initialization involves fetching bucket if exists, or creating it if
        it does not.

        Raises:
          StorageBucketCreateError: If bucket creation fails
          StorageBucketGetError: If fetching existing bucket fails
          StorageInitError: If general initialization fails.
        """
        self.client = ibm.get_cos_client(self.region)
        self.s3_resource = ibm.get_cos_resource(self.region)
        self.bucket, is_new_bucket = self._get_bucket()
        if self.is_sky_managed is None:
            # If is_sky_managed is not specified, then this is a new storage
            # object (i.e., did not exist in global_user_state) and we should
            # set the is_sky_managed property.
            # If is_sky_managed is specified, then we take no action.
            self.is_sky_managed = is_new_bucket

    def upload(self):
        """Uploads files from local machine to bucket.

        Upload must be called by the Storage handler - it is not called on
        Store initialization.

        Raises:
            StorageUploadError: if upload fails.
        """
        try:
            if isinstance(self.source, list):
                self.batch_ibm_rsync(self.source, create_dirs=True)
            elif self.source is not None:
                if self.source.startswith('cos://'):
                    # cos bucket used as a dest, can't be used as source.
                    pass
                elif self.source.startswith('s3://'):
                    raise Exception('IBM COS currently not supporting'
                                    'data transfers between COS and S3')
                elif self.source.startswith('nebius://'):
                    raise Exception('IBM COS currently not supporting'
                                    'data transfers between COS and Nebius')
                elif self.source.startswith('gs://'):
                    raise Exception('IBM COS currently not supporting'
                                    'data transfers between COS and GS')
                elif self.source.startswith('r2://'):
                    raise Exception('IBM COS currently not supporting'
                                    'data transfers between COS and r2')
                else:
                    self.batch_ibm_rsync([self.source])

        except Exception as e:
            raise exceptions.StorageUploadError(
                f'Upload failed for store {self.name}') from e

    def delete(self) -> None:
        if self._bucket_sub_path is not None and not self.is_sky_managed:
            return self._delete_sub_path()

        self._delete_cos_bucket()
        logger.info(f'{colorama.Fore.GREEN}Deleted COS bucket {self.name}.'
                    f'{colorama.Style.RESET_ALL}')

    def _delete_sub_path(self) -> None:
        assert self._bucket_sub_path is not None, 'bucket_sub_path is not set'
        bucket = self.s3_resource.Bucket(self.name)
        try:
            self._delete_cos_bucket_objects(bucket, self._bucket_sub_path + '/')
        except ibm.ibm_botocore.exceptions.ClientError as e:
            if e.__class__.__name__ == 'NoSuchBucket':
                logger.debug('bucket already removed')

    def get_handle(self) -> StorageHandle:
        return self.s3_resource.Bucket(self.name)

    def batch_ibm_rsync(self,
                        source_path_list: List[Path],
                        create_dirs: bool = False) -> None:
        """Invokes rclone copy to batch upload a list of local paths to cos

        Since rclone does not support batch operations, we construct
        multiple commands to be run in parallel.

        Args:
            source_path_list: List of paths to local files or directories
            create_dirs: If the local_path is a directory and this is set to
                False, the contents of the directory are directly uploaded to
                root of the bucket. If the local_path is a directory and this is
                set to True, the directory is created in the bucket root and
                contents are uploaded to it.
        """
        sub_path = (f'/{self._bucket_sub_path}'
                    if self._bucket_sub_path else '')

        def get_dir_sync_command(src_dir_path, dest_dir_name) -> str:
            """returns an rclone command that copies a complete folder
              from 'src_dir_path' to bucket/'dest_dir_name'.

            `rclone copy` copies files from source path to target.
            files with identical names at won't be copied over, unless
            their modification date is more recent.
            works similarly to `aws sync` (without --delete).

            Args:
                src_dir_path (str): local source path from which to copy files.
                dest_dir_name (str): remote target path files are copied to.

            Returns:
                str: bash command using rclone to sync files. Executed remotely.
            """

            # .git directory is excluded from the sync
            # wrapping src_dir_path with "" to support path with spaces
            src_dir_path = shlex.quote(src_dir_path)
            sync_command = ('rclone copy --exclude ".git/*" '
                            f'{src_dir_path} '
                            f'{self.rclone_profile_name}:{self.name}{sub_path}'
                            f'/{dest_dir_name}')
            return sync_command

        def get_file_sync_command(base_dir_path, file_names) -> str:
            """returns an rclone command that copies files: 'file_names'
               from base directory: `base_dir_path` to bucket.

            `rclone copy` copies files from source path to target.
            files with identical names at won't be copied over, unless
            their modification date is more recent.
            works similarly to `aws sync` (without --delete).

            Args:
                base_dir_path (str): local path from which to copy files.
                file_names (List): specific file names to copy.

            Returns:
                str: bash command using rclone to sync files
            """

            # wrapping file_name with "" to support spaces
            includes = ' '.join([
                f'--include {shlex.quote(file_name)}'
                for file_name in file_names
            ])
            base_dir_path = shlex.quote(base_dir_path)
            sync_command = ('rclone copy '
                            f'{includes} {base_dir_path} '
                            f'{self.rclone_profile_name}:{self.name}{sub_path}')
            return sync_command

        # Generate message for upload
        if len(source_path_list) > 1:
            source_message = f'{len(source_path_list)} paths'
        else:
            source_message = source_path_list[0]

        log_path = sky_logging.generate_tmp_logging_file_path(
            _STORAGE_LOG_FILE_NAME)
        sync_path = (
            f'{source_message} -> cos://{self.region}/{self.name}{sub_path}/')
        with rich_utils.safe_status(
                ux_utils.spinner_message(f'Syncing {sync_path}',
                                         log_path=log_path)):
            data_utils.parallel_upload(
                source_path_list,
                get_file_sync_command,
                get_dir_sync_command,
                log_path,
                self.name,
                self._ACCESS_DENIED_MESSAGE,
                create_dirs=create_dirs,
                max_concurrent_uploads=_MAX_CONCURRENT_UPLOADS)
        logger.info(
            ux_utils.finishing_message(f'Storage synced: {sync_path}',
                                       log_path))

    def _get_bucket(self) -> Tuple[StorageHandle, bool]:
        """returns IBM COS bucket object if exists, otherwise creates it.

        Returns:
          StorageHandle(str): bucket name
          bool: indicates whether a new bucket was created.

        Raises:
            StorageSpecError: If externally created bucket is attempted to be
                mounted without specifying storage source.
            StorageBucketCreateError: If bucket creation fails.
            StorageBucketGetError: If fetching a bucket fails
            StorageExternalDeletionError: If externally deleted storage is
                attempted to be fetched while reconstructing the storage for
                'sky storage delete' or 'sky start'
        """

        bucket_profile_name = (data_utils.Rclone.RcloneStores.IBM.value +
                               self.name)
        try:
            bucket_region = data_utils.get_ibm_cos_bucket_region(self.name)
        except exceptions.StorageBucketGetError as e:
            with ux_utils.print_exception_no_traceback():
                command = f'rclone lsd {bucket_profile_name}: '
                raise exceptions.StorageBucketGetError(
                    _BUCKET_FAIL_TO_CONNECT_MESSAGE.format(name=self.name) +
                    f' To debug, consider running `{command}`.') from e

        try:
            uri_region = data_utils.split_cos_path(
                self.source)[2]  # type: ignore
        except ValueError:
            # source isn't a cos uri
            uri_region = ''

        # bucket's region doesn't match specified region in URI
        if bucket_region and uri_region and uri_region != bucket_region\
              and self.sync_on_reconstruction:
            with ux_utils.print_exception_no_traceback():
                raise exceptions.StorageBucketGetError(
                    f'Bucket {self.name} exists in '
                    f'region {bucket_region}, '
                    f'but URI specified region {uri_region}.')

        if not bucket_region and uri_region:
            # bucket doesn't exist but source is a bucket URI
            with ux_utils.print_exception_no_traceback():
                raise exceptions.StorageBucketGetError(
                    'Attempted to use a non-existent bucket as a source: '
                    f'{self.name} by providing URI. Consider using '
                    '`rclone lsd <remote>` on relevant remotes returned '
                    'via `rclone listremotes` to debug.')

        data_utils.Rclone.store_rclone_config(
            self.name,
            data_utils.Rclone.RcloneStores.IBM,
            self.region,  # type: ignore
        )

        if not bucket_region and self.sync_on_reconstruction:
            # bucket doesn't exist
            return self._create_cos_bucket(self.name, self.region), True
        elif not bucket_region and not self.sync_on_reconstruction:
            # Raised when Storage object is reconstructed for sky storage
            # delete or to re-mount Storages with sky start but the storage
            # is already removed externally.
            raise exceptions.StorageExternalDeletionError(
                'Attempted to fetch a non-existent bucket: '
                f'{self.name}')
        else:
            # bucket exists
            bucket = self.s3_resource.Bucket(self.name)
            self._validate_existing_bucket()
            return bucket, False

    def _download_file(self, remote_path: str, local_path: str) -> None:
        """Downloads file from remote to local on s3 bucket
        using the boto3 API

        Args:
          remote_path: str; Remote path on S3 bucket
          local_path: str; Local path on user's device
        """
        self.client.download_file(self.name, local_path, remote_path)

    def mount_command(self, mount_path: str) -> str:
        """Returns the command to mount the bucket to the mount_path.

        Uses rclone to mount the bucket.
        Source: https://github.com/rclone/rclone

        Args:
          mount_path: str; Path to mount the bucket to.
        """
        # install rclone if not installed.
        install_cmd = mounting_utils.get_rclone_install_cmd()
        rclone_config = data_utils.Rclone.RcloneStores.IBM.get_config(
            rclone_profile_name=self.rclone_profile_name,
            region=self.region)  # type: ignore
        mount_cmd = (
            mounting_utils.get_cos_mount_cmd(
                rclone_config,
                self.rclone_profile_name,
                self.bucket.name,
                mount_path,
                self._bucket_sub_path,  # type: ignore
            ))
        return mounting_utils.get_mounting_command(mount_path, install_cmd,
                                                   mount_cmd)

    def _create_cos_bucket(self,
                           bucket_name: str,
                           region='us-east') -> StorageHandle:
        """Creates IBM COS bucket with specific name in specific region

        Args:
          bucket_name: str; Name of bucket
          region: str; Region name, e.g. us-east, us-south
        Raises:
          StorageBucketCreateError: If bucket creation fails.
        """
        try:
            self.client.create_bucket(
                Bucket=bucket_name,
                CreateBucketConfiguration={
                    'LocationConstraint': f'{region}-smart'
                })
            logger.info(f'  {colorama.Style.DIM}Created IBM COS bucket '
                        f'{bucket_name!r} in {region} '
                        'with storage class smart tier'
                        f'{colorama.Style.RESET_ALL}')
            self.bucket = self.s3_resource.Bucket(bucket_name)

        except ibm.ibm_botocore.exceptions.ClientError as e:  # type: ignore[union-attr]  # pylint: disable=line-too-long
            with ux_utils.print_exception_no_traceback():
                raise exceptions.StorageBucketCreateError(
                    f'Failed to create bucket: '
                    f'{bucket_name}') from e

        s3_bucket_exists_waiter = self.client.get_waiter('bucket_exists')
        s3_bucket_exists_waiter.wait(Bucket=bucket_name)

        return self.bucket

    def _delete_cos_bucket_objects(self,
                                   bucket: Any,
                                   prefix: Optional[str] = None) -> None:
        bucket_versioning = self.s3_resource.BucketVersioning(bucket.name)
        if bucket_versioning.status == 'Enabled':
            if prefix is not None:
                res = list(
                    bucket.object_versions.filter(Prefix=prefix).delete())
            else:
                res = list(bucket.object_versions.delete())
        else:
            if prefix is not None:
                res = list(bucket.objects.filter(Prefix=prefix).delete())
            else:
                res = list(bucket.objects.delete())
        logger.debug(f'Deleted bucket\'s content:\n{res}, prefix: {prefix}')

    def _delete_cos_bucket(self) -> None:
        bucket = self.s3_resource.Bucket(self.name)
        try:
            self._delete_cos_bucket_objects(bucket)
            bucket.delete()
            bucket.wait_until_not_exists()
        except ibm.ibm_botocore.exceptions.ClientError as e:
            if e.__class__.__name__ == 'NoSuchBucket':
                logger.debug('bucket already removed')
        data_utils.Rclone.delete_rclone_bucket_profile(
            self.name, data_utils.Rclone.RcloneStores.IBM)


class OciStore(AbstractStore):
    """OciStore inherits from Storage Object and represents the backend
    for OCI buckets.
    """

    _ACCESS_DENIED_MESSAGE = 'AccessDeniedException'

    def __init__(self,
                 name: str,
                 source: Optional[SourceType],
                 region: Optional[str] = None,
                 is_sky_managed: Optional[bool] = None,
                 sync_on_reconstruction: Optional[bool] = True,
                 _bucket_sub_path: Optional[str] = None):
        self.client: Any
        self.bucket: StorageHandle
        self.oci_config_file: str
        self.config_profile: str
        self.compartment: str
        self.namespace: str

        # Region is from the specified name in <bucket>@<region> format.
        # Another case is name can also be set by the source, for example:
        #   /datasets-storage:
        #       source: oci://RAGData@us-sanjose-1
        # The name in above mount will be set to RAGData@us-sanjose-1
        region_in_name = None
        if name is not None and '@' in name:
            self._validate_bucket_expr(name)
            name, region_in_name = name.split('@')

        # Region is from the specified source in oci://<bucket>@<region> format
        region_in_source = None
        if isinstance(source,
                      str) and source.startswith('oci://') and '@' in source:
            self._validate_bucket_expr(source)
            source, region_in_source = source.split('@')

        if region_in_name is not None and region_in_source is not None:
            # This should never happen because name and source will never be
            # the remote bucket at the same time.
            assert region_in_name == region_in_source, (
                f'Mismatch region specified. Region in name {region_in_name}, '
                f'but region in source is {region_in_source}')

        if region_in_name is not None:
            region = region_in_name
        elif region_in_source is not None:
            region = region_in_source

        # Default region set to what specified in oci config.
        if region is None:
            region = oci.get_oci_config()['region']

        # So far from now on, the name and source are canonical, means there
        # is no region (@<region> suffix) associated with them anymore.

        super().__init__(name, source, region, is_sky_managed,
                         sync_on_reconstruction, _bucket_sub_path)
        # TODO(zpoint): add _bucket_sub_path to the sync/mount/delete commands

    def _validate_bucket_expr(self, bucket_expr: str):
        pattern = r'^(\w+://)?[A-Za-z0-9-._]+(@\w{2}-\w+-\d{1})$'
        if not re.match(pattern, bucket_expr):
            raise ValueError(
                'The format for the bucket portion is <bucket>@<region> '
                'when specify a region with a bucket.')

    def _validate(self):
        if self.source is not None and isinstance(self.source, str):
            if self.source.startswith('oci://'):
                assert self.name == data_utils.split_oci_path(self.source)[0], (
                    'OCI Bucket is specified as path, the name should be '
                    'the same as OCI bucket.')
            elif not re.search(r'^\w+://', self.source):
                # Treat it as local path.
                pass
            else:
                raise NotImplementedError(
                    f'Moving data from {self.source} to OCI is not supported.')

        # Validate name
        self.name = self.validate_name(self.name)
        # Check if the storage is enabled
        if not _is_storage_cloud_enabled(str(clouds.OCI())):
            with ux_utils.print_exception_no_traceback():
                raise exceptions.ResourcesUnavailableError(
                    'Storage \'store: oci\' specified, but ' \
                    'OCI access is disabled. To fix, enable '\
                    'OCI by running `sky check`. '\
                    'More info: https://skypilot.readthedocs.io/en/latest/getting-started/installation.html.' # pylint: disable=line-too-long
                    )

    @classmethod
    def validate_name(cls, name) -> str:
        """Validates the name of the OCI store.

        Source for rules: https://docs.oracle.com/en-us/iaas/Content/Object/Tasks/managingbuckets.htm#Managing_Buckets # pylint: disable=line-too-long
        """

        def _raise_no_traceback_name_error(err_str):
            with ux_utils.print_exception_no_traceback():
                raise exceptions.StorageNameError(err_str)

        if name is not None and isinstance(name, str):
            # Check for overall length
            if not 1 <= len(name) <= 256:
                _raise_no_traceback_name_error(
                    f'Invalid store name: name {name} must contain 1-256 '
                    'characters.')

            # Check for valid characters and start/end with a number or letter
            pattern = r'^[A-Za-z0-9-._]+$'
            if not re.match(pattern, name):
                _raise_no_traceback_name_error(
                    f'Invalid store name: name {name} can only contain '
                    'upper or lower case letters, numeric characters, hyphens '
                    '(-), underscores (_), and dots (.). Spaces are not '
                    'allowed. Names must start and end with a number or '
                    'letter.')
        else:
            _raise_no_traceback_name_error('Store name must be specified.')
        return name

    def initialize(self):
        """Initializes the OCI store object on the cloud.

        Initialization involves fetching bucket if exists, or creating it if
        it does not.

        Raises:
          StorageBucketCreateError: If bucket creation fails
          StorageBucketGetError: If fetching existing bucket fails
          StorageInitError: If general initialization fails.
        """
        # pylint: disable=import-outside-toplevel
        from sky.clouds.utils import oci_utils
        from sky.provision.oci.query_utils import query_helper

        self.oci_config_file = oci.get_config_file()
        self.config_profile = oci_utils.oci_config.get_profile()

        ## pylint: disable=line-too-long
        # What's compartment? See thttps://docs.oracle.com/en/cloud/foundation/cloud_architecture/governance/compartments.html
        self.compartment = query_helper.find_compartment(self.region)
        self.client = oci.get_object_storage_client(region=self.region,
                                                    profile=self.config_profile)
        self.namespace = self.client.get_namespace(
            compartment_id=oci.get_oci_config()['tenancy']).data

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
                self.batch_oci_rsync(self.source, create_dirs=True)
            elif self.source is not None:
                if self.source.startswith('oci://'):
                    pass
                else:
                    self.batch_oci_rsync([self.source])
        except exceptions.StorageUploadError:
            raise
        except Exception as e:
            raise exceptions.StorageUploadError(
                f'Upload failed for store {self.name}') from e

    def delete(self) -> None:
        deleted_by_skypilot = self._delete_oci_bucket(self.name)
        if deleted_by_skypilot:
            msg_str = f'Deleted OCI bucket {self.name}.'
        else:
            msg_str = (f'OCI bucket {self.name} may have been deleted '
                       f'externally. Removing from local state.')
        logger.info(f'{colorama.Fore.GREEN}{msg_str}'
                    f'{colorama.Style.RESET_ALL}')

    def get_handle(self) -> StorageHandle:
        return self.client.get_bucket(namespace_name=self.namespace,
                                      bucket_name=self.name).data

    def batch_oci_rsync(self,
                        source_path_list: List[Path],
                        create_dirs: bool = False) -> None:
        """Invokes oci sync to batch upload a list of local paths to Bucket

        Use OCI bulk operation to batch process the file upload

        Args:
            source_path_list: List of paths to local files or directories
            create_dirs: If the local_path is a directory and this is set to
                False, the contents of the directory are directly uploaded to
                root of the bucket. If the local_path is a directory and this is
                set to True, the directory is created in the bucket root and
                contents are uploaded to it.
        """
        sub_path = (f'{self._bucket_sub_path}/'
                    if self._bucket_sub_path else '')

        @oci.with_oci_env
        def get_file_sync_command(base_dir_path, file_names):
            includes = ' '.join(
                [f'--include "{file_name}"' for file_name in file_names])
            prefix_arg = ''
            if sub_path:
                prefix_arg = f'--object-prefix "{sub_path.strip("/")}"'
            sync_command = (
                'oci os object bulk-upload --no-follow-symlinks --overwrite '
                f'--bucket-name {self.name} --namespace-name {self.namespace} '
                f'--region {self.region} --src-dir "{base_dir_path}" '
                f'{prefix_arg} '
                f'{includes}')

            return sync_command

        @oci.with_oci_env
        def get_dir_sync_command(src_dir_path, dest_dir_name):
            if dest_dir_name and not str(dest_dir_name).endswith('/'):
                dest_dir_name = f'{dest_dir_name}/'

            excluded_list = storage_utils.get_excluded_files(src_dir_path)
            excluded_list.append('.git/*')
            excludes = ' '.join([
                f'--exclude {shlex.quote(file_name)}'
                for file_name in excluded_list
            ])

            # we exclude .git directory from the sync
            sync_command = (
                'oci os object bulk-upload --no-follow-symlinks --overwrite '
                f'--bucket-name {self.name} --namespace-name {self.namespace} '
                f'--region {self.region} '
                f'--object-prefix "{sub_path}{dest_dir_name}" '
                f'--src-dir "{src_dir_path}" {excludes}')

            return sync_command

        # Generate message for upload
        if len(source_path_list) > 1:
            source_message = f'{len(source_path_list)} paths'
        else:
            source_message = source_path_list[0]

        log_path = sky_logging.generate_tmp_logging_file_path(
            _STORAGE_LOG_FILE_NAME)
        sync_path = f'{source_message} -> oci://{self.name}/{sub_path}'
        with rich_utils.safe_status(
                ux_utils.spinner_message(f'Syncing {sync_path}',
                                         log_path=log_path)):
            data_utils.parallel_upload(
                source_path_list=source_path_list,
                filesync_command_generator=get_file_sync_command,
                dirsync_command_generator=get_dir_sync_command,
                log_path=log_path,
                bucket_name=self.name,
                access_denied_message=self._ACCESS_DENIED_MESSAGE,
                create_dirs=create_dirs,
                max_concurrent_uploads=1)

            logger.info(
                ux_utils.finishing_message(f'Storage synced: {sync_path}',
                                           log_path))

    def _get_bucket(self) -> Tuple[StorageHandle, bool]:
        """Obtains the OCI bucket.
        If the bucket exists, this method will connect to the bucket.

        If the bucket does not exist, there are three cases:
          1) Raise an error if the bucket source starts with oci://
          2) Return None if bucket has been externally deleted and
             sync_on_reconstruction is False
          3) Create and return a new bucket otherwise

        Return tuple (Bucket, Boolean): The first item is the bucket
        json payload from the OCI API call, the second item indicates
        if this is a new created bucket(True) or an existing bucket(False).

        Raises:
            StorageBucketCreateError: If creating the bucket fails
            StorageBucketGetError: If fetching a bucket fails
        """
        try:
            get_bucket_response = self.client.get_bucket(
                namespace_name=self.namespace, bucket_name=self.name)
            bucket = get_bucket_response.data
            return bucket, False
        except oci.service_exception() as e:
            if e.status == 404:  # Not Found
                if isinstance(self.source,
                              str) and self.source.startswith('oci://'):
                    with ux_utils.print_exception_no_traceback():
                        raise exceptions.StorageBucketGetError(
                            'Attempted to connect to a non-existent bucket: '
                            f'{self.source}') from e
                else:
                    # If bucket cannot be found (i.e., does not exist), it is
                    # to be created by Sky. However, creation is skipped if
                    # Store object is being reconstructed for deletion.
                    if self.sync_on_reconstruction:
                        bucket = self._create_oci_bucket(self.name)
                        return bucket, True
                    else:
                        return None, False
            elif e.status == 401:  # Unauthorized
                # AccessDenied error for buckets that are private and not
                # owned by user.
                command = (
                    f'oci os object list --namespace-name {self.namespace} '
                    f'--bucket-name {self.name}')
                with ux_utils.print_exception_no_traceback():
                    raise exceptions.StorageBucketGetError(
                        _BUCKET_FAIL_TO_CONNECT_MESSAGE.format(name=self.name) +
                        f' To debug, consider running `{command}`.') from e
            else:
                # Unknown / unexpected error happened. This might happen when
                # Object storage service itself functions not normal (e.g.
                # maintainance event causes internal server error or request
                # timeout, etc).
                with ux_utils.print_exception_no_traceback():
                    raise exceptions.StorageBucketGetError(
                        f'Failed to connect to OCI bucket {self.name}') from e

    def mount_command(self, mount_path: str) -> str:
        """Returns the command to mount the bucket to the mount_path.

        Uses Rclone to mount the bucket.

        Args:
          mount_path: str; Path to mount the bucket to.
        """
        install_cmd = mounting_utils.get_rclone_install_cmd()
        mount_cmd = mounting_utils.get_oci_mount_cmd(
            mount_path=mount_path,
            store_name=self.name,
            region=str(self.region),
            namespace=self.namespace,
            compartment=self.bucket.compartment_id,
            config_file=self.oci_config_file,
            config_profile=self.config_profile)
        version_check_cmd = mounting_utils.get_rclone_version_check_cmd()

        return mounting_utils.get_mounting_command(mount_path, install_cmd,
                                                   mount_cmd, version_check_cmd)

    def _download_file(self, remote_path: str, local_path: str) -> None:
        """Downloads file from remote to local on OCI bucket

        Args:
          remote_path: str; Remote path on OCI bucket
          local_path: str; Local path on user's device
        """
        if remote_path.startswith(f'/{self.name}'):
            # If the remote path is /bucket_name, we need to
            # remove the leading /
            remote_path = remote_path.lstrip('/')

        filename = os.path.basename(remote_path)
        if not local_path.endswith(filename):
            local_path = os.path.join(local_path, filename)

        @oci.with_oci_env
        def get_file_download_command(remote_path, local_path):
            download_command = (f'oci os object get --bucket-name {self.name} '
                                f'--namespace-name {self.namespace} '
                                f'--region {self.region} --name {remote_path} '
                                f'--file {local_path}')

            return download_command

        download_command = get_file_download_command(remote_path, local_path)

        try:
            with rich_utils.safe_status(
                    f'[bold cyan]Downloading: {remote_path} -> {local_path}[/]'
            ):
                subprocess.check_output(download_command,
                                        stderr=subprocess.STDOUT,
                                        shell=True)
        except subprocess.CalledProcessError as e:
            logger.error(f'Download failed: {remote_path} -> {local_path}.\n'
                         f'Detail errors: {e.output}')
            with ux_utils.print_exception_no_traceback():
                raise exceptions.StorageBucketDeleteError(
                    f'Failed download file {self.name}:{remote_path}.') from e

    def _create_oci_bucket(self, bucket_name: str) -> StorageHandle:
        """Creates OCI bucket with specific name in specific region

        Args:
          bucket_name: str; Name of bucket
          region: str; Region name, e.g. us-central1, us-west1
        """
        logger.debug(f'_create_oci_bucket: {bucket_name}')
        try:
            create_bucket_response = self.client.create_bucket(
                namespace_name=self.namespace,
                create_bucket_details=oci.oci.object_storage.models.
                CreateBucketDetails(
                    name=bucket_name,
                    compartment_id=self.compartment,
                ))
            bucket = create_bucket_response.data
            return bucket
        except oci.service_exception() as e:
            with ux_utils.print_exception_no_traceback():
                raise exceptions.StorageBucketCreateError(
                    f'Failed to create OCI bucket: {self.name}') from e

    def _delete_oci_bucket(self, bucket_name: str) -> bool:
        """Deletes OCI bucket, including all objects in bucket

        Args:
          bucket_name: str; Name of bucket

        Returns:
         bool; True if bucket was deleted, False if it was deleted externally.
        """
        logger.debug(f'_delete_oci_bucket: {bucket_name}')

        @oci.with_oci_env
        def get_bucket_delete_command(bucket_name):
            remove_command = (f'oci os bucket delete --bucket-name '
                              f'--region {self.region} '
                              f'{bucket_name} --empty --force')

            return remove_command

        remove_command = get_bucket_delete_command(bucket_name)

        try:
            with rich_utils.safe_status(
                    f'[bold cyan]Deleting OCI bucket {bucket_name}[/]'):
                subprocess.check_output(remove_command.split(' '),
                                        stderr=subprocess.STDOUT)
        except subprocess.CalledProcessError as e:
            if 'BucketNotFound' in e.output.decode('utf-8'):
                logger.debug(
                    _BUCKET_EXTERNALLY_DELETED_DEBUG_MESSAGE.format(
                        bucket_name=bucket_name))
                return False
            else:
                logger.error(e.output)
                with ux_utils.print_exception_no_traceback():
                    raise exceptions.StorageBucketDeleteError(
                        f'Failed to delete OCI bucket {bucket_name}.')
        return True


@register_s3_compatible_store
class S3Store(S3CompatibleStore):
    """S3Store inherits from S3CompatibleStore and represents the backend
    for S3 buckets.
    """

    _DEFAULT_REGION = 'us-east-1'
    _CUSTOM_ENDPOINT_REGIONS = [
        'ap-east-1', 'me-south-1', 'af-south-1', 'eu-south-1', 'eu-south-2',
        'ap-south-2', 'ap-southeast-3', 'ap-southeast-4', 'me-central-1',
        'il-central-1'
    ]

    def __init__(self,
                 name: str,
                 source: str,
                 region: Optional[str] = None,
                 is_sky_managed: Optional[bool] = None,
                 sync_on_reconstruction: bool = True,
                 _bucket_sub_path: Optional[str] = None):
        # TODO(romilb): This is purely a stopgap fix for
        #  https://github.com/skypilot-org/skypilot/issues/3405
        # We should eventually make all opt-in regions also work for S3 by
        # passing the right endpoint flags.
        if region in self._CUSTOM_ENDPOINT_REGIONS:
            logger.warning('AWS opt-in regions are not supported for S3. '
                           f'Falling back to default region '
                           f'{self._DEFAULT_REGION} for bucket {name!r}.')
            region = self._DEFAULT_REGION
        super().__init__(name, source, region, is_sky_managed,
                         sync_on_reconstruction, _bucket_sub_path)

    @classmethod
    def get_config(cls) -> S3CompatibleConfig:
        """Return the configuration for AWS S3."""
        return S3CompatibleConfig(
            store_type='S3',
            url_prefix='s3://',
            client_factory=data_utils.create_s3_client,
            resource_factory=lambda name: aws.resource('s3').Bucket(name),
            split_path=data_utils.split_s3_path,
            verify_bucket=data_utils.verify_s3_bucket,
            cloud_name=str(clouds.AWS()),
            default_region=cls._DEFAULT_REGION,
            mount_cmd_factory=mounting_utils.get_s3_mount_cmd,
        )

    def mount_cached_command(self, mount_path: str) -> str:
        install_cmd = mounting_utils.get_rclone_install_cmd()
        rclone_profile_name = (
            data_utils.Rclone.RcloneStores.S3.get_profile_name(self.name))
        rclone_config = data_utils.Rclone.RcloneStores.S3.get_config(
            rclone_profile_name=rclone_profile_name)
        mount_cached_cmd = mounting_utils.get_mount_cached_cmd(
            rclone_config, rclone_profile_name, self.bucket.name, mount_path)
        return mounting_utils.get_mounting_command(mount_path, install_cmd,
                                                   mount_cached_cmd)


@register_s3_compatible_store
class R2Store(S3CompatibleStore):
    """R2Store inherits from S3CompatibleStore and represents the backend
    for R2 buckets.
    """

    def __init__(self,
                 name: str,
                 source: str,
                 region: Optional[str] = 'auto',
                 is_sky_managed: Optional[bool] = None,
                 sync_on_reconstruction: bool = True,
                 _bucket_sub_path: Optional[str] = None):
        super().__init__(name, source, region, is_sky_managed,
                         sync_on_reconstruction, _bucket_sub_path)

    @classmethod
    def get_config(cls) -> S3CompatibleConfig:
        """Return the configuration for Cloudflare R2."""
        return S3CompatibleConfig(
            store_type='R2',
            url_prefix='r2://',
            client_factory=lambda region: data_utils.create_r2_client(region or
                                                                      'auto'),
            resource_factory=lambda name: cloudflare.resource('s3').Bucket(name
                                                                          ),
            split_path=data_utils.split_r2_path,
            verify_bucket=data_utils.verify_r2_bucket,
            credentials_file=cloudflare.R2_CREDENTIALS_PATH,
            aws_profile=cloudflare.R2_PROFILE_NAME,
            get_endpoint_url=lambda: cloudflare.create_endpoint(),  # pylint: disable=unnecessary-lambda
            extra_cli_args=['--checksum-algorithm', 'CRC32'],  # R2 specific
            cloud_name=cloudflare.NAME,
            default_region='auto',
            mount_cmd_factory=cls._get_r2_mount_cmd,
        )

    @classmethod
    def _get_r2_mount_cmd(cls, bucket_name: str, mount_path: str,
                          bucket_sub_path: Optional[str]) -> str:
        """Factory method for R2 mount command."""
        endpoint_url = cloudflare.create_endpoint()
        return mounting_utils.get_r2_mount_cmd(cloudflare.R2_CREDENTIALS_PATH,
                                               cloudflare.R2_PROFILE_NAME,
                                               endpoint_url, bucket_name,
                                               mount_path, bucket_sub_path)

    def mount_cached_command(self, mount_path: str) -> str:
        """R2-specific cached mount implementation using rclone."""
        install_cmd = mounting_utils.get_rclone_install_cmd()
        rclone_profile_name = (
            data_utils.Rclone.RcloneStores.R2.get_profile_name(self.name))
        rclone_config = data_utils.Rclone.RcloneStores.R2.get_config(
            rclone_profile_name=rclone_profile_name)
        mount_cached_cmd = mounting_utils.get_mount_cached_cmd(
            rclone_config, rclone_profile_name, self.bucket.name, mount_path)
        return mounting_utils.get_mounting_command(mount_path, install_cmd,
                                                   mount_cached_cmd)


@register_s3_compatible_store
class NebiusStore(S3CompatibleStore):
    """NebiusStore inherits from S3CompatibleStore and represents the backend
    for Nebius Object Storage buckets.
    """

    @classmethod
    def get_config(cls) -> S3CompatibleConfig:
        """Return the configuration for Nebius Object Storage."""
        return S3CompatibleConfig(
            store_type='NEBIUS',
            url_prefix='nebius://',
            client_factory=lambda region: data_utils.create_nebius_client(),
            resource_factory=lambda name: nebius.resource('s3').Bucket(name),
            split_path=data_utils.split_nebius_path,
            verify_bucket=data_utils.verify_nebius_bucket,
            aws_profile=nebius.NEBIUS_PROFILE_NAME,
            cloud_name=str(clouds.Nebius()),
            mount_cmd_factory=cls._get_nebius_mount_cmd,
        )

    @classmethod
    def _get_nebius_mount_cmd(cls, bucket_name: str, mount_path: str,
                              bucket_sub_path: Optional[str]) -> str:
        """Factory method for Nebius mount command."""
        # We need to get the endpoint URL, but since this is a static method,
        # we'll need to create a client to get it
        client = data_utils.create_nebius_client()
        endpoint_url = client.meta.endpoint_url
        return mounting_utils.get_nebius_mount_cmd(nebius.NEBIUS_PROFILE_NAME,
                                                   bucket_name, endpoint_url,
                                                   mount_path, bucket_sub_path)

    def mount_cached_command(self, mount_path: str) -> str:
        """Nebius-specific cached mount implementation using rclone."""
        install_cmd = mounting_utils.get_rclone_install_cmd()
        rclone_profile_name = (
            data_utils.Rclone.RcloneStores.NEBIUS.get_profile_name(self.name))
        rclone_config = data_utils.Rclone.RcloneStores.NEBIUS.get_config(
            rclone_profile_name=rclone_profile_name)
        mount_cached_cmd = mounting_utils.get_mount_cached_cmd(
            rclone_config, rclone_profile_name, self.bucket.name, mount_path)
        return mounting_utils.get_mounting_command(mount_path, install_cmd,
                                                   mount_cached_cmd)
