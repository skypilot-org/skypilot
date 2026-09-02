"""Splitting a recorded launch attempt into phase durations."""
import types

from sky import global_user_state
from sky.metrics import launch_phases


def _row(**kw):
    """A launch_attempts row, defaulting every milestone to unset."""
    fields = {
        'provision_start': 100.0,
        'instances_requested': None,
        'admitted': None,
        'instances_ready': None,
        'outcome': 'succeeded',
        'workspace': 'eng',
    }
    fields.update(kw)
    return types.SimpleNamespace(**fields)


def _phases(row):
    samples, dropped = launch_phases.compute_phases(row)
    return ({s.phase: s.duration for s in samples},
            {d.phase: d.reason for d in dropped})


def test_outcome_strings_match_global_user_state():
    """The literals here must track the enum they stand in for.

    They are spelled out to avoid an import cycle, so nothing but this test
    stops them drifting apart.
    """
    assert (launch_phases._OUTCOME_SUCCEEDED ==
            global_user_state.LaunchOutcome.SUCCEEDED.value)
    assert (launch_phases._OUTCOME_ABANDONED ==
            global_user_state.LaunchOutcome.ABANDONED.value)


def test_gated_launch_splits_queue_wait_from_node_startup():
    """The whole point: the quota wait is separated from the node coming up.

    Merged, a three-hour queue wait and a three-hour node startup look
    identical, and they are owned by completely different people.
    """
    samples, _ = _phases(
        _row(instances_requested=110.0, admitted=3710.0,
             instances_ready=3760.0))

    assert samples == {
        launch_phases.PROVISION_SETUP: 10.0,
        launch_phases.QUEUE_WAIT: 3600.0,
        launch_phases.NODE_STARTUP: 50.0,
    }


def test_phases_sum_to_the_whole_attempt():
    """A stacked panel of the phases must add up, with nothing double counted.

    node_startup measuring from provision_start rather than from admission
    would overlap the other two and inflate the total.
    """
    row = _row(instances_requested=110.0,
               admitted=3710.0,
               instances_ready=3760.0)
    samples, _ = _phases(row)

    assert sum(samples.values()) == row.instances_ready - row.provision_start


def test_ungated_launch_reports_no_queue_wait_at_all():
    """Absent, not zero.

    Zeros from every launch that never queued would drag the queue-wait
    distribution down and hide the tenants who are actually waiting.
    """
    samples, _ = _phases(_row(instances_requested=110.0, instances_ready=160.0))

    assert launch_phases.QUEUE_WAIT not in samples
    assert samples[launch_phases.NODE_STARTUP] == 50.0


def test_cloud_without_a_request_milestone_reports_one_startup_phase():
    """VM clouds stamp no 'instances requested' step.

    Their provisioning still has to be attributed somewhere, or the launch
    would vanish from the metrics entirely.
    """
    samples, _ = _phases(_row(instances_ready=400.0))

    assert samples == {launch_phases.NODE_STARTUP: 300.0}


def test_failed_attempt_contributes_only_the_phases_it_finished():
    """The phase it died in never happened; it was not lost.

    Recording the time up to the failure as if it were a completed startup
    would bias the distribution towards looking fast.
    """
    samples, dropped = _phases(
        _row(instances_requested=110.0, admitted=200.0, outcome='failed'))

    assert samples == {
        launch_phases.PROVISION_SETUP: 10.0,
        launch_phases.QUEUE_WAIT: 90.0,
    }
    assert launch_phases.NODE_STARTUP not in samples
    assert not dropped, 'a failure is not a lost measurement'


def test_abandoned_attempt_reports_the_measurement_it_lost():
    """A crashed writer is the one case where a phase really was lost.

    Counting it keeps the gap visible; staying silent makes a lost phase look
    the same as a fast one.
    """
    samples, dropped = _phases(
        _row(instances_requested=110.0, outcome='abandoned'))

    assert samples == {launch_phases.PROVISION_SETUP: 10.0}
    assert dropped == {
        launch_phases.QUEUE_WAIT: 'abandoned',
        launch_phases.NODE_STARTUP: 'abandoned',
    }


def test_superseded_attempts_are_labelled_apart_from_the_final_one(monkeypatch):
    """Doomed attempts must be separable from the one that delivered.

    Mixing them into one quantile answers neither question.
    """
    observed = []
    monkeypatch.setattr(launch_phases.metrics_utils, 'observe_launch_phase',
                        lambda p, a, w, d: observed.append((p, a, w)))
    monkeypatch.setattr(launch_phases.metrics_utils,
                        'count_launch_phase_dropped', lambda *a: None)

    launch_phases.observe_attempt(
        _row(instances_requested=110.0, instances_ready=160.0,
             outcome='failed'))
    launch_phases.observe_attempt(
        _row(instances_requested=110.0,
             instances_ready=160.0,
             outcome='succeeded'))

    assert {a for _, a, _ in observed
           } == {launch_phases.ATTEMPT_SUPERSEDED, launch_phases.ATTEMPT_FINAL}


def test_missing_workspace_becomes_a_label_value(monkeypatch):
    """Prometheus labels cannot be None.

    An unresolved workspace must not take the whole observation down with it.
    """
    observed = []
    monkeypatch.setattr(launch_phases.metrics_utils, 'observe_launch_phase',
                        lambda p, a, w, d: observed.append(w))
    launch_phases.observe_attempt(_row(instances_ready=200.0, workspace=None))

    assert observed == ['unknown']
