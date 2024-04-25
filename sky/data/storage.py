"""Storage and Store Classes for Sky Data."""
import enum
import os
import re
import shlex
import subprocess
import time
import typing
from typing import Any, Dict, List, Optional, Tuple, Type, Union
import urllib.parse

import colorama

from sky import check as sky_check
from sky import clouds
from sky import exceptions
from sky import global_user_state
from sky import sky_logging
from sky import status_lib
from sky.adaptors import aws
from sky.adaptors import cloudflare
from sky.adaptors import gcp
from sky.adaptors import ibm
from sky.data import data_transfer
from sky.data import data_utils
from sky.data import mounting_utils
from sky.data import storage_utils
from sky.data.data_utils import Rclone
from sky.utils import common_utils
from sky.utils import rich_utils
from sky.utils import schemas
from sky.utils import ux_utils

if typing.TYPE_CHECKING:
    import boto3  # type: ignore
    from google.cloud import storage  # type: ignore

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
    str(clouds.IBM()), cloudflare.NAME
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


def get_cached_enabled_storage_clouds_or_refresh(
        raise_if_no_cloud_access: bool = False) -> List[str]:
    # This is a temporary solution until https://github.com/skypilot-org/skypilot/issues/1943 # pylint: disable=line-too-long
    # is resolved by implementing separate 'enabled_storage_clouds'
    enabled_clouds = sky_check.get_cached_enabled_clouds_or_refresh()
    enabled_clouds = [str(cloud) for cloud in enabled_clouds]

    enabled_storage_clouds = [
        cloud for cloud in enabled_clouds if cloud in STORE_ENABLED_CLOUDS
    ]
    r2_is_enabled, _ = cloudflare.check_credentials()
    if r2_is_enabled:
        enabled_storage_clouds.append(cloudflare.NAME)
    if raise_if_no_cloud_access and not enabled_storage_clouds:
        raise exceptions.NoCloudAccessError(
            'No cloud access available for storage. '
            'Please check your cloud credentials.')
    return enabled_storage_clouds


def _is_storage_cloud_enabled(cloud_name: str,
                              try_fix_with_sky_check: bool = True) -> bool:
    enabled_storage_clouds = get_cached_enabled_storage_clouds_or_refresh()
    if cloud_name in enabled_storage_clouds:
        return True
    if try_fix_with_sky_check:
        # TODO(zhwu): Only check the specified cloud to speed up.
        sky_check.check(quiet=True)
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

    @classmethod
    def from_cloud(cls, cloud: str) -> 'StoreType':
        if cloud.lower() == str(clouds.AWS()).lower():
            return StoreType.S3
        elif cloud.lower() == str(clouds.GCP()).lower():
            return StoreType.GCS
        elif cloud.lower() == str(clouds.IBM()).lower():
            return StoreType.IBM
        elif cloud.lower() == cloudflare.NAME.lower():
            return StoreType.R2
        elif cloud.lower() == str(clouds.Azure()).lower():
            with ux_utils.print_exception_no_traceback():
                raise ValueError('Azure Blob Storage is not supported yet.')
        elif cloud.lower() == str(clouds.Lambda()).lower():
            with ux_utils.print_exception_no_traceback():
                raise ValueError('Lambda Cloud does not provide cloud storage.')
        elif cloud.lower() == str(clouds.SCP()).lower():
            with ux_utils.print_exception_no_traceback():
                raise ValueError('SCP does not provide cloud storage.')

        raise ValueError(f'Unsupported cloud for StoreType: {cloud}')

    @classmethod
    def from_store(cls, store: 'AbstractStore') -> 'StoreType':
        if isinstance(store, S3Store):
            return StoreType.S3
        elif isinstance(store, GcsStore):
            return StoreType.GCS
        elif isinstance(store, R2Store):
            return StoreType.R2
        elif isinstance(store, IBMCosStore):
            return StoreType.IBM
        else:
            with ux_utils.print_exception_no_traceback():
                raise ValueError(f'Unknown store type: {store}')

    def store_prefix(self) -> str:
        if self == StoreType.S3:
            return 's3://'
        elif self == StoreType.GCS:
            return 'gs://'
        elif self == StoreType.R2:
            return 'r2://'
        elif self == StoreType.IBM:
            return 'cos://'
        elif self == StoreType.AZURE:
            with ux_utils.print_exception_no_traceback():
                raise ValueError('Azure Blob Storage is not supported yet.')
        else:
            with ux_utils.print_exception_no_traceback():
                raise ValueError(f'Unknown store type: {self}')


