"""Exceptions."""
import enum


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


class FetchIPError(Exception):
    """Raised when fetching the IP fails."""

    class Reason(enum.Enum):
        HEAD = 'HEAD'
        WORKER = 'WORKER'

    def __init__(self, reason: Reason) -> None:
        super().__init__()
        self.reason = reason
