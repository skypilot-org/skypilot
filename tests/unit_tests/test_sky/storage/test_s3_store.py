"""Unit tests for AWS S3 store behavior."""

from unittest import mock

from sky.data import storage as storage_lib


def test_zurich_falls_back_to_default_s3_region() -> None:
    with mock.patch.object(storage_lib.S3CompatibleStore,
                           '__init__',
                           return_value=None) as mock_parent_init:
        storage_lib.S3Store(name='bucket', source=None, region='eu-central-2')

    mock_parent_init.assert_called_once_with('bucket', None, 'us-east-1', None,
                                             True, None)
