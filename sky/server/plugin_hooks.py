"""Plugin lifecycle hooks for the SkyPilot API server.

Plugins can register callbacks here to react to events emitted by core code
(e.g. a volume being deleted). Hooks are best-effort: any exception raised
by a callback is logged and swallowed so it cannot affect the originating
operation.
"""
from typing import Callable, Dict, TYPE_CHECKING

from sky import sky_logging

if TYPE_CHECKING:
    from sky import models

logger = sky_logging.init_logger(__name__)

VolumeDeletedHook = Callable[[str, 'models.VolumeConfig'], None]

_VOLUME_DELETED_HOOKS: Dict[str, VolumeDeletedHook] = {}


def register_volume_deleted_hook(hook_id: str, fn: VolumeDeletedHook) -> None:
    """Register a callback to run after a volume is removed from the database.

    The callback receives the volume name and its ``VolumeConfig``. It is
    invoked whether the deletion path is normal or ``purge=True``.

    Args:
        hook_id: A stable, unique identifier for this hook (e.g.
            ``"automount.volume_delete_cleanup"``). Re-registering with the
            same ID replaces the previous callback so a plugin loaded twice
            in the same process does not fire its hook twice.
        fn: The callback to invoke.
    """
    if hook_id in _VOLUME_DELETED_HOOKS:
        logger.debug(
            f'Replacing existing volume-deleted hook for id {hook_id!r}.')
    _VOLUME_DELETED_HOOKS[hook_id] = fn


def fire_volume_deleted(name: str, config: 'models.VolumeConfig') -> None:
    """Invoke all registered volume-deleted callbacks.

    Each callback runs in isolation; an exception in one does not affect
    other callbacks or the caller.
    """
    # Iterate over a snapshot so a hook that registers another hook (or a
    # concurrent register call) cannot mutate the dict mid-iteration.
    for hook_id, fn in list(_VOLUME_DELETED_HOOKS.items()):
        try:
            fn(name, config)
        except Exception as e:  # pylint: disable=broad-except
            logger.warning(
                f'volume-deleted hook {hook_id!r} raised for {name!r}: {e}')
