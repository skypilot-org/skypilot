"""GCP cloud adaptors"""

# pylint: disable=import-outside-toplevel
import json

from sky.adaptors import common

_IMPORT_ERROR_MESSAGE = ('Failed to import dependencies for GCP. '
                         'Try pip install "skypilot[gcp]"')
googleapiclient = common.LazyImport('googleapiclient',
                                    import_error_message=_IMPORT_ERROR_MESSAGE)
google = common.LazyImport('google', import_error_message=_IMPORT_ERROR_MESSAGE)
_LAZY_MODULES = (google, googleapiclient)


# The google-api-python-client library is built on top of the httplib2 library,
# which is not thread-safe.
# Reference: https://googleapis.github.io/google-api-python-client/docs/thread_safety.html
# We use a thread-local LRU cache to ensure that each thread has its own
# httplib2.Http object.
@common.load_lazy_modules(_LAZY_MODULES)
@common.thread_local_lru_cache()
def build(service_name: str, version: str, *args, **kwargs):
    """Build a GCP service.

    Args:
        service_name: GCP service name (e.g., 'compute', 'storagetransfer').
        version: Service version (e.g., 'v1').
    """
    import google_auth_httplib2
    import googleapiclient
    from googleapiclient import discovery
    import httplib2

    credentials = kwargs.pop('credentials', None)
    if credentials is None:
        credentials, _ = google.auth.default()
    
    # Create a new Http() object for every request, to ensure that each thread
    # has its own httplib2.Http object for thread safety.
    def build_request(http, *args, **kwargs):
        new_http = google_auth_httplib2.AuthorizedHttp(credentials,
                                                       http=httplib2.Http())
        return googleapiclient.http.HttpRequest(new_http, *args, **kwargs)

    authorized_http = google_auth_httplib2.AuthorizedHttp(credentials,
                                                          http=httplib2.Http())
    return discovery.build(service_name,
                           version,
                           *args,
                           requestBuilder=build_request,
                           http=authorized_http,
                           **kwargs)


@common.load_lazy_modules(_LAZY_MODULES)
def storage_client():
    """Helper that connects to GCS Storage Client for GCS Bucket"""
    from google.cloud import storage
    return storage.Client()


@common.load_lazy_modules(_LAZY_MODULES)
def anonymous_storage_client():
    """Helper that connects to GCS Storage Client for Public GCS Buckets"""
    from google.cloud import storage
    return storage.Client.create_anonymous_client()


@common.load_lazy_modules(_LAZY_MODULES)
def not_found_exception():
    """NotFound exception."""
    from google.api_core import exceptions as gcs_exceptions
    return gcs_exceptions.NotFound


@common.load_lazy_modules(_LAZY_MODULES)
def forbidden_exception():
    """Forbidden exception."""
    from google.api_core import exceptions as gcs_exceptions
    return gcs_exceptions.Forbidden


@common.load_lazy_modules(_LAZY_MODULES)
def http_error_exception():
    """HttpError exception."""
    from googleapiclient import errors
    return errors.HttpError


@common.load_lazy_modules(_LAZY_MODULES)
def credential_error_exception():
    """CredentialError exception."""
    from google.auth import exceptions
    return exceptions.DefaultCredentialsError


@common.load_lazy_modules(_LAZY_MODULES)
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
