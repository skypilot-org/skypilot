"""GCP cloud adaptors"""

# pylint: disable=import-outside-toplevel
import json

from sky.adaptors import common

_IMPORT_ERROR_MESSAGE = ('Fail to import dependencies for GCP. '
                         'Try pip install "skypilot[gcp]"')
googleapiclient = common.LazyImport('googleapiclient',
                                    import_error_message=_IMPORT_ERROR_MESSAGE)
google = common.LazyImport('google', import_error_message=_IMPORT_ERROR_MESSAGE)


def build(service_name: str, version: str, *args, **kwargs):
    """Build a GCP service.

    Args:
        service_name: GCP service name (e.g., 'compute', 'storagetransfer').
        version: Service version (e.g., 'v1').
    """
    return googleapiclient.discovery.build(service_name, version, *args,
                                           **kwargs)


def storage_client():
    """Connects to GCS Storage Client for GCS Bucket"""
    return google.cloud.storage.Client()


def anonymous_storage_client():
    """Connects to GCS Storage Client for Public GCS Buckets"""
    return google.cloud.storage.Client.create_anonymous_client()


def not_found_exception():
    """NotFound exception."""
    return google.api_core.exceptions.NotFound


def forbidden_exception():
    """Forbidden exception."""
    return google.api_core.exceptions.Forbidden


def http_error_exception():
    """HttpError exception."""
    return googleapiclient.errors.HttpError


def credential_error_exception():
    """CredentialError exception."""
    return google.auth.exceptions.DefaultCredentialsError


def get_credentials(cred_type: str, credentials_field: str):
    """Get GCP credentials."""
    if cred_type == 'service_account':
        # If parsing the gcp_credentials failed, then the user likely made a
        # mistake in copying the credentials into the config yaml.
        try:
            service_account_info = json.loads(credentials_field)
        except json.decoder.JSONDecodeError as e:
            raise RuntimeError('gcp_credentials found in cluster yaml file but '
                               'formatted improperly.') from e
        credentials = (google.oauth2.service_account.Credentials.
                       from_service_account_info(service_account_info))
    elif cred_type == 'credentials_token':
        # Otherwise the credentials type must be credentials_token.
        credentials = google.oauth2.credentials.Credentials(credentials_field)
    return credentials
