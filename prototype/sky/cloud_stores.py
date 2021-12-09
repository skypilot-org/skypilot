"""Cloud object stores.

Currently, used for transferring data in bulk.  Thus, this module does not
offer file-level calls (e.g., open, reading, writing).

TODO:
* Better interface.
* Better implementation (e.g., fsspec, smart_open, using each cloud's SDK).
  The full-blown impl should handle authentication so each user's private
  datasets can be accessed.
"""
import subprocess
import urllib.parse

from sky.backends import backend_utils


class CloudStorage(object):
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

    def make_download_dir_command(self, source: str, destination: str) -> str:
        """Downloads using AWS CLI.
        """
        get_awscli = [
            'pip install awscli',
        ]
        # AWS Sync by default uses 10 threads to upload files to the bucket.
        # To increase parallelism, modify max_concurrent_requests in your
        # aws config file (Default path: ~/.aws/config).
        download_via_awscli = f'mkdir -p {destination} && \
                                aws s3 sync {source} {destination}'

        all_commands = get_awscli
        all_commands.append(download_via_awscli)
        return ' && '.join(all_commands)


class GcsCloudStorage(CloudStorage):
    """Google Cloud Storage."""

    # We use gsutil as a basic implementation.  One pro is that its -m
    # multi-threaded download is nice, which frees us from implementing
    # parellel workers on our end.
    _GET_GSUTIL = [
        'pushd /tmp &>/dev/null',
        # Skip if /tmp/gsutil already exists.
        'ls gsutil &>/dev/null || (wget --quiet '
        'https://storage.googleapis.com/pub/gsutil.tar.gz && '
        'tar xzf gsutil.tar.gz)',
        'popd &>/dev/null',
    ]

    _GSUTIL = '/tmp/gsutil/gsutil'

    def is_directory(self, url: str) -> bool:
        """Returns whether 'url' is a directory.

        In cloud object stores, a "directory" refers to a regular object whose
        name is a prefix of other objects.
        """
        commands = list(self._GET_GSUTIL)
        commands.append(f'{self._GSUTIL} ls -d {url}')
        command = ' && '.join(commands)
        p = backend_utils.run(command, stdout=subprocess.PIPE)
        out = p.stdout.decode().strip()
        # gsutil ls -d url
        #   --> url.rstrip('/')          if url is not a directory
        #   --> url with an ending '/'   if url is a directory
        if not out.endswith('/'):
            assert out == url.rstrip('/'), (out, url)
            return False
        url = url if url.endswith('/') else (url + '/')
        assert out == url, (out, url)
        return True

    def make_sync_dir_command(self, source: str, destination: str) -> str:
        """Downloads a directory using gsutil.

        Limitation: no authentication support; 'source' is assumed to in a
        publicly accessible bucket.
        """
        download_via_gsutil = (
            f'{self._GSUTIL} -m rsync -r {source} {destination}')
        all_commands = list(self._GET_GSUTIL)
        all_commands.append(download_via_gsutil)
        return ' && '.join(all_commands)

    def make_sync_file_command(self, source: str, destination: str) -> str:
        """Downloads a file using gsutil.

        Limitation: no authentication support; 'source' is assumed to in a
        publicly accessible bucket.
        """
        download_via_gsutil = f'{self._GSUTIL} -m cp {source} {destination}'
        all_commands = list(self._GET_GSUTIL)
        all_commands.append(download_via_gsutil)
        return ' && '.join(all_commands)


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
