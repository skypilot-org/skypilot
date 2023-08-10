# Playground scripts for experimenting with storage
# These are not exhaustive tests. Actual Tests are in tests/test_storage.py and
# tests/test_smoke.py.

from sky.data import storage
from sky.data import StoreType


def get_args():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('-b', '--bucket-name', default='sky-play-bucket')
    parser.add_argument('-l', '--local-source', default='/home/romilb/tmp')
    return parser.parse_args()


def test_bucket_creation():
    print("Running test_bucket_creation")
    storage_1 = storage.Storage(name=TEST_BUCKET_NAME, source=LOCAL_SOURCE_PATH)
    storage_1.add_store(StoreType.S3)  # Transfers data from local to S3
    storage_1.add_store(StoreType.GCS)  # Transfers data from local to GCS


def test_bucket_deletion():
    print("Running test_bucket_deletion")
    storage_1 = storage.Storage(name=TEST_BUCKET_NAME, source=LOCAL_SOURCE_PATH)
    storage_1.add_store(StoreType.S3)
    storage_1.add_store(StoreType.GCS)
    storage_1.delete()  # Deletes Data


def test_bucket_transfer():
    print("Running test_bucket_transfer")
    # First time upload to s3
    storage_1 = storage.Storage(name=TEST_BUCKET_NAME, source=LOCAL_SOURCE_PATH)
    store = storage_1.add_store(StoreType.S3)  # Transfers local to S3
    storage_2 = storage.Storage(source="s3://" + TEST_BUCKET_NAME)
    storage_2.add_store(StoreType.GCS)  # Transfer data from S3 to GCS bucket


def test_public_bucket_aws():
    print("Running test_public_bucket_aws")
    try:
        # This should fail as you can't write to a public bucket
        storage_2 = storage.Storage(name='tcga-2-open',
                                    source=LOCAL_SOURCE_PATH)
    except Exception:
        print("Uploading to public bucket failed successfully.")
    storage_2.delete()


def test_public_bucket_gcp():
    print("Running test_public_bucket_gcp")
    try:
        # This should fail as you can't write to a public bucket
        storage_2 = storage.Storage(name='cloud-tpu-test-datasets',
                                    source=LOCAL_SOURCE_PATH)
        storage_2.add_store(StoreType.GCS)
    except Exception:
        print("Uploading to public bucket failed successfully.")
    storage_2.delete()


if __name__ == '__main__':
    args = get_args()
    TEST_BUCKET_NAME = args.bucket_name
    LOCAL_SOURCE_PATH = args.local_source
    test_bucket_creation()
    test_bucket_transfer()
    test_public_bucket_aws()
    test_public_bucket_gcp()
    test_bucket_deletion()
