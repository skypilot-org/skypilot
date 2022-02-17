from sky.data import storage


def test_bucket_creation():
    storage_1 = storage.Storage(name='mluo-data', source='~/Downloads/temp/')
    storage_1.get_or_copy_to_s3()  # Transfers data from local to S3
    storage_1.get_or_copy_to_gcs()  # Transfers data from local to GCS


def test_bucket_deletion():
    storage_1 = storage.Storage(name='mluo-data', source='~/Downloads/temp/')
    storage_1.get_or_copy_to_s3()
    storage_1.get_or_copy_to_gcs()
    storage_1.delete()  # Deletes Data


def test_bucket_transfer():
    # First time upload to s3
    storage_1 = storage.Storage(name='mluo-data', source='~/Downloads/temp/')
    bucket_path = storage_1.get_or_copy_to_s3(
    )  # Transfers data from local to S3

    storage_2 = storage.Storage(name='mluo-data', source=bucket_path)
    storage_2.get_or_copy_to_s3()
    storage_2.get_or_copy_to_gcs()  # Transfer data from S3 to Gs bucket


def test_public_bucket_aws():
    # Public Dataset: https://registry.opendata.aws/tcga/#usageexamples
    storage_1 = storage.Storage(name='tcga-2-open', source='s3://tcga-2-open')

    storage_2 = storage.Storage(name='tcga-2-open', source='~/Downloads/temp/')
    try:
        # This should fail as you can't write to a public bucket
        storage_2.get_or_copy_to_s3()
    except Exception:
        pass
    storage_2.delete()


def test_public_bucket_gcp():
    # Public Dataset: https://registry.opendata.aws/tcga/#usageexamples
    storage_1 = storage.Storage(name='cloud-tpu-test-datasets',
                                source='gs://cloud-tpu-test-datasets')

    storage_2 = storage.Storage(name='cloud-tpu-test-datasets',
                                source='~/Downloads/temp/')
    try:
        # This should fail as you can't write to a public bucket
        storage_2.get_or_copy_to_gcs()
    except Exception:
        pass
    storage_2.delete()


if __name__ == '__main__':
    test_bucket_creation()
    test_bucket_deletion()
    test_bucket_transfer()
    test_public_bucket_aws()
    test_public_bucket_gcp()
