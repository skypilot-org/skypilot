"""Service / pool status runner: abstraction + registry.

A ``ServiceStatusRunner`` is the strategy object that the server's
service/pool status entry points delegate to. The registered runner
decides *how* the operation executes — the default runner talks to the
controller via gRPC or codegen+subprocess, while a plugin-provided
runner might call the serve DB directly when the controller is
in-process (consolidation mode).

At most one runner is registered at a time. If nothing has registered,
``current()`` lazily constructs ``_DefaultServiceStatusRunner`` from
``sky.serve.server.impl`` so there's always a usable runner regardless
of import ordering. Plugins override the default by calling
``register(MyRunner())`` in their ``install()`` phase.

Thread-safety: ``register()`` is only expected to be called during
server/plugin startup, before request handling begins. The module-level
reference is written and read atomically under the GIL, and only the
default + plugin-provided runner are registered (sequentially), so no
lock is needed.
"""
import typing
from typing import Any, Dict, List, Optional, Protocol

from sky import sky_logging

if typing.TYPE_CHECKING:
    from sky import backends

logger = sky_logging.init_logger(__name__)


class ServiceStatusRunner(Protocol):
    """Strategy interface for service/pool status fetching.

    Note: only ``handle`` is passed in. The default runner's RPC path
    does not need a ``CloudVmRayBackend``, so we don't compute one
    eagerly. Implementations that need a backend (e.g. the legacy
    codegen + ``run_on_head`` fallback) should derive it lazily from
    ``handle`` via ``backend_utils.get_backend_from_handle``.
    """

    def get_service_status(
        self,
        *,
        handle: 'backends.CloudVmRayResourceHandle',
        service_names: Optional[List[str]],
        pool: bool,
    ) -> List[Dict[str, Any]]:
        ...


_current: Optional[ServiceStatusRunner] = None


def register(runner: ServiceStatusRunner) -> None:
    """Install ``runner`` as the currently-active service status runner.

    Last registration wins. Plugins override the default in ``install()``.
    """
    # pylint: disable=global-statement
    global _current
    _current = runner
    logger.debug('Registered ServiceStatusRunner: %s', type(runner).__name__)


def current() -> ServiceStatusRunner:
    """Return the registered runner, falling back to the default.

    If nothing has been registered, constructs and installs
    ``_DefaultServiceStatusRunner`` so there's always a usable runner
    regardless of import ordering.
    """
    # pylint: disable=global-statement
    global _current
    if _current is None:
        # pylint: disable=import-outside-toplevel
        from sky.serve.server.impl import _DefaultServiceStatusRunner
        _current = _DefaultServiceStatusRunner()
    return _current


def reset_for_testing() -> None:
    """Reset the registered runner. Tests only."""
    # pylint: disable=global-statement
    global _current
    _current = None
