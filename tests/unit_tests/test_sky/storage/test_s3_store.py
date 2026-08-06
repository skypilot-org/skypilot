"""Unit tests for S3Store region handling in MOUNT_CACHED commands."""

from unittest import mock

from sky.adaptors import aws
from sky.data import storage as storage_lib


def _make_s3_store(client=None, **attrs):
    """Creates a bare S3Store object without running __init__."""
    store = object.__new__(storage_lib.S3Store)
    store.name = attrs.get('name', 'test-bucket')
    store.source = attrs.get('source', 's3://test-bucket')
    store.region = attrs.get('region', 'us-east-1')
    store._bucket_sub_path = attrs.get('_bucket_sub_path', None)
    store.client = client if client is not None else mock.MagicMock()
    store.bucket = mock.MagicMock()
    store.bucket.name = store.name
    return store


def _head_bucket_response(region):
    headers = {} if region is None else {'x-amz-bucket-region': region}
    return {'ResponseMetadata': {'HTTPHeaders': headers}}


class TestS3StoreGetBucketRegion:
    """Tests for S3Store._get_bucket_region."""

    def test_returns_region_from_head_bucket(self):
        client = mock.MagicMock()
        client.head_bucket.return_value = _head_bucket_response('eu-south-2')
        store = _make_s3_store(client=client)
        assert store._get_bucket_region() == 'eu-south-2'
        client.head_bucket.assert_called_once_with(Bucket='test-bucket')

    def test_returns_region_from_error_response(self):
        """The region header is present even on error responses."""
        error_response = _head_bucket_response('eu-south-2')
        error_response['Error'] = {
            'Code': '301',
            'Message': 'Moved Permanently'
        }
        client = mock.MagicMock()
        client.head_bucket.side_effect = aws.botocore_exceptions().ClientError(
            error_response, 'HeadBucket')
        store = _make_s3_store(client=client)
        assert store._get_bucket_region() == 'eu-south-2'

    def test_returns_none_when_header_missing(self):
        client = mock.MagicMock()
        client.head_bucket.return_value = _head_bucket_response(None)
        store = _make_s3_store(client=client)
        assert store._get_bucket_region() is None


class TestS3StoreMountCachedCommand:
    """Tests for S3Store.mount_cached_command region handling."""

    @mock.patch('sky.clouds.AWS.should_use_env_auth_for_s3', return_value=True)
    def test_rclone_profile_sets_bucket_region(self, mock_env_auth):
        """The bucket's actual region must end up in the rclone profile.

        Regression test for #9540: rclone defaulted to the us-east-1
        endpoint, breaking MOUNT_CACHED for buckets in opt-in regions
        such as eu-south-2.
        """
        client = mock.MagicMock()
        client.head_bucket.return_value = _head_bucket_response('eu-south-2')
        store = _make_s3_store(client=client)
        cmd = store.mount_cached_command('/mnt/data')
        assert 'region = eu-south-2' in cmd

    @mock.patch('sky.clouds.AWS.should_use_env_auth_for_s3', return_value=True)
    def test_rclone_profile_without_detected_region(self, mock_env_auth):
        """If the region cannot be detected, the profile stays unchanged."""
        client = mock.MagicMock()
        client.head_bucket.return_value = _head_bucket_response(None)
        store = _make_s3_store(client=client)
        cmd = store.mount_cached_command('/mnt/data')
        assert 'region =' not in cmd
