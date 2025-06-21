"""State for API server process."""

_block_requests = False
_block_all = False

# TODO(aylei): refactor, state should be a instance property of API server app
# instead of a global variable.
def get_block_requests() -> bool:
    """Whether to block sky requests being submitted."""
    return _block_requests

def get_block_all() -> bool:
    """Whether to block all API operations."""
    return _block_all

def set_block_requests(block: bool) -> None:
    global _block_requests
    _block_requests = block

def set_block_all(block: bool) -> None:
    global _block_all
    _block_all = block
    