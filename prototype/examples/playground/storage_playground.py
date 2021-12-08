from sky import Storage


def test_bucket_creation():
    storage = Storage(name='mluo-data', source='~/Downloads/temp/')

    storage.add_backend('AWS')
    storage.add_backend('GCP')


def test_bucket_deletion():
    storage = Storage(name='mluo-data', source='~/Downloads/temp/')
    storage.add_backend('AWS')
    storage.add_backend('GCP')
    storage.delete()


def test_bucket_transfer():
    # First time upload to s3
    storage = Storage(name='mluo-data', source='~/Downloads/temp/')
    storage.add_backend("AWS")

    storage = Storage(name='mluo-data', source='s3://mluo-data')
    storage.add_backend('AWS')
    storage.add_backend('GCP')
