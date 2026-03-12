"""Cloud object stores.

Currently, used for transferring data in bulk.  Thus, this module does not
offer file-level calls (e.g., open, reading, writing).

TODO:
* Better interface.
* Better implementation (e.g., fsspec, smart_open, using each cloud's SDK).
"""
import os
import shlex
import subprocess
import time
import urllib.parse

from sky import exceptions as sky_exceptions
from sky import sky_logging
from sky.adaptors import aws
from sky.adaptors import azure
from sky.adaptors import cloudflare
from sky.adaptors import coreweave
from sky.adaptors import ibm
from sky.adaptors import nebius
from sky.adaptors import oci
from sky.clouds import gcp
from sky.data import data_utils
from sky.skylet import constants
from sky.utils import ux_utils

logger = sky_logging.init_logger(__name__)


class CloudStorage:
    """Interface for a cloud object store."""

    def is_directory(self, url: str) -> bool:
        """Returns whether 'url' is a directory.

        In cloud object stores, a "directory" refers to a regular object whose
        name is a prefix of other objects.
        """
        raise NotImplementedError

    def make_sync_dir_command(self, source: str, destination: str) -> str:
        """Makes a runnable bash command to sync a 'directory'."""
        raise NotImplementedError

    def make_sync_file_command(self, source: str, destination: str) -> str:
        """Makes a runnable bash command to sync a file."""
        raise NotImplementedError


class S3CloudStorage(CloudStorage):
    """AWS Cloud Storage."""

    # List of commands to install AWS CLI
    _GET_AWSCLI = [
        'awscli_path=$(which aws) || '
        f'{{ {constants.SKY_UV_PIP_CMD} install awscli && '
        f'awscli_path={constants.SKY_REMOTE_PYTHON_ENV}/bin/aws; }}',
    ]

    def is_directory(self, url: str) -> bool:
        """Returns whether S3 'url' is a directory.

        In cloud object stores, a "directory" refers to a regular object whose
        name is a prefix of other objects.
        """
        s3 = aws.resource('s3')
        bucket_name, path = data_utils.split_s3_path(url)
        bucket = s3.Bucket(bucket_name)

        num_objects = 0
        for obj in bucket.objects.filter(Prefix=path):
            num_objects += 1
            if obj.key == path:
                return False
            # If there are more than 1 object in filter, then it is a directory
            if num_objects == 3:
                return True

        # A directory with few or no items
        return True

    def make_sync_dir_command(self, source: str, destination: str) -> str:
        """Downloads using AWS CLI."""
        # AWS Sync by default uses 10 threads to upload files to the bucket.
        # To increase parallelism, modify max_concurrent_requests in your
        # aws config file (Default path: ~/.aws/config).
        download_via_awscli = (f'$awscli_path s3 sync --no-follow-symlinks '
                               f'{source} {destination}')

        all_commands = list(self._GET_AWSCLI)
        all_commands.append(download_via_awscli)
        return ' && '.join(all_commands)

    def make_sync_file_command(self, source: str, destination: str) -> str:
        """Downloads a file using AWS CLI."""
        download_via_awscli = (f'$awscli_path s3 cp {source} {destination}')

        all_commands = list(self._GET_AWSCLI)
        all_commands.append(download_via_awscli)
        return ' && '.join(all_commands)


