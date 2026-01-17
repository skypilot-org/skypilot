import unittest
from unittest.mock import MagicMock
from sky.task import Task
from sky.data.storage import Storage, StoreType


class TestStorageRegion(unittest.TestCase):

    def test_should_construct_storage_with_region(self):
        # Case 1: New bucket (source is None)
        storage = MagicMock(spec=Storage)
        storage.source = None
        self.assertTrue(
            Task.should_construct_storage_with_region(storage, StoreType.S3))

        # Case 2: New bucket (source is list of files)
        storage.source = ['/tmp/file1', '/tmp/file2']
        self.assertTrue(
            Task.should_construct_storage_with_region(storage, StoreType.S3))

        # Case 3: New bucket (source is file://)
        storage.source = 'file:///tmp/file1'
        self.assertTrue(
            Task.should_construct_storage_with_region(storage, StoreType.S3))

        # Case 4: Matching Cloud Store (S3 -> S3)
        storage.source = 's3://my-bucket'
        self.assertTrue(
            Task.should_construct_storage_with_region(storage, StoreType.S3))

        # Case 5: Matching Cloud Store (Azure -> Azure)
        storage.source = 'https://myaccount.blob.core.windows.net/mycontainer'
        self.assertTrue(
            Task.should_construct_storage_with_region(storage, StoreType.AZURE))

        # Case 6: Mismatching Cloud Store (S3 bucket, but task on Azure)
        storage.source = 's3://my-bucket'
        self.assertFalse(
            Task.should_construct_storage_with_region(storage, StoreType.AZURE))

        # Case 7: Mismatching Cloud Store (Azure bucket, but task on S3)
        storage.source = 'https://myaccount.blob.core.windows.net/mycontainer'
        self.assertFalse(
            Task.should_construct_storage_with_region(storage, StoreType.S3))
