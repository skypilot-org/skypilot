"""AWS cloud adaptors"""

# pylint: disable=import-outside-toplevel

import functools
import threading
import time

from sky.utils import common_utils

boto3 = None
botocore = None
_session_creation_lock = threading.RLock()
_local = threading.local()


def import_package(func):

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        global boto3, botocore
        if boto3 is None or botocore is None:
            try:
                import boto3 as _boto3
                import botocore as _botocore
                boto3 = _boto3
                botocore = _botocore
            except ImportError:
                raise ImportError('Fail to import dependencies for AWS.'
                                  'Try pip install "skypilot[aws]"') from None
        return func(*args, **kwargs)

    return wrapper


@import_package
def session():
    """Create an AWS session."""
    # Creating the session object is not thread-safe for boto3,
    # so we add a reentrant lock to synchronize the session creation.
    # Reference: https://github.com/boto/boto3/issues/1592

    # Retry 5 times by default for potential credential errors,
    # mentioned in
    # https://github.com/skypilot-org/skypilot/pull/1988
    if hasattr(_local, 'session'):
        return _local.session

    max_attempts = 5
    attempt = 0
    backoff = common_utils.Backoff()
    err = None
    while attempt < max_attempts:
        try:
            with _session_creation_lock:
                # NOTE: we need the lock here to avoid
                # thread-safety issues when creating the session,
                # because Python module is a shared object,
                # and we are not sure the if code inside
                # boto3.session.Session() is thread-safe.
                _local.session = boto3.session.Session()
            return _local.session
        except (botocore_exceptions().CredentialRetrievalError,
                botocore_exceptions().NoCredentialsError) as e:
            time.sleep(backoff.current_backoff())
            err = e
            attempt += 1
    raise err


@import_package
def resource(service_name: str, **kwargs):
    """Create an AWS resource of a certain service.

    It is relatively fast to create a resource from the session (<1s), so we
    don't need to cache the resource object.

    Args:
        resource_name: AWS resource name (e.g., 's3').
        kwargs: Other options.
    """
    if not hasattr(_local, 'resource'):
        _local.resource = {}

    # Using service name and kwargs as key
    sorted_kwargs = tuple(sorted(kwargs.items(), key=lambda x: x[0]))
    key = (service_name, sorted_kwargs)
    if key not in _local.resource:
        with _session_creation_lock:
            # NOTE: we need the lock here to avoid
            # thread-safety issues when creating the resource,
            # because Python module is a shared object,
            # and we are not sure if the code inside
            # 'session().resource()' is thread-safe.
            _local.resource[key] = session().resource(service_name, **kwargs)

    return _local.resource[key]


def client(service_name: str, **kwargs):
    """Create an AWS client of a certain service.

    Args:
        service_name: AWS service name (e.g., 's3', 'ec2').
        kwargs: Other options.
    """
    # Need to use the client retrieved from the per-thread session
    # to avoid thread-safety issues (Directly creating the client
    # with boto3.client() is not thread-safe).
    # Reference: https://stackoverflow.com/a/59635814
    if not hasattr(_local, 'client'):
        _local.client = {}

    # Using service name and kwargs as key
    sorted_kwargs = tuple(sorted(kwargs.items(), key=lambda x: x[0]))
    key = (service_name, sorted_kwargs)
    if key not in _local.client:
        with _session_creation_lock:
            # NOTE: we need the lock here to avoid
            # thread-safety issues when creating the client,
            # because Python module is a shared object,
            # and we are not sure if the code inside
            # 'session().client()' is thread-safe.
            _local.client[key] = session().client(service_name, **kwargs)

    return _local.client[key]


@import_package
def botocore_exceptions():
    """AWS botocore exception."""
    from botocore import exceptions
    return exceptions
