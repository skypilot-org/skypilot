"""State for API server process."""

_block_requests = False


# TODO(aylei): refactor, state should be a instance property of API server app
# instead of a global variable.
def get_block_requests() -> bool:
    """Whether the API server is shutting down."""
    return _block_requests


def set_block_requests(shutting_down: bool) -> None:
    """Set the API server to be shutting down."""
    global _block_requests
    _block_requests = shutting_down
