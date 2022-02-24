"""Exceptions."""


class ResourcesUnavailableError(Exception):
    """Raised when resources are unavailable."""

    def __init__(self, *args: object, no_retry: bool = False) -> None:
        super().__init__(*args)
        self.no_retry = no_retry


class ResourcesMismatchError(Exception):
    """Raised when resources are mismatched."""

    pass
