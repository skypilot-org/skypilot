"""Coverage for `sky.jobs.scheduler.submit_jobs` exit-code semantics.

`submit_jobs` does two things in sequence:

1. `scheduler_set_waiting` — DB write that transitions the row to WAITING
   and stamps the dag/env/config content. If this succeeds, the
   periodic managed-job-status-refresh-daemon will see the WAITING row
   and spawn a controller within ~one event cycle.
2. `maybe_start_controllers(from_scheduler=True)` — eager optimization
   that spawns controllers in the same process so the new job picks up
   quickly instead of waiting for the next recovery pass.

A failure in step 2 is recoverable: the next recovery pass will retry
the spawn for any WAITING+no-controller_pid row. Therefore step 2
failures must NOT propagate up to the caller (`_consolidated_launch`),
which now uses the script's exit code to decide whether the user's
`jobs.launch` request is SUCCEEDED or FAILED. Letting the step-2 error
propagate would tell the user the launch failed while the job goes on
to run anyway — leading to user-visible "FAILED request, job runs"
inversion and prompting a retry that double-launches.
"""

from unittest import mock

import pytest

from sky.jobs import scheduler


@pytest.fixture
def _mock_submit_jobs_io(tmp_path, monkeypatch):
    """Stub out filesystem reads + DB writes that `submit_jobs` does
    before reaching `maybe_start_controllers`."""
    dag_yaml = tmp_path / 'dag.yaml'
    user_yaml = tmp_path / 'user.yaml'
    env_file = tmp_path / 'env'
    for p in (dag_yaml, user_yaml, env_file):
        p.write_text(f'# stub content for {p.name}')

    # No controller process running for any job — submit_jobs's
    # pre-filter at the top should be a no-op.
    monkeypatch.setattr(
        'sky.jobs.state.get_job_controller_process',
        lambda _job_id: None,
    )

    scheduler_set_waiting_mock = mock.MagicMock()
    monkeypatch.setattr(
        'sky.jobs.state.scheduler_set_waiting',
        scheduler_set_waiting_mock,
    )

    return {
        'dag_yaml': str(dag_yaml),
        'user_yaml': str(user_yaml),
        'env_file': str(env_file),
        'scheduler_set_waiting': scheduler_set_waiting_mock,
    }


def test_maybe_start_controllers_failure_is_swallowed(monkeypatch,
                                                     _mock_submit_jobs_io):
    """If `maybe_start_controllers` raises AFTER `scheduler_set_waiting`
    has committed, `submit_jobs` must NOT propagate the exception. The
    row is already WAITING in the DB and the periodic recovery daemon
    will retry the controller spawn."""
    monkeypatch.setattr(
        'sky.jobs.scheduler.maybe_start_controllers',
        mock.MagicMock(side_effect=RuntimeError('simulated spawn failure')),
    )

    # Must not raise. The script will exit 0, the user's `jobs.launch`
    # request will be marked SUCCEEDED, and the job will run via the
    # recovery-driven retry.
    scheduler.submit_jobs(
        job_ids=[42],
        dag_yaml_path=_mock_submit_jobs_io['dag_yaml'],
        original_user_yaml_path=_mock_submit_jobs_io['user_yaml'],
        env_file_path=_mock_submit_jobs_io['env_file'],
        priority=500,
        priority_class=None,
    )

    # scheduler_set_waiting was called exactly once (with the job_id).
    _mock_submit_jobs_io['scheduler_set_waiting'].assert_called_once()
    call_job_ids = _mock_submit_jobs_io['scheduler_set_waiting'].call_args[0][0]
    assert call_job_ids == [42]


def test_maybe_start_controllers_success_returns_normally(
        monkeypatch, _mock_submit_jobs_io):
    """Happy path: both calls succeed, submit_jobs returns without raising."""
    msc_mock = mock.MagicMock()
    monkeypatch.setattr('sky.jobs.scheduler.maybe_start_controllers', msc_mock)

    scheduler.submit_jobs(
        job_ids=[1],
        dag_yaml_path=_mock_submit_jobs_io['dag_yaml'],
        original_user_yaml_path=_mock_submit_jobs_io['user_yaml'],
        env_file_path=_mock_submit_jobs_io['env_file'],
        priority=500,
        priority_class=None,
    )

    msc_mock.assert_called_once_with(from_scheduler=True)
    _mock_submit_jobs_io['scheduler_set_waiting'].assert_called_once()


def test_scheduler_set_waiting_failure_does_propagate(monkeypatch,
                                                     _mock_submit_jobs_io):
    """`scheduler_set_waiting` failures *must* propagate. If the DB
    write fails, the row was never transitioned to WAITING — recovery
    cannot resurrect this job, so the user must be told the launch
    failed."""
    _mock_submit_jobs_io['scheduler_set_waiting'].side_effect = RuntimeError(
        'simulated DB write failure')

    # maybe_start_controllers shouldn't even be reached.
    msc_mock = mock.MagicMock()
    monkeypatch.setattr('sky.jobs.scheduler.maybe_start_controllers', msc_mock)

    with pytest.raises(RuntimeError, match='simulated DB write failure'):
        scheduler.submit_jobs(
            job_ids=[1],
            dag_yaml_path=_mock_submit_jobs_io['dag_yaml'],
            original_user_yaml_path=_mock_submit_jobs_io['user_yaml'],
            env_file_path=_mock_submit_jobs_io['env_file'],
            priority=500,
            priority_class=None,
        )

    msc_mock.assert_not_called()
