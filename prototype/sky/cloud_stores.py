"""Cloud object stores.

Currently, used for transferring data in bulk.  Thus, this module does not
offer file-level calls (e.g., open, reading, writing).

TODO:
* Better interface.
* Better implementation (e.g., smart_open, using each cloud's SDK).  The
  full-blown impl should handle authentication so each user's private datasets
  can be accessed.
"""
import os
import urllib.parse


class CloudStorage(object):
    """Interface for a cloud object store."""

    def make_download_dir_command(self, source: str, destination: str) -> str:
        """Makes a runnable bash command to download a 'directory'."""
        raise NotImplementedError


class GcsCloudStorage(CloudStorage):
    """Google Cloud Storage."""

    def make_download_dir_command(self, source: str, destination: str) -> str:
        """Downloads using gsutil.

        Limitation: no authentication support; 'source' is assumed to in a
        publicly accessible bucket.
        """
        # We use gsutil as a basic implementation.  One pro is that its -m
        # multi-threaded download is nice, which frees us from implementing
        # parellel workers on our end.
        get_gsutil = [
            'pushd /tmp &>/dev/null',
            # Skip if /tmp/gsutil already exists.
            'ls gsutil &>/dev/null || (wget --quiet '
            'https://storage.googleapis.com/pub/gsutil.tar.gz && '
            'tar xzf gsutil.tar.gz)',
            'popd &>/dev/null',
        ]
        download_via_gsutil = '/tmp/gsutil/gsutil -m rsync -r {} {}'.format(
            source, destination)

        all_commands = get_gsutil
        all_commands.append(download_via_gsutil)
        return ' && '.join(all_commands)


def get_storage_from_path(url: str) -> CloudStorage:
    """Returns a CloudStorage by identifying the scheme:// in a URL."""
    result = urllib.parse.urlsplit(url)
    if result.scheme not in _REGISTRY:
        assert False, 'Scheme {} not found in'
        ' supported storage ({}); path {}'.format(result.scheme,
                                                  _REGISTRY.keys(), url)
    return _REGISTRY[result.scheme]


_REGISTRY = {
    'gs': GcsCloudStorage(),
}
