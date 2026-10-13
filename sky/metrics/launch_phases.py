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
from typing import Any, Dict, List, Optional, Tuple

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


def queue_wait_from(attempt: Any) -> Optional[float]:
    """Where an admission wait is measured from.

    Whichever boundary the cloud gave us: a cloud that stamps ``admitted`` but
    has no separate request step would otherwise have the phase dropped in one
    reader and reported in another -- two answers for one launch.

    Named rather than inlined because three readers need the same answer: the
    per-attempt phases, the job timeline, and the admission event, which sits
    in another module and would otherwise carry a duration that disagrees with
    `t_queue_wait` on exactly the clouds this fallback exists for.
    """
    return _first_set(attempt.instances_requested, attempt.provision_start)


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
    queue_from = queue_wait_from(row)
    # Only where an external scheduler was involved at all. An abandoned
    # attempt counts a dropped phase, so without the queue check every
    # abandoned launch on a deployment with no scheduler would report losing a
    # measurement that never existed -- noise in a counter that is supposed to
    # mean exactly that something was lost.
    if row.admitted is not None or (abandoned and row.queue):
        add(QUEUE_WAIT, queue_from, row.admitted)
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
        # Also against the queue it waited in, where a scheduler named one.
        if sample.phase == QUEUE_WAIT and row.queue:
            metrics_utils.observe_launch_queue_wait(workspace, row.queue,
                                                    sample.duration)
    for drop in dropped:
        metrics_utils.count_launch_phase_dropped(drop.phase, drop.reason)


# --- Job timeline -----------------------------------------------------------
#
# The phases above describe one provisioning attempt. These describe a managed
# job's whole path from submission to running, which is what the user actually
# waited through: it spans the attempts that were thrown away as well as the
# one that worked.

CONTROLLER_QUEUE = 'controller_queue'
RETRY_OVERHEAD = 'retry_overhead'
RUNTIME_SETUP = 'runtime_setup'
# Time that belongs to no phase we can name, kept apart from retry_overhead so
# a job that threw nothing away does not read as though it had.
UNATTRIBUTED = 'unattributed'

# The spot column each phase is denormalized into.
_JOB_PHASE_COLUMNS = {
    CONTROLLER_QUEUE: 't_controller_queue',
    RETRY_OVERHEAD: 't_retry_overhead',
    UNATTRIBUTED: 't_unattributed',
    PROVISION_SETUP: 't_provision_setup',
    QUEUE_WAIT: 't_queue_wait',
    NODE_STARTUP: 't_node_startup',
    RUNTIME_SETUP: 't_runtime_setup',
}

# How late `instances_ready` can be. It is stamped when the provisioner's
# readiness poll returns rather than when the instances became ready, so it
# trails the real event by up to one poll interval -- 2s on Kubernetes today.
# Held here as a plain bound rather than imported from any one provisioner:
# this module is deliberately cloud-agnostic, and the number only has to be
# large enough to cover a poll and far smaller than a recovery, which cannot
# happen without a run and a teardown first.
_READINESS_OBSERVATION_SLACK = 5.0


def _delivering_attempt(attempts: List[Any], start_at: float) -> Optional[Any]:
    """The attempt that delivered the cluster the job first ran on.

    The newest success bounded by ``start_at``, not simply the newest: a job
    can be preempted and recover before this is computed, and that recovery's
    attempt finished after the job first ran. Picking it would make
    ``runtime_setup`` negative and break the invariant that the phases sum to
    the total -- which the detail page renders directly, as a segment of
    negative width.

    ``instances_ready`` is an observation and runs up to one poll interval
    late, so a job that began work promptly after its instances came up can
    fail that bound through no fault of its own. Rather than widen the bound --
    which would also admit a short recovery, and change which attempt is picked
    for jobs that work correctly today -- the slack is a second pass, reached
    only when the strict one finds nothing.
    """
    for bound in (start_at, start_at + _READINESS_OBSERVATION_SLACK):
        found = next(
            (a for a in reversed(attempts)
             if a.outcome == _OUTCOME_SUCCEEDED and
             a.instances_ready is not None and a.instances_ready <= bound),
            None)
        if found is not None:
            return found
    return None


