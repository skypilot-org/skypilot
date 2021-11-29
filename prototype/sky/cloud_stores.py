"""Cloud object stores.

Currently, used for transferring data in bulk.  Thus, this module does not
offer file-level calls (e.g., open, reading, writing).

TODO:
* Better interface.
* Better implementation (e.g., fsspec, smart_open, using each cloud's SDK).
  The full-blown impl should handle authentication so each user's private
  datasets can be accessed.
"""
import urllib.parse


class CloudStorage(object):
    """Interface for a cloud object store."""

    def make_sync_dir_command(self, source: str, destination: str) -> str:
        """Makes a runnable bash command to sync a 'directory'.

        Both 'source' and 'destination' must end with a trailing slash (/).
        """
        raise NotImplementedError

    def make_sync_file_command(self, source: str, destination: str) -> str:
        """Makes a runnable bash command to sync a file.

        Both 'source' and 'destination' must not end with a trailing slash (/).
        """
        raise NotImplementedError


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

    def make_sync_dir_command(self, source: str, destination: str) -> str:
        """Downloads a directory using gsutil.

        Limitation: no authentication support; 'source' is assumed to in a
        publicly accessible bucket.
        """
        assert source.endswith('/') and destination.endswith('/'), (source,
                                                                    destination)
        download_via_gsutil = (
            f'/tmp/gsutil/gsutil -m rsync -r {source[:-1]} {destination[:-1]}')
        all_commands = self._GET_GSUTIL
        all_commands.append(download_via_gsutil)
        return ' && '.join(all_commands)

    def make_sync_file_command(self, source: str, destination: str) -> str:
        """Downloads a file using gsutil.

        Limitation: no authentication support; 'source' is assumed to in a
        publicly accessible bucket.
        """
        assert not source.endswith('/') and not destination.endswith('/'), (
            source, destination)
        download_via_gsutil = (
            f'/tmp/gsutil/gsutil -m cp {source} {destination}')
        all_commands = self._GET_GSUTIL
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
}
