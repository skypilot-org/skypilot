"""Miscellaneous Utils for Sky Data
"""
import concurrent.futures
import enum
from multiprocessing import pool
import os
import re
import subprocess
import textwrap
import time
from typing import Any, Callable, Dict, List, Optional, Tuple
import urllib.parse

from filelock import FileLock

from sky import clouds
from sky import exceptions
from sky import sky_logging
from sky.adaptors import aws
from sky.adaptors import azure
from sky.adaptors import cloudflare
from sky.adaptors import coreweave
from sky.adaptors import gcp
from sky.adaptors import ibm
from sky.adaptors import nebius
from sky.adaptors import oci
from sky.skylet import constants
from sky.skylet import log_lib
from sky.utils import common_utils
from sky.utils import ux_utils

Client = Any

logger = sky_logging.init_logger(__name__)

AZURE_CONTAINER_URL = (
    'https://{storage_account_name}.blob.core.windows.net/{container_name}')

# Retry 5 times by default for delayed propagation to Azure system
# when creating Storage Account.
_STORAGE_ACCOUNT_KEY_RETRIEVE_MAX_ATTEMPT = 5


def split_s3_path(s3_path: str) -> Tuple[str, str]:
    """Splits S3 Path into Bucket name and Relative Path to Bucket

    Args:
      s3_path: str; S3 Path, e.g. s3://imagenet/train/
    """
    path_parts = s3_path.replace('s3://', '').split('/')
    bucket = path_parts.pop(0)
    key = '/'.join(path_parts)
    return bucket, key


def split_gcs_path(gcs_path: str) -> Tuple[str, str]:
    """Splits GCS Path into Bucket name and Relative Path to Bucket

    Args:
      gcs_path: str; GCS Path, e.g. gcs://imagenet/train/
    """
    path_parts = gcs_path.replace('gs://', '').split('/')
    bucket = path_parts.pop(0)
    key = '/'.join(path_parts)
    return bucket, key


def split_az_path(az_path: str) -> Tuple[str, str, str]:
    """Splits Path into Storage account and Container names and Relative Path

    Args:
        az_path: Container Path,
          e.g. https://azureopendatastorage.blob.core.windows.net/nyctlc

    Returns:
        str: Name of the storage account
        str: Name of the container
        str: Paths of the file/directory defined within the container
    """
    path_parts = az_path.replace('https://', '').split('/')
    service_endpoint = path_parts.pop(0)
    service_endpoint_parts = service_endpoint.split('.')
    storage_account_name = service_endpoint_parts[0]
    container_name = path_parts.pop(0)
    path = '/'.join(path_parts)

    return storage_account_name, container_name, path


def split_r2_path(r2_path: str) -> Tuple[str, str]:
    """Splits R2 Path into Bucket name and Relative Path to Bucket

    Args:
      r2_path: str; R2 Path, e.g. r2://imagenet/train/
    """
    path_parts = r2_path.replace('r2://', '').split('/')
    bucket = path_parts.pop(0)
    key = '/'.join(path_parts)
    return bucket, key


def split_nebius_path(nebius_path: str) -> Tuple[str, str]:
    """Splits Nebius Path into Bucket name and Relative Path to Bucket

    Args:
      nebius_path: str; Nebius Path, e.g. nebius://imagenet/train/
    """
    path_parts = nebius_path.replace('nebius://', '').split('/')
    bucket = path_parts.pop(0)
    key = '/'.join(path_parts)
    return bucket, key


def split_cos_path(s3_path: str) -> Tuple[str, str, str]:
    """returns extracted region, bucket name and bucket path to data
        from the specified cos bucket's url.
        url expected format: "cos://region/bucket_name/optional_data_path"

        Raises:
          ValueError if s3_path isn't a valid IBM COS bucket URI."""

    # regex pattern: 3rd group differs from 2nd by accepting '/'
    pattern_region_bucket_data = r'cos://([-\w]+)/([-\w]+)(.*)'
    try:
        match = re.match(pattern_region_bucket_data, s3_path)
    except TypeError:
        # URI provided isn't a string
        raise ValueError(f'URI received: {s3_path} is not valid.')  # pylint: disable=raise-missing-from
    if match:
        region, bucket_name, data_path = match.group(1), match.group(
            2), match.group(3)
    else:
        raise ValueError(
            f'Bucket URI received: {s3_path} does not match expected pattern '
            'of "cos://region/bucket_name/optional_data_path"')

    if region not in get_cos_regions():
        raise ValueError('region missing/invalid in IBM COS URI.')

    return bucket_name, data_path, region


