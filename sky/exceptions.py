"""Exceptions."""
import builtins
import enum
import traceback
import types
import typing
from typing import Any, Dict, List, Optional, Sequence

from sky.utils import env_options

if typing.TYPE_CHECKING:
    from sky.backends import backend
    from sky.utils import status_lib

# Return code for keyboard interruption and SIGTSTP
KEYBOARD_INTERRUPT_CODE = 130
SIGTSTP_CODE = 146
RSYNC_FILE_NOT_FOUND_CODE = 23
# Arbitrarily chosen value. Used in SkyPilot's storage mounting scripts
MOUNT_PATH_NON_EMPTY_CODE = 42
# Arbitrarily chosen value. Used to provision Kubernetes instance in Skypilot
INSUFFICIENT_PRIVILEGES_CODE = 52
# Return code when git command is ran in a dir that is not git repo
GIT_FATAL_EXIT_CODE = 128


def is_safe_exception(exc: Exception) -> bool:
    """Returns True if the exception is safe to send to clients.

    Safe exceptions are:
    1. Built-in exceptions
    2. SkyPilot's own exceptions
    """
    module = type(exc).__module__

    # Builtin exceptions (e.g., ValueError, RuntimeError)
    if module == 'builtins':
        return True

    # SkyPilot exceptions
    if module.startswith('sky.'):
        return True

    return False


def wrap_exception(exc: Exception) -> Exception:
    """Wraps non-safe exceptions into SkyPilot exceptions

    This is used to wrap exceptions that are not safe to deserialize at clients.

    Examples include exceptions from cloud providers whose packages are not
    available at clients.
    """
    if is_safe_exception(exc):
        return exc

    return CloudError(message=str(exc),
                      cloud_provider=type(exc).__module__.split('.')[0],
                      error_type=type(exc).__name__)


def serialize_exception(e: Exception) -> Dict[str, Any]:
    """Serialize the exception.

    This function also wraps any unsafe exceptions (e.g., cloud exceptions)
    into SkyPilot's CloudError before serialization to ensure clients can
    deserialize them without needing cloud provider packages installed.
    """
    # Wrap unsafe exceptions before serialization
    e = wrap_exception(e)

    stacktrace = getattr(e, 'stacktrace', None)
    attributes = e.__dict__.copy()
    if 'stacktrace' in attributes:
        del attributes['stacktrace']
    for attr_k in list(attributes.keys()):
        attr_v = attributes[attr_k]
        if isinstance(attr_v, types.TracebackType):
            attributes[attr_k] = traceback.format_tb(attr_v)

    data = {
        'type': e.__class__.__name__,
        'message': str(e),
        'args': e.args,
        'attributes': attributes,
        'stacktrace': stacktrace,
    }
    if isinstance(e, SkyPilotExcludeArgsBaseException):
        data['args'] = tuple()
    return data


def deserialize_exception(serialized: Dict[str, Any]) -> Exception:
    """Deserialize the exception."""
    exception_type = serialized['type']
    if hasattr(builtins, exception_type):
        exception_class = getattr(builtins, exception_type)
    else:
        exception_class = globals().get(exception_type, None)
    if exception_class is None:
        # Unknown exception type.
        return Exception(f'{exception_type}: {serialized["message"]}')
    e = exception_class(*serialized['args'], **serialized['attributes'])
    if serialized['stacktrace'] is not None:
        setattr(e, 'stacktrace', serialized['stacktrace'])
    return e


class CloudError(Exception):
    """Wraps cloud-specific errors into a SkyPilot exception."""

    def __init__(self, message: str, cloud_provider: str, error_type: str):
        super().__init__(message)
        self.cloud_provider = cloud_provider
        self.error_type = error_type

    def __str__(self):
        return (f'{self.cloud_provider} error ({self.error_type}): '
                f'{super().__str__()}')


class InvalidSkyPilotConfigError(ValueError):
    """Raised when the SkyPilot config is invalid."""
    pass


