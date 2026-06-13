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


def _set_ownership(engine, job_id, *, claim_id, pid, schedule_state):
    """Force a job_info row to a specific ownership state (test setup)."""
    table = managed_job_state.job_info_table
    with engine.begin() as conn:
        conn.execute(
            sqlalchemy.update(table).where(
                table.c.spot_job_id == job_id).values({
                    table.c.claim_id: claim_id,
                    table.c.controller_pid: pid,
                    table.c.schedule_state: schedule_state,
                }))


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
        lambda: (managed_job_state.get_job_controller_process(job_id) is
                 not None and managed_job_state.get_job_controller_process(
                     job_id).pid == harness_b.pid), 'job re-claimed by B')
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

    # Reset and DON'T re-claim. The job's row stays WAITING + claim NULL.
    # The controller's own collision trigger sees its locally-running job
    # back in WAITING and steps it down; stand-down sees "reset, unclaimed".
    managed_job_state.reset_job_for_recovery(job_id)

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


@pytest.mark.asyncio
async def test_collision_metric_and_verdict(jobs_db, fake_cloud,
                                            controller_harness, monkeypatch):
    """The collision trigger emits ownership-lost{detection=collision} and a
    stand-down{verdict=unclaimed} for a reset-but-unclaimed job."""
    del jobs_db
    from unittest import mock  # pylint: disable=import-outside-toplevel

    from sky.jobs import controller as controller_module
    from sky.metrics import utils as metrics_lib
    monkeypatch.setattr(metrics_lib, 'METRICS_ENABLED', True)
    lost_metric = mock.MagicMock()
    standdown_metric = mock.MagicMock()
    monkeypatch.setattr(metrics_lib, 'SKY_MANAGED_JOBS_OWNERSHIP_LOST_TOTAL',
                        lost_metric)
    monkeypatch.setattr(metrics_lib, 'SKY_MANAGED_JOBS_STANDDOWN_TOTAL',
                        standdown_metric)
    # The harness speeds the monitor tick to 0.05s, which would let the tick
    # check beat the (real-cadence) collision poll and win detection. In
    # production the order is reversed (collision ~10s, tick ~60s); suppress
    # the tick here so this test exercises the collision path it names.
    monkeypatch.setattr(controller_module, 'OWNERSHIP_CHECK_EVERY_N_TICKS',
                        10**9)

    harness = controller_harness
    job_id = harness.submit_job(name='collision-metric')
    cluster_name = harness.cluster_name_for(job_id, 'collision-metric')

    await harness.start()
    await harness.wait_for_job_status(job_id, [ManagedJobStatus.RUNNING])

    managed_job_state.reset_job_for_recovery(job_id)
    await harness.wait_for(lambda: cluster_name in fake_cloud.terminate_calls,
                           'stand-down terminated the unclaimed cluster')
    await harness.wait_for(lambda: job_id not in harness.manager.job_tasks,
                           'loop deregistered after stand-down')

    lost_labels = [c.kwargs for c in lost_metric.labels.call_args_list]
    assert {
        'detection': 'collision',
        'pid': str(harness.pid)
    } in lost_labels, lost_labels
    standdown_labels = [
        c.kwargs for c in standdown_metric.labels.call_args_list
    ]
    assert {
        'verdict': 'unclaimed',
        'pid': str(harness.pid)
    } in standdown_labels, standdown_labels


@pytest.mark.asyncio
async def test_tick_detects_loss_for_reclaimed_running_job(
        jobs_db, fake_cloud, make_controller_harness):
    """A RUNNING job re-claimed by another controller (so never WAITING for
    the collision trigger) is still detected by the idle-monitor tick check,
    which steps the loser down with the reclaimed verdict."""
    del jobs_db
    harness_a = make_controller_harness(pid=20001)
    harness_b = make_controller_harness(pid=20002)
    job_id = harness_a.submit_job(name='tick-detect')
    cluster_name = harness_a.cluster_name_for(job_id, 'tick-detect')

    await harness_a.start()
    await _wait_running_owned_by(harness_a, job_id)

    # Reset, then have B re-claim. Once B owns it and resumes, the row is no
    # longer WAITING -- A's collision trigger won't fire, but A's tick check
    # reads the claim and finds it changed.
    managed_job_state.reset_job_for_recovery(job_id)
    await harness_b.start()
    await harness_b.wait_for(
        lambda: (managed_job_state.get_job_controller_process(job_id) is
                 not None and managed_job_state.get_job_controller_process(
                     job_id).pid == harness_b.pid), 'job re-claimed by B')

    # A's loop deregisters after standing down (reclaimed verdict: no
    # terminate, no writes).
    await harness_a.wait_for(lambda: job_id not in harness_a.manager.job_tasks,
                             'A deregistered after tick-detected stand-down')
    assert cluster_name not in fake_cloud.terminate_calls

    fake_cloud.finish_job(cluster_name)
    await harness_b.wait_for_job_status(job_id, [ManagedJobStatus.SUCCEEDED])


