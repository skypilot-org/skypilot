"""Splitting a managed job's submission-to-running time into phases."""
import types

from sky.metrics import launch_phases


def _task(created_at=0.0, submitted_at=10.0, start_at=1000.0):
    return {
        'spot_job_id': 1,
        'task_id': 0,
        'task_name': 'train',
        'created_at': created_at,
        'submitted_at': submitted_at,
        'start_at': start_at,
        'workspace': 'eng',
    }


def _attempt(provision_start=20.0,
             instances_requested=30.0,
             admitted=630.0,
             instances_ready=930.0,
             outcome='succeeded'):
    return types.SimpleNamespace(provision_start=provision_start,
                                 instances_requested=instances_requested,
                                 admitted=admitted,
                                 instances_ready=instances_ready,
                                 outcome=outcome,
                                 workspace='eng')


def test_phases_account_for_the_whole_wait():
    """The user waited from submission to running; every second belongs to
    some phase, or the breakdown quietly loses time."""
    total, phases = launch_phases.compute_job_timeline(_task(), [_attempt()])

    assert total == 1000.0
    assert sum(phases.values()) == total


def test_the_gap_between_attempts_becomes_retry_overhead():
    """A job slow because it retried must look different from a fast one.

    Nothing in the successful attempt's own record describes the abandoned
    tries, so without this span they simply vanish and the job reads as prompt.
    """
    # The successful attempt only starts at 620s: everything from the first
    # attempt up to it was spent on tries that were thrown away.
    total, phases = launch_phases.compute_job_timeline(_task(), [
        _attempt(provision_start=20.0,
                 instances_requested=30.0,
                 admitted=None,
                 instances_ready=None,
                 outcome='failed'),
        _attempt(provision_start=620.0,
                 instances_requested=630.0,
                 admitted=640.0,
                 instances_ready=930.0),
    ])

    assert phases[launch_phases.RETRY_OVERHEAD] == 600.0
    assert sum(phases.values()) == total


def test_a_clean_run_has_no_retry_overhead():
    """The residual must be zero when nothing was retried, or every job would
    look like it had wasted time."""
    _, phases = launch_phases.compute_job_timeline(_task(), [_attempt()])

    assert phases[launch_phases.RETRY_OVERHEAD] == 0.0


def test_controller_queue_is_the_wait_for_a_controller_not_for_quota():
    """These are different queues with opposite fixes.

    Conflating them is the misdiagnosis this whole breakdown exists to stop.
    """
    _, phases = launch_phases.compute_job_timeline(
        _task(created_at=0.0, submitted_at=10.0), [_attempt()])

    assert phases[launch_phases.CONTROLLER_QUEUE] == 10.0
    assert phases[launch_phases.QUEUE_WAIT] == 600.0


def test_runtime_setup_covers_instances_up_to_the_job_running():
    """Our own setup time must not be hidden inside node startup."""
    _, phases = launch_phases.compute_job_timeline(
        _task(start_at=1000.0), [_attempt(instances_ready=930.0)])

    assert phases[launch_phases.RUNTIME_SETUP] == 70.0


def test_a_job_with_no_recorded_attempt_is_not_reported_as_retried():
    """A pool job never provisions, so it has no attempt to break down.

    Putting its whole wait in retry_overhead renders a job that threw nothing
    away as almost entirely retries -- the exact misdiagnosis this breakdown
    exists to prevent. It has to be named as unknown instead.
    """
    total, phases = launch_phases.compute_job_timeline(_task(), [])

    assert total == 1000.0
    assert sum(phases.values()) == total
    assert launch_phases.QUEUE_WAIT not in phases
    assert phases.get(launch_phases.RETRY_OVERHEAD) in (None, 0)
    assert phases[launch_phases.UNATTRIBUTED] == 990.0


def test_an_admission_without_a_request_milestone_is_measured_not_dropped():
    """This was the one subtraction here with no guard on both endpoints.

    Raising is expensive -- the caller writes a whole batch of jobs, and a row
    that raises stays in the pending set to be retried forever. Skipping the
    phase instead would be quieter but drop the interval from the total. The
    wait is real, so it is measured from whichever boundary the cloud gave us.
    """
    total, phases = launch_phases.compute_job_timeline(_task(), [
        _attempt(
            instances_requested=None, admitted=700.0, instances_ready=900.0)
    ])

    assert sum(phases.values()) == total
    assert phases[launch_phases.QUEUE_WAIT] == 680.0


def test_timeline_columns_include_the_headline_total():
    """The jobs list reads one row; the total has to be on it."""
    total, phases = launch_phases.compute_job_timeline(_task(), [_attempt()])
    columns = launch_phases.timeline_columns(phases, total)

    assert columns['t_time_to_running'] == total
    assert columns['t_queue_wait'] == 600.0
    assert columns['t_controller_queue'] == 10.0


def test_a_recovery_after_first_running_does_not_become_the_final_attempt():
    """A job can be preempted and recover before its timeline is computed.

    That recovery's attempt finishes *after* the job first ran, so treating it
    as the delivering attempt makes runtime_setup negative and breaks the sum;
    the detail page would render a segment of negative width.
    """
    total, phases = launch_phases.compute_job_timeline(
        _task(start_at=1000.0),
        [
            _attempt(),  # delivered the cluster the job first ran on
            # A recovery that finished long after the job first reached RUNNING.
            _attempt(provision_start=1200.0,
                     instances_requested=1210.0,
                     admitted=1220.0,
                     instances_ready=1500.0),
        ])

    assert phases[launch_phases.RUNTIME_SETUP] > 0
    assert sum(phases.values()) == total