def create_s3_client(region: Optional[str] = None) -> Client:
    """Helper method that connects to Boto3 client for S3 Bucket

    Args:
      region: str; Region name, e.g. us-west-1, us-east-2. If None, default
        region us-east-1 is used.
    """
    if region is None:
        region = 'us-east-1'
    return aws.client('s3', region_name=region)


def verify_s3_bucket(name: str) -> bool:
    """Helper method that checks if the S3 bucket exists

    Args:
      name: str; Name of S3 Bucket (without s3:// prefix)
    """
    s3 = aws.resource('s3')
    bucket = s3.Bucket(name)
    return bucket in s3.buckets.all()


def verify_gcs_bucket(name: str) -> bool:
    """Helper method that checks if the GCS bucket exists

    Args:
      name: str; Name of GCS Bucket (without gs:// prefix)
    """
    try:
        gcp.storage_client().get_bucket(name)
        return True
    except gcp.not_found_exception():
        return False


def create_az_client(client_type: str, **kwargs: Any) -> Client:
    """Helper method that connects to AZ client for diverse Resources.

    Args:
      client_type: str; specify client type, e.g. storage, resource, container

    Returns:
        Client object facing AZ Resource of the 'client_type'.
    """
    resource_group_name = kwargs.pop('resource_group_name', None)
    container_url = kwargs.pop('container_url', None)
    storage_account_name = kwargs.pop('storage_account_name', None)
    refresh_client = kwargs.pop('refresh_client', False)
    if client_type == 'container':
        # We do not assert on resource_group_name as it is set to None when the
        # container_url is for public container with user access.
        assert container_url is not None, ('container_url must be provided for '
                                           'container client')
        assert storage_account_name is not None, ('storage_account_name must '
                                                  'be provided for container '
                                                  'client')

    if refresh_client:
        azure.get_client.cache_clear()

    subscription_id = azure.get_subscription_id()
    client = azure.get_client(client_type,
                              subscription_id,
                              container_url=container_url,
                              storage_account_name=storage_account_name,
                              resource_group_name=resource_group_name)
    return client


def verify_az_bucket(storage_account_name: str, container_name: str) -> bool:
    """Helper method that checks if the AZ Container exists

    Args:
      storage_account_name: str; Name of the storage account
      container_name: str; Name of the container

    Returns:
      True if the container exists, False otherwise.
    """
    container_url = AZURE_CONTAINER_URL.format(
        storage_account_name=storage_account_name,
        container_name=container_name)
    resource_group_name = azure.get_az_resource_group(storage_account_name)
    container_client = create_az_client(
        client_type='container',
        container_url=container_url,
        storage_account_name=storage_account_name,
        resource_group_name=resource_group_name)
    return container_client.exists()


def get_az_storage_account_key(
    storage_account_name: str,
    resource_group_name: Optional[str] = None,
    storage_client: Optional[Client] = None,
    resource_client: Optional[Client] = None,
) -> Optional[str]:
    """Returns access key of the given name of storage account.

    Args:
        storage_account_name: Name of the storage account
        resource_group_name: Name of the resource group the
            passed storage account belongs to.
        storage_clent: Client object facing Storage
        resource_client: Client object facing Resource

    Returns:
        One of the two access keys to the given storage account, or None if
        the account is not found.
    """
    if resource_client is None:
        resource_client = create_az_client('resource')
    if storage_client is None:
        storage_client = create_az_client('storage')
    if resource_group_name is None:
        resource_group_name = azure.get_az_resource_group(
            storage_account_name, storage_client)
    # resource_group_name is None when using a public container or
    # a private container not belonging to the user.
    if resource_group_name is None:
        return None

    attempt = 0
    backoff = common_utils.Backoff()
    while True:
        storage_account_keys = None
        resources = resource_client.resources.list_by_resource_group(
            resource_group_name)
        # resource group is either created or read when Storage initializes.
        assert resources is not None
        for resource in resources:
            if (resource.type == 'Microsoft.Storage/storageAccounts' and
                    resource.name == storage_account_name):
                assert storage_account_keys is None
                keys = storage_client.storage_accounts.list_keys(
                    resource_group_name, storage_account_name)
                storage_account_keys = [key.value for key in keys.keys]
        # If storage account was created right before call to this method,
        # it is possible to fail to retrieve the key as the creation did not
        # propagate to Azure yet. We retry several times.
        if storage_account_keys is None:
            attempt += 1
            time.sleep(backoff.current_backoff())
            if attempt > _STORAGE_ACCOUNT_KEY_RETRIEVE_MAX_ATTEMPT:
                raise RuntimeError('Failed to obtain key value of storage '
                                   f'account {storage_account_name!r}. '
                                   'Check if the storage account was created.')
            continue
        # Azure provides two sets of working storage account keys and we use
        # one of it.
        storage_account_key = storage_account_keys[0]
        return storage_account_key