# --- Verdict-matrix unit tests: call _stand_down directly with a crafted
# ownership row. These cover the defense-in-depth branches that are awkward
# to reach end to end (mixed-version, claim-is-ours, row-gone). ---


def _token_for(job_id, claim_id='my-claim'):
    from sky.jobs import fencing  # pylint: disable=import-outside-toplevel
    return fencing.FencingToken(job_id=job_id, claim_id=claim_id, lost=True)


@pytest.mark.asyncio
async def test_verdict_reclaimed_does_nothing(jobs_db, fake_cloud,
                                              controller_harness):
    harness = controller_harness
    job_id = harness.submit_job(name='v-reclaimed')
    _set_ownership(jobs_db,
                   job_id,
                   claim_id='other-claim',
                   pid=999,
                   schedule_state=ManagedJobScheduleState.ALIVE.value)

    stood_down = await harness.manager._stand_down(  # pylint: disable=protected-access
        job_id, None, _token_for(job_id))
    assert stood_down is True
    assert fake_cloud.terminate_calls == []


@pytest.mark.asyncio
async def test_verdict_claim_is_ours_falls_through(jobs_db, fake_cloud,
                                                   controller_harness):
    """The "impossible" anomaly: fence tripped but the claim is still ours.
    Stand-down must NOT own the exit (returns False) so the caller runs the
    normal failure handling instead of orphaning a live-claimed zombie."""
    harness = controller_harness
    job_id = harness.submit_job(name='v-ours')
    _set_ownership(jobs_db,
                   job_id,
                   claim_id='my-claim',
                   pid=harness.pid,
                   schedule_state=ManagedJobScheduleState.ALIVE.value)

    stood_down = await harness.manager._stand_down(  # pylint: disable=protected-access
        job_id, None, _token_for(job_id, claim_id='my-claim'))
    assert stood_down is False
    assert fake_cloud.terminate_calls == []


@pytest.mark.asyncio
async def test_verdict_mixed_version_does_not_terminate(jobs_db, fake_cloud,
                                                        controller_harness):
    """claim_id NULL but pid set / not WAITING: an old-version controller may
    have legitimately re-claimed, so never terminate."""
    harness = controller_harness
    job_id = harness.submit_job(name='v-mixed')
    _set_ownership(jobs_db,
                   job_id,
                   claim_id=None,
                   pid=4242,
                   schedule_state=ManagedJobScheduleState.LAUNCHING.value)

    stood_down = await harness.manager._stand_down(  # pylint: disable=protected-access
        job_id, None, _token_for(job_id))
    assert stood_down is True
    assert fake_cloud.terminate_calls == []


@pytest.mark.asyncio
async def test_verdict_row_gone(jobs_db, fake_cloud, controller_harness):
    harness = controller_harness
    job_id = harness.submit_job(name='v-gone')
    with jobs_db.begin() as conn:
        conn.execute(
            sqlalchemy.delete(managed_job_state.job_info_table).where(
                managed_job_state.job_info_table.c.spot_job_id == job_id))

    stood_down = await harness.manager._stand_down(  # pylint: disable=protected-access
        job_id, None, _token_for(job_id))
    assert stood_down is True
    assert fake_cloud.terminate_calls == []


@pytest.mark.asyncio
async def test_verdict_unclaimed_terminates(jobs_db, fake_cloud,
                                            controller_harness):
    """claim NULL, pid NULL, WAITING: terminate the orphaned cluster."""
    harness = controller_harness
    job_id = harness.submit_job(name='v-unclaimed')
    cluster_name = harness.cluster_name_for(job_id, 'v-unclaimed')
    # Pretend a cluster exists so terminate is observable.
    fake_cloud.launch(cluster_name)
    _set_ownership(jobs_db,
                   job_id,
                   claim_id=None,
                   pid=None,
                   schedule_state=ManagedJobScheduleState.WAITING.value)

    stood_down = await harness.manager._stand_down(  # pylint: disable=protected-access
        job_id, None, _token_for(job_id))
    assert stood_down is True
    assert cluster_name in fake_cloud.terminate_calls


@pytest.mark.asyncio
async def test_unclaimed_terminate_aborts_if_claim_appears(
        jobs_db, fake_cloud, controller_harness):
    """The per-attempt re-read: if a claim appears before the terminate,
    stand-down aborts rather than killing a now-claimed cluster."""
    harness = controller_harness
    job_id = harness.submit_job(name='v-race')
    cluster_name = harness.cluster_name_for(job_id, 'v-race')
    fake_cloud.launch(cluster_name)
    # A claim is already present: the re-read in _stand_down_terminate_one
    # sees it and aborts.
    _set_ownership(jobs_db,
                   job_id,
                   claim_id='winner',
                   pid=5,
                   schedule_state=ManagedJobScheduleState.ALIVE.value)

    await harness.manager._stand_down_terminate_clusters(  # pylint: disable=protected-access
        job_id, None)
    assert cluster_name not in fake_cloud.terminate_calls
