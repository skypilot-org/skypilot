"""Attaching the live startup breakdown to a job that has not started yet.

The breakdown is computed on the API server from its own launch_attempts, so
the seam is here rather than on the controller: what gets asked for, what gets
skipped, and what a failure costs.
"""
import inspect
import types

import pytest

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
        'pool': None,
    }
    job.update(overrides)
    return job


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