def is_az_container_endpoint(endpoint_url: str) -> bool:
    """Checks if provided url follows a valid container endpoint naming format.

    Args:
      endpoint_url: Url of container endpoint.
        e.g. https://azureopendatastorage.blob.core.windows.net/nyctlc

    Returns:
      bool: True if the endpoint is valid, False otherwise.
    """
    # Storage account must be length of 3-24
    # Reference: https://learn.microsoft.com/en-us/azure/azure-resource-manager/management/resource-name-rules#microsoftstorage # pylint: disable=line-too-long
    pattern = re.compile(
        r'^https://([a-z0-9]{3,24})\.blob\.core\.windows\.net(/[^/]+)*$')
    match = pattern.match(endpoint_url)
    if match is None:
        return False
    return True


def create_r2_client(region: str = 'auto') -> Client:
    """Helper method that connects to Boto3 client for R2 Bucket

    Args:
      region: str; Region for CLOUDFLARE R2 is set to auto
    """
    return cloudflare.client('s3', region)


def create_nebius_client() -> Client:
    """Helper method that connects to Boto3 client for Nebius Object Storage"""
    return nebius.client('s3')


def verify_r2_bucket(name: str) -> bool:
    """Helper method that checks if the R2 bucket exists

    Args:
      name: str; Name of R2 Bucket (without r2:// prefix)
    """
    r2 = cloudflare.resource('s3')
    bucket = r2.Bucket(name)
    return bucket in r2.buckets.all()


def verify_nebius_bucket(name: str) -> bool:
    """Helper method that checks if the Nebius bucket exists

    Args:
      name: str; Name of Nebius Object Storage (without nebius:// prefix)
    """
    nebius_s = nebius.resource('s3')
    bucket = nebius_s.Bucket(name)
    return bucket in nebius_s.buckets.all()


def verify_ibm_cos_bucket(name: str) -> bool:
    """Helper method that checks if the cos bucket exists

    Args:
      name: str; Name of a COS Bucket (without cos://region/ prefix)
    """
    return get_ibm_cos_bucket_region(name) != ''


def verify_oci_bucket(name: str) -> bool:
    """Helper method that checks if the OCI bucket exists

    Args:
      name: str; Name of OCI Bucket (without oci:// prefix)

    Returns:
      bool: True if the bucket exists, False otherwise
    """
    try:
        # Get OCI client and check if bucket exists
        client = oci.get_object_storage_client()
        namespace = client.get_namespace(
            compartment_id=oci.get_oci_config()['tenancy']).data

        # Try to get the bucket
        client.get_bucket(namespace_name=namespace, bucket_name=name)
        return True
    except Exception:  # pylint: disable=broad-except
        # If any exception occurs (bucket not found, permission issues, etc.),
        # return False
        return False


def _get_ibm_cos_bucket_region(region, bucket_name):
    """helper function of get_ibm_cos_bucket_region

    returns region of bucket if exists in a region,
    else returns an empty string.
    located outside of get_ibm_cos_bucket_region since
    inner function aren't pickable for processes.

    Args:
        region (str): region to search the bucket in.
        bucket_name (str): name of bucket in search.

    Returns:
        str: region or ''
    """
    try:
        # reinitialize a client to search in different regions
        tmp_client = ibm.get_cos_client(region)
        tmp_client.head_bucket(Bucket=bucket_name)
        return region
    except ibm.ibm_botocore.exceptions.ClientError as e:  # type: ignore[union-attr] # pylint: disable=line-too-long
        if e.response['Error']['Code'] == '404':
            logger.debug(f'bucket {bucket_name} was not found '
                         f'in {region}')
        # Failed to connect to bucket, e.g. owned by different user
        elif e.response['Error']['Code'] == '403':
            raise exceptions.StorageBucketGetError()
        else:
            raise e
    return ''