class GcsCloudStorage(CloudStorage):
    """Google Cloud Storage."""

    # We use gsutil as a basic implementation.  One pro is that its -m
    # multi-threaded download is nice, which frees us from implementing
    # parellel workers on our end.
    # The gsutil command is part of the Google Cloud SDK, and we reuse
    # the installation logic here.
    _INSTALL_GSUTIL = gcp.GOOGLE_SDK_INSTALLATION_COMMAND

    @property
    def _gsutil_command(self):
        gsutil_alias, alias_gen = data_utils.get_gsutil_command()
        return (
            f'{alias_gen}; GOOGLE_APPLICATION_CREDENTIALS='
            f'{gcp.DEFAULT_GCP_APPLICATION_CREDENTIAL_PATH}; '
            # Explicitly activate service account. Unlike the gcp packages
            # and other GCP commands, gsutil does not automatically pick up
            # the default credential keys when it is a service account.
            'gcloud auth activate-service-account '
            '--key-file=$GOOGLE_APPLICATION_CREDENTIALS '
            '2> /dev/null || true; '
            f'{gsutil_alias}')

    def is_directory(self, url: str) -> bool:
        """Returns whether 'url' is a directory.
        In cloud object stores, a "directory" refers to a regular object whose
        name is a prefix of other objects.
        """
        commands = [self._INSTALL_GSUTIL]
        commands.append(f'{self._gsutil_command} ls -d {url}')
        command = ' && '.join(commands)
        p = subprocess.run(command,
                           stdout=subprocess.PIPE,
                           shell=True,
                           check=True,
                           executable='/bin/bash')
        out = p.stdout.decode().strip()
        # Edge Case: Gcloud command is run for first time #437
        out = out.split('\n')[-1]
        # If <url> is a bucket root, then we only need `gsutil` to succeed
        # to make sure the bucket exists. It is already a directory.
        _, key = data_utils.split_gcs_path(url)
        if not key:
            return True
        # Otherwise, gsutil ls -d url will return:
        #   --> url.rstrip('/')          if url is not a directory
        #   --> url with an ending '/'   if url is a directory
        if not out.endswith('/'):
            assert out == url.rstrip('/'), (out, url)
            return False
        url = url if url.endswith('/') else (url + '/')
        assert out == url, (out, url)
        return True

    def make_sync_dir_command(self, source: str, destination: str) -> str:
        """Downloads a directory using gsutil."""
        download_via_gsutil = (f'{self._gsutil_command} '
                               f'rsync -e -r {source} {destination}')
        all_commands = [self._INSTALL_GSUTIL]
        all_commands.append(download_via_gsutil)
        return ' && '.join(all_commands)

    def make_sync_file_command(self, source: str, destination: str) -> str:
        """Downloads a file using gsutil."""
        download_via_gsutil = f'{self._gsutil_command} ' \
                              f'cp {source} {destination}'
        all_commands = [self._INSTALL_GSUTIL]
        all_commands.append(download_via_gsutil)
        return ' && '.join(all_commands)


