"""State for API server process."""

# This state is used to block requests except /api operations, which is useful
# when a server is shutting down: new requests will be blocked, but existing
# requests will be allowed to finish and be operated via /api operations, e.g.
# /api/logs, /api/cancel, etc.
_block_requests = False

# Set in the API server main process once it decides to shut itself down, so
# health checks served from that process can report it as not ready.
_shutting_down = False


# TODO(aylei): refactor, state should be a instance property of API server app
# instead of a global variable.
def get_block_requests() -> bool:
    """Whether block requests except /api operations."""
    return _block_requests


def set_block_requests(shutting_down: bool) -> None:
    """Set the API server to block requests except /api operations."""
    global _block_requests
    _block_requests = shutting_down


def is_shutting_down() -> bool:
    """Whether the API server has decided to shut itself down."""
    return _shutting_down


def set_shutting_down() -> None:
    """Mark the API server as shutting itself down."""
    global _shutting_down
    _shutting_down = True