def get_ibm_cos_bucket_region(bucket_name: str) -> str:
    """Returns the bucket's region if exists, otherwise returns empty string.

    Args:
        bucket_name (str): name of IBM COS bucket.

    Raises:
        exceptions.StorageBucketGetError if failed to connect to bucket.

    Returns:
        str: region of bucket if bucket exists, else empty string.
    """

    # parallel lookup on bucket storage region
    # Using threads would be more efficient, but raises a leaked semaphore
    # warning on Mac machines when guarding cos client with a thread lock
    # nested within the process lock. Source:
    # https://github.com/skypilot-org/skypilot/pull/1966#issuecomment-1646992938
    with concurrent.futures.ProcessPoolExecutor() as executor:
        futures = []
        for region_scanned in get_cos_regions():
            future = executor.submit(_get_ibm_cos_bucket_region, region_scanned,
                                     bucket_name)
            futures.append(future)

        for future in concurrent.futures.as_completed(futures):
            region = future.result()
            if region:
                logger.debug(f'bucket {bucket_name} was found in {region}')
                return region
    return ''


def is_cloud_store_url(url):
    result = urllib.parse.urlsplit(url)
    # '' means non-cloud URLs.
    return result.netloc


def _group_files_by_dir(
        source_list: List[str]) -> Tuple[Dict[str, List[str]], List[str]]:
    """Groups a list of paths based on their directory

    Given a list of paths, generates a dict of {dir_name: List[file_name]}
    which groups files with same dir, and a list of dirs in the source_list.

    This is used to optimize uploads by reducing the number of calls to rsync.
    E.g., ['a/b/c.txt', 'a/b/d.txt', 'a/e.txt'] will be grouped into
    {'a/b': ['c.txt', 'd.txt'], 'a': ['e.txt']}, and these three files can be
    uploaded in two rsync calls instead of three.

    Args:
        source_list: List[str]; List of paths to group
    """
    grouped_files: Dict[str, List[str]] = {}
    dirs = []
    for source in source_list:
        source = os.path.abspath(os.path.expanduser(source))
        if os.path.isdir(source):
            dirs.append(source)
        else:
            base_path = os.path.dirname(source)
            file_name = os.path.basename(source)
            if base_path not in grouped_files:
                grouped_files[base_path] = []
            grouped_files[base_path].append(file_name)
    return grouped_files, dirs


def parallel_upload(source_path_list: List[str],
                    filesync_command_generator: Callable[[str, List[str]], str],
                    dirsync_command_generator: Callable[[str, str], str],
                    log_path: str,
                    bucket_name: str,
                    access_denied_message: str,
                    create_dirs: bool = False,
                    max_concurrent_uploads: Optional[int] = None) -> None:
    """Helper function to run parallel uploads for a list of paths.

    Used by S3Store, GCSStore, and R2Store to run rsync commands in parallel by
    providing appropriate command generators.

    Args:
        source_path_list: List of paths to local files or directories
        filesync_command_generator: Callable that generates rsync command
            for a list of files belonging to the same dir.
        dirsync_command_generator: Callable that generates rsync command
            for a directory.
        log_path: Path to the log file.
        access_denied_message: Message to intercept from the underlying
            upload utility when permissions are insufficient. Used in
            exception handling.
        create_dirs: If the local_path is a directory and this is set to
            False, the contents of the directory are directly uploaded to
            root of the bucket. If the local_path is a directory and this is
            set to True, the directory is created in the bucket root and
            contents are uploaded to it.
        max_concurrent_uploads: Maximum number of concurrent threads to use
            to upload files.
    """
    # Generate gsutil rsync command for files and dirs
    commands = []
    grouped_files, dirs = _group_files_by_dir(source_path_list)
    # Generate file upload commands
    for dir_path, file_names in grouped_files.items():
        sync_command = filesync_command_generator(dir_path, file_names)
        commands.append(sync_command)
    # Generate dir upload commands
    for dir_path in dirs:
        if create_dirs:
            dest_dir_name = os.path.basename(dir_path)
        else:
            dest_dir_name = ''
        sync_command = dirsync_command_generator(dir_path, dest_dir_name)
        commands.append(sync_command)

    # Run commands in parallel
    with pool.ThreadPool(processes=max_concurrent_uploads) as p:
        p.starmap(
            run_upload_cli,
            zip(commands, [access_denied_message] * len(commands),
                [bucket_name] * len(commands), [log_path] * len(commands)))


