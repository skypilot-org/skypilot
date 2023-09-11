"""Cloud object stores.

Currently, used for transferring data in bulk.  Thus, this module does not
offer file-level calls (e.g., open, reading, writing).

TODO:
* Better interface.
* Better implementation (e.g., fsspec, smart_open, using each cloud's SDK).
"""
import subprocess
import urllib.parse

from sky.adaptors import aws
from sky.adaptors import cloudflare
from sky.adaptors import ibm
from sky.clouds import gcp
from sky.data import data_utils
from sky.data.data_utils import Rclone


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
        'aws --version >/dev/null 2>&1 || pip3 install awscli',
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
        download_via_awscli = ('aws s3 sync --no-follow-symlinks '
                               f'{source} {destination}')

        all_commands = list(self._GET_AWSCLI)
        all_commands.append(download_via_awscli)
        return ' && '.join(all_commands)

    def make_sync_file_command(self, source: str, destination: str) -> str:
        """Downloads a file using AWS CLI."""
        download_via_awscli = f'aws s3 cp {source} {destination}'

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
        return (f'{alias_gen}; GOOGLE_APPLICATION_CREDENTIALS='
                f'{gcp.DEFAULT_GCP_APPLICATION_CREDENTIAL_PATH} {gsutil_alias}')

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
        if len(key) == 0:
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


class R2CloudStorage(CloudStorage):
    """Cloudflare Cloud Storage."""

    # List of commands to install AWS CLI
    _GET_AWSCLI = [
        'aws --version >/dev/null 2>&1 || pip3 install awscli',
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
                               'aws s3 sync --no-follow-symlinks '
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
                               f'aws s3 cp {source} {destination} '
                               f'--endpoint {endpoint_url} '
                               f'--profile={cloudflare.R2_PROFILE_NAME}')

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
        bucket_rclone_profile = Rclone.generate_rclone_bucket_profile_name(
            bucket_name, Rclone.RcloneClouds.IBM)
        data_path_in_bucket = bucket_name + data_path
        rclone_config_data = Rclone.get_rclone_config(bucket_name,
                                                      Rclone.RcloneClouds.IBM,
                                                      bucket_region)
        # configure_rclone stores bucket profile in remote cluster's rclone.conf
        configure_rclone = (
            f' mkdir -p ~/.config/rclone/ &&'
            f' echo "{rclone_config_data}">> {Rclone.RCLONE_CONFIG_PATH}')
        download_via_rclone = (
            'rclone copy '
            f'{bucket_rclone_profile}:{data_path_in_bucket} {destination}')

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


# Maps bucket's URIs prefix(scheme) to its corresponding storage class
_REGISTRY = {
    'gs': GcsCloudStorage(),
    's3': S3CloudStorage(),
    'r2': R2CloudStorage(),
    'cos': IBMCosCloudStorage(),
}
