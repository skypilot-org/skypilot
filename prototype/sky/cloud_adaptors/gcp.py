"""GCP cloud adaptors"""

# pylint: disable=import-outside-toplevel


def build(service_name: str, version: str, *args, **kwargs):
    """Build a GCP service.

    Args:
        service_name: GCP service name (e.g., 'compute', 'storagetransfer').
        version: Service version (e.g., 'v1').
    """
    from googleapiclient import discovery
    return discovery.build(service_name, version, *args, **kwargs)


def storage_client():
    """Helper method that connects to GCS Storage Client for
    GCS Bucket
    """
    from google.cloud import storage
    return storage.Client()


def anonymous_storage_client():
    """Helper method that connects to GCS Storage Client for
    Public GCS Buckets
    """
    from google.cloud import storage
    return storage.Client.create_anonymous_client()


def not_found_exception():
    """NotFound exception."""
    from google.api_core import exceptions as gcs_exceptions
    return gcs_exceptions.NotFound


def forbidden_exception():
    """Forbidden exception."""
    from google.api_core import exceptions as gcs_exceptions
    return gcs_exceptions.Forbidden
