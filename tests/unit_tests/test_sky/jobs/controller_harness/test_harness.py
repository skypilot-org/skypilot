"""Smoke scenarios proving the controller harness drives real code paths.

These intentionally cover the trivial happy paths -- claim, run-to-success,
preemption recovery, and the claim/reset/re-claim DB machinery -- to pin the
harness itself. Failure-injection and ownership scenarios build on top of
this in follow-up changes.
"""
import pytest

from sky.jobs import state as managed_job_state
from sky.skylet import job_lib

ManagedJobStatus = managed_job_state.ManagedJobStatus
ManagedJobScheduleState = managed_job_state.ManagedJobScheduleState


@pytest.mark.asyncio
async def test_claim_reset_reclaim(jobs_db, controller_harness):
    """The claim transaction and the recovery reset, at the DB level.

    No controller loops run here: the test calls get_waiting_job_async (the
    controller's claim step) and reset_job_for_recovery (the HA-recovery
    reset) directly to pin their semantics.
    """
    del jobs_db
    harness = controller_harness
    job_id = harness.submit_job(name='claim-test')

    # Claim: WAITING -> LAUNCHING with the claimer's pid stamped.
    claimed = await managed_job_state.get_waiting_job_async(
        pid=1234, pid_started_at=1000.0)
    assert claimed is not None
    assert claimed['job_id'] == job_id
    assert (managed_job_state.get_job_schedule_state(job_id) ==
            ManagedJobScheduleState.LAUNCHING)
    record = managed_job_state.get_job_controller_process(job_id)
    assert record is not None and record.pid == 1234

    # No second claim: the job is no longer WAITING.
    assert await managed_job_state.get_waiting_job_async(
        pid=5678, pid_started_at=2000.0) is None

    # Reset for recovery: back to WAITING, ownership columns cleared.
    managed_job_state.reset_job_for_recovery(job_id)
    assert (managed_job_state.get_job_schedule_state(job_id) ==
            ManagedJobScheduleState.WAITING)
    assert managed_job_state.get_job_controller_process(job_id) is None

    # Re-claim by a different controller succeeds and restamps.
    reclaimed = await managed_job_state.get_waiting_job_async(
        pid=5678, pid_started_at=2000.0)
    assert reclaimed is not None and reclaimed['job_id'] == job_id
    record = managed_job_state.get_job_controller_process(job_id)
    assert record is not None and record.pid == 5678


@pytest.mark.asyncio
async def test_job_runs_to_success(jobs_db, fake_cloud, controller_harness):
    """Full lifecycle: claim -> launch -> RUNNING -> SUCCEEDED -> DONE."""
    del jobs_db
    harness = controller_harness
    job_id = harness.submit_job(name='success-test')
    cluster_name = harness.cluster_name_for(job_id, 'success-test')

    await harness.start()

    # The controller claims the job and stamps its own pid.
    await harness.wait_for_schedule_state(job_id, [
        ManagedJobScheduleState.LAUNCHING,
        ManagedJobScheduleState.ALIVE,
    ])
    record = managed_job_state.get_job_controller_process(job_id)
    assert record is not None and record.pid == harness.pid

    # The fake launch brings the job up RUNNING.
    await harness.wait_for_job_status(job_id, [ManagedJobStatus.RUNNING])
    assert fake_cloud.launch_calls == [cluster_name]

    # The job finishes on the cluster; the controller notices, records
    # success, tears the cluster down, and retires the job.
    fake_cloud.finish_job(cluster_name)
    await harness.wait_for_job_status(job_id, [ManagedJobStatus.SUCCEEDED])
    await harness.wait_for_schedule_state(job_id,
                                          [ManagedJobScheduleState.DONE])
    assert cluster_name in fake_cloud.terminate_calls
    # The job loop deregistered itself.
    await harness.wait_for(lambda: job_id not in harness.manager.job_tasks,
                           f'job {job_id} to leave job_tasks')


@pytest.mark.asyncio
async def test_job_recovers_from_preemption(jobs_db, fake_cloud,
                                            controller_harness):
    """Preemption drives RECOVERING and a relaunch, then completes."""
    del jobs_db
    harness = controller_harness
    job_id = harness.submit_job(name='recovery-test')
    cluster_name = harness.cluster_name_for(job_id, 'recovery-test')

    await harness.start()
    await harness.wait_for_job_status(job_id, [ManagedJobStatus.RUNNING])
    assert fake_cloud.launch_calls == [cluster_name]

    # Take the cluster away; the monitor loop should enter recovery and
    # relaunch (same deterministic cluster name).
    fake_cloud.preempt(cluster_name)
    await harness.wait_for(lambda: len(fake_cloud.launch_calls) == 2,
                           'recovery relaunch')
    await harness.wait_for_job_status(job_id, [ManagedJobStatus.RUNNING])
    assert fake_cloud.launch_calls == [cluster_name, cluster_name]
    # The default (EAGER_NEXT_REGION) strategy tears the unhealthy cluster
    # down before relaunching.
    assert cluster_name in fake_cloud.terminate_calls

    jobs, _ = managed_job_state.get_managed_jobs_with_filters(job_ids=[job_id])
    assert jobs[0]['recovery_count'] == 1

    fake_cloud.finish_job(cluster_name)
    await harness.wait_for_job_status(job_id, [ManagedJobStatus.SUCCEEDED])
    await harness.wait_for_schedule_state(job_id,
                                          [ManagedJobScheduleState.DONE])


@pytest.mark.asyncio
async def test_launch_failure_retries(jobs_db, fake_cloud, controller_harness):
    """A failed sky.launch goes through backoff and retries to success."""
    del jobs_db
    harness = controller_harness
    job_id = harness.submit_job(name='retry-test')
    cluster_name = harness.cluster_name_for(job_id, 'retry-test')

    fail_once = [True]

    def _fail_first_launch(name: str) -> None:
        del name
        if fail_once[0]:
            fail_once[0] = False
            raise RuntimeError('fake provision failure')

    fake_cloud.launch_hook = _fail_first_launch

    await harness.start()
    await harness.wait_for_job_status(job_id, [ManagedJobStatus.RUNNING])
    # First attempt failed, second succeeded.
    assert fake_cloud.launch_calls == [cluster_name, cluster_name]

    fake_cloud.finish_job(cluster_name)
    await harness.wait_for_job_status(job_id, [ManagedJobStatus.SUCCEEDED])


@pytest.mark.asyncio
async def test_job_failure_is_recorded(jobs_db, fake_cloud, controller_harness):
    """A user-code failure ends the job FAILED with the cluster cleaned up."""
    del jobs_db
    harness = controller_harness
    job_id = harness.submit_job(name='failure-test')
    cluster_name = harness.cluster_name_for(job_id, 'failure-test')

    await harness.start()
    await harness.wait_for_job_status(job_id, [ManagedJobStatus.RUNNING])

    fake_cloud.finish_job(cluster_name, job_status=job_lib.JobStatus.FAILED)
    await harness.wait_for_job_status(job_id, [ManagedJobStatus.FAILED])
    await harness.wait_for_schedule_state(job_id,
                                          [ManagedJobScheduleState.DONE])
    assert cluster_name in fake_cloud.terminate_calls
