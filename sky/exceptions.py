"""Exceptions."""
import enum
import typing
from typing import List, Optional, Sequence

if typing.TYPE_CHECKING:
    from sky import status_lib
    from sky.backends import backend

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


class ResourcesMismatchError(Exception):
    """Raised when resources are mismatched."""
    pass


class CommandError(Exception):
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
            if len(command) > 100:
                # Chunck the command to avoid overflow.
                command = command[:100] + '...'
            message = (f'Command {command} failed with return code '
                       f'{returncode}.\n{error_msg}')
        super().__init__(message)


class ClusterNotUpError(Exception):
    """Raised when a cluster is not up."""

    def __init__(self,
                 message: str,
                 cluster_status: Optional['status_lib.ClusterStatus'],
                 handle: Optional['backend.ResourceHandle'] = None) -> None:
        super().__init__(message)
        self.cluster_status = cluster_status
        self.handle = handle


class ClusterSetUpError(Exception):
    """Raised when a cluster has setup error."""
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


class AWSAzFetchingError(Exception):
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