def test_a_job_that_starts_within_a_poll_of_readiness_is_still_attributed():
    """instances_ready is an observation, so it can land after start_at.

    The provisioner stamps it when its readiness poll returns, up to one
    interval after the instances were actually up. A runtime where the pod is
    the job has no separate bootstrap to fill that window, so the job routinely
    begins work inside it -- and rejecting the attempt for that reason reports
    the entire wait as unattributed, which is the misdiagnosis this breakdown
    exists to prevent.
    """
    total, phases = launch_phases.compute_job_timeline(
        _task(start_at=1000.0),
        [_attempt(instances_ready=1001.2)],  # observed 1.2s "after" the start
    )

    assert launch_phases.UNATTRIBUTED not in phases
    assert phases[launch_phases.PROVISION_SETUP] > 0
    assert sum(phases.values()) == total


def test_the_slack_does_not_let_a_recovery_become_the_final_attempt():
    """The reason the slack is a second pass rather than a wider bound.

    A recovery that finished a few seconds after the job first ran would
    satisfy a widened bound and be picked over the attempt that actually
    delivered the cluster -- silently changing the breakdown of jobs that are
    correct today. Falling back only when nothing qualifies strictly cannot do
    that: the delivering attempt already qualified.
    """
    total, phases = launch_phases.compute_job_timeline(
        _task(start_at=1000.0),
        [
            _attempt(),  # delivered the cluster, instances_ready=930
            # Inside the slack window, but not the attempt the job ran on.
            _attempt(provision_start=990.0,
                     instances_requested=995.0,
                     admitted=997.0,
                     instances_ready=1002.0),
        ])

    # Picked the first: retry_overhead spans nothing, and runtime_setup is the
    # real 70s gap rather than a negative sliver.
    assert phases[launch_phases.RETRY_OVERHEAD] == 0
    assert phases[launch_phases.RUNTIME_SETUP] == 70.0
    assert sum(phases.values()) == total


def test_the_observation_overlap_moves_rather_than_vanishing():
    """node_startup is long by exactly what runtime_setup is short by.

    Both end at the same late observation. Clamping runtime_setup to zero and
    leaving node_startup alone would drop the overlap from the total instead of
    attributing it, so it is charged back to the phase that over-counted it.
    """
    ready = 1002.0
    total, phases = launch_phases.compute_job_timeline(
        _task(start_at=1000.0),
        [
            _attempt(
                admitted=None, instances_requested=900.0, instances_ready=ready)
        ],
    )

    # Raw node_startup would be 102s and raw runtime_setup -2s.
    assert phases[launch_phases.NODE_STARTUP] == 100.0
    assert phases[launch_phases.RUNTIME_SETUP] == 0.0
    assert sum(phases.values()) == total


def test_no_phase_is_ever_negative():
    """The detail page renders these as segment widths.

    A negative one is not a small error; it draws backwards over its
    neighbour.
    """
    for ready in (930.0, 1000.0, 1000.5, 1002.0, 1004.9):
        total, phases = launch_phases.compute_job_timeline(
            _task(start_at=1000.0),
            [
                _attempt(admitted=None,
                         instances_requested=900.0,
                         instances_ready=ready)
            ],
        )
        assert all(v >= 0 for v in phases.values()), (ready, phases)
        assert abs(sum(phases.values()) - total) < 1e-9, (ready, phases)


def test_a_started_job_is_counted_on_the_path_it_took(monkeypatch):
    """The timings need a denominator, split by whether provisioning happened.

    A pool job skips provisioning, so its absence from those phases is expected
    rather than missing data -- without the split the two are indistinguishable.
    """
    counted = []
    monkeypatch.setattr(launch_phases.metrics_utils,
                        'observe_managed_job_time_to_running', lambda *a: None)
    monkeypatch.setattr(launch_phases.metrics_utils,
                        'observe_managed_job_phase', lambda *a: None)
    monkeypatch.setattr(launch_phases.metrics_utils, 'count_managed_job_start',
                        lambda o, p, w: counted.append((o, p, w)))

    launch_phases.observe_job_timeline('eng', 100.0, {}, on_pool=False)
    launch_phases.observe_job_timeline('eng', 100.0, {}, on_pool=True)

    assert counted == [('running', 'provision', 'eng'),
                       ('running', 'pool', 'eng')]


def test_a_job_that_never_ran_is_still_counted(monkeypatch):
    """It has no latency to report, but excluding it from the counts is how a
    fleet that mostly fails to start comes to look fast."""
    counted = []
    monkeypatch.setattr(launch_phases.metrics_utils, 'count_managed_job_start',
                        lambda o, p, w: counted.append((o, p, w)))

    launch_phases.count_job_that_never_ran(None, on_pool=False)

    assert counted == [('never_ran', 'provision', 'unknown')]


def test_a_cloud_without_a_request_milestone_still_accounts_for_everything():
    """VM clouds record no separate "instances requested" step.

    The preparation before provisioning still happened; leaving it out of every
    phase drops it from the total instead of attributing it, and the stack then
    silently under-reports the wait on those clouds.
    """
    total, phases = launch_phases.compute_job_timeline(_task(start_at=1000.0), [
        _attempt(instances_requested=None, admitted=None, instances_ready=900.0)
    ])

    assert sum(phases.values()) == total
    assert launch_phases.QUEUE_WAIT not in phases
