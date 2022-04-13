"""Cloud object stores.

Currently, used for transferring data in bulk.  Thus, this module does not
offer file-level calls (e.g., open, reading, writing).

TODO:
* Better interface.
* Better implementation (e.g., fsspec, smart_open, using each cloud's SDK).
"""
import subprocess
import urllib.parse

from sky.data import data_utils
from sky.adaptors import aws


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
        download_via_awscli = f'mkdir -p {destination} && \
                                aws s3 sync {source} {destination}'

        all_commands = list(self._GET_AWSCLI)
        all_commands.append(download_via_awscli)
        return ' && '.join(all_commands)

    def make_sync_file_command(self, source: str, destination: str) -> str:
        """Downloads a file using AWS CLI."""
        download_via_awscli = f'mkdir -p {destination} && \
                                aws s3 cp {source} {destination}'

        all_commands = list(self._GET_AWSCLI)
        all_commands.append(download_via_awscli)
        return ' && '.join(all_commands)


class GcsCloudStorage(CloudStorage):
    """Google Cloud Storage."""

    # We use gsutil as a basic implementation.  One pro is that its -m
    # multi-threaded download is nice, which frees us from implementing
    # parellel workers on our end.
    _GET_GSUTIL = [
        # Skip if gsutil already exists.
        'pushd /tmp &>/dev/null',
        '(test -f ~/google-cloud-sdk/bin/gsutil || (wget --quiet '
        'https://dl.google.com/dl/cloudsdk/channels/rapid/downloads/'
        'google-cloud-sdk-367.0.0-linux-x86_64.tar.gz && '
        'tar xzf google-cloud-sdk-367.0.0-linux-x86_64.tar.gz && '
        'mv google-cloud-sdk ~/ && '
        '~/google-cloud-sdk/install.sh -q ))',
        'popd &>/dev/null',
    ]

    _GSUTIL = '~/google-cloud-sdk/bin/gsutil'

    def is_directory(self, url: str) -> bool:
        """Returns whether 'url' is a directory.
        In cloud object stores, a "directory" refers to a regular object whose
        name is a prefix of other objects.
        """
        commands = list(self._GET_GSUTIL)
        commands.append(f'{self._GSUTIL} ls -d {url}')
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
        download_via_gsutil = (f'{self._GSUTIL} -m rsync -r {source} {destination}')
        all_commands = list(self._GET_GSUTIL)
        all_commands.append(download_via_gsutil)
        return ' && '.join(all_commands)

    def make_sync_file_command(self, source: str, destination: str) -> str:
        """Downloads a file using gsutil."""
        download_via_gsutil = f'{self._GSUTIL} -m cp {source} {destination}'
        all_commands = list(self._GET_GSUTIL)
        all_commands.append(download_via_gsutil)
        return ' && '.join(all_commands)


def get_storage_from_path(url: str) -> CloudStorage:
    """Returns a CloudStorage by identifying the scheme:// in a URL."""
    result = urllib.parse.urlsplit(url)

    if result.scheme not in _REGISTRY:
        assert False, (f'Scheme {result.scheme} not found in'
                       f' supported storage ({_REGISTRY.keys()}); path {url}')
    return _REGISTRY[result.scheme]


_REGISTRY = {
    'gs': GcsCloudStorage(),
    's3': S3CloudStorage(),
}