def get_gsutil_command() -> Tuple[str, str]:
    """Gets the alias'd command for gsutil and a command to define the alias.

    This is required for applying platform-specific flags to gsutil.

    In particular, we disable multiprocessing on Mac using
    `-o "GSUtil:parallel_process_count=1"`. Multithreading is still enabled.
    gsutil on Mac has a bug with multiprocessing that causes it to crash
    when uploading files. Related issues:
    https://bugs.python.org/issue33725
    https://github.com/GoogleCloudPlatform/gsutil/issues/464

    The flags are added by checking the platform using bash in a one-liner.
    The platform check is done inline to have the flags match where the command
    is executed, rather than where the code is run. This is important when
    the command is run in a remote VM.

    Returns:
        Tuple[str, str] : (gsutil_alias, command to generate the alias)
        The command to generate alias must be run before using the alias. E.g.,
        ```
        gsutil_alias, alias_gen = get_gsutil_command()
        cmd_to_run = f'{alias_gen}; {gsutil_alias} cp ...'
        ```
    """
    gsutil_alias = 'skypilot_gsutil'
    disable_multiprocessing_flag = '-o "GSUtil:parallel_process_count=1"'

    # Define skypilot_gsutil as a shell function instead of an alias.
    # This function will behave just like alias, but can be called immediately
    # after its definition on the same line
    alias_gen = (f'[[ "$(uname)" == "Darwin" ]] && {gsutil_alias}() {{ '
                 f'gsutil -m {disable_multiprocessing_flag} "$@"; }} '
                 f'|| {gsutil_alias}() {{ gsutil -m "$@"; }}')

    return gsutil_alias, alias_gen


def run_upload_cli(command: str, access_denied_message: str, bucket_name: str,
                   log_path: str):
    returncode, stdout, stderr = log_lib.run_with_log(
        command,
        log_path,
        shell=True,
        require_outputs=True,
        # We need to use bash as some of the cloud commands uses bash syntax,
        # such as [[ ... ]]
        executable='/bin/bash',
        log_cmd=True)
    if access_denied_message in stderr:
        with ux_utils.print_exception_no_traceback():
            raise PermissionError('Failed to upload files to '
                                  'the remote bucket. The bucket does not have '
                                  'write permissions. It is possible that '
                                  'the bucket is public.')
    if returncode != 0:
        with ux_utils.print_exception_no_traceback():
            logger.error(stderr)
            raise exceptions.StorageUploadError(
                f'Upload to bucket failed for store {bucket_name}. '
                f'Please check the logs: {log_path}')
    if not stdout:
        logger.debug('No file uploaded. This could be due to an error or '
                     'because all files already exist on the cloud.')


def get_cos_regions() -> List[str]:
    return [
        'us-south', 'us-east', 'eu-de', 'eu-gb', 'eu-es', 'ca-tor', 'au-syd',
        'br-sao', 'jp-osa', 'jp-tok'
    ]


