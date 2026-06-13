"""Integration scenarios for graceful stand-down after ownership loss.

These boot real controllers via the harness and drive the split-brain
failure class end to end: a job is reset out from under a live controller
(as HA recovery would), and we assert the loser stands down without
clobbering state or destroying the winner's resources.
"""
import pytest
import sqlalchemy

from sky.jobs import state as managed_job_state

ManagedJobStatus = managed_job_state.ManagedJobStatus
ManagedJobScheduleState = managed_job_state.ManagedJobScheduleState


def _read_row(engine, job_id):
    """Read (claim_id, controller_pid, schedule_state) from the DB."""
    with engine.connect() as conn:
        return conn.execute(
            sqlalchemy.select(
                managed_job_state.job_info_table.c.claim_id,
                managed_job_state.job_info_table.c.controller_pid,
                managed_job_state.job_info_table.c.schedule_state,
            ).where(managed_job_state.job_info_table.c.spot_job_id ==
                    job_id)).fetchone()


async def _wait_running_owned_by(harness, job_id):
    """Wait until the job is RUNNING and claimed by this harness's pid."""
    await harness.wait_for_job_status(job_id, [ManagedJobStatus.RUNNING])
    record = managed_job_state.get_job_controller_process(job_id)
    assert record is not None and record.pid == harness.pid


@pytest.mark.asyncio
async def test_reset_and_reclaim_loser_stands_down(jobs_db, fake_cloud,
                                                   make_controller_harness):
    """T1 (incident shape): A claims and runs; the job is reset and B
    re-claims; A stands down without tearing down B's cluster or writing
    terminal state, and the job completes under B."""
    del jobs_db
    harness_a = make_controller_harness(pid=10001)
    harness_b = make_controller_harness(pid=10002)
    job_id = harness_a.submit_job(name='split-brain')
    cluster_name = harness_a.cluster_name_for(job_id, 'split-brain')

    await harness_a.start()
    await _wait_running_owned_by(harness_a, job_id)
    assert fake_cloud.launch_calls == [cluster_name]
    terminates_before = list(fake_cloud.terminate_calls)

    # Reset the job out from under A (what HA recovery does), then let B
    # claim and resume it.
    managed_job_state.reset_job_for_recovery(job_id)
    await harness_b.start()

    # B re-claims and drives the job to success.
    await harness_b.wait_for(
        lambda: (managed_job_state.get_job_controller_process(job_id)
                 is not None and
                 managed_job_state.get_job_controller_process(job_id).pid ==
                 harness_b.pid), 'job re-claimed by B')
    await harness_b.wait_for_job_status(job_id, [ManagedJobStatus.RUNNING])

    # A must have stood down: it did not terminate the (shared-name)
    # cluster B is using, and the job is not in any terminal/cancelled
    # state from A's writes.
    assert fake_cloud.terminate_calls == terminates_before
    assert (managed_job_state.get_status(job_id) == ManagedJobStatus.RUNNING)

    fake_cloud.finish_job(cluster_name)
    await harness_b.wait_for_job_status(job_id, [ManagedJobStatus.SUCCEEDED])
    await harness_b.wait_for_schedule_state(job_id,
                                            [ManagedJobScheduleState.DONE])


