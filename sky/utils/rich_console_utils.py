"""Utility functions for rich console."""
import os
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
        # When FORCE_COLOR is set, force terminal mode. Otherwise pass
        # None to let Rich auto-detect via isatty(). Using bool() would
        # return False when unset, explicitly disabling terminal detection.
        force_color = os.environ.get('FORCE_COLOR')
        force_terminal = True if force_color else None
        _console = rich_console.Console(soft_wrap=True,
                                        force_terminal=force_terminal)
    return _console