class AzureBlobCloudStorage(CloudStorage):
    """Azure Blob Storage."""
    # AzCopy is utilized for downloading data from Azure Blob Storage
    # containers to remote systems due to its superior performance compared to
    # az-cli. While az-cli's `az storage blob sync` can synchronize data from
    # local to container, it lacks support to sync from container to remote
    # synchronization. Moreover, `az storage blob download-batch` in az-cli
    # does not leverage AzCopy's efficient multi-threaded capabilities, leading
    # to slower performance.
    #
    # AzCopy requires appending SAS tokens directly in commands, as it does not
    # support using STORAGE_ACCOUNT_KEY, unlike az-cli, which can generate
    # SAS tokens but lacks direct multi-threading support like AzCopy.
    # Hence, az-cli for SAS token generation is ran on the local machine and
    # AzCopy is installed at the remote machine for efficient data transfer
    # from containers to remote systems.
    # Note that on Azure instances, both az-cli and AzCopy are typically
    # pre-installed. And installing both would be used with AZ container is
    # used from non-Azure instances.

    _GET_AZCOPY = [
        'azcopy --version > /dev/null 2>&1 || '
        '(mkdir -p /usr/local/bin; '
        'ARCH=$(uname -m) && '
        'if [ "$ARCH" = "aarch64" ] || [ "$ARCH" = "arm64" ]; then '
        '  ARCH="arm64"; '
        'elif [ "$ARCH" = "x86_64" ]; then '
        '  ARCH="amd64"; '
        'fi && '
        'curl -fL https://github.com/Azure/azure-storage-azcopy/releases/download/v10.30.1/azcopy_linux_${ARCH}_10.30.1.tar.gz -o azcopy.tar.gz && '  # pylint: disable=line-too-long
        'sudo tar -xvzf azcopy.tar.gz --strip-components=1 -C /usr/local/bin --exclude=*.txt && '  # pylint: disable=line-too-long
        'sudo chmod +x /usr/local/bin/azcopy && '
        'rm azcopy.tar.gz)'
    ]

    def is_directory(self, url: str) -> bool:
        """Returns whether 'url' of the AZ Container is a directory.

        In cloud object stores, a "directory" refers to a regular object whose
        name is a prefix of other objects.

        Args:
            url: Endpoint url of the container/blob.

        Returns:
            True if the url is an endpoint of a directory and False if it
            is a blob(file).

        Raises:
            azure.core.exceptions.HttpResponseError: If the user's Azure
                Azure account does not have sufficient IAM role for the given
                storage account.
            StorageBucketGetError: Provided container name does not exist.
            TimeoutError: If unable to determine the container path status
                in time.
        """
        storage_account_name, container_name, path = data_utils.split_az_path(
            url)

        # If there are more, we need to check if it is a directory or a file.
        container_url = data_utils.AZURE_CONTAINER_URL.format(
            storage_account_name=storage_account_name,
            container_name=container_name)
        resource_group_name = azure.get_az_resource_group(storage_account_name)
        role_assignment_start = time.time()
        refresh_client = False
        role_assigned = False

        # 1. List blobs in the container_url to decide wether it is a directory
        # 2. If it fails due to permission issues, try to assign a permissive
        # role for the storage account to the current Azure account
        # 3. Wait for the role assignment to propagate and retry.
        while (time.time() - role_assignment_start <
               constants.WAIT_FOR_STORAGE_ACCOUNT_ROLE_ASSIGNMENT):
            container_client = data_utils.create_az_client(
                client_type='container',
                container_url=container_url,
                storage_account_name=storage_account_name,
                resource_group_name=resource_group_name,
                refresh_client=refresh_client)

            if not container_client.exists():
                with ux_utils.print_exception_no_traceback():
                    raise sky_exceptions.StorageBucketGetError(
                        f'The provided container {container_name!r} from the '
                        f'passed endpoint url {url!r} does not exist. Please '
                        'check if the name is correct.')

            # If there aren't more than just container name and storage account,
            # that's a directory.
            # Note: This must be ran after existence of the storage account is
            # checked while obtaining container client.
            if not path:
                return True

            num_objects = 0
            try:
                for blob in container_client.list_blobs(name_starts_with=path):
                    if blob.name == path:
                        return False
                    num_objects += 1
                    if num_objects > 1:
                        return True
                # A directory with few or no items
                return True
            except azure.exceptions().HttpResponseError as e:
                # Handle case where user lacks sufficient IAM role for
                # a private container in the same subscription. Attempt to
                # assign appropriate role to current user.
                if 'AuthorizationPermissionMismatch' in str(e):
                    if not role_assigned:
                        logger.info('Failed to list blobs in container '
                                    f'{container_url!r}. This implies '
                                    'insufficient IAM role for storage account'
                                    f' {storage_account_name!r}.')
                        azure.assign_storage_account_iam_role(
                            storage_account_name=storage_account_name,
                            resource_group_name=resource_group_name)
                        role_assigned = True
                        refresh_client = True
                    else:
                        logger.info(
                            'Waiting due to the propagation delay of IAM '
                            'role assignment to the storage account '
                            f'{storage_account_name!r}.')
                        time.sleep(
                            constants.RETRY_INTERVAL_AFTER_ROLE_ASSIGNMENT)
                    continue
                raise
        else:
            raise TimeoutError(
                'Failed to determine the container path status within '
                f'{constants.WAIT_FOR_STORAGE_ACCOUNT_ROLE_ASSIGNMENT}'
                'seconds.')

    def _get_azcopy_source(self, source: str, is_dir: bool) -> str:
        """Converts the source so it can be used as an argument for azcopy."""
        storage_account_name, container_name, blob_path = (
            data_utils.split_az_path(source))
        storage_account_key = data_utils.get_az_storage_account_key(
            storage_account_name)

        if storage_account_key is None:
            # public containers do not require SAS token for access
            sas_token = ''
        else:
            if is_dir:
                sas_token = azure.get_az_container_sas_token(
                    storage_account_name, storage_account_key, container_name)
            else:
                sas_token = azure.get_az_blob_sas_token(storage_account_name,
                                                        storage_account_key,
                                                        container_name,
                                                        blob_path)
        # "?" is a delimiter character used when SAS token is attached to the
        # container endpoint.
        # Reference: https://learn.microsoft.com/en-us/azure/ai-services/translator/document-translation/how-to-guides/create-sas-tokens?tabs=Containers # pylint: disable=line-too-long
        converted_source = f'{source}?{sas_token}' if sas_token else source

        return shlex.quote(converted_source)

    def make_sync_dir_command(self, source: str, destination: str) -> str:
        """Fetches a directory using AZCOPY from storage to remote instance."""
        source = self._get_azcopy_source(source, is_dir=True)
        # destination is guaranteed to not have '/' at the end of the string
        # by tasks.py::set_file_mounts(). It is necessary to add from this
        # method due to syntax of azcopy.
        destination = f'{destination}/'
        download_command = (f'azcopy sync {source} {destination} '
                            '--recursive --delete-destination=false')
        all_commands = list(self._GET_AZCOPY)
        all_commands.append(download_command)
        return ' && '.join(all_commands)

    def make_sync_file_command(self, source: str, destination: str) -> str:
        """Fetches a file using AZCOPY from storage to remote instance."""
        source = self._get_azcopy_source(source, is_dir=False)
        download_command = f'azcopy copy {source} {destination}'
        all_commands = list(self._GET_AZCOPY)
        all_commands.append(download_command)
        return ' && '.join(all_commands)


