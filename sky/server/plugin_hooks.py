"""Plugin lifecycle hooks for the SkyPilot API server.

Plugins can register callbacks here to react to events emitted by core code
(e.g. a volume being deleted). Hooks are best-effort: any exception raised
by a callback is logged and swallowed so it cannot affect the originating
operation.
"""
from typing import Callable, List, TYPE_CHECKING

from sky import sky_logging

if TYPE_CHECKING:
    from sky import models

logger = sky_logging.init_logger(__name__)

VolumeDeletedHook = Callable[[str, 'models.VolumeConfig'], None]

_VOLUME_DELETED_HOOKS: List[VolumeDeletedHook] = []


def register_volume_deleted_hook(fn: VolumeDeletedHook) -> None:
    """Register a callback to run after a volume is removed from the database.

    The callback receives the volume name and its ``VolumeConfig``. It is
    invoked whether the deletion path is normal or ``purge=True``.
    """
    _VOLUME_DELETED_HOOKS.append(fn)


def fire_volume_deleted(name: str, config: 'models.VolumeConfig') -> None:
    """Invoke all registered volume-deleted callbacks.

    Each callback runs in isolation; an exception in one does not affect
    other callbacks or the caller.
    """
    for fn in _VOLUME_DELETED_HOOKS[:]:
        try:
            fn(name, config)
        except Exception as e:  # pylint: disable=broad-except
            logger.warning(
                f'volume-deleted hook {fn!r} raised for {name!r}: {e}')
