"""Attaching the live startup breakdown to a job that has not started yet.

The breakdown is computed on the API server from its own launch_attempts, so
the seam is here rather than on the controller: what gets asked for, what gets
skipped, and what a failure costs.
"""
import inspect
import types

import pytest

from sky.jobs import state as managed_job_state
from sky.jobs.server import core
from sky.metrics import launch_phases
from sky.server.requests import payloads


def _attempt(instances_requested=30.0, admitted=None, queue='eng-lq'):
    return types.SimpleNamespace(provision_start=20.0,
                                 instances_requested=instances_requested,
                                 admitted=admitted,
                                 instances_ready=None,
                                 outcome=None,
                                 queue=queue)


def _job(**overrides):
    job = {
        'job_id': 7,
        'task_id': 0,
        'task_name': 'train',
        'eligible_at': 0.0,
        'submitted_at': 10.0,
        'start_at': None,
        'end_at': None,
        'pool': None,
        'status': managed_job_state.ManagedJobStatus.STARTING,
    }
    job.update(overrides)
    return job


@pytest.fixture(autouse=True)
def _consolidation_mode(monkeypatch):
    """Every case below assumes the attempts are readable here, which is only
    true in consolidation mode; `test_without_consolidation_mode...` turns it
    off explicitly."""
    monkeypatch.setattr(core.managed_job_utils, 'is_consolidation_mode',
                        lambda: True)


@pytest.fixture(name='one_attempt')
def _one_attempt(monkeypatch):
    """Every job looks up the same single queued attempt."""
    asked = []

    def fake(cluster_name):
        asked.append(cluster_name)
        return [_attempt()]

    monkeypatch.setattr(core.global_user_state,
                        'get_launch_attempts_for_cluster', fake)
    return asked


def test_a_job_still_starting_gets_its_breakdown(one_attempt):
    jobs = [_job()]

    core._attach_startup_progress(jobs)

    progress = jobs[0]['startup_progress']
    assert progress['open_phase'] == launch_phases.QUEUE_WAIT
    assert progress['total'] > 0
    assert progress['phases'][launch_phases.CONTROLLER_QUEUE] == 10.0
    # The cluster is named from the task, not the job: a pipeline's tasks share
    # a job name and each launches its own cluster.
    assert one_attempt == ['train-7']


def test_a_job_that_has_started_is_left_alone(one_attempt):
    jobs = [_job(start_at=1000.0)]

    core._attach_startup_progress(jobs)

    assert 'startup_progress' not in jobs[0]
    # Not merely absent from the output -- the read must not happen either.
    assert one_attempt == []


def test_a_pool_job_is_not_given_another_jobs_attempts(one_attempt):
    """A pool cluster is shared, so its launch attempts belong to whichever
    job provisioned it. Charging them here would show a job that skipped
    provisioning as having provisioned."""
    jobs = [_job(pool='e2epool')]

    core._attach_startup_progress(jobs)

    assert 'startup_progress' not in jobs[0]
    assert one_attempt == []


def test_without_consolidation_mode_nothing_is_read_or_reported(
        monkeypatch, one_attempt):
    """The milestones are written by the provisioner, which for a managed job
    runs on the controller -- the same database only in consolidation mode.

    Elsewhere the lookup finds nothing, and with no attempts the split has only
    the controller wait to name: everything after it lands in the phase that
    follows. A job parked in a scheduler queue for an hour would read
    "Preparing the launch, 59m so far", which is the misdiagnosis the whole
    breakdown exists to prevent. Showing nothing is the honest degradation, and
    the one the settled path already makes."""
    monkeypatch.setattr(core.managed_job_utils, 'is_consolidation_mode',
                        lambda: False)
    jobs = [_job()]

    core._attach_startup_progress(jobs)

    assert 'startup_progress' not in jobs[0]
    assert one_attempt == []


def test_an_empty_attempt_list_is_not_the_discriminator(one_attempt,
                                                        monkeypatch):
    """In consolidation mode no attempts yet is the honest early state of a
    job that has been claimed but has not begun provisioning, so it must still
    report -- which is why the mode, not the rows, decides."""
    monkeypatch.setattr(core.global_user_state,
                        'get_launch_attempts_for_cluster', lambda name: [])
    jobs = [_job()]

    core._attach_startup_progress(jobs)

    assert jobs[0]['startup_progress']['open_phase'] == (
        launch_phases.PROVISION_SETUP)


@pytest.mark.parametrize('status', [
    managed_job_state.ManagedJobStatus.CANCELLED,
    managed_job_state.ManagedJobStatus.FAILED_NO_RESOURCE,
    managed_job_state.ManagedJobStatus.FAILED_PRECHECKS,
])
def test_a_terminal_job_is_not_still_starting(one_attempt, status):
    """`set_pending_cancelled` updates the status alone, so a job cancelled
    while PENDING has neither start_at nor end_at -- the two timestamp guards
    both miss it, and it reports "Starting, 1d so far" a day later, growing on
    every page open. The tenant query behind this feature's design found nine
    rows of exactly that shape."""
    jobs = [_job(status=status, start_at=None, end_at=None)]

    core._attach_startup_progress(jobs)

    assert 'startup_progress' not in jobs[0]
    assert one_attempt == []


