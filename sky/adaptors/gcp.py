"""GCP cloud adaptors"""

# pylint: disable=import-outside-toplevel
import functools
import json

from sky.adaptors import common

_IMPORT_ERROR_MESSAGE = ('Failed to import dependencies for GCP. '
                         'Try pip install "skypilot[gcp]"')
googleapiclient = common.LazyImport('googleapiclient',
                                    import_error_message=_IMPORT_ERROR_MESSAGE)
google = common.LazyImport('google', import_error_message=_IMPORT_ERROR_MESSAGE)


def import_package(func):
    # import_package decorator is to check the dependencies and output user-
    # friendly error messages if the dependencies are not installed before any
    # function is called.

    # We cannot use LazyImport for the underlying google.cloud.storage, because
    # google.cloud can be imported but google.cloud.storage cannot be retrieved
    # with getattr(google.cloud, 'storage').

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        google.load_module()
        googleapiclient.load_module()
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


@import_package
def get_credentials(cred_type: str, credentials_field: str):
    """Get GCP credentials."""
    from google.oauth2 import service_account
    from google.oauth2.credentials import Credentials as OAuthCredentials

    if cred_type == 'service_account':
        # If parsing the gcp_credentials failed, then the user likely made a
        # mistake in copying the credentials into the config yaml.
        try:
            service_account_info = json.loads(credentials_field)
        except json.decoder.JSONDecodeError as e:
            raise RuntimeError('gcp_credentials found in cluster yaml file but '
                               'formatted improperly.') from e
        credentials = service_account.Credentials.from_service_account_info(
            service_account_info)
    elif cred_type == 'credentials_token':
        # Otherwise the credentials type must be credentials_token.
        credentials = OAuthCredentials(credentials_field)
    return credentials