class R2CloudStorage(CloudStorage):
    """Cloudflare Cloud Storage."""

    # List of commands to install AWS CLI
    _GET_AWSCLI = [
        'aws --version >/dev/null 2>&1 || '
        f'{constants.SKY_UV_PIP_CMD} install awscli',
    ]

    def is_directory(self, url: str) -> bool:
        """Returns whether R2 'url' is a directory.

        In cloud object stores, a "directory" refers to a regular object whose
        name is a prefix of other objects.
        """
        r2 = cloudflare.resource('s3')
        bucket_name, path = data_utils.split_r2_path(url)
        bucket = r2.Bucket(bucket_name)

        num_objects = 0
        for obj in bucket.objects.filter(Prefix=path):
            num_objects += 1
            if obj.key == path:
                return False
            # If there are more than 1 object in filter, then it is a directory
            if num_objects == 3:
                return True

        # A directory with few or no items
        return True

    def make_sync_dir_command(self, source: str, destination: str) -> str:
        """Downloads using AWS CLI."""
        # AWS Sync by default uses 10 threads to upload files to the bucket.
        # To increase parallelism, modify max_concurrent_requests in your
        # aws config file (Default path: ~/.aws/config).
        endpoint_url = cloudflare.create_endpoint()
        if 'r2://' in source:
            source = source.replace('r2://', 's3://')
        download_via_awscli = ('AWS_SHARED_CREDENTIALS_FILE='
                               f'{cloudflare.R2_CREDENTIALS_PATH} '
                               f'{constants.SKY_REMOTE_PYTHON_ENV}/bin/aws s3 '
                               'sync --no-follow-symlinks '
                               f'{source} {destination} '
                               f'--endpoint {endpoint_url} '
                               f'--profile={cloudflare.R2_PROFILE_NAME}')

        all_commands = list(self._GET_AWSCLI)
        all_commands.append(download_via_awscli)
        return ' && '.join(all_commands)

    def make_sync_file_command(self, source: str, destination: str) -> str:
        """Downloads a file using AWS CLI."""
        endpoint_url = cloudflare.create_endpoint()
        if 'r2://' in source:
            source = source.replace('r2://', 's3://')
        download_via_awscli = ('AWS_SHARED_CREDENTIALS_FILE='
                               f'{cloudflare.R2_CREDENTIALS_PATH} '
                               f'{constants.SKY_REMOTE_PYTHON_ENV}/bin/aws s3 '
                               f'cp {source} {destination} '
                               f'--endpoint {endpoint_url} '
                               f'--profile={cloudflare.R2_PROFILE_NAME}')

        all_commands = list(self._GET_AWSCLI)
        all_commands.append(download_via_awscli)
        return ' && '.join(all_commands)


