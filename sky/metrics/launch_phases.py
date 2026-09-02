"""Turning recorded launch attempts into phase-duration observations.

The milestones live in the ``launch_attempts`` table (see
``global_user_state.launch_attempt_table``) rather than in memory, because a
launch that parks on an external condition unwinds its whole provision call and
resumes in a different process. This module reads those rows back and computes
the segments between them.

The observer is deliberately not the process that did the provisioning: that
one is disposable (a new process per burst request), and it is not even
guaranteed to be the process that closes a segment it opened.
"""
import dataclasses
from typing import Any, List, Optional, Tuple

from sky import sky_logging
from sky.metrics import utils as metrics_utils

logger = sky_logging.init_logger(__name__)

# The phases of one provisioning attempt, in order. Together they partition
# the attempt's wall clock.
PROVISION_SETUP = 'provision_setup'
QUEUE_WAIT = 'queue_wait'
NODE_STARTUP = 'node_startup'

# attempt label values.
ATTEMPT_FINAL = 'final'
ATTEMPT_SUPERSEDED = 'superseded'

_UNKNOWN_WORKSPACE = 'unknown'

# global_user_state.LaunchOutcome values, spelled out rather than imported:
# global_user_state imports this package for its timing decorator, so importing
# it back here would close the cycle. Kept in sync by
# test_launch_phases_outcomes_match_global_user_state.
_OUTCOME_SUCCEEDED = 'succeeded'
_OUTCOME_ABANDONED = 'abandoned'


@dataclasses.dataclass
class PhaseSample:
    """One measured phase of an attempt."""
    phase: str
    duration: float


@dataclasses.dataclass
class DroppedPhase:
    """A phase whose measurement was lost rather than simply not applicable."""
    phase: str
    reason: str


def _first_set(*values: Optional[float]) -> Optional[float]:
    for value in values:
        if value is not None:
            return value
    return None


def compute_phases(row: Any) -> Tuple[List[PhaseSample], List[DroppedPhase]]:
    """Split one finished attempt into its phases.

    A phase is emitted only when both of its endpoints were recorded, which
    makes the three cases fall out without special-casing:

    * A failed attempt contributes the phases it got through and nothing for
      the one it died in -- the missing end is not a lost measurement, it is a
      segment that never happened. Emitting a truncated duration instead would
      quietly bias the distribution downwards.
    * A launch on a cloud that never stamps ``instances_requested`` (no pod
      creation step) reports its whole provisioning as ``node_startup``, which
      is what that time is there.
    * ``queue_wait`` is absent, not zero, where nothing gated the workload, so
      that jobs which never queued do not dilute the queue-wait distribution.

    Only an abandoned attempt yields dropped phases: there, a start with no end
    really does mean a measurement was lost when the writer died.
    """
    samples: List[PhaseSample] = []
    dropped: List[DroppedPhase] = []
    abandoned = row.outcome == _OUTCOME_ABANDONED

    def add(phase: str, start: Optional[float], end: Optional[float]) -> None:
        if start is None:
            return
        if end is None:
            if abandoned:
                dropped.append(DroppedPhase(phase, 'abandoned'))
            return
        samples.append(PhaseSample(phase, end - start))

    add(PROVISION_SETUP, row.provision_start, row.instances_requested)
    # Only where an external scheduler actually gated the workload.
    if row.admitted is not None or abandoned:
        add(QUEUE_WAIT, row.instances_requested, row.admitted)
    add(NODE_STARTUP,
        _first_set(row.admitted, row.instances_requested, row.provision_start),
        row.instances_ready)
    return samples, dropped


def observe_attempt(row: Any) -> None:
    """Emit the metrics for one finished attempt."""
    attempt = (ATTEMPT_FINAL
               if row.outcome == _OUTCOME_SUCCEEDED else ATTEMPT_SUPERSEDED)
    workspace = row.workspace or _UNKNOWN_WORKSPACE
    samples, dropped = compute_phases(row)
    for sample in samples:
        metrics_utils.observe_launch_phase(sample.phase, attempt, workspace,
                                           sample.duration)
    for drop in dropped:
        metrics_utils.count_launch_phase_dropped(drop.phase, drop.reason)
