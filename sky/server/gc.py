"""Pluggable hook to pause server-side garbage-collection loops.

For operational/maintenance reasons it is sometimes useful to temporarily
pause the API server's background garbage-collection loops (e.g. the file-mount
blob GC and the various retention sweeps) without restarting the server.

This module defines the *seam* only. ``GCPauseProvider`` is the interface every
server-side GC loop consults (via ``skypilot_config.gc_should_skip``); the
default :class:`NoOpGCPauseProvider` always reports "not paused", so by default
every GC loop runs normally and the hook costs nothing.

A deployment that needs an actual pause control installs its own provider via
``ExtensionContext.register_gc_pause_provider`` (mirroring the blob-storage and
log-provider seams), backing it with whatever source of truth it prefers.
"""
import abc
import datetime
from typing import Optional

from sky import sky_logging

logger = sky_logging.init_logger(__name__)


class GCPauseProvider(abc.ABC):
    """Decides whether server-side garbage collection is currently paused.

    Deployments install a custom provider via
    ``ExtensionContext.register_gc_pause_provider`` to back the pause control
    with whatever source of truth they prefer (e.g. an auto-expiring config
    key). The default never pauses.
    """

    @abc.abstractmethod
    def is_paused(self) -> bool:
        """Returns whether all server-side GC should currently be paused."""
        raise NotImplementedError

    def paused_until(self) -> Optional[datetime.datetime]:
        """Returns the timestamp GC is paused until, or None.

        Optional, for observability/logging. Providers with no notion of an
        expiry (or that are not paused) return None. The default
        implementation returns None.
        """
        return None


class NoOpGCPauseProvider(GCPauseProvider):
    """Default provider: GC is never paused.

    Every GC loop runs normally. The pause capability is opt-in via a custom
    provider installed by the deployment.
    """

    def is_paused(self) -> bool:
        return False


_provider: Optional[GCPauseProvider] = None


def get_gc_pause_provider() -> GCPauseProvider:
    """Returns the registered GC pause provider (the no-op default if none)."""
    global _provider
    if _provider is None:
        _provider = NoOpGCPauseProvider()
    return _provider


def set_gc_pause_provider(provider: GCPauseProvider) -> None:
    """Sets the active GC pause provider.

    Called by plugins via ``ExtensionContext.register_gc_pause_provider``.
    """
    global _provider
    _provider = provider


# Alias matching the repo's ``register_*`` hook naming for pluggable
# components, for callers that prefer the verb form.
register_gc_pause_provider = set_gc_pause_provider


def reset_gc_pause_provider() -> None:
    """Resets to the no-op default provider (used by tests)."""
    global _provider
    _provider = None