class ResourcesUnavailableError(Exception):
    """Raised when resources are unavailable.

    This is mainly used for the APIs in sky.execution; please refer to
    the docstring of sky.launch for more details about how the
    failover_history will be set.
    """

    def __init__(self,
                 message: str,
                 no_failover: bool = False,
                 failover_history: Optional[List[Exception]] = None) -> None:
        super().__init__(message)
        self.no_failover = no_failover
        if failover_history is None:
            failover_history = []
        # Copy the list to avoid modifying from outside.
        self.failover_history: List[Exception] = list(failover_history)

    def with_failover_history(
            self,
            failover_history: List[Exception]) -> 'ResourcesUnavailableError':
        # Copy the list to avoid modifying from outside.
        self.failover_history = list(failover_history)
        return self


class InvalidCloudConfigs(Exception):
    """Raised when invalid configurations are provided for a given cloud."""
    pass


class ProvisionPrechecksError(Exception):
    """Raised when a managed job fails prechecks before provision.

    Developer note: For now this should only be used by managed
    jobs code path (technically, this can/should be raised by the
    lower-level sky.launch()). Please refer to the docstring of
    `jobs.recovery_strategy._launch` for more details about when
    the error will be raised.

    Args:
        reasons: (Sequence[Exception]) The reasons why the prechecks failed.
    """

    def __init__(self, reasons: Sequence[Exception]) -> None:
        super().__init__()
        self.reasons = reasons


class ManagedJobReachedMaxRetriesError(Exception):
    """Raised when a managed job fails to be launched after maximum retries.

    Developer note: For now this should only be used by managed jobs code
    path. Please refer to the docstring of `jobs.recovery_strategy._launch`
    for more details about when the error will be raised.
    """
    pass


class ManagedJobStatusError(Exception):
    """Raised when a managed job task status update is invalid.

    For instance, a RUNNING job cannot become SUBMITTED.
    """
    pass


class ResourcesMismatchError(Exception):
    """Raised when resources are mismatched."""
    pass


class SkyPilotExcludeArgsBaseException(Exception):
    """Base class for exceptions that don't need args while serialization.

    Due to our serialization/deserialization logic, when an exception does
    not take `args` as an argument in __init__, `args` should not be included
    in the serialized exception.

    This is useful when an exception needs to construct the error message based
    on the arguments passed in instead of directly having the error message as
    the first argument in __init__. Refer to `CommandError` for an example.
    """
    pass


class CommandError(SkyPilotExcludeArgsBaseException):
    """Raised when a command fails.

    Args:
        returncode: The returncode of the command.
        command: The command that was run.
        error_message: The error message to print.
        detailed_reason: The stderr of the command.
    """

    def __init__(self, returncode: int, command: str, error_msg: str,
                 detailed_reason: Optional[str]) -> None:
        self.returncode = returncode
        self.command = command
        self.error_msg = error_msg
        self.detailed_reason = detailed_reason

        if not command:
            message = error_msg
        else:
            if (len(command) > 100 and
                    not env_options.Options.SHOW_DEBUG_INFO.get()):
                # Chunck the command to avoid overflow.
                command = command[:100] + '...'
            message = (f'Command {command} failed with return code '
                       f'{returncode}.\n{error_msg}')
        super().__init__(message)


class ClusterNotUpError(Exception):
    """Raised when a cluster is not up."""

    def __init__(self,
                 message: str,
                 cluster_status: Optional['status_lib.ClusterStatus'] = None,
                 handle: Optional['backend.ResourceHandle'] = None) -> None:
        super().__init__(message)
        self.cluster_status = cluster_status
        self.handle = handle


class ClusterSetUpError(Exception):
    """Raised when a cluster has setup error."""
    pass


class ClusterDoesNotExist(ValueError):
    """Raise when trying to operate on a cluster that does not exist."""
    # This extends ValueError for compatibility reasons - we used to throw
    # ValueError instead of this.
    pass


class NotSupportedError(Exception):
    """Raised when a feature is not supported."""
    pass


class StorageError(Exception):
    pass


class StorageSpecError(ValueError):
    # Errors raised due to invalid specification of the Storage object
    pass


class StorageInitError(StorageError):
    # Error raised when Initialization fails - either due to permissions,
    # unavailable name, or other reasons.
    pass


class StorageBucketCreateError(StorageInitError):
    # Error raised when bucket creation fails.
    pass


