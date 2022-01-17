"""GCP cloud adaptors"""


def build(serviceName: str, version: str, *args, **kwargs):
    from googleapiclient import discovery
    return discovery.build(serviceName, version, *args, **kwargs)


def storage_client():
    """Helper method that connects to GCS Storage Client for
    GCS Bucket
    """
    from google.cloud import storage
    return storage.Client()


def not_found_exception():
    from google.api_core import exceptions as gcs_exceptions
    return gcs_exceptions.NotFound
