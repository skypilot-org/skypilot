"""The same breakdown for a job that has not started yet.

The settled timeline closes every phase against start_at. Here one phase is
still running, and which one it is -- not how long it has been -- is the answer
someone opens the page for.
"""
import types

from sky.metrics import launch_phases

# eligible at 0, claimed by a controller at 10, first provisioned at 20.
_ORIGIN = 0.0
_SUBMITTED = 10.0


def _task(eligible_at=_ORIGIN, submitted_at=_SUBMITTED, start_at=None):
    return {
        'job_id': 1,
        'task_id': 0,
        'task_name': 'train',
        'eligible_at': eligible_at,
        'submitted_at': submitted_at,
        'start_at': start_at,
    }


def _attempt(provision_start=20.0,
             instances_requested=None,
             admitted=None,
             instances_ready=None,
             outcome=None,
             queue=None):
    return types.SimpleNamespace(provision_start=provision_start,
                                 instances_requested=instances_requested,
                                 admitted=admitted,
                                 instances_ready=instances_ready,
                                 outcome=outcome,
                                 queue=queue)


def test_the_phases_sum_to_the_elapsed_time_at_every_milestone():
    """The bar is drawn as fractions of the total beside it, so a breakdown
    that does not add up renders as a bar that does not fill."""
    ladder = [
        [],
        [_attempt()],
        [_attempt(instances_requested=30.0, queue='eng-lq')],
        [_attempt(instances_requested=30.0, admitted=630.0, queue='eng-lq')],
        [
            _attempt(instances_requested=30.0,
                     admitted=630.0,
                     instances_ready=930.0,
                     queue='eng-lq')
        ],
    ]
    for attempts in ladder:
        progress = launch_phases.compute_job_progress(_task(), attempts, 1000.0)
        assert progress is not None
        assert progress.total == 1000.0
        assert abs(sum(progress.phases.values()) - 1000.0) < 1e-9, attempts
        assert progress.open_phase in progress.phases


def test_a_job_still_waiting_for_a_controller_has_one_phase():
    progress = launch_phases.compute_job_progress(_task(submitted_at=None), [],
                                                  120.0)

    assert progress.open_phase == launch_phases.CONTROLLER_QUEUE
    assert progress.phases == {launch_phases.CONTROLLER_QUEUE: 120.0}


def test_a_job_sitting_in_a_scheduler_queue_says_so_while_it_is_stuck():
    """The reason for computing this live at all: the stored breakdown cannot
    exist yet, and 'waiting for quota, 40 minutes so far' is the answer."""
    attempts = [_attempt(instances_requested=30.0, queue='eng-lq')]

    progress = launch_phases.compute_job_progress(_task(), attempts, 2430.0)

    assert progress.open_phase == launch_phases.QUEUE_WAIT
    assert progress.phases[launch_phases.QUEUE_WAIT] == 2400.0
    assert progress.phases[launch_phases.CONTROLLER_QUEUE] == 10.0
    assert progress.phases[launch_phases.PROVISION_SETUP] == 20.0


def test_nothing_gating_the_launch_means_the_time_is_scale_up():
    """A queue name is written when the workload is submitted to a scheduler,
    not when it is admitted, so its absence really does mean no scheduler --
    and calling that wait queue_wait would send someone to look at a quota
    that is not involved."""
    attempts = [_attempt(instances_requested=30.0)]

    progress = launch_phases.compute_job_progress(_task(), attempts, 300.0)

    assert progress.open_phase == launch_phases.NODE_STARTUP
    assert launch_phases.QUEUE_WAIT not in progress.phases


def test_the_wait_between_attempts_is_retry_overhead_not_provisioning():
    """A job whose launch failed is in backoff with no attempt open. Charging
    that to the phase the dead attempt died in would blame provisioning for
    time nothing is provisioning in."""
    attempts = [_attempt(provision_start=20.0, outcome='failed')]

    progress = launch_phases.compute_job_progress(_task(), attempts, 500.0)

    assert progress.open_phase == launch_phases.RETRY_OVERHEAD
    assert progress.phases[launch_phases.RETRY_OVERHEAD] == 480.0
    assert progress.phases[launch_phases.PROVISION_SETUP] == 10.0


def test_an_earlier_attempt_that_was_thrown_away_is_not_the_current_one():
    """With a retry in flight, the open phase belongs to the newest attempt;
    reading the oldest would report a milestone the job has moved past."""
    attempts = [
        _attempt(provision_start=20.0,
                 instances_requested=30.0,
                 outcome='failed'),
        _attempt(provision_start=200.0,
                 instances_requested=210.0,
                 queue='eng-lq'),
    ]

    progress = launch_phases.compute_job_progress(_task(), attempts, 900.0)

    assert progress.open_phase == launch_phases.QUEUE_WAIT
    assert progress.phases[launch_phases.RETRY_OVERHEAD] == 180.0
    # 210 - 10 submitted - 180 retried.
    assert progress.phases[launch_phases.PROVISION_SETUP] == 20.0
    assert progress.phases[launch_phases.QUEUE_WAIT] == 690.0


def test_instances_up_but_not_running_yet_is_the_job_setting_itself_up():
    attempts = [
        _attempt(instances_requested=30.0,
                 admitted=630.0,
                 instances_ready=930.0,
                 outcome='succeeded',
                 queue='eng-lq')
    ]

    progress = launch_phases.compute_job_progress(_task(), attempts, 1000.0)

    assert progress.open_phase == launch_phases.RUNTIME_SETUP
    assert progress.phases[launch_phases.RUNTIME_SETUP] == 70.0
    assert progress.phases[launch_phases.NODE_STARTUP] == 300.0


def test_a_job_that_has_started_is_left_to_the_settled_breakdown():
    """The daemon writes the t_* columns up to a minute after start_at. In
    that window a live number would keep growing past the wait it measures."""
    assert launch_phases.compute_job_progress(_task(start_at=1000.0),
                                              [_attempt()], 1200.0) is None


def test_a_job_with_no_origin_gets_no_breakdown_rather_than_a_guess():
    assert launch_phases.compute_job_progress(_task(eligible_at=None), [],
                                              100.0) is None


def test_milestones_that_do_not_fit_the_elapsed_time_draw_nothing():
    """Clocks can disagree: the timestamps come from the controller and the
    attempts from this server. A bar longer than its own total would have to
    misattribute a phase, so nothing is shown instead."""
    attempts = [_attempt(instances_requested=30.0, admitted=630.0)]

    assert launch_phases.compute_job_progress(_task(), attempts, 100.0) is None


def test_no_phase_is_ever_negative():
    """Rendered as widths, so a negative one is not a wrong number on a page
    that otherwise works -- it breaks the bar."""
    attempts = [
        # instances_requested before the controller claimed the job.
        _attempt(provision_start=2.0, instances_requested=4.0, queue='eng-lq')
    ]

    progress = launch_phases.compute_job_progress(_task(), attempts, 600.0)

    assert all(value >= 0 for value in progress.phases.values())
    assert abs(sum(progress.phases.values()) - progress.total) < 1e-9
