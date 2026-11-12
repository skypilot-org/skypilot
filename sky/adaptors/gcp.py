"""GCP cloud adaptors"""

# pylint: disable=import-outside-toplevel
import json
import logging
import warnings

from sky.adaptors import common


def _suppress_google_deprecation_warnings() -> None:
    """Suppresses informational FutureWarnings from Google client libraries.

    These warnings do not affect functionality, but they are printed in the
    middle of SkyPilot's CLI output on every GCP operation.
    Reference: https://github.com/skypilot-org/skypilot/issues/7886
    """
    # google.api_core warns about Python versions nearing or past end of
    # life ("You are using a Python version ... which Google will stop
    # supporting" and "You are using a non-supported Python version"), and
    # about deprecated dependency versions such as grpcio < 1.83.0.
    warnings.filterwarnings(
        'ignore',
        category=FutureWarning,
        module=r'google\.api_core\._python_(version|package)_support',
    )
    # Kept for google.api_core versions that emit the Python version warning
    # from a different module.
    warnings.filterwarnings(
        'ignore',
        category=FutureWarning,
        message=(r'.*You are using a Python version.*'
                 r'which Google will stop supporting.*'),
    )
    # google.auth (and other Google packages) warn that grpcio < 1.83.0 does
    # not support Post-Quantum Cryptography.
    warnings.filterwarnings(
        'ignore',
        category=FutureWarning,
        message=r'.*does not support Post-Quantum Cryptography.*',
    )


_suppress_google_deprecation_warnings()

# google_auth_httplib2 logs a benign per-request-timeout warning on every GCE
# metadata-server credential refresh. It's a logging call (not warnings.warn),
# so silence it by raising the logger level rather than filtering warnings.
logging.getLogger('google_auth_httplib2').setLevel(logging.ERROR)

_IMPORT_ERROR_MESSAGE = ('Failed to import dependencies for GCP. '
                         'Try pip install "skypilot[gcp]"')
googleapiclient = common.LazyImport('googleapiclient',
                                    import_error_message=_IMPORT_ERROR_MESSAGE)
google = common.LazyImport('google', import_error_message=_IMPORT_ERROR_MESSAGE)
_LAZY_MODULES = (google, googleapiclient)


@common.load_lazy_modules(_LAZY_MODULES)
def build(service_name: str, version: str, *args, **kwargs):
    """Build a GCP service.

    Args:
        service_name: GCP service name (e.g., 'compute', 'storagetransfer').
        version: Service version (e.g., 'v1').
    """

    return googleapiclient.discovery.build(service_name, version, *args,
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
def auth_error_exception():
    """GoogleAuthError exception."""
    from google.auth import exceptions
    return exceptions.GoogleAuthError


@common.load_lazy_modules(_LAZY_MODULES)
def gcp_auth_refresh_error_exception():
    """GCP auth refresh error exception."""
    from google.auth import exceptions
    return exceptions.RefreshError


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