class Rclone:
    """Provides methods to manage and generate Rclone configuration profile."""

    # TODO(syang) Move the enum's functionality into AbstractStore subclass and
    # deprecate this class.
    class RcloneStores(enum.Enum):
        """Rclone supporting storage types and supporting methods."""
        S3 = 'S3'
        GCS = 'GCS'
        IBM = 'IBM'
        R2 = 'R2'
        AZURE = 'AZURE'
        NEBIUS = 'NEBIUS'
        COREWEAVE = 'COREWEAVE'

        def get_profile_name(self, bucket_name: str) -> str:
            """Gets the Rclone profile name for a given bucket.

            Args:
                bucket_name: The name of the bucket.

            Returns:
                A string containing the Rclone profile name, which combines
                prefix based on the storage type and the bucket name.
            """
            profile_prefix = {
                Rclone.RcloneStores.S3: 'sky-s3',
                Rclone.RcloneStores.GCS: 'sky-gcs',
                Rclone.RcloneStores.IBM: 'sky-ibm',
                Rclone.RcloneStores.R2: 'sky-r2',
                Rclone.RcloneStores.AZURE: 'sky-azure',
                Rclone.RcloneStores.NEBIUS: 'sky-nebius',
                Rclone.RcloneStores.COREWEAVE: 'sky-coreweave'
            }
            return f'{profile_prefix[self]}-{bucket_name}'

        def get_config(self,
                       bucket_name: Optional[str] = None,
                       rclone_profile_name: Optional[str] = None,
                       region: Optional[str] = None,
                       storage_account_name: Optional[str] = None,
                       storage_account_key: Optional[str] = None) -> str:
            """Generates an Rclone configuration for a specific storage type.

            This method creates an Rclone configuration string based on the
            storage type and the provided parameters.

            Args:
                bucket_name: The name of the bucket.
                rclone_profile_name: The name of the Rclone profile. If not
                    provided, it will be generated using the bucket_name.
                region: Region of bucket.

            Returns:
                A string containing the Rclone configuration.

            Raises:
                NotImplementedError: If the storage type is not supported.
            """
            if rclone_profile_name is None:
                assert bucket_name is not None
                rclone_profile_name = self.get_profile_name(bucket_name)
            if self is Rclone.RcloneStores.S3:
                if clouds.AWS.should_use_env_auth_for_s3():
                    # Use environment-based auth for SSO, IAM roles, etc.
                    # This allows rclone to use the AWS SDK credential chain
                    # which properly handles temporary credentials and
                    # container credentials (Pod Identity, IRSA, etc.)
                    config = textwrap.dedent(f"""\
                        [{rclone_profile_name}]
                        type = s3
                        provider = AWS
                        env_auth = true
                        acl = private
                        """)
                else:
                    # Use static credentials for shared-credentials-file
                    aws_credentials = (aws.session().get_credentials().
                                       get_frozen_credentials())
                    access_key_id = aws_credentials.access_key
                    secret_access_key = aws_credentials.secret_key
                    config = textwrap.dedent(f"""\
                        [{rclone_profile_name}]
                        type = s3
                        provider = AWS
                        access_key_id = {access_key_id}
                        secret_access_key = {secret_access_key}
                        acl = private
                        """)
            elif self is Rclone.RcloneStores.GCS:
                config = textwrap.dedent(f"""\
                    [{rclone_profile_name}]
                    type = google cloud storage
                    project_number = {clouds.GCP.get_project_id()}
                    bucket_policy_only = true
                    """)
            elif self is Rclone.RcloneStores.IBM:
                access_key_id, secret_access_key = ibm.get_hmac_keys()
                config = textwrap.dedent(f"""\
                    [{rclone_profile_name}]
                    type = s3
                    provider = IBMCOS
                    access_key_id = {access_key_id}
                    secret_access_key = {secret_access_key}
                    region = {region}
                    endpoint = s3.{region}.cloud-object-storage.appdomain.cloud
                    location_constraint = {region}-smart
                    acl = private
                    """)
            elif self is Rclone.RcloneStores.R2:
                cloudflare_session = cloudflare.session()
                cloudflare_credentials = (
                    cloudflare.get_r2_credentials(cloudflare_session))
                endpoint = cloudflare.create_endpoint()
                access_key_id = cloudflare_credentials.access_key
                secret_access_key = cloudflare_credentials.secret_key
                config = textwrap.dedent(f"""\
                    [{rclone_profile_name}]
                    type = s3
                    provider = Cloudflare
                    access_key_id = {access_key_id}
                    secret_access_key = {secret_access_key}
                    endpoint = {endpoint}
                    region = auto
                    acl = private
                    """)
            elif self is Rclone.RcloneStores.AZURE:
                assert storage_account_name and storage_account_key
                config = textwrap.dedent(f"""\
                    [{rclone_profile_name}]
                    type = azureblob
                    account = {storage_account_name}
                    key = {storage_account_key}
                    """)
            elif self is Rclone.RcloneStores.NEBIUS:
                nebius_session = nebius.session()
                nebius_credentials = nebius.get_nebius_credentials(
                    nebius_session)
                # Get endpoint URL from the client
                client = nebius.client('s3')
                endpoint_url = client.meta.endpoint_url
                access_key_id = nebius_credentials.access_key
                secret_access_key = nebius_credentials.secret_key
                config = textwrap.dedent(f"""\
                    [{rclone_profile_name}]
                    type = s3
                    provider = Other
                    access_key_id = {access_key_id}
                    secret_access_key = {secret_access_key}
                    endpoint = {endpoint_url}
                    acl = private
                    """)
            elif self is Rclone.RcloneStores.COREWEAVE:
                coreweave_session = coreweave.session()
                coreweave_credentials = coreweave.get_coreweave_credentials(
                    coreweave_session)
                # Get endpoint URL from the client
                endpoint_url = coreweave.get_endpoint()
                access_key_id = coreweave_credentials.access_key
                secret_access_key = coreweave_credentials.secret_key
                config = textwrap.dedent(f"""\
                    [{rclone_profile_name}]
                    type = s3
                    provider = Other
                    access_key_id = {access_key_id}
                    secret_access_key = {secret_access_key}
                    endpoint = {endpoint_url}
                    region = auto
                    acl = private
                    force_path_style = false
                    """)
            else:
                with ux_utils.print_exception_no_traceback():
                    raise NotImplementedError(
                        f'Unsupported store type for Rclone: {self}')
            return config

    @staticmethod
    def store_rclone_config(bucket_name: str, cloud: RcloneStores,
                            region: str) -> str:
        """Creates rclone configuration files for bucket syncing and mounting.

        Args:
            bucket_name: Name of the bucket.
            cloud: RcloneStores enum representing the cloud provider.
            region: Region of the bucket.

        Returns:
            str: The configuration data written to the file.

        Raises:
            StorageError: If rclone is not installed.
        """
        rclone_config_path = os.path.expanduser(constants.RCLONE_CONFIG_PATH)
        config_data = cloud.get_config(bucket_name=bucket_name, region=region)
        try:
            subprocess.run('rclone version',
                           shell=True,
                           check=True,
                           stdout=subprocess.PIPE)
        except subprocess.CalledProcessError:
            with ux_utils.print_exception_no_traceback():
                raise exceptions.StorageError(
                    'rclone wasn\'t detected. '
                    'Consider installing via: '
                    '"curl https://rclone.org/install.sh '
                    '| sudo bash" ') from None

        os.makedirs(os.path.dirname(rclone_config_path), exist_ok=True)
        if not os.path.isfile(rclone_config_path):
            open(rclone_config_path, 'w', encoding='utf-8').close()

        # write back file without profile: [bucket_name]
        # to which the new bucket profile is appended
        with FileLock(rclone_config_path + '.lock'):
            profiles_to_keep = Rclone._remove_bucket_profile_rclone(
                bucket_name, cloud)
            with open(f'{rclone_config_path}', 'w', encoding='utf-8') as file:
                if profiles_to_keep:
                    file.writelines(profiles_to_keep)
                    if profiles_to_keep[-1].strip():
                        # add a new line to config_data
                        # if last file line contains data
                        config_data += '\n'
                file.write(config_data)

        return config_data

    @staticmethod
    def get_region_from_rclone(bucket_name: str, cloud: RcloneStores) -> str:
        """Returns the region field of the specified bucket in rclone.conf.

        Args:
            bucket_name: Name of the bucket.
            cloud: RcloneStores enum representing the cloud provider.

        Returns:
            The region field if the bucket exists, otherwise an empty string.
        """
        rclone_profile = cloud.get_profile_name(bucket_name)
        rclone_config_path = os.path.expanduser(constants.RCLONE_CONFIG_PATH)
        with open(rclone_config_path, 'r', encoding='utf-8') as file:
            bucket_profile_found = False
            for line in file:
                if line.lstrip().startswith('#'):  # skip user's comments.
                    continue
                if line.strip() == f'[{rclone_profile}]':
                    bucket_profile_found = True
                elif bucket_profile_found and line.startswith('region'):
                    return line.split('=')[1].strip()
                elif bucket_profile_found and line.startswith('['):
                    # for efficiency stop if we've searched past the
                    # requested bucket profile with no match
                    return ''
        # segment for bucket and/or region field for bucket wasn't found
        return ''

    @staticmethod
    def delete_rclone_bucket_profile(bucket_name: str, cloud: RcloneStores):
        """Deletes specified bucket profile from rclone.conf.

        Args:
            bucket_name: Name of the bucket.
            cloud: RcloneStores enum representing the cloud provider.
        """
        rclone_profile = cloud.get_profile_name(bucket_name)
        rclone_config_path = os.path.expanduser(constants.RCLONE_CONFIG_PATH)

        if not os.path.isfile(rclone_config_path):
            logger.warning('Failed to locate "rclone.conf" while '
                           f'trying to delete rclone profile: {rclone_profile}')
            return

        with FileLock(rclone_config_path + '.lock'):
            profiles_to_keep = Rclone._remove_bucket_profile_rclone(
                bucket_name, cloud)

            # write back file without profile: [rclone_profile]
            with open(f'{rclone_config_path}', 'w', encoding='utf-8') as file:
                file.writelines(profiles_to_keep)

    @staticmethod
    def _remove_bucket_profile_rclone(bucket_name: str,
                                      cloud: RcloneStores) -> List[str]:
        """Returns rclone profiles without ones matching [prefix+bucket_name].

        Args:
            bucket_name: Name of the bucket.
            cloud: RcloneStores enum representing the cloud provider.

        Returns:
            Lines to keep in the rclone config file.
        """
        rclone_profile_name = cloud.get_profile_name(bucket_name)
        rclone_config_path = os.path.expanduser(constants.RCLONE_CONFIG_PATH)

        with open(rclone_config_path, 'r', encoding='utf-8') as file:
            lines = file.readlines()  # returns a list of the file's lines
            # delete existing bucket profile matching:
            # '[profile_prefix+bucket_name]'
            lines_to_keep = []  # lines to write back to file
            # while skip_lines is True avoid adding lines to lines_to_keep
            skip_lines = False

        for line in lines:
            if line.lstrip().startswith('#') and not skip_lines:
                # keep user comments only if they aren't under
                # a profile we are discarding
                lines_to_keep.append(line)
            elif f'[{rclone_profile_name}]' in line:
                skip_lines = True
            elif skip_lines:
                if '[' in line:
                    # former profile segment ended, new one begins
                    skip_lines = False
                    lines_to_keep.append(line)
            else:
                lines_to_keep.append(line)

        return lines_to_keep


