"""Exceptions."""
import enum

# Return code for keyboard interruption
KEYBOARD_INTERRUPT_CODE = 130


class ResourcesUnavailableError(Exception):
    """Raised when resources are unavailable."""

    def __init__(self, *args: object, no_retry: bool = False) -> None:
        super().__init__(*args)
        self.no_retry = no_retry


class ResourcesMismatchError(Exception):
    """Raised when resources are mismatched."""
    pass


class CommandError(Exception):
    """Raised when a command fails.

      returncode: The returncode of the command.
      command: The command that was run.
      error_message: The error message to print.
    """

    def __init__(self, returncode: int, command: str, error_msg: str) -> None:
        super().__init__()
        self.returncode = returncode
        self.command = command
        self.error_msg = error_msg


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


class FetchIPError(Exception):
    """Raised when fetching the IP fails."""

    class Reason(enum.Enum):
        HEAD = 'HEAD'
        WORKER = 'WORKER'

    def __init__(self, reason: Reason) -> None:
        super().__init__()
        self.reason = reason


class NetworkError(Exception):
    """Raised when network fails."""
    pass
