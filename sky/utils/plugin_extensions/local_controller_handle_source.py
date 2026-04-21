"""Extension point for custom local controller handles.

In consolidation mode, the jobs/serve controller runs in the same process
as the API server. SkyPilot represents this via a `LocalResourcesHandle`
whose `get_command_runners()` returns a `LocalProcessCommandRunner` that
spawns a subprocess for each codegen invocation.

This extension point lets plugins provide a custom factory that returns a
subclass of `LocalResourcesHandle`, typically to swap in a CommandRunner
that executes codegen in-process (avoiding the ~2.5s subprocess + import
overhead per operation).

Example usage in a plugin:

    from sky.utils.plugin_extensions import LocalControllerHandleSource

    class MyFastHandle(backends.LocalResourcesHandle):
        def get_command_runners(self, *a, **kw):
            return [MyInProcessRunner()]

    def _factory(cluster_name):
        return MyFastHandle(
            cluster_name=cluster_name,
            cluster_name_on_cloud=cluster_name,
            cluster_yaml=None,
            launched_nodes=1,
            launched_resources=sky.Resources(
                cloud=clouds.Cloud(), instance_type=cluster_name),
        )

    LocalControllerHandleSource.register(_factory)

Example usage in core SkyPilot:

    handle = LocalControllerHandleSource.create(cluster_name='my-ctrl')
    if handle is None:
        handle = backends.LocalResourcesHandle(...)  # default
"""
from typing import Any, Optional, Protocol

from sky import sky_logging

logger = sky_logging.init_logger(__name__)


class LocalControllerHandleFactoryFunc(Protocol):
    """Protocol for plugin-provided LocalResourcesHandle factories."""

    def __call__(self, cluster_name: str) -> Any:
        ...


class LocalControllerHandleSource:
    """Singleton registry for the plugin-provided handle factory.

    At most one factory can be registered; the last registration wins.
    If no factory is registered, `create()` returns None and callers
    should fall back to the default LocalResourcesHandle construction.
    """

    _factory: Optional[LocalControllerHandleFactoryFunc] = None

    @classmethod
    def register(cls, factory: LocalControllerHandleFactoryFunc) -> None:
        """Register a factory that constructs a LocalResourcesHandle.

        Args:
            factory: Callable taking `cluster_name: str` and returning a
                LocalResourcesHandle (or subclass).
        """
        cls._factory = factory
        logger.debug('Registered local controller handle factory')

    @classmethod
    def is_registered(cls) -> bool:
        """Return True if a plugin has registered a factory."""
        return cls._factory is not None

    @classmethod
    def create(cls, cluster_name: str) -> Optional[Any]:
        """Construct a LocalResourcesHandle via the registered factory.

        Returns None if no factory is registered, or if the factory raises
        an exception (in which case the caller should use the default).
        """
        if cls._factory is None:
            return None
        try:
            # pylint: disable=not-callable
            return cls._factory(cluster_name=cluster_name)
        except Exception as e:  # pylint: disable=broad-except
            logger.warning(
                f'Plugin-provided LocalResourcesHandle factory failed: '
                f'{e}; falling back to default handle.')
            return None
