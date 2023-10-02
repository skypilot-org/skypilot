"""GCP cloud adaptors"""

# pylint: disable=import-outside-toplevel
import functools

googleapiclient = None
google = None


def import_package(func):

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        global googleapiclient, google
        if googleapiclient is None or google is None:
            try:
                import google as _google
                import googleapiclient as _googleapiclient
                googleapiclient = _googleapiclient
                google = _google
            except ImportError:
                raise ImportError('Failed to import dependencies for GCP. '
                                  'Try: pip install "skypilot[gcp]"') from None
        return func(*args, **kwargs)

    return wrapper


@import_package
def build(service_name: str, version: str, *args, **kwargs):
    """Build a GCP service.

    Args:
        service_name: GCP service name (e.g., 'compute', 'storagetransfer').
        version: Service version (e.g., 'v1').
    """
    from googleapiclient import discovery
    return discovery.build(service_name, version, *args, **kwargs)


@import_package
def storage_client():
    """Helper method that connects to GCS Storage Client for
    GCS Bucket
    """
    from google.cloud import storage
    return storage.Client()


@import_package
def anonymous_storage_client():
    """Helper method that connects to GCS Storage Client for
    Public GCS Buckets
    """
    from google.cloud import storage
    return storage.Client.create_anonymous_client()


@import_package
def not_found_exception():
    """NotFound exception."""
    from google.api_core import exceptions as gcs_exceptions
    return gcs_exceptions.NotFound


@import_package
def forbidden_exception():
    """Forbidden exception."""
    from google.api_core import exceptions as gcs_exceptions
    return gcs_exceptions.Forbidden


@import_package
def http_error_exception():
    """HttpError exception."""
    from googleapiclient import errors
    return errors.HttpError


@import_package
def credential_error_exception():
    """CredentialError exception."""
    from google.auth import exceptions
    return exceptions.DefaultCredentialsError
