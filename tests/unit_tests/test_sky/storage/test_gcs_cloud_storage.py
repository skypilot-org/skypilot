"""Unit tests for GcsCloudStorage download command construction.

Regression tests for the gsutil -> ``gcloud storage`` migration (issue #8457):
the GCS download path must invoke ``gcloud storage cp``/``gcloud storage
rsync`` rather than the slower ``gsutil`` alias, while still explicitly
activating the service account.
"""

import unittest

from sky import cloud_stores


class TestGcsCloudStorageCommands(unittest.TestCase):
    """GCS download commands must use ``gcloud storage`` (issue #8457)."""

    def test_make_sync_file_command_uses_gcloud_storage(self):
        cmd = cloud_stores.GcsCloudStorage().make_sync_file_command(
            'gs://bucket/path/file', '/dest')
        self.assertIn('gcloud storage cp gs://bucket/path/file /dest', cmd)
        # The legacy gsutil alias must no longer be used on the download path.
        self.assertNotIn('skypilot_gsutil', cmd)
        # The service account must still be activated explicitly, otherwise
        # the gcloud CLI would not authenticate with the key file.
        self.assertIn('gcloud auth activate-service-account', cmd)

    def test_make_sync_dir_command_uses_gcloud_storage(self):
        cmd = cloud_stores.GcsCloudStorage().make_sync_dir_command(
            'gs://bucket/path', '/dest')
        # ``gcloud storage rsync`` ignores symlinks by default, matching the
        # previous ``gsutil rsync -e``; recursion is requested with ``-r``.
        self.assertIn('gcloud storage rsync -r gs://bucket/path /dest', cmd)
        self.assertNotIn('skypilot_gsutil', cmd)
        self.assertIn('gcloud auth activate-service-account', cmd)


if __name__ == '__main__':
    unittest.main()