def compute_job_timeline(task: Any,
                         attempts: List[Any]) -> Tuple[float, Dict[str, float]]:
    """Split a job's submission-to-running time into phases.

    ``task`` carries eligible_at (when this task could first have started),
    submitted_at (claimed by a controller) and start_at (running); ``attempts``
    is every launch attempt made for its cluster, oldest first.

    The origin is eligible_at rather than the job's creation time because a
    pipeline's tasks run one after another: task N is not waiting on anything
    of ours until task N-1 finishes, and measuring from submission would fold
    every upstream task's runtime into controller_queue. For a single task, and
    for every task of a job group, the two are the same moment.

    ``retry_overhead`` spans from the first attempt to the one that worked, so
    it covers the tries that were thrown away and the backoff between them.
    Measuring it that way rather than as a leftover matters: a leftover would
    also absorb the ordinary preparation between a controller claiming the job
    and provisioning starting, and every clean run would then report retry
    overhead it never had.

    Returns the total and the per-phase durations, which sum to it.
    """
    # Indexed, not defaulted: the queries that produce these rows
    # require eligible_at, so a missing one is a broken invariant and
    # should surface as an error rather than as a plausible number
    # measured from the wrong instant.
    origin = task['eligible_at']
    total = task['start_at'] - origin
    phases: Dict[str, float] = {
        CONTROLLER_QUEUE: task['submitted_at'] - origin,
    }

    final = _delivering_attempt(attempts, task['start_at'])
    if final is None:
        # Nothing to break down: a job placed on a warm pool never
        # provisions, and one launched before these milestones has no attempt
        # recorded. The wait still happened, so report it whole rather than
        # dropping the job -- but as unattributed. Calling it retry overhead
        # would render a pool job that threw nothing away as almost entirely
        # retries, which is the misdiagnosis this breakdown exists to prevent.
        phases[UNATTRIBUTED] = total - sum(phases.values())
        return total, phases

    retry_overhead = final.provision_start - attempts[0].provision_start
    phases[RETRY_OVERHEAD] = retry_overhead
    # Everything from the controller claiming the job up to the point the
    # instances were asked for, minus the attempts that were thrown away.
    # Measured to instances_requested where the cloud records it, and to the
    # start of provisioning where it does not -- on a cloud with no separate
    # request step the preparation still happened, and leaving it out would
    # drop it from the total rather than attribute it.
    startup_from = queue_wait_from(final)
    phases[PROVISION_SETUP] = (startup_from - task['submitted_at'] -
                               retry_overhead)
    if final.admitted is not None:
        # Measured from whichever boundary the cloud gave us. Subtracting
        # instances_requested unguarded was the one place here that could raise
        # on a missing milestone, and skipping the phase instead would drop the
        # interval from the total rather than attribute it -- the caller writes
        # a whole batch of jobs, so both failure modes are expensive.
        phases[QUEUE_WAIT] = final.admitted - startup_from
    phases[NODE_STARTUP] = final.instances_ready - _first_set(
        final.admitted, startup_from)
    phases[RUNTIME_SETUP] = task['start_at'] - final.instances_ready
    if phases[RUNTIME_SETUP] < 0:
        # The job began work before the instances were *observed* ready. Both
        # phases end at that same observation, so node_startup is long by
        # exactly what runtime_setup is short by -- charge it back rather than
        # clamping, which would drop the interval from the total instead of
        # attributing it. Bounded by node_startup itself: it cannot be spent
        # twice, and anything left over is a real inconsistency rather than
        # poll lag, so it stays visible here instead of being smeared into a
        # neighbouring phase.
        moved = max(phases[RUNTIME_SETUP], -phases[NODE_STARTUP])
        phases[NODE_STARTUP] += moved
        phases[RUNTIME_SETUP] -= moved
    return total, phases


def timeline_columns(phases: Dict[str, float],
                     total: float) -> Dict[str, float]:
    """The spot columns to write for a computed timeline."""
    columns = {
        _JOB_PHASE_COLUMNS[phase]: duration
        for phase, duration in phases.items()
        if phase in _JOB_PHASE_COLUMNS
    }
    columns['t_time_to_running'] = total
    return columns


def observe_job_timeline(workspace: Optional[str], total: float,
                         phases: Dict[str, float], on_pool: bool) -> None:
    """Emit the metrics for one job's submission-to-running time."""
    label = workspace or _UNKNOWN_WORKSPACE
    metrics_utils.observe_managed_job_time_to_running(label, total)
    for phase, duration in phases.items():
        metrics_utils.observe_managed_job_phase(phase, label, duration)
    metrics_utils.count_managed_job_start(
        metrics_utils.JOB_OUTCOME_RUNNING, metrics_utils.JOB_PATH_POOL
        if on_pool else metrics_utils.JOB_PATH_PROVISION, label)


def count_job_that_never_ran(workspace: Optional[str], on_pool: bool) -> None:
    """Record a job that went terminal without ever running.

    It has no timing to report, but leaving it out of the count entirely is how
    a fleet that mostly fails to start comes to look fast.
    """
    metrics_utils.count_managed_job_start(
        metrics_utils.JOB_OUTCOME_NEVER_RAN, metrics_utils.JOB_PATH_POOL
        if on_pool else metrics_utils.JOB_PATH_PROVISION, workspace or
        _UNKNOWN_WORKSPACE)