class IBMCosCloudStorage(CloudStorage):
    """IBM Cloud Storage."""
    # install rclone if package isn't already installed
    _GET_RCLONE = [
        'rclone version >/dev/null 2>&1 || '
        'curl https://rclone.org/install.sh | sudo bash',
    ]

    def is_directory(self, url: str) -> bool:
        """Returns whether IBM COS bucket's 'url' is a directory.

        In cloud object stores, a "directory" refers to a regular object whose
        name is a prefix of other objects.
        """

        bucket_name, path, region = data_utils.split_cos_path(url)
        s3 = ibm.get_cos_resource(region)
        bucket = s3.Bucket(bucket_name)

        num_objects = 0
        for obj in bucket.objects.filter(Prefix=path):
            num_objects += 1
            if obj.key == path:
                return False
            # If there are more than 1 object in filter, then it is a directory
            if num_objects == 3:
                return True

        # A directory with few or no items
        return True

    def _get_rclone_sync_command(self, source: str, destination: str):
        bucket_name, data_path, bucket_region = data_utils.split_cos_path(
            source)
        rclone_profile_name = (
            data_utils.Rclone.RcloneStores.IBM.get_profile_name(bucket_name))
        data_path_in_bucket = f'{bucket_name}{data_path}'
        rclone_config = data_utils.Rclone.RcloneStores.IBM.get_config(
            rclone_profile_name=rclone_profile_name, region=bucket_region)
        # configure_rclone stores bucket profile in remote cluster's rclone.conf
        configure_rclone = (
            f' mkdir -p {constants.RCLONE_CONFIG_DIR} &&'
            f' echo "{rclone_config}">> {constants.RCLONE_CONFIG_PATH}')
        download_via_rclone = (
            'rclone copy '
            f'{rclone_profile_name}:{data_path_in_bucket} {destination}')

        all_commands = list(self._GET_RCLONE)
        all_commands.append(configure_rclone)
        all_commands.append(download_via_rclone)
        return ' && '.join(all_commands)

    def make_sync_dir_command(self, source: str, destination: str) -> str:
        """Downloads a directory from 'source' bucket to remote vm
          at 'destination' using rclone."""
        return self._get_rclone_sync_command(source, destination)

    def make_sync_file_command(self, source: str, destination: str) -> str:
        """Downloads a file from 'source' bucket to remote vm
          at 'destination' using rclone."""

        # underlying rclone command is the same for dirs and files.
        return self.make_sync_dir_command(source, destination)


