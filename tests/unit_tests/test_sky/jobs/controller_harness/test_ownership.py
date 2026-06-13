"""Tests for managed-job claim identity (claim_id) and ownership fencing."""
import pytest
import sqlalchemy

from sky.jobs import state as managed_job_state

ManagedJobScheduleState = managed_job_state.ManagedJobScheduleState


def _read_ownership_row(engine, job_id):
    """Read (claim_id, controller_pid, schedule_state) straight from the DB."""
    with engine.connect() as conn:
        row = conn.execute(
            sqlalchemy.select(
                managed_job_state.job_info_table.c.claim_id,
                managed_job_state.job_info_table.c.controller_pid,
                managed_job_state.job_info_table.c.schedule_state,
            ).where(managed_job_state.job_info_table.c.spot_job_id ==
                    job_id)).fetchone()
    assert row is not None, job_id
    return row


@pytest.mark.asyncio
async def test_claim_stamps_claim_id(jobs_db, controller_harness):
    """Claiming stamps a fresh claim_id and returns it to the claimer."""
    harness = controller_harness
    job_id_a = harness.submit_job(name='claim-id-a')
    job_id_b = harness.submit_job(name='claim-id-b')

    claimed_a = await managed_job_state.get_waiting_job_async(
        pid=1111, pid_started_at=1.0)
    claimed_b = await managed_job_state.get_waiting_job_async(
        pid=1111, pid_started_at=1.0)
    assert claimed_a is not None and claimed_a['job_id'] == job_id_a
    assert claimed_b is not None and claimed_b['job_id'] == job_id_b

    # The returned claim_id matches the stamped row, and every claim gets a
    # distinct id even from the same claimer.
    claim_id_a, pid_a, state_a = _read_ownership_row(jobs_db, job_id_a)
    assert claimed_a['claim_id'] == claim_id_a
    assert claim_id_a is not None
    assert pid_a == 1111
    assert state_a == ManagedJobScheduleState.LAUNCHING.value
    claim_id_b, _, _ = _read_ownership_row(jobs_db, job_id_b)
    assert claimed_b['claim_id'] == claim_id_b
    assert claim_id_a != claim_id_b


@pytest.mark.asyncio
async def test_resets_clear_claim_id(jobs_db, controller_harness):
    """Both recovery-reset paths clear the claim, and a re-claim is new."""
    harness = controller_harness
    job_id = harness.submit_job(name='reset-claim')

    claimed = await managed_job_state.get_waiting_job_async(pid=2222,
                                                            pid_started_at=2.0)
    assert claimed is not None

    # Single-job reset.
    managed_job_state.reset_job_for_recovery(job_id)
    claim_id, pid, schedule_state = _read_ownership_row(jobs_db, job_id)
    assert claim_id is None
    assert pid is None
    assert schedule_state == ManagedJobScheduleState.WAITING.value

    # Re-claim: a same-pid re-claim still gets a brand new claim_id.
    reclaimed = await managed_job_state.get_waiting_job_async(
        pid=2222, pid_started_at=2.0)
    assert reclaimed is not None
    assert reclaimed['claim_id'] != claimed['claim_id']

    # Bulk reset (the rolling-update / HA recovery path).
    managed_job_state.reset_jobs_for_recovery()
    claim_id, pid, schedule_state = _read_ownership_row(jobs_db, job_id)
    assert claim_id is None
    assert pid is None
    assert schedule_state == ManagedJobScheduleState.WAITING.value


@pytest.mark.asyncio
async def test_scheduler_set_waiting_clears_ownership(jobs_db,
                                                      controller_harness):
    """Re-queueing a claimed job clears the previous claim's ownership.

    scheduler_set_waiting's only caller (scheduler.submit_jobs) skips jobs
    whose controller process is alive, so by the time it runs, any recorded
    ownership is stale and must not survive into the new WAITING state.
    """
    harness = controller_harness
    job_id = harness.submit_job(name='requeue-claim')

    claimed = await managed_job_state.get_waiting_job_async(pid=3333,
                                                            pid_started_at=3.0)
    assert claimed is not None and claimed['job_id'] == job_id

    # Re-queue (submit_job drives scheduler_set_waiting underneath).
    managed_job_state.scheduler_set_waiting([job_id],
                                            dag_yaml_content='unused: yaml',
                                            original_user_yaml_content='',
                                            env_file_content='',
                                            config_file_content=None,
                                            priority=500)
    claim_id, pid, schedule_state = _read_ownership_row(jobs_db, job_id)
    assert claim_id is None
    assert pid is None
    assert schedule_state == ManagedJobScheduleState.WAITING.value
