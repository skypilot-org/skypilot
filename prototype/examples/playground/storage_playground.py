from sky.data import storage


def test_bucket_creation():
    storage_1 = storage.Storage(name='mluo-data1', source='~/Downloads/temp/')
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


if __name__ == '__main__':
    test_bucket_creation()
    #test_bucket_deletion()
    #test_bucket_transfer()