@pytest.mark.asyncio
async def test_reset_unclaimed_loser_terminates_cluster(jobs_db, fake_cloud,
                                                        controller_harness):
    """T2 (unclaimed): the job is reset but never re-claimed. The loser
    terminates its cluster (nothing else would) and leaves the job WAITING
    with no terminal status, so it relaunches when next claimed."""
    harness = controller_harness
    job_id = harness.submit_job(name='unclaimed')
    cluster_name = harness.cluster_name_for(job_id, 'unclaimed')

    await harness.start()
    await harness.wait_for_job_status(job_id, [ManagedJobStatus.RUNNING])

    # Reset and DON'T re-claim. The collision trigger isn't in this commit,
    # so nudge detection the way a fenced write will: cancel the live task.
    # The job's row stays WAITING + claim NULL, so when the controller's
    # next state write fences out, stand-down sees "reset, unclaimed".
    managed_job_state.reset_job_for_recovery(job_id)

    async with harness.manager._job_tasks_lock:  # pylint: disable=protected-access
        token = harness.manager._tokens.get(job_id)  # pylint: disable=protected-access
        inner = harness.manager.job_tasks.get(job_id)
    assert token is not None and inner is not None
    # Marker-then-cancel, exactly as the collision trigger (commit D) will:
    token.lost = True
    inner.cancel()

    # Stand-down terminates the orphaned cluster...
    await harness.wait_for(lambda: cluster_name in fake_cloud.terminate_calls,
                           'orphaned cluster terminated by stand-down')
    # ...and leaves the job WAITING with claim cleared and no terminal write.
    await harness.wait_for(lambda: job_id not in harness.manager.job_tasks,
                           'loop deregistered after stand-down')
    claim_id, _, schedule_state = _read_row(jobs_db, job_id)
    assert schedule_state == ManagedJobScheduleState.WAITING.value
    assert claim_id is None
    assert (managed_job_state.get_status(job_id) == ManagedJobStatus.RUNNING)


@pytest.mark.asyncio
async def test_same_process_no_reclaim_via_exclusion(jobs_db, fake_cloud,
                                                     controller_harness):
    """T8 (claim exclusion): a controller never re-claims a job it is
    already running, even after that job's row is reset to WAITING."""
    del jobs_db, fake_cloud
    harness = controller_harness
    job_id = harness.submit_job(name='no-self-reclaim')

    await harness.start()
    await harness.wait_for_job_status(job_id, [ManagedJobStatus.RUNNING])

    # Reset the row to WAITING while the controller still runs it locally.
    managed_job_state.reset_job_for_recovery(job_id)

    # The controller must not re-claim its own live job: its claim query
    # excludes locally-registered ids. Confirm directly that a claim
    # attempt with this job excluded finds nothing.
    async with harness.manager._job_tasks_lock:  # pylint: disable=protected-access
        local_ids = set(harness.manager.job_tasks) | harness.manager.starting
    assert job_id in local_ids
    claimed = await managed_job_state.get_waiting_job_async(
        pid=harness.pid,
        pid_started_at=float(harness.pid),
        exclude_job_ids=local_ids)
    assert claimed is None
    # The job is still WAITING and unclaimed (nobody stole it).
    assert (managed_job_state.get_job_schedule_state(job_id) ==
            ManagedJobScheduleState.WAITING)


@pytest.mark.asyncio
async def test_duplicate_invocation_is_non_destructive(jobs_db, fake_cloud,
                                                       controller_harness):
    """The belt-and-suspenders guard: a second run_job_loop for a job
    already in job_tasks exits without cleanup or terminal writes."""
    del jobs_db
    harness = controller_harness
    job_id = harness.submit_job(name='dup-guard')
    cluster_name = harness.cluster_name_for(job_id, 'dup-guard')

    await harness.start()
    await harness.wait_for_job_status(job_id, [ManagedJobStatus.RUNNING])
    terminates_before = list(fake_cloud.terminate_calls)

    # Directly invoke a duplicate run_job_loop for the live job (a fresh
    # claim_id, as a re-claim would carry). It must short-circuit.
    await harness.manager.run_job_loop(job_id,
                                       log_file='/dev/null',
                                       pool=None,
                                       claim_id='duplicate-claim')

    # The original run is untouched: still registered, still RUNNING, no
    # cluster torn down.
    assert job_id in harness.manager.job_tasks
    assert (managed_job_state.get_status(job_id) == ManagedJobStatus.RUNNING)
    assert fake_cloud.terminate_calls == terminates_before

    fake_cloud.finish_job(cluster_name)
    await harness.wait_for_job_status(job_id, [ManagedJobStatus.SUCCEEDED])
