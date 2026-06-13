"""Tests for managed-job claim identity (claim_id) and ownership fencing."""
import contextlib
import time

import pytest
import sqlalchemy

from sky import exceptions
from sky.jobs import fencing
from sky.jobs import state as managed_job_state

ManagedJobStatus = managed_job_state.ManagedJobStatus
ManagedJobScheduleState = managed_job_state.ManagedJobScheduleState


async def _noop_callback(status: str) -> None:
    del status


@contextlib.contextmanager
def _capture_sql(engine):
    """Capture every SQL statement executed on the jobs DB (sync + async)."""
    statements = []

    def _on_execute(conn, cursor, statement, parameters, context, executemany):
        del conn, cursor, parameters, context, executemany
        statements.append(statement)

    targets = [engine]
    async_engine = managed_job_state._db_manager._engine_async  # pylint: disable=protected-access
    if async_engine is not None:
        targets.append(async_engine.sync_engine)
    for target in targets:
        sqlalchemy.event.listen(target, 'before_cursor_execute', _on_execute)
    try:
        yield statements
    finally:
        for target in targets:
            sqlalchemy.event.remove(target, 'before_cursor_execute',
                                    _on_execute)


async def _claim(harness, job_id, pid=1000):
    claimed = await managed_job_state.get_waiting_job_async(
        pid=pid, pid_started_at=float(pid))
    assert claimed is not None and claimed['job_id'] == job_id
    return fencing.FencingToken(job_id=job_id, claim_id=claimed['claim_id'])


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


@pytest.mark.asyncio
async def test_unfenced_writes_are_inert(jobs_db, controller_harness):
    """With no fence passed, no write site's SQL references claim_id.

    This is the safety argument for shipping the fence machinery before any
    caller passes a token: fence=None must produce exactly today's
    statements. Any claim_id reference in unfenced SQL is a regression.
    """
    harness = controller_harness
    job_id = harness.submit_job(name='inert-test')
    await _claim(harness, job_id)

    async def _drive_all_unfenced_writers():
        # Every fenced-capable write site, called WITHOUT a fence, covering
        # both valid transitions and 0-row outcomes (some raise on 0 rows --
        # irrelevant here, only the emitted SQL matters).
        writers = [
            managed_job_state.scheduler_set_launching_async(job_id),
            managed_job_state.set_starting_async(job_id, 0, 'sky-run-ts',
                                                 time.time(), '-', {},
                                                 _noop_callback),
            managed_job_state.set_started_async(job_id, 0, time.time(),
                                                _noop_callback),
            managed_job_state.scheduler_set_alive_async(job_id),
            managed_job_state.set_backoff_pending_async(job_id, 0),
            managed_job_state.set_restarting_async(job_id, 0, False),
            managed_job_state.set_recovering_async(job_id, 0, False,
                                                   _noop_callback),
            managed_job_state.set_recovered_async(job_id, 0, time.time(),
                                                  _noop_callback),
            managed_job_state.set_succeeded_async(job_id, 0, time.time(),
                                                  _noop_callback),
            managed_job_state.set_failed_async(
                job_id, 0, ManagedJobStatus.FAILED_CONTROLLER, 'test'),
            managed_job_state.set_cancelling_async(job_id, _noop_callback),
            managed_job_state.set_cancelled_async(job_id, _noop_callback),
            managed_job_state.set_job_id_on_pool_cluster_async(job_id, 1),
            managed_job_state.scheduler_set_done_async(job_id, idempotent=True),
        ]
        for coro in writers:
            try:
                await coro
            except exceptions.ManagedJobStatusError:
                pass  # 0-row strictness is fine; we only inspect the SQL.
        for sync_call in [
                lambda: managed_job_state.save_batch_states(job_id, [[0, 10]]),
                lambda: managed_job_state.set_batch_status(
                    job_id, 0, 'PENDING'),
                lambda: managed_job_state.reset_dispatched_batches(job_id),
                lambda: managed_job_state.set_winding_down(job_id, 0),
                lambda: managed_job_state.scheduler_set_done(job_id,
                                                             idempotent=True),
        ]:
            sync_call()

    with _capture_sql(jobs_db) as statements:
        await _drive_all_unfenced_writers()

    offending = [s for s in statements if 'claim_id' in s]
    assert not offending, offending


@pytest.mark.asyncio
async def test_fenced_write_raises_on_lost_claim(jobs_db, controller_harness):
    """A fenced write after a reset (or re-claim) raises and does not land."""
    del jobs_db
    harness = controller_harness
    job_id = harness.submit_job(name='lost-claim')
    token = await _claim(harness, job_id)
    await managed_job_state.set_starting_async(job_id,
                                               0,
                                               'sky-run-ts',
                                               time.time(),
                                               '-', {},
                                               _noop_callback,
                                               fence=token)

    # Reset: claim cleared -> ownership lost.
    managed_job_state.reset_job_for_recovery(job_id)
    with pytest.raises(exceptions.JobOwnershipLostError):
        await managed_job_state.set_started_async(job_id,
                                                  0,
                                                  time.time(),
                                                  _noop_callback,
                                                  fence=token)
    assert token.lost
    # The write did not land.
    assert (managed_job_state.get_job_status_with_task_id(
        job_id, 0) == ManagedJobStatus.STARTING)

    # Re-claim by another controller: the old token is still fenced out.
    new_token = await _claim(harness, job_id, pid=2000)
    stale_again = fencing.FencingToken(job_id=job_id, claim_id=token.claim_id)
    with pytest.raises(exceptions.JobOwnershipLostError):
        await managed_job_state.set_cancelling_async(job_id,
                                                     _noop_callback,
                                                     fence=stale_again)
    assert stale_again.lost
    # ...while the current claimant's writes land (the task row is already
    # STARTING from before the reset; the re-claimant resumes from there).
    await managed_job_state.set_started_async(job_id,
                                              0,
                                              time.time(),
                                              _noop_callback,
                                              fence=new_token)
    assert (managed_job_state.get_job_status_with_task_id(
        job_id, 0) == ManagedJobStatus.RUNNING)
    assert not new_token.lost


