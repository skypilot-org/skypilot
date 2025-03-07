"""Utility functions for rich console."""
import typing

from sky.adaptors import common as adaptors_common

if typing.TYPE_CHECKING:
    import rich.console as rich_console
else:
    rich_console = adaptors_common.LazyImport('rich.console')

_console = None  # Lazy initialized console


# Move global console to a function to avoid
# importing rich console if not used
def get_console():
    """Get or create the rich console."""
    global _console
    if _console is None:
        _console = rich_console.Console(soft_wrap=True)
    return _console
