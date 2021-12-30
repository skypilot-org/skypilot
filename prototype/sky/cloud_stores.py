"""Cloud object stores.

Currently, used for transferring data in bulk.  Thus, this module does not
offer file-level calls (e.g., open, reading, writing).

TODO:
* Better interface.
* Better implementation (e.g., fsspec, smart_open, using each cloud's SDK).
"""
import urllib.parse

import boto3
import botocore

from sky.backends import backend_utils
from sky.data import data_utils


class CloudStorage(object):
    """Interface for a cloud object store."""

    def is_file(self, url: str) -> bool:
        """Returns whether <url> is a regular file.

        True means <url> is a regular file. False false means <url> is a
        directory, or does not exist. Useful for deciding whether to use
        cp or sync/rsync to download.
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

    def is_file(self, url: str) -> bool:
        """Returns whether <url> is a regular file."""
        bucket_name, path = data_utils.split_s3_path(url)
        if len(path) == 0:
            return False
        s3 = boto3.client('s3')
        try:
            s3.head_object(Bucket=bucket_name, Key=path)
            return True
        except botocore.errorfactory.ClientError:
            return False

    def make_sync_dir_command(self, source: str, destination: str) -> str:
        """Downloads using AWS CLI."""
        # AWS Sync by default uses 10 threads to upload files to the bucket.
        # To increase parallelism, modify max_concurrent_requests in your
        # aws config file (Default path: ~/.aws/config).
        return (f'mkdir -p {destination} && '
                f'aws s3 sync {source} {destination} --delete')

    def make_sync_file_command(self, source: str, destination: str) -> str:
        """Downloads a file using AWS CLI."""
        return (f'mkdir -p {destination} && '
                f'aws s3 cp {source} {destination}')


class GcsCloudStorage(CloudStorage):
    """Google Cloud Storage."""

    # We use gsutil as a basic implementation.  One pro is that its -m
    # multi-threaded download is nice, which frees us from implementing
    # parellel workers on our end.
    _GSUTIL = '~/google-cloud-sdk/bin/gsutil'

    def is_file(self, url: str) -> bool:
        """Returns whether <url> is a regular file."""
        command = ' && '.join(self._GET_GSUTIL)
        backend_utils.run(command)

        # https://cloud.google.com/storage/docs/gsutil/commands/stat
        # gsutil stat returns 0 iff <url> is a regular file. If it is a
        # directory, or does not exist, it will return 1. Any other return
        # code means something else is wrong.
        gsutil_cmd = f'{self._GSUTIL} -q stat {url}'
        p = backend_utils.run(gsutil_cmd, check=False)
        rc = p.returncode
        assert rc in [0, 1], command
        return rc == 0

    def make_sync_dir_command(self, source: str, destination: str) -> str:
        """Downloads a directory using gsutil."""
        return f'{self._GSUTIL} -m rsync -d -r {source} {destination}'

    def make_sync_file_command(self, source: str, destination: str) -> str:
        """Downloads a file using gsutil."""
        return f'{self._GSUTIL} -m cp {source} {destination}'


def get_storage_from_path(url: str) -> CloudStorage:
    """Returns a CloudStorage by identifying the scheme:// in a URL."""
    result = urllib.parse.urlsplit(url)

    if result.scheme not in _REGISTRY:
        assert False, ('Scheme {} not found in'
                       ' supported storage ({}); path {}'.format(
                           result.scheme, _REGISTRY.keys(), url))
    return _REGISTRY[result.scheme]


_REGISTRY = {
    'gs': GcsCloudStorage(),
    's3': S3CloudStorage(),
}
