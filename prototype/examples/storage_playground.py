from sky import Storage


def test_bucket_creation():
    storage = Storage(name="mluo-data",
                      source_path="~/Downloads/temp/",
                      default_mount_path="/tmp/mluo-data")

    storage.add_backend("AWS")
    storage.add_backend("GCP")


def test_bucket_deletion():
    storage = Storage(name="mluo-data",
                      source_path="~/Downloads/temp/",
                      default_mount_path="/tmp/mluo-data")
    storage.add_backend("AWS")
    storage.add_backend("GCP")
    storage.cleanup()


def test_bucket_transfer():

    # First time upload to s3
    storage = Storage(name="mluo-data",
                      source_path="~/Downloads/temp/",
                      default_mount_path="/tmp/mluo-data")
    storage.add_backend("AWS")

    storage = Storage(name="mluo-data",
                      source_path="s3://mluo-data",
                      default_mount_path="/tmp/data/")
    storage.add_backend("AWS")
    storage.add_backend("GCP")