class OciCloudStorage(CloudStorage):
    """OCI Cloud Storage."""

    def is_directory(self, url: str) -> bool:
        """Returns whether OCI 'url' is a directory.
        In cloud object stores, a "directory" refers to a regular object whose
        name is a prefix of other objects.
        """
        bucket_name, path = data_utils.split_oci_path(url)

        client = oci.get_object_storage_client()
        namespace = client.get_namespace(
            compartment_id=oci.get_oci_config()['tenancy']).data

        objects = client.list_objects(namespace_name=namespace,
                                      bucket_name=bucket_name,
                                      prefix=path).data.objects

        if len(objects) == 0:
            # A directory with few or no items
            return True

        if len(objects) > 1:
            # A directory with more than 1 items
            return True

        object_name = objects[0].name
        if path.endswith(object_name):
            # An object path
            return False

        # A directory with only 1 item
        return True

    @oci.with_oci_env
    def make_sync_dir_command(self, source: str, destination: str) -> str:
        """Downloads using OCI CLI."""
        bucket_name, path = data_utils.split_oci_path(source)

        download_via_ocicli = (f'oci os object sync --no-follow-symlinks '
                               f'--bucket-name {bucket_name} '
                               f'--prefix "{path}" --dest-dir "{destination}"')

        return download_via_ocicli

    @oci.with_oci_env
    def make_sync_file_command(self, source: str, destination: str) -> str:
        """Downloads a file using OCI CLI."""
        bucket_name, path = data_utils.split_oci_path(source)
        filename = os.path.basename(path)
        destination = os.path.join(destination, filename)

        download_via_ocicli = (f'oci os object get --bucket-name {bucket_name} '
                               f'--name "{path}" --file "{destination}"')

        return download_via_ocicli


class NebiusCloudStorage(CloudStorage):
    """Nebius Cloud Storage."""

    # List of commands to install AWS CLI
    _GET_AWSCLI = [
        'aws --version >/dev/null 2>&1 || '
        f'{constants.SKY_UV_PIP_CMD} install awscli',
    ]

    def is_directory(self, url: str) -> bool:
        """Returns whether nebius 'url' is a directory.

        In cloud object stores, a "directory" refers to a regular object whose
        name is a prefix of other objects.
        """
        nebius_s3 = nebius.resource('s3')
        bucket_name, path = data_utils.split_nebius_path(url)
        bucket = nebius_s3.Bucket(bucket_name)

        num_objects = 0
        for obj in bucket.objects.filter(Prefix=path):
            num_objects += 1
            if obj.key == path:
                return False
            # If there are more than 1 object in filter, then it is a directory
            if num_objects == 3:
                return True

        # A directory with few or no items
        return True

    def make_sync_dir_command(self, source: str, destination: str) -> str:
        """Downloads using AWS CLI."""
        # AWS Sync by default uses 10 threads to upload files to the bucket.
        # To increase parallelism, modify max_concurrent_requests in your
        # aws config file (Default path: ~/.aws/config).
        assert 'nebius://' in source, 'nebius:// is not in source'
        source = source.replace('nebius://', 's3://')
        download_via_awscli = (f'{constants.SKY_REMOTE_PYTHON_ENV}/bin/aws s3 '
                               'sync --no-follow-symlinks '
                               f'{source} {destination} '
                               f'--profile={nebius.NEBIUS_PROFILE_NAME}')

        all_commands = list(self._GET_AWSCLI)
        all_commands.append(download_via_awscli)
        return ' && '.join(all_commands)

    def make_sync_file_command(self, source: str, destination: str) -> str:
        """Downloads a file using AWS CLI."""
        assert 'nebius://' in source, 'nebius:// is not in source'
        source = source.replace('nebius://', 's3://')
        download_via_awscli = (f'{constants.SKY_REMOTE_PYTHON_ENV}/bin/aws s3 '
                               f'cp {source} {destination} '
                               f'--profile={nebius.NEBIUS_PROFILE_NAME}')

        all_commands = list(self._GET_AWSCLI)
        all_commands.append(download_via_awscli)
        return ' && '.join(all_commands)


