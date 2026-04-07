"""Replica manager for the SkyPilot API server.

Handles replica lifecycle, failover, and cross-replica operations.
In single-replica mode (default), the LocalReplicaManager is a no-op.
Plugins may provide a full implementation for multi-replica HA deployments
via ExtensionContext.register_replica_manager().
"""
import abc
from typing import AsyncIterator, Optional

from sky import sky_logging

logger = sky_logging.init_logger(__name__)


class ReplicaManager(abc.ABC):
    """Manages replica lifecycle, failover, and cross-replica operations."""

    @abc.abstractmethod
    def get_replica_id(self) -> str:
        """Get the unique ID of this replica."""
        raise NotImplementedError

    @abc.abstractmethod
    async def start(self) -> None:
        """Start replica manager (heartbeat, failover loop).

        Called during server lifespan startup.
        """
        raise NotImplementedError

    @abc.abstractmethod
    async def stop(self) -> None:
        """Stop replica manager.

        Called during server shutdown.
        """
        raise NotImplementedError

    @abc.abstractmethod
    def is_request_local(self, request_replica_id: Optional[str]) -> bool:
        """Check if a request is running on this replica.

        Args:
            request_replica_id: The replica_id stored in the request record.
                None means the request has no replica assignment (legacy).
        """
        raise NotImplementedError

    async def proxy_log_stream(self, request_id: str, replica_id: str,
                               **kwargs) -> AsyncIterator[bytes]:
        """Proxy log streaming to the replica that owns the request.

        Default: raises NotImplementedError (single-replica has no proxying).
        """
        raise NotImplementedError
        # Make this an async generator for type checking.
        yield b''  # pylint: disable=unreachable

    async def cancel_remote_request(self, request_id: str,
                                    replica_id: str, pid: int) -> None:
        """Cancel a request running on another replica.

        Default: raises NotImplementedError.
        """
        raise NotImplementedError


class LocalReplicaManager(ReplicaManager):
    """Single-replica manager for local/default deployments.

    All operations are local. No cross-replica communication needed.
    """

    _REPLICA_ID = 'local'

    def get_replica_id(self) -> str:
        return self._REPLICA_ID

    async def start(self) -> None:
        logger.debug('LocalReplicaManager started (single-replica mode)')

    async def stop(self) -> None:
        logger.debug('LocalReplicaManager stopped')

    def is_request_local(self, request_replica_id: Optional[str]) -> bool:
        # In single-replica mode, all requests are local.
        return True


_replica_manager: Optional[ReplicaManager] = None


def get_replica_manager() -> ReplicaManager:
    """Get the registered replica manager.

    Returns the plugin-provided manager if one has been registered,
    otherwise lazily creates and returns the default LocalReplicaManager.
    """
    global _replica_manager
    if _replica_manager is None:
        _replica_manager = LocalReplicaManager()
    return _replica_manager


def set_replica_manager(manager: ReplicaManager) -> None:
    """Set the replica manager.

    Called by plugins via ExtensionContext.register_replica_manager().
    """
    global _replica_manager
    _replica_manager = manager