def split_oci_path(oci_path: str) -> Tuple[str, str]:
    """Splits OCI Path into Bucket name and Relative Path to Bucket
    Args:
      oci_path: str; OCI Path, e.g. oci://imagenet/train/
    """
    path_parts = oci_path.replace('oci://', '').split('/')
    bucket = path_parts.pop(0)
    key = '/'.join(path_parts)
    return bucket, key


def create_coreweave_client() -> Client:
    """Create CoreWeave S3 client."""
    return coreweave.client('s3')


def split_coreweave_path(coreweave_path: str) -> Tuple[str, str]:
    """Splits CoreWeave Path into Bucket name and Relative Path to Bucket

    Args:
      coreweave_path: str; CoreWeave Path, e.g. cw://imagenet/train/
    """
    path_parts = coreweave_path.replace('cw://', '').split('/')
    bucket = path_parts.pop(0)
    key = '/'.join(path_parts)
    return bucket, key


def verify_coreweave_bucket(name: str, retry: int = 0) -> bool:
    """Verify CoreWeave bucket exists and is accessible.

    Retries head_bucket operation up to retry times with 5 second intervals
    to handle DNS propagation delays or temporary connectivity issues.
    """
    coreweave_client = create_coreweave_client()
    max_retries = retry + 1  # 5s * (retry+1) = total seconds to retry
    retry_count = 0

    while retry_count < max_retries:
        try:
            coreweave_client.head_bucket(Bucket=name)
            if retry_count > 0:
                logger.debug(
                    f'Successfully verified bucket {name} after '
                    f'{retry_count} retries ({retry_count * 5} seconds)')
            return True

        except coreweave.botocore.exceptions.ClientError as e:  # type: ignore[union-attr] # pylint: disable=line-too-long:
            error_code = e.response['Error']['Code']
            if error_code == '403':
                logger.error(f'Access denied to bucket {name}')
                return False
            elif error_code == '404':
                logger.debug(f'Bucket {name} does not exist')
            else:
                logger.debug(
                    f'Unexpected error checking CoreWeave bucket {name}: {e}')
        except Exception as e:  # pylint: disable=broad-except
            logger.debug(
                f'Unexpected error checking CoreWeave bucket {name}: {e}')

        # Common retry logic for all transient errors
        retry_count += 1
        if retry_count < max_retries:
            logger.debug(f'Error checking CoreWeave bucket {name} '
                         f'(attempt {retry_count}/{max_retries}). '
                         f'Retrying in 5 seconds...')
            time.sleep(5)
        else:
            attempt_str = 'attempt'
            if max_retries > 1:
                attempt_str += 's'
            logger.error(f'Failed to verify CoreWeave bucket {name} after '
                         f'{max_retries} {attempt_str}.')
            return False

    # Should not reach here, but just in case
    return False