class StorageMode(enum.Enum):
    MOUNT = 'MOUNT'
    COPY = 'COPY'


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
                 source: Optional[SourceType],
                 region: Optional[str] = None,
                 is_sky_managed: Optional[bool] = None,
                 sync_on_reconstruction: Optional[bool] = True):
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
                                                    metadata.is_sky_managed),
                   sync_on_reconstruction=override_args.get(
                       'sync_on_reconstruction', True))

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

        Includes the setup commands to install mounting tools.

        Args:
          mount_path: str; Mount path on remote server
        """
        raise NotImplementedError

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
                with ux_utils.print_exception_no_traceback():
                    store_prefix = StoreType.from_store(self).store_prefix()
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
                        f'`source: {store_prefix}{self.name}`.')


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

    def __init__(self,
                 name: Optional[str] = None,
                 source: Optional[SourceType] = None,
                 stores: Optional[Dict[StoreType, AbstractStore]] = None,
                 persistent: Optional[bool] = True,
                 mode: StorageMode = StorageMode.MOUNT,
                 sync_on_reconstruction: bool = True) -> None:
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
        """
        self.name: str
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
        self._validate_storage_spec(name)

        # Sky optimizer either adds a storage object instance or selects
        # from existing ones
        self.stores = {} if stores is None else stores

        # Logic to rebuild Storage if it is in global user state
        handle = global_user_state.get_handle_from_storage_name(self.name)
        if handle is not None:
            self.handle = handle
            # Reconstruct the Storage object from the global_user_state
            logger.debug('Detected existing storage object, '
                         f'loading Storage: {self.name}')
            self._add_store_from_metadata(self.handle.sky_stores)

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
                if s.is_sky_managed
            }
            self.handle = self.StorageMetadata(storage_name=self.name,
                                               source=self.source,
                                               mode=self.mode,
                                               sky_stores=sky_managed_stores)

            if self.source is not None:
                # If source is a pre-existing bucket, connect to the bucket
                # If the bucket does not exist, this will error out
                if isinstance(self.source, str):
                    if self.source.startswith('s3://'):
                        self.add_store(StoreType.S3)
                    elif self.source.startswith('gs://'):
                        self.add_store(StoreType.GCS)
                    elif self.source.startswith('r2://'):
                        self.add_store(StoreType.R2)
                    elif self.source.startswith('cos://'):
                        self.add_store(StoreType.IBM)

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
            elif split_path.scheme in ['s3', 'gs', 'r2', 'cos']:
                is_local_source = False
                # Storage mounting does not support mounting specific files from
                # cloud store - ensure path points to only a directory
                if mode == StorageMode.MOUNT:
                    if ((not split_path.scheme == 'cos' and
                         split_path.path.strip('/') != '') or
                        (split_path.scheme == 'cos' and
                         not re.match(r'^/[-\w]+(/\s*)?$', split_path.path))):
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
                        f'Supported paths: local, s3://, gs://, '
                        f'r2://, cos://. Got: {source}')
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
            if prefix in ['s3', 'gs', 'r2', 'cos']:
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
                if s_type == StoreType.S3:
                    store = S3Store.from_metadata(
                        s_metadata,
                        source=self.source,
                        sync_on_reconstruction=self.sync_on_reconstruction)
                elif s_type == StoreType.GCS:
                    store = GcsStore.from_metadata(
                        s_metadata,
                        source=self.source,
                        sync_on_reconstruction=self.sync_on_reconstruction)
                elif s_type == StoreType.R2:
                    store = R2Store.from_metadata(
                        s_metadata,
                        source=self.source,
                        sync_on_reconstruction=self.sync_on_reconstruction)
                elif s_type == StoreType.IBM:
                    store = IBMCosStore.from_metadata(
                        s_metadata,
                        source=self.source,
                        sync_on_reconstruction=self.sync_on_reconstruction)
                else:
                    with ux_utils.print_exception_no_traceback():
                        raise ValueError(f'Unknown store type: {s_type}')
            # Following error is raised from _get_bucket and caught only when
            # an externally removed storage is attempted to be fetched.
            except exceptions.StorageExternalDeletionError:
                logger.debug(f'Storage object {self.name!r} was attempted to '
                             'be reconstructed while the corresponding bucket'
                             ' was externally deleted.')
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
        if isinstance(store_type, str):
            store_type = StoreType(store_type)

        if store_type in self.stores:
            logger.info(f'Storage type {store_type} already exists.')
            return self.stores[store_type]

        store_cls: Type[AbstractStore]
        if store_type == StoreType.S3:
            store_cls = S3Store
        elif store_type == StoreType.GCS:
            store_cls = GcsStore
        elif store_type == StoreType.R2:
            store_cls = R2Store
        elif store_type == StoreType.IBM:
            store_cls = IBMCosStore
        else:
            with ux_utils.print_exception_no_traceback():
                raise exceptions.StorageSpecError(
                    f'{store_type} not supported as a Store.')

        # Initialize store object and get/create bucket
        try:
            store = store_cls(
                name=self.name,
                source=self.source,
                region=region,
                sync_on_reconstruction=self.sync_on_reconstruction)
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
        except exceptions.StorageSpecError:
            logger.error(f'Could not mount externally created {store_type}'
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
                delete = all(
                    s.is_sky_managed is False for s in self.stores.values())
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
        if force_delete is None:
            force_delete = False

        if isinstance(mode_str, str):
            # Make mode case insensitive, if specified
            mode = StorageMode(mode_str.upper())
        else:
            # Make sure this keeps the same as the default mode in __init__
            mode = StorageMode.MOUNT
        persistent = config.pop('persistent', None)
        if persistent is None:
            persistent = True

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

    _ACCESS_DENIED_MESSAGE = 'Access Denied'

    def __init__(self,
                 name: str,
                 source: str,
                 region: Optional[str] = 'us-east-2',
                 is_sky_managed: Optional[bool] = None,
                 sync_on_reconstruction: bool = True):
        self.client: 'boto3.client.Client'
        self.bucket: 'StorageHandle'
        super().__init__(name, source, region, is_sky_managed,
                         sync_on_reconstruction)

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
            elif self.source.startswith('r2://'):
                assert self.name == data_utils.split_r2_path(self.source)[0], (
                    'R2 Bucket is specified as path, the name should be '
                    'the same as R2 bucket.')
                assert data_utils.verify_r2_bucket(self.name), (
                    f'Source specified as {self.source}, a R2 bucket. ',
                    'R2 Bucket should exist.')
            elif self.source.startswith('cos://'):
                assert self.name == data_utils.split_cos_path(self.source)[0], (
                    'COS Bucket is specified as path, the name should be '
                    'the same as COS bucket.')
                assert data_utils.verify_ibm_cos_bucket(self.name), (
                    f'Source specified as {self.source}, a COS bucket. ',
                    'COS Bucket should exist.')
        # Validate name
        self.name = self.validate_name(self.name)

        # Check if the storage is enabled
        if not _is_storage_cloud_enabled(str(clouds.AWS())):
            with ux_utils.print_exception_no_traceback():
                raise exceptions.ResourcesUnavailableError(
                    'Storage \'store: s3\' specified, but ' \
                    'AWS access is disabled. To fix, enable '\
                    'AWS by running `sky check`. More info: '\
                    'https://skypilot.readthedocs.io/en/latest/getting-started/installation.html.' # pylint: disable=line-too-long
                    )

    @classmethod
    def validate_name(cls, name) -> str:
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
                elif self.source.startswith('r2://'):
                    self._transfer_to_s3()
                else:
                    self.batch_aws_rsync([self.source])
        except exceptions.StorageUploadError:
            raise
        except Exception as e:
            raise exceptions.StorageUploadError(
                f'Upload failed for store {self.name}') from e

    def delete(self) -> None:
        deleted_by_skypilot = self._delete_s3_bucket(self.name)
        if deleted_by_skypilot:
            msg_str = f'Deleted S3 bucket {self.name}.'
        else:
            msg_str = f'S3 bucket {self.name} may have been deleted ' \
                      f'externally. Removing from local state.'
        logger.info(f'{colorama.Fore.GREEN}{msg_str}'
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
            includes = ' '.join([
                f'--include {shlex.quote(file_name)}'
                for file_name in file_names
            ])
            base_dir_path = shlex.quote(base_dir_path)
            sync_command = ('aws s3 sync --no-follow-symlinks --exclude="*" '
                            f'{includes} {base_dir_path} '
                            f's3://{self.name}')
            return sync_command

        def get_dir_sync_command(src_dir_path, dest_dir_name):
            # we exclude .git directory from the sync
            excluded_list = storage_utils.get_excluded_files_from_gitignore(
                src_dir_path)
            excluded_list.append('.git/*')
            excludes = ' '.join([
                f'--exclude {shlex.quote(file_name)}'
                for file_name in excluded_list
            ])
            src_dir_path = shlex.quote(src_dir_path)
            sync_command = (f'aws s3 sync --no-follow-symlinks {excludes} '
                            f'{src_dir_path} '
                            f's3://{self.name}/{dest_dir_name}')
            return sync_command

        # Generate message for upload
        if len(source_path_list) > 1:
            source_message = f'{len(source_path_list)} paths'
        else:
            source_message = source_path_list[0]

        with rich_utils.safe_status(
                f'[bold cyan]Syncing '
                f'[green]{source_message}[/] to [green]s3://{self.name}/[/]'):
            data_utils.parallel_upload(
                source_path_list,
                get_file_sync_command,
                get_dir_sync_command,
                self.name,
                self._ACCESS_DENIED_MESSAGE,
                create_dirs=create_dirs,
                max_concurrent_uploads=_MAX_CONCURRENT_UPLOADS)

    def _transfer_to_s3(self) -> None:
        assert isinstance(self.source, str), self.source
        if self.source.startswith('gs://'):
            data_transfer.gcs_to_s3(self.name, self.name)
        elif self.source.startswith('r2://'):
            data_transfer.r2_to_s3(self.name, self.name)

    def _get_bucket(self) -> Tuple[StorageHandle, bool]:
        """Obtains the S3 bucket.

        If the bucket exists, this method will return the bucket.
        If the bucket does not exist, there are three cases:
          1) Raise an error if the bucket source starts with s3://
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
        s3 = aws.resource('s3')
        bucket = s3.Bucket(self.name)

        try:
            # Try Public bucket case.
            # This line does not error out if the bucket is an external public
            # bucket or if it is a user's bucket that is publicly
            # accessible.
            self.client.head_bucket(Bucket=self.name)
            self._validate_existing_bucket()
            return bucket, False
        except aws.botocore_exceptions().ClientError as e:
            error_code = e.response['Error']['Code']
            # AccessDenied error for buckets that are private and not owned by
            # user.
            if error_code == '403':
                command = f'aws s3 ls {self.name}'
                with ux_utils.print_exception_no_traceback():
                    raise exceptions.StorageBucketGetError(
                        _BUCKET_FAIL_TO_CONNECT_MESSAGE.format(name=self.name) +
                        f' To debug, consider running `{command}`.') from e

        if isinstance(self.source, str) and self.source.startswith('s3://'):
            with ux_utils.print_exception_no_traceback():
                raise exceptions.StorageBucketGetError(
                    'Attempted to use a non-existent bucket as a source: '
                    f'{self.source}. Consider using `aws s3 ls '
                    f'{self.source}` to debug.')

        # If bucket cannot be found in both private and public settings,
        # the bucket is to be created by Sky. However, creation is skipped if
        # Store object is being reconstructed for deletion or re-mount with
        # sky start, and error is raised instead.
        if self.sync_on_reconstruction:
            bucket = self._create_s3_bucket(self.name, self.region)
            return bucket, True
        else:
            # Raised when Storage object is reconstructed for sky storage
            # delete or to re-mount Storages with sky start but the storage
            # is already removed externally.
            raise exceptions.StorageExternalDeletionError(
                'Attempted to fetch a non-existent bucket: '
                f'{self.name}')

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
        install_cmd = mounting_utils.get_s3_mount_install_cmd()
        mount_cmd = mounting_utils.get_s3_mount_cmd(self.bucket.name,
                                                    mount_path)
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
                if region == 'us-east-1':
                    # If default us-east-1 region is used, the
                    # LocationConstraint must not be specified.
                    # https://stackoverflow.com/a/51912090
                    s3_client.create_bucket(Bucket=bucket_name)
                else:
                    location = {'LocationConstraint': region}
                    s3_client.create_bucket(Bucket=bucket_name,
                                            CreateBucketConfiguration=location)
                logger.info(f'Created S3 bucket {bucket_name} in {region}')
        except aws.botocore_exceptions().ClientError as e:
            with ux_utils.print_exception_no_traceback():
                raise exceptions.StorageBucketCreateError(
                    f'Attempted to create a bucket '
                    f'{self.name} but failed.') from e
        return aws.resource('s3').Bucket(bucket_name)

    def _delete_s3_bucket(self, bucket_name: str) -> bool:
        """Deletes S3 bucket, including all objects in bucket

        Args:
          bucket_name: str; Name of bucket

        Returns:
         bool; True if bucket was deleted, False if it was deleted externally.
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
            with rich_utils.safe_status(
                    f'[bold cyan]Deleting S3 bucket {bucket_name}[/]'):
                subprocess.check_output(remove_command.split(' '),
                                        stderr=subprocess.STDOUT)
        except subprocess.CalledProcessError as e:
            if 'NoSuchBucket' in e.output.decode('utf-8'):
                logger.debug(
                    _BUCKET_EXTERNALLY_DELETED_DEBUG_MESSAGE.format(
                        bucket_name=bucket_name))
                return False
            else:
                logger.error(e.output)
                with ux_utils.print_exception_no_traceback():
                    raise exceptions.StorageBucketDeleteError(
                        f'Failed to delete S3 bucket {bucket_name}.')

        # Wait until bucket deletion propagates on AWS servers
        while data_utils.verify_s3_bucket(bucket_name):
            time.sleep(0.1)
        return True


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
                 sync_on_reconstruction: Optional[bool] = True):
        self.client: 'storage.Client'
        self.bucket: StorageHandle
        super().__init__(name, source, region, is_sky_managed,
                         sync_on_reconstruction)

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
                elif self.source.startswith('r2://'):
                    assert self.name == data_utils.split_r2_path(
                        self.source
                    )[0], ('R2 Bucket is specified as path, the name should be '
                           'the same as R2 bucket.')
                    assert data_utils.verify_r2_bucket(self.name), (
                        f'Source specified as {self.source}, a R2 bucket. ',
                        'R2 Bucket should exist.')
                elif self.source.startswith('cos://'):
                    assert self.name == data_utils.split_cos_path(
                        self.source
                    )[0], (
                        'COS Bucket is specified as path, the name should be '
                        'the same as COS bucket.')
                    assert data_utils.verify_ibm_cos_bucket(self.name), (
                        f'Source specified as {self.source}, a COS bucket. ',
                        'COS Bucket should exist.')
        # Validate name
        self.name = self.validate_name(self.name)
        # Check if the storage is enabled
        if not _is_storage_cloud_enabled(str(clouds.GCP())):
            with ux_utils.print_exception_no_traceback():
                raise exceptions.ResourcesUnavailableError(
                    'Storage \'store: gcs\' specified, but '
                    'GCP access is disabled. To fix, enable '
                    'GCP by running `sky check`. '
                    'More info: https://skypilot.readthedocs.io/en/latest/getting-started/installation.html.')  # pylint: disable=line-too-long

    @classmethod
    def validate_name(cls, name) -> str:
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
        deleted_by_skypilot = self._delete_gcs_bucket(self.name)
        if deleted_by_skypilot:
            msg_str = f'Deleted GCS bucket {self.name}.'
        else:
            msg_str = f'GCS bucket {self.name} may have been deleted ' \
                      f'externally. Removing from local state.'
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
        sync_command = (f'{alias_gen}; echo "{copy_list}" | {gsutil_alias} '
                        f'cp -e -n -r -I gs://{self.name}')

        with rich_utils.safe_status(
                f'[bold cyan]Syncing '
                f'[green]{source_message}[/] to [green]gs://{self.name}/[/]'):
            data_utils.run_upload_cli(sync_command,
                                      self._ACCESS_DENIED_MESSAGE,
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
            gsutil_alias, alias_gen = data_utils.get_gsutil_command()
            base_dir_path = shlex.quote(base_dir_path)
            sync_command = (f'{alias_gen}; {gsutil_alias} '
                            f'rsync -e -x \'^(?!{sync_format}$).*\' '
                            f'{base_dir_path} gs://{self.name}')
            return sync_command

        def get_dir_sync_command(src_dir_path, dest_dir_name):
            excluded_list = storage_utils.get_excluded_files_from_gitignore(
                src_dir_path)
            # we exclude .git directory from the sync
            excluded_list.append(r'^\.git/.*$')
            excludes = '|'.join(excluded_list)
            gsutil_alias, alias_gen = data_utils.get_gsutil_command()
            src_dir_path = shlex.quote(src_dir_path)
            sync_command = (f'{alias_gen}; {gsutil_alias} '
                            f'rsync -e -r -x \'({excludes})\' {src_dir_path} '
                            f'gs://{self.name}/{dest_dir_name}')
            return sync_command

        # Generate message for upload
        if len(source_path_list) > 1:
            source_message = f'{len(source_path_list)} paths'
        else:
            source_message = source_path_list[0]

        with rich_utils.safe_status(
                f'[bold cyan]Syncing '
                f'[green]{source_message}[/] to [green]gs://{self.name}/[/]'):
            data_utils.parallel_upload(
                source_path_list,
                get_file_sync_command,
                get_dir_sync_command,
                self.name,
                self._ACCESS_DENIED_MESSAGE,
                create_dirs=create_dirs,
                max_concurrent_uploads=_MAX_CONCURRENT_UPLOADS)

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
                                                     mount_path)
        version_check_cmd = (
            f'gcsfuse --version | grep -q {mounting_utils.GCSFUSE_VERSION}')
        return mounting_utils.get_mounting_command(mount_path, install_cmd,
                                                   mount_cmd, version_check_cmd)

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

    def _delete_gcs_bucket(self, bucket_name: str) -> bool:
        """Deletes GCS bucket, including all objects in bucket

        Args:
          bucket_name: str; Name of bucket

        Returns:
         bool; True if bucket was deleted, False if it was deleted externally.
        """

        with rich_utils.safe_status(
                f'[bold cyan]Deleting GCS bucket {bucket_name}[/]'):
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
                remove_obj_command = (f'{alias_gen};{gsutil_alias} '
                                      f'rm -r gs://{bucket_name}')
                subprocess.check_output(remove_obj_command,
                                        stderr=subprocess.STDOUT,
                                        shell=True,
                                        executable='/bin/bash')
                return True
            except subprocess.CalledProcessError as e:
                logger.error(e.output)
                with ux_utils.print_exception_no_traceback():
                    raise exceptions.StorageBucketDeleteError(
                        f'Failed to delete GCS bucket {bucket_name}.')


class R2Store(AbstractStore):
    """R2Store inherits from S3Store Object and represents the backend
    for R2 buckets.
    """

    _ACCESS_DENIED_MESSAGE = 'Access Denied'

    def __init__(self,
                 name: str,
                 source: str,
                 region: Optional[str] = 'auto',
                 is_sky_managed: Optional[bool] = None,
                 sync_on_reconstruction: Optional[bool] = True):
        self.client: 'boto3.client.Client'
        self.bucket: 'StorageHandle'
        super().__init__(name, source, region, is_sky_managed,
                         sync_on_reconstruction)

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
            elif self.source.startswith('r2://'):
                assert self.name == data_utils.split_r2_path(self.source)[0], (
                    'R2 Bucket is specified as path, the name should be '
                    'the same as R2 bucket.')
            elif self.source.startswith('cos://'):
                assert self.name == data_utils.split_cos_path(self.source)[0], (
                    'IBM COS Bucket is specified as path, the name should be '
                    'the same as COS bucket.')
                assert data_utils.verify_ibm_cos_bucket(self.name), (
                    f'Source specified as {self.source}, a COS bucket. ',
                    'COS Bucket should exist.')
        # Validate name
        self.name = S3Store.validate_name(self.name)
        # Check if the storage is enabled
        if not _is_storage_cloud_enabled(cloudflare.NAME):
            with ux_utils.print_exception_no_traceback():
                raise exceptions.ResourcesUnavailableError(
                    'Storage \'store: r2\' specified, but ' \
                    'Cloudflare R2 access is disabled. To fix, '\
                    'enable Cloudflare R2 by running `sky check`. '\
                    'More info: https://skypilot.readthedocs.io/en/latest/getting-started/installation.html.'  # pylint: disable=line-too-long
                    )

    def initialize(self):
        """Initializes the R2 store object on the cloud.

        Initialization involves fetching bucket if exists, or creating it if
        it does not.

        Raises:
          StorageBucketCreateError: If bucket creation fails
          StorageBucketGetError: If fetching existing bucket fails
          StorageInitError: If general initialization fails.
        """
        self.client = data_utils.create_r2_client(self.region)
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
                    self._transfer_to_r2()
                elif self.source.startswith('gs://'):
                    self._transfer_to_r2()
                elif self.source.startswith('r2://'):
                    pass
                else:
                    self.batch_aws_rsync([self.source])
        except exceptions.StorageUploadError:
            raise
        except Exception as e:
            raise exceptions.StorageUploadError(
                f'Upload failed for store {self.name}') from e

    def delete(self) -> None:
        deleted_by_skypilot = self._delete_r2_bucket(self.name)
        if deleted_by_skypilot:
            msg_str = f'Deleted R2 bucket {self.name}.'
        else:
            msg_str = f'R2 bucket {self.name} may have been deleted ' \
                      f'externally. Removing from local state.'
        logger.info(f'{colorama.Fore.GREEN}{msg_str}'
                    f'{colorama.Style.RESET_ALL}')

    def get_handle(self) -> StorageHandle:
        return cloudflare.resource('s3').Bucket(self.name)

    def batch_aws_rsync(self,
                        source_path_list: List[Path],
                        create_dirs: bool = False) -> None:
        """Invokes aws s3 sync to batch upload a list of local paths to R2

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
            includes = ' '.join([
                f'--include {shlex.quote(file_name)}'
                for file_name in file_names
            ])
            endpoint_url = cloudflare.create_endpoint()
            base_dir_path = shlex.quote(base_dir_path)
            sync_command = ('AWS_SHARED_CREDENTIALS_FILE='
                            f'{cloudflare.R2_CREDENTIALS_PATH} '
                            'aws s3 sync --no-follow-symlinks --exclude="*" '
                            f'{includes} {base_dir_path} '
                            f's3://{self.name} '
                            f'--endpoint {endpoint_url} '
                            f'--profile={cloudflare.R2_PROFILE_NAME}')
            return sync_command

        def get_dir_sync_command(src_dir_path, dest_dir_name):
            # we exclude .git directory from the sync
            excluded_list = storage_utils.get_excluded_files_from_gitignore(
                src_dir_path)
            excluded_list.append('.git/*')
            excludes = ' '.join([
                f'--exclude {shlex.quote(file_name)}'
                for file_name in excluded_list
            ])
            endpoint_url = cloudflare.create_endpoint()
            src_dir_path = shlex.quote(src_dir_path)
            sync_command = ('AWS_SHARED_CREDENTIALS_FILE='
                            f'{cloudflare.R2_CREDENTIALS_PATH} '
                            f'aws s3 sync --no-follow-symlinks {excludes} '
                            f'{src_dir_path} '
                            f's3://{self.name}/{dest_dir_name} '
                            f'--endpoint {endpoint_url} '
                            f'--profile={cloudflare.R2_PROFILE_NAME}')
            return sync_command

        # Generate message for upload
        if len(source_path_list) > 1:
            source_message = f'{len(source_path_list)} paths'
        else:
            source_message = source_path_list[0]

        with rich_utils.safe_status(
                f'[bold cyan]Syncing '
                f'[green]{source_message}[/] to [green]r2://{self.name}/[/]'):
            data_utils.parallel_upload(
                source_path_list,
                get_file_sync_command,
                get_dir_sync_command,
                self.name,
                self._ACCESS_DENIED_MESSAGE,
                create_dirs=create_dirs,
                max_concurrent_uploads=_MAX_CONCURRENT_UPLOADS)

    def _transfer_to_r2(self) -> None:
        assert isinstance(self.source, str), self.source
        if self.source.startswith('gs://'):
            data_transfer.gcs_to_r2(self.name, self.name)
        elif self.source.startswith('s3://'):
            data_transfer.s3_to_r2(self.name, self.name)

    def _get_bucket(self) -> Tuple[StorageHandle, bool]:
        """Obtains the R2 bucket.

        If the bucket exists, this method will return the bucket.
        If the bucket does not exist, there are three cases:
          1) Raise an error if the bucket source starts with s3://
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
        r2 = cloudflare.resource('s3')
        bucket = r2.Bucket(self.name)
        endpoint_url = cloudflare.create_endpoint()
        try:
            # Try Public bucket case.
            # This line does not error out if the bucket is an external public
            # bucket or if it is a user's bucket that is publicly
            # accessible.
            self.client.head_bucket(Bucket=self.name)
            self._validate_existing_bucket()
            return bucket, False
        except aws.botocore_exceptions().ClientError as e:
            error_code = e.response['Error']['Code']
            # AccessDenied error for buckets that are private and not owned by
            # user.
            if error_code == '403':
                command = ('AWS_SHARED_CREDENTIALS_FILE='
                           f'{cloudflare.R2_CREDENTIALS_PATH} '
                           f'aws s3 ls s3://{self.name} '
                           f'--endpoint {endpoint_url} '
                           f'--profile={cloudflare.R2_PROFILE_NAME}')
                with ux_utils.print_exception_no_traceback():
                    raise exceptions.StorageBucketGetError(
                        _BUCKET_FAIL_TO_CONNECT_MESSAGE.format(name=self.name) +
                        f' To debug, consider running `{command}`.') from e

        if isinstance(self.source, str) and self.source.startswith('r2://'):
            with ux_utils.print_exception_no_traceback():
                raise exceptions.StorageBucketGetError(
                    'Attempted to use a non-existent bucket as a source: '
                    f'{self.source}. Consider using '
                    '`AWS_SHARED_CREDENTIALS_FILE='
                    f'{cloudflare.R2_CREDENTIALS_PATH} aws s3 ls '
                    f's3://{self.name} '
                    f'--endpoint {endpoint_url} '
                    f'--profile={cloudflare.R2_PROFILE_NAME}\' '
                    'to debug.')

        # If bucket cannot be found in both private and public settings,
        # the bucket is to be created by Sky. However, creation is skipped if
        # Store object is being reconstructed for deletion or re-mount with
        # sky start, and error is raised instead.
        if self.sync_on_reconstruction:
            bucket = self._create_r2_bucket(self.name)
            return bucket, True
        else:
            # Raised when Storage object is reconstructed for sky storage
            # delete or to re-mount Storages with sky start but the storage
            # is already removed externally.
            raise exceptions.StorageExternalDeletionError(
                'Attempted to fetch a non-existent bucket: '
                f'{self.name}')

    def _download_file(self, remote_path: str, local_path: str) -> None:
        """Downloads file from remote to local on r2 bucket
        using the boto3 API

        Args:
          remote_path: str; Remote path on R2 bucket
          local_path: str; Local path on user's device
        """
        self.bucket.download_file(remote_path, local_path)

    def mount_command(self, mount_path: str) -> str:
        """Returns the command to mount the bucket to the mount_path.

        Uses goofys to mount the bucket.

        Args:
          mount_path: str; Path to mount the bucket to.
        """
        install_cmd = mounting_utils.get_s3_mount_install_cmd()
        endpoint_url = cloudflare.create_endpoint()
        r2_credential_path = cloudflare.R2_CREDENTIALS_PATH
        r2_profile_name = cloudflare.R2_PROFILE_NAME
        mount_cmd = mounting_utils.get_r2_mount_cmd(r2_credential_path,
                                                    r2_profile_name,
                                                    endpoint_url,
                                                    self.bucket.name,
                                                    mount_path)
        return mounting_utils.get_mounting_command(mount_path, install_cmd,
                                                   mount_cmd)

    def _create_r2_bucket(self,
                          bucket_name: str,
                          region='auto') -> StorageHandle:
        """Creates R2 bucket with specific name in specific region

        Args:
          bucket_name: str; Name of bucket
          region: str; Region name, r2 automatically sets region
        Raises:
          StorageBucketCreateError: If bucket creation fails.
        """
        r2_client = self.client
        try:
            if region is None:
                r2_client.create_bucket(Bucket=bucket_name)
            else:
                location = {'LocationConstraint': region}
                r2_client.create_bucket(Bucket=bucket_name,
                                        CreateBucketConfiguration=location)
                logger.info(f'Created R2 bucket {bucket_name} in {region}')
        except aws.botocore_exceptions().ClientError as e:
            with ux_utils.print_exception_no_traceback():
                raise exceptions.StorageBucketCreateError(
                    f'Attempted to create a bucket '
                    f'{self.name} but failed.') from e
        return cloudflare.resource('s3').Bucket(bucket_name)

    def _delete_r2_bucket(self, bucket_name: str) -> bool:
        """Deletes R2 bucket, including all objects in bucket

        Args:
          bucket_name: str; Name of bucket

        Returns:
         bool; True if bucket was deleted, False if it was deleted externally.
        """
        # Deleting objects is very slow programatically
        # (i.e. bucket.objects.all().delete() is slow).
        # In addition, standard delete operations (i.e. via `aws s3 rm`)
        # are slow, since AWS puts deletion markers.
        # https://stackoverflow.com/questions/49239351/why-is-it-so-much-slower-to-delete-objects-in-aws-s3-than-it-is-to-create-them
        # The fastest way to delete is to run `aws s3 rb --force`,
        # which removes the bucket by force.
        endpoint_url = cloudflare.create_endpoint()
        remove_command = (
            f'AWS_SHARED_CREDENTIALS_FILE={cloudflare.R2_CREDENTIALS_PATH} '
            f'aws s3 rb s3://{bucket_name} --force '
            f'--endpoint {endpoint_url} '
            f'--profile={cloudflare.R2_PROFILE_NAME}')
        try:
            with rich_utils.safe_status(
                    f'[bold cyan]Deleting R2 bucket {bucket_name}[/]'):
                subprocess.check_output(remove_command,
                                        stderr=subprocess.STDOUT,
                                        shell=True)
        except subprocess.CalledProcessError as e:
            if 'NoSuchBucket' in e.output.decode('utf-8'):
                logger.debug(
                    _BUCKET_EXTERNALLY_DELETED_DEBUG_MESSAGE.format(
                        bucket_name=bucket_name))
                return False
            else:
                logger.error(e.output)
                with ux_utils.print_exception_no_traceback():
                    raise exceptions.StorageBucketDeleteError(
                        f'Failed to delete R2 bucket {bucket_name}.')

        # Wait until bucket deletion propagates on AWS servers
        while data_utils.verify_r2_bucket(bucket_name):
            time.sleep(0.1)
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
                 sync_on_reconstruction: bool = True):
        self.client: 'storage.Client'
        self.bucket: 'StorageHandle'
        super().__init__(name, source, region, is_sky_managed,
                         sync_on_reconstruction)
        self.bucket_rclone_profile = \
          Rclone.generate_rclone_bucket_profile_name(
            self.name, Rclone.RcloneClouds.IBM)

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
            elif self.source.startswith('r2://'):
                assert self.name == data_utils.split_r2_path(self.source)[0], (
                    'R2 Bucket is specified as path, the name should be '
                    'the same as R2 bucket.')
                assert data_utils.verify_r2_bucket(self.name), (
                    f'Source specified as {self.source}, a R2 bucket. ',
                    'R2 Bucket should exist.')
            elif self.source.startswith('cos://'):
                assert self.name == data_utils.split_cos_path(self.source)[0], (
                    'COS Bucket is specified as path, the name should be '
                    'the same as COS bucket.')
        # Validate name
        self.name = IBMCosStore.validate_name(self.name)

    @classmethod
    def validate_name(cls, name) -> str:
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
        self._delete_cos_bucket()
        logger.info(f'{colorama.Fore.GREEN}Deleted COS bucket {self.name}.'
                    f'{colorama.Style.RESET_ALL}')

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
            sync_command = (
                'rclone copy --exclude ".git/*" '
                f'{src_dir_path} '
                f'{self.bucket_rclone_profile}:{self.name}/{dest_dir_name}')
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
                            f'{self.bucket_rclone_profile}:{self.name}')
            return sync_command

        # Generate message for upload
        if len(source_path_list) > 1:
            source_message = f'{len(source_path_list)} paths'
        else:
            source_message = source_path_list[0]

        with rich_utils.safe_status(
                f'[bold cyan]Syncing '
                f'[green]{source_message}[/] to '
                f'[green]cos://{self.region}/{self.name}/[/]'):
            data_utils.parallel_upload(
                source_path_list,
                get_file_sync_command,
                get_dir_sync_command,
                self.name,
                self._ACCESS_DENIED_MESSAGE,
                create_dirs=create_dirs,
                max_concurrent_uploads=_MAX_CONCURRENT_UPLOADS)

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

        bucket_profile_name = Rclone.RcloneClouds.IBM.value + self.name
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

        Rclone.store_rclone_config(
            self.name,
            Rclone.RcloneClouds.IBM,
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
        install_cmd = mounting_utils.get_cos_mount_install_cmd()
        rclone_config_data = Rclone.get_rclone_config(
            self.bucket.name,
            Rclone.RcloneClouds.IBM,
            self.region,  # type: ignore
        )
        mount_cmd = mounting_utils.get_cos_mount_cmd(rclone_config_data,
                                                     Rclone.RCLONE_CONFIG_PATH,
                                                     self.bucket_rclone_profile,
                                                     self.bucket.name,
                                                     mount_path)
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
            logger.info(f'Created IBM COS bucket {bucket_name} in {region} '
                        f'with storage class smart tier')
            self.bucket = self.s3_resource.Bucket(bucket_name)

        except ibm.ibm_botocore.exceptions.ClientError as e:  # type: ignore[union-attr]  # pylint: disable=line-too-long
            with ux_utils.print_exception_no_traceback():
                raise exceptions.StorageBucketCreateError(
                    f'Failed to create bucket: '
                    f'{bucket_name}') from e

        s3_bucket_exists_waiter = self.client.get_waiter('bucket_exists')
        s3_bucket_exists_waiter.wait(Bucket=bucket_name)

        return self.bucket

    def _delete_cos_bucket(self):
        bucket = self.s3_resource.Bucket(self.name)
        try:
            bucket_versioning = self.s3_resource.BucketVersioning(self.name)
            if bucket_versioning.status == 'Enabled':
                res = list(bucket.object_versions.delete())
            else:
                res = list(bucket.objects.delete())
            logger.debug(f'Deleted bucket\'s content:\n{res}')
            bucket.delete()
            bucket.wait_until_not_exists()
        except ibm.ibm_botocore.exceptions.ClientError as e:
            if e.__class__.__name__ == 'NoSuchBucket':
                logger.debug('bucket already removed')
        Rclone.delete_rclone_bucket_profile(self.name, Rclone.RcloneClouds.IBM)