def test_the_status_may_arrive_as_the_raw_column(one_attempt):
    """The gRPC path decodes the proto into a ManagedJobStatus; the
    consolidation path hands back the string the database holds. Reading only
    one of them would let the other through unchecked."""
    jobs = [
        _job(status=managed_job_state.ManagedJobStatus.CANCELLED.value),
        _job(job_id=8,
             status=managed_job_state.ManagedJobStatus.STARTING.value),
    ]

    core._attach_startup_progress(jobs)

    assert 'startup_progress' not in jobs[0]
    assert 'startup_progress' in jobs[1]


def test_an_unreadable_status_is_treated_as_terminal(one_attempt):
    """A number that grows forever is worse than a missing panel."""
    jobs = [_job(status='not-a-status')]

    core._attach_startup_progress(jobs)

    assert 'startup_progress' not in jobs[0]


def test_a_job_with_no_origin_is_skipped_without_a_lookup(one_attempt):
    jobs = [_job(eligible_at=None)]

    core._attach_startup_progress(jobs)

    assert 'startup_progress' not in jobs[0]
    assert one_attempt == []


def test_a_failed_read_costs_the_breakdown_not_the_queue(monkeypatch):
    """This decorates a response the caller actually asked for. Letting a
    database error out of here would turn a working `sky jobs queue` into a
    failure over a panel."""

    def boom(cluster_name):
        raise RuntimeError('database is gone')

    monkeypatch.setattr(core.global_user_state,
                        'get_launch_attempts_for_cluster', boom)
    jobs = [_job(), _job(job_id=8)]

    core._attach_startup_progress(jobs)

    assert all('startup_progress' not in job for job in jobs)


def test_one_bad_job_does_not_cost_the_others_theirs(monkeypatch):

    def half_broken(cluster_name):
        if cluster_name == 'train-7':
            raise RuntimeError('database is gone')
        return [_attempt()]

    monkeypatch.setattr(core.global_user_state,
                        'get_launch_attempts_for_cluster', half_broken)
    jobs = [_job(), _job(job_id=8)]

    core._attach_startup_progress(jobs)

    assert 'startup_progress' not in jobs[0]
    assert 'startup_progress' in jobs[1]


def _queue_returning(jobs):
    return lambda **kwargs: (jobs, len(jobs), {}, len(jobs), [])


def test_a_caller_that_did_not_ask_pays_nothing(monkeypatch):
    """Off by default, including for a caller that named its jobs -- the
    jobs list's own id filter does that, and it draws no panel."""
    called = []
    monkeypatch.setattr(core, 'queue_v2', _queue_returning([_job()]))
    monkeypatch.setattr(core, '_attach_startup_progress',
                        lambda jobs: called.append(jobs))

    core.queue_v2_api(refresh=False, job_ids=[7])

    assert called == []


def test_asking_for_it_is_what_computes_it(monkeypatch):
    called = []
    monkeypatch.setattr(core, 'queue_v2', _queue_returning([_job()]))
    monkeypatch.setattr(core, '_attach_startup_progress',
                        lambda jobs: called.append(jobs))

    core.queue_v2_api(refresh=False, include_startup_progress=True)

    assert len(called) == 1


def test_the_request_field_reaches_the_function_that_reads_it():
    """The executor calls the entrypoint with `**body.to_kwargs()`, so a field
    declared on the body but absent from the signature is a TypeError on every
    request -- and one in the signature that no body declares can never be
    turned on."""
    body = payloads.JobsQueueV2Body(refresh=False,
                                    include_startup_progress=True)

    kwargs = body.to_kwargs()

    assert kwargs['include_startup_progress'] is True
    signature = inspect.signature(core.queue_v2_api)
    assert set(kwargs) <= set(signature.parameters)


def test_a_client_that_does_not_know_the_field_gets_the_old_behaviour():
    """Old client, new server: the field is simply absent from the body."""
    body = payloads.JobsQueueV2Body(refresh=False)

    assert body.to_kwargs()['include_startup_progress'] is False


def test_the_breakdown_survives_a_caller_that_named_its_fields(monkeypatch):
    """`fields` drops every key the caller did not ask for, and this key is
    computed rather than selected -- so it is not in any caller's list and
    would be filtered out of the one response that wanted it."""
    monkeypatch.setattr(core, 'queue_v2', _queue_returning([_job()]))
    monkeypatch.setattr(core.global_user_state,
                        'get_launch_attempts_for_cluster',
                        lambda cluster_name: [_attempt()])

    records, *_ = core.queue_v2_api(refresh=False,
                                    include_startup_progress=True,
                                    fields=['job_id', 'status'])

    assert records[0].startup_progress is not None
