"""Runtime observations that a provisioner reports while a launch is waiting.

A provisioner whose workload can start, fail and restart before the launch
returns reports what it saw here instead of writing the launching caller's
state. The caller names a target in the launch context under
``LAUNCH_CONTEXT_KEY``; the provisioner passes that target back unchanged, and
the sink registered for the target's ``kind`` persists the observation.
"""
import typing
from typing import Any, Dict, Optional, Protocol

if typing.TYPE_CHECKING:
    from sky.jobs import runtime as managed_job_runtime

LAUNCH_CONTEXT_KEY = 'runtime_observation_target'

Target = Dict[str, Any]


class ObservationSink(Protocol):
    """Persists observations for one kind of observation target."""

    def previous(
            self,
            target: Target) -> Optional['managed_job_runtime.RuntimeCursor']:
        """Return the last persisted observation for the target, if any."""
        ...  # pylint: disable=unnecessary-ellipsis

    def report(self, target: Target,
               observation: 'managed_job_runtime.RuntimeObservation') -> None:
        """Persist an observation made before the launch returned."""
        ...  # pylint: disable=unnecessary-ellipsis


_sinks: Dict[str, ObservationSink] = {}


def register_sink(kind: str, sink: ObservationSink) -> None:
    _sinks[kind] = sink


def _sink(target: Target) -> ObservationSink:
    kind = target.get('kind')
    if kind not in _sinks:
        raise ValueError(f'No runtime observation sink for {kind!r}')
    return _sinks[kind]


def previous(
        target: Optional[Target]
) -> Optional['managed_job_runtime.RuntimeCursor']:
    """Return the target's last persisted observation; None without a target."""
    if target is None:
        return None
    return _sink(target).previous(target)


def report(target: Optional[Target],
           observation: 'managed_job_runtime.RuntimeObservation') -> None:
    """Persist an observation; a launch without a target ignores it."""
    if target is not None:
        _sink(target).report(target, observation)