@pytest.mark.asyncio
async def test_fenced_zero_row_with_claim_held_keeps_behavior(
        jobs_db, controller_harness):
    """Claim-held 0-row outcomes keep each function's existing semantics."""
    del jobs_db
    harness = controller_harness
    job_id = harness.submit_job(name='held-claim')
    token = await _claim(harness, job_id)

    # Strict site: invalid transition (PENDING -> RUNNING needs
    # STARTING/PENDING; drive to SUCCEEDED first to make it 0-row) still
    # raises ManagedJobStatusError, NOT JobOwnershipLostError.
    await managed_job_state.set_starting_async(job_id,
                                               0,
                                               'sky-run-ts',
                                               time.time(),
                                               '-', {},
                                               _noop_callback,
                                               fence=token)
    await managed_job_state.set_started_async(job_id,
                                              0,
                                              time.time(),
                                              _noop_callback,
                                              fence=token)
    await managed_job_state.set_succeeded_async(job_id,
                                                0,
                                                time.time(),
                                                _noop_callback,
                                                fence=token)
    with pytest.raises(exceptions.ManagedJobStatusError):
        await managed_job_state.set_started_async(job_id,
                                                  0,
                                                  time.time(),
                                                  _noop_callback,
                                                  fence=token)
    assert not token.lost

    # Tolerant sites: terminal job -> skipped without raising.
    await managed_job_state.set_cancelling_async(job_id,
                                                 _noop_callback,
                                                 fence=token)
    await managed_job_state.set_cancelled_async(job_id,
                                                _noop_callback,
                                                fence=token)
    await managed_job_state.set_failed_async(job_id,
                                             0,
                                             ManagedJobStatus.FAILED,
                                             'should not land',
                                             fence=token)
    assert not token.lost
    assert (managed_job_state.get_job_status_with_task_id(
        job_id, 0) == ManagedJobStatus.SUCCEEDED)


@pytest.mark.asyncio
async def test_job_done_skips_on_lost_claim(jobs_db, controller_harness):
    """scheduler_set_done* has skip semantics: log + mark lost, no raise."""
    del jobs_db
    harness = controller_harness
    job_id = harness.submit_job(name='done-skip')
    token = await _claim(harness, job_id)
    managed_job_state.reset_job_for_recovery(job_id)

    await managed_job_state.scheduler_set_done_async(job_id,
                                                     idempotent=True,
                                                     fence=token)
    assert token.lost
    assert (managed_job_state.get_job_schedule_state(job_id) ==
            ManagedJobScheduleState.WAITING)

    token_sync = fencing.FencingToken(job_id=job_id, claim_id=token.claim_id)
    managed_job_state.scheduler_set_done(job_id,
                                         idempotent=False,
                                         fence=token_sync)
    assert token_sync.lost
    assert (managed_job_state.get_job_schedule_state(job_id) ==
            ManagedJobScheduleState.WAITING)


@pytest.mark.asyncio
async def test_sync_batch_writers_fenced(jobs_db, controller_harness):
    """The sync (worker-thread) write sites honor the fence too."""
    del jobs_db
    harness = controller_harness
    job_id = harness.submit_job(name='batch-fence')
    token = await _claim(harness, job_id)

    managed_job_state.save_batch_states(job_id, [[0, 10], [10, 20]],
                                        fence=token)
    managed_job_state.set_batch_status(job_id,
                                       0,
                                       'DISPATCHED',
                                       worker_cluster='w1',
                                       fence=token)

    managed_job_state.reset_job_for_recovery(job_id)
    with pytest.raises(exceptions.JobOwnershipLostError):
        managed_job_state.set_batch_status(job_id, 1, 'DISPATCHED', fence=token)
    assert token.lost
    batches = {
        b['batch_idx']: b['status']
        for b in managed_job_state.get_batch_states(job_id)
    }
    assert batches == {0: 'DISPATCHED', 1: 'PENDING'}

    with pytest.raises(exceptions.JobOwnershipLostError):
        managed_job_state.reset_dispatched_batches(job_id, fence=token)
    with pytest.raises(exceptions.JobOwnershipLostError):
        managed_job_state.save_batch_states(job_id, [[20, 30]], fence=token)

    # set_winding_down has skip semantics: marks lost, no raise.
    token2 = fencing.FencingToken(job_id=job_id, claim_id=token.claim_id)
    managed_job_state.set_winding_down(job_id, 0, fence=token2)
    assert token2.lost