class StorageBucketGetError(StorageInitError):
    # Error raised if attempt to fetch an existing bucket fails.
    pass


class StorageBucketDeleteError(StorageError):
    # Error raised if attempt to delete an existing bucket fails.
    pass


class StorageUploadError(StorageError):
    # Error raised when bucket is successfully initialized, but upload fails,
    # either due to permissions, ctrl-c, or other reasons.
    pass


class StorageSourceError(StorageSpecError):
    # Error raised when the source of the storage is invalid. E.g., does not
    # exist, malformed path, or other reasons.
    pass


class StorageNameError(StorageSpecError):
    # Error raised when the source of the storage is invalid. E.g., does not
    # exist, malformed path, or other reasons.
    pass


class StorageModeError(StorageSpecError):
    # Error raised when the storage mode is invalid or does not support the
    # requested operation (e.g., passing a file as source to MOUNT mode)
    pass


class StorageExternalDeletionError(StorageBucketGetError):
    # Error raised when the bucket is attempted to be fetched while it has been
    # deleted externally.
    pass


class NonExistentStorageAccountError(StorageExternalDeletionError):
    # Error raise when storage account provided through config.yaml or read
    # from store handle(local db) does not exist.
    pass


class FetchClusterInfoError(Exception):
    """Raised when fetching the cluster info fails."""

    class Reason(enum.Enum):
        HEAD = 'HEAD'
        WORKER = 'WORKER'

    def __init__(self, reason: Reason) -> None:
        super().__init__()
        self.reason = reason


class NetworkError(Exception):
    """Raised when network fails."""
    pass


class ClusterStatusFetchingError(Exception):
    """Raised when fetching the cluster status fails."""
    pass


class ManagedJobUserCancelledError(Exception):
    """Raised when a user cancels a managed job."""
    pass


class InvalidClusterNameError(Exception):
    """Raised when the cluster name is invalid."""
    pass


class CloudUserIdentityError(Exception):
    """Raised when the cloud identity is invalid."""
    pass


class ClusterOwnerIdentityMismatchError(Exception):
    """The cluster's owner identity does not match the current user identity."""
    pass


class NoCloudAccessError(Exception):
    """Raised when all clouds are disabled."""
    pass


class AWSAzFetchingError(SkyPilotExcludeArgsBaseException):
    """Raised when fetching the AWS availability zone fails."""

    class Reason(enum.Enum):
        """Reason for fetching availability zone failure."""

        AUTH_FAILURE = 'AUTH_FAILURE'
        AZ_PERMISSION_DENIED = 'AZ_PERMISSION_DENIED'

        @property
        def message(self) -> str:
            if self == self.AUTH_FAILURE:
                return ('Failed to access AWS services. Please check your AWS '
                        'credentials.')
            elif self == self.AZ_PERMISSION_DENIED:
                return (
                    'Failed to retrieve availability zones. '
                    'Please ensure that the `ec2:DescribeAvailabilityZones` '
                    'action is enabled for your AWS account in IAM. '
                    'Ref: https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_DescribeAvailabilityZones.html.'  # pylint: disable=line-too-long
                )
            else:
                raise ValueError(f'Unknown reason {self}')

    def __init__(self, region: str,
                 reason: 'AWSAzFetchingError.Reason') -> None:
        self.region = region
        self.reason = reason

        super().__init__(reason.message)


class ServeUserTerminatedError(Exception):
    """Raised by serve controller when a user tear down the service."""
    pass


class PortDoesNotExistError(Exception):
    """Raised when the port does not exist."""


class UserRequestRejectedByPolicy(Exception):
    """Raised when a user request is rejected by an admin policy."""
    pass


class NoClusterLaunchedError(Exception):
    """No cluster launched, so cleanup can be skipped during failover."""
    pass


class RequestCancelled(Exception):
    """Raised when a request is cancelled."""
    pass


class ApiServerConnectionError(RuntimeError):
    """Raised when the API server cannot be connected."""

    def __init__(self, server_url: str):
        super().__init__(
            f'Could not connect to SkyPilot API server at {server_url}. '
            f'Please ensure that the server is running. '
            f'Try: curl {server_url}/api/health')
