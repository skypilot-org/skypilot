import time

import pytest

from sky import exceptions
from sky.data import storage as storage_lib


class TestStorageSpecLocalSource:
    """Tests for local sources"""

    def test_nonexist_local_source(self):
        with pytest.raises(exceptions.StorageSourceError) as e:
            storage_lib.Storage(name='test',
                                source=f'/tmp/test-{int(time.time())}')
        assert 'Local source path does not exist' in str(e)

    def test_source_trailing_slashes(self):
        with pytest.raises(exceptions.StorageSourceError) as e:
            storage_lib.Storage(name='test', source='/bin/')
        assert 'Storage source paths cannot end with a slash' in str(e)


class TestStorageSpecValidation:
    """Storage specification validation tests"""

    # These tests do not create any buckets and can be run offline
    def test_source_and_name(self):
        """Tests when both name and source are specified"""
        # When source is local and name is also specified - valid spec
        storage_lib.Storage(name='test', source='/bin')

        # When source is bucket URL and name is specified - invalid spec
        with pytest.raises(exceptions.StorageSpecError) as e:
            storage_lib.Storage(name='test', source='s3://tcga-2-open')

        assert 'Storage name should not be specified if the source is a ' \
               'remote URI.' in str(e)

    def test_source_and_noname(self):
        """Tests when only source is specified"""
        # When source is local, name must be specified
        with pytest.raises(exceptions.StorageNameError) as e:
            storage_lib.Storage(source='/bin')

        assert 'Storage name must be specified if the source is local' in str(e)

        # When source is bucket URL and name is not specified - valid spec
        # Cannot run this test because it requires AWS credentials to initialize
        # bucket.
        # storage_lib.Storage(source='s3://tcga-2-open')

    def test_name_and_nosource(self):
        """Tests when only name is specified"""
        # When mode is COPY - invalid spec
        with pytest.raises(exceptions.StorageSourceError) as e:
            storage_lib.Storage(name='my-bucket',
                                mode=storage_lib.StorageMode.COPY)

        assert 'Storage source must be specified when using COPY mode' in str(e)

        # When mode is MOUNT - valid spec (e.g., use for scratch space)
        storage_lib.Storage(name='my-bucket',
                            mode=storage_lib.StorageMode.MOUNT)

    def test_noname_and_nosource(self):
        """Tests when neither name nor source is specified"""
        # Storage cannot be specified without name or source - invalid spec
        with pytest.raises(exceptions.StorageSpecError) as e:
            storage_lib.Storage()

        assert 'Storage source or storage name must be specified' in str(e)