class CoreWeaveCloudStorage(CloudStorage):
    """CoreWeave Cloud Storage."""

    # List of commands to install AWS CLI
    _GET_AWSCLI = [
        'aws --version >/dev/null 2>&1 || '
        f'{constants.SKY_UV_PIP_CMD} install awscli',
    ]

    def is_directory(self, url: str) -> bool:
        """Checks if the coreweave object is a directory.

        In cloud object stores, a "directory" refers to a regular object whose
        name is a prefix of other objects.

        Args:
            url: coreweave object URL.
        """
        cw = coreweave.resource('s3')
        bucket_name, path = data_utils.split_coreweave_path(url)
        bucket = cw.Bucket(bucket_name)

        num_objects = 0
        for obj in bucket.objects.filter(Prefix=path):
            num_objects += 1
            if obj.key == path:
                return False
            # If there are more than 1 object in filter, then it is a directory
            if num_objects == 3:
                return True

        # A directory with few or no items
        return True

    def make_sync_dir_command(self, source: str, destination: str) -> str:
        """Downloads using AWS CLI."""
        # AWS Sync by default uses 10 threads to upload files to the bucket.
        # To increase parallelism, modify max_concurrent_requests in your
        # aws config file (Default path: ~/.coreweave/cw.config).
        assert 'cw://' in source, 'cw:// is not in source'
        source = source.replace('cw://', 's3://')
        download_via_awscli = (
            'AWS_SHARED_CREDENTIALS_FILE='
            f'{coreweave.COREWEAVE_CREDENTIALS_PATH} '
            f'AWS_CONFIG_FILE={coreweave.COREWEAVE_CONFIG_PATH} '
            f'{constants.SKY_REMOTE_PYTHON_ENV}/bin/aws s3 '
            'sync --no-follow-symlinks '
            f'{source} {destination} '
            f'--profile={coreweave.COREWEAVE_PROFILE_NAME}')

        all_commands = list(self._GET_AWSCLI)
        all_commands.append(download_via_awscli)
        return ' && '.join(all_commands)

    def make_sync_file_command(self, source: str, destination: str) -> str:
        """Downloads a file using AWS CLI."""
        assert 'cw://' in source, 'cw:// is not in source'
        source = source.replace('cw://', 's3://')
        download_via_awscli = (
            'AWS_SHARED_CREDENTIALS_FILE='
            f'{coreweave.COREWEAVE_CREDENTIALS_PATH} '
            f'AWS_CONFIG_FILE={coreweave.COREWEAVE_CONFIG_PATH} '
            f'{constants.SKY_REMOTE_PYTHON_ENV}/bin/aws s3 '
            f'cp {source} {destination} '
            f'--profile={coreweave.COREWEAVE_PROFILE_NAME}')

        all_commands = list(self._GET_AWSCLI)
        all_commands.append(download_via_awscli)
        return ' && '.join(all_commands)


def get_storage_from_path(url: str) -> CloudStorage:
    """Returns a CloudStorage by identifying the scheme:// in a URL."""
    result = urllib.parse.urlsplit(url)
    if result.scheme not in _REGISTRY:
        assert False, (f'Scheme {result.scheme} not found in'
                       f' supported storage ({_REGISTRY.keys()}); path {url}')
    return _REGISTRY[result.scheme]


# Maps bucket's URIs prefix(scheme) to its corresponding storage class
_REGISTRY = {
    'gs': GcsCloudStorage(),
    's3': S3CloudStorage(),
    'r2': R2CloudStorage(),
    'cos': IBMCosCloudStorage(),
    'oci': OciCloudStorage(),
    'nebius': NebiusCloudStorage(),
    'cw': CoreWeaveCloudStorage(),
    # TODO: This is a hack, as Azure URL starts with https://, we should
    # refactor the registry to be able to take regex, so that Azure blob can
    # be identified with `https://(.*?)\.blob\.core\.windows\.net`
    'https': AzureBlobCloudStorage()
}
