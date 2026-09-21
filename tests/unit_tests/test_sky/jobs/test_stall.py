"""The two stall scans, one clause at a time.

Every case has a baseline arm: the row that must be reported sits next to the
row that must not, and the assertion names both. A suppression test that only
asserts silence passes just as well when the query returns nothing at all.
"""
import dataclasses
import time
from typing import Optional

import pytest
import sqlalchemy
from sqlalchemy import orm

from sky.jobs import stall
from sky.jobs import state as managed_job_state
from sky.skylet import constants as skylet_constants
from sky.utils.db import db_utils

# Captured at import, before the autouse fixture below replaces them. A test
# that wants the real body has to use these names: reaching for the module
# attribute inside a test gets the stub and asserts nothing, which is the
# failure mode that left these two functions untested to begin with.
_REAL_TASKS_ACTIVE_RECENTLY = stall._tasks_active_recently
_REAL_CLUSTERS_WITH_LIVE_REQUESTS = stall._clusters_with_live_requests

# Comfortably past both thresholds (10 and 15 minutes).
_OLD = 3600
# Comfortably inside both.
_RECENT = 60


@pytest.fixture(name='engine')
def engine_fixture(tmp_path, monkeypatch):
    """A managed-jobs database of this test's own.

    Without the runtime-dir override the manager opens whatever an earlier run
    left in the real one, and the failure then reads as a missing column rather
    than as a test pointed at the wrong file.
    """
    monkeypatch.setenv(skylet_constants.SKY_RUNTIME_DIR_ENV_VAR_KEY,
                       str(tmp_path))
    monkeypatch.setattr(
        managed_job_state, '_db_manager',
        db_utils.DatabaseManager('spot_jobs', managed_job_state.create_table))
    return managed_job_state.get_engine()


@pytest.fixture(name='no_cross_store', autouse=True)
def no_cross_store_fixture(monkeypatch):
    """Neutral answers from the three stores the claimed scan also reads.

    Neutral means "nothing is suppressing anything", so a test that wants a
    suppressor has to ask for it explicitly and cannot pass by accident.
    """
    # The scan only runs where the controllers share the host: the collector
    # is registered behind this condition, and off it the never-claimed phase
    # is suppressed outright. Tests state the production mode rather than
    # inheriting whatever the test host happens to be.
    monkeypatch.setattr(stall.managed_job_utils, 'is_consolidation_mode',
                        lambda: True)
    monkeypatch.setattr(stall, '_clusters_with_live_requests',
                        lambda names: set())
    monkeypatch.setattr(stall, '_tasks_active_recently',
                        lambda engine, job_ids, deadline: set())
    monkeypatch.setattr(stall.global_user_state,
                        'get_launch_attempts_for_cluster', lambda name: [])


def _add_job(engine,
             job_id: int,
             *,
             status: str,
             task_id: int = 0,
             eligible_at: Optional[float] = None,
             submitted_at: Optional[float] = None,
             start_at: Optional[float] = None,
             end_at: Optional[float] = None,
             workspace: str = 'ws',
             priority: Optional[int] = None,
             pool: Optional[str] = None,
             is_batch: Optional[bool] = None,
             schedule_state: Optional[str] = None,
             with_info: bool = True):
    with orm.Session(engine) as session:
        session.execute(managed_job_state.spot_table.insert().values(
            spot_job_id=job_id,
            task_id=task_id,
            task_name=f'task-{job_id}-{task_id}',
            status=status,
            eligible_at=eligible_at,
            submitted_at=submitted_at,
            start_at=start_at,
            end_at=end_at))
        if with_info:
            values = {
                'spot_job_id': job_id,
                'name': f'job-{job_id}',
                'workspace': workspace,
                'priority': priority,
                'pool': pool,
                'is_batch': is_batch,
            }
            if schedule_state is not None:
                values['schedule_state'] = schedule_state
            session.execute(
                managed_job_state.job_info_table.insert().values(**values))
        session.commit()


def _never_claimed(engine, job_id: int, *, age: float, **kwargs):
    """A task nothing has picked up, `age` seconds old."""
    _add_job(engine,
             job_id,
             status='PENDING',
             eligible_at=time.time() - age,
             submitted_at=None,
             **kwargs)


def _claimed(engine,
             job_id: int,
             *,
             age: float,
             status: str = 'STARTING',
             **kwargs):
    """A task something claimed `age` seconds ago that has not started."""
    _add_job(engine,
             job_id,
             status=status,
             submitted_at=time.time() - age,
             start_at=None,
             end_at=None,
             **kwargs)


def _ids(scan) -> set:
    return {task.spot_job_id for task in scan.tasks}


# ---------------------------------------------------------------- never_claimed


def test_an_old_unclaimed_task_is_reported_and_a_fresh_one_is_not(engine):
    _never_claimed(engine, 1, age=_OLD)
    _never_claimed(engine, 2, age=_RECENT)

    assert _ids(stall.scan_never_claimed()) == {1}


def test_a_task_whose_sibling_is_progressing_is_not_stalled(engine):
    # Job 1: a second task of the same job is running, so task 0 is simply not
    # eligible yet. Job 2 is the same shape with no running sibling.
    _never_claimed(engine, 1, age=_OLD)
    _add_job(engine, 1, task_id=1, status='RUNNING', with_info=False)
    _never_claimed(engine, 2, age=_OLD)

    assert _ids(stall.scan_never_claimed()) == {2}


def test_a_winding_down_sibling_counts_as_progress(engine):
    """A job group merging its output is progressing, not stalled."""
    _never_claimed(engine, 1, age=_OLD)
    _add_job(engine, 1, task_id=1, status='WINDING_DOWN', with_info=False)
    _never_claimed(engine, 2, age=_OLD)

    assert _ids(stall.scan_never_claimed()) == {2}


def test_a_task_behind_higher_priority_is_waiting_not_stalled(engine):
    # Job 1 is at the head of the queue and job 2 is behind it. Only the one
    # that could have started is reported.
    _never_claimed(engine, 1, age=_OLD, priority=500)
    _never_claimed(engine, 2, age=_OLD, priority=100)
    _add_job(engine,
             3,
             status='PENDING',
             priority=500,
             schedule_state='WAITING')

    assert _ids(stall.scan_never_claimed()) == {1}


def test_a_batch_job_waiting_on_its_busy_pool_is_not_stalled(engine):
    # Job 1's pool is occupied by job 3; job 2's pool is not.
    _never_claimed(engine, 1, age=_OLD, pool='busy', is_batch=True)
    _never_claimed(engine, 2, age=_OLD, pool='idle', is_batch=True)
    _add_job(engine,
             3,
             status='STARTING',
             pool='busy',
             is_batch=True,
             schedule_state='LAUNCHING')

    assert _ids(stall.scan_never_claimed()) == {2}


def test_a_batch_job_does_not_suppress_itself(engine):
    """The pool exclusion must not match the candidate's own row.

    A batch job wedged in LAUNCHING is claimed but has no submitted_at yet --
    exactly a never-claimed target -- and it occupies its own pool, so an
    exclusion that did not exclude itself would silence precisely the case
    this phase exists for.
    """
    _never_claimed(engine,
                   1,
                   age=_OLD,
                   pool='solo',
                   is_batch=True,
                   schedule_state='LAUNCHING')

    assert _ids(stall.scan_never_claimed()) == {1}


def test_a_task_with_no_eligible_at_can_never_be_reported(engine):
    """A row that predates the timeline columns is invisible to this phase.

    Locks in the behaviour, not one clause of the query: a NULL eligible_at
    already fails the age comparison, so the explicit IS NOT NULL is
    belt-and-braces and removing it changes nothing. What would change this is
    someone giving the comparison a COALESCE default, which is why the
    behaviour is worth pinning even though no single clause owns it.
    """
    _add_job(engine, 1, status='PENDING', eligible_at=None, submitted_at=None)
    _never_claimed(engine, 2, age=_OLD)

    assert _ids(stall.scan_never_claimed()) == {2}


# ------------------------------------------------------------------ unattended


class _Attempt:
    """A launch_attempts row, only the fields the scan reads."""

    def __init__(self,
                 outcome: Optional[str] = None,
                 instances_requested: Optional[float] = None,
                 provision_start: Optional[float] = None):
        self.outcome = outcome
        self.instances_requested = instances_requested
        self.provision_start = provision_start


def _attempts(monkeypatch, attempts):
    monkeypatch.setattr(stall.global_user_state,
                        'get_launch_attempts_for_cluster',
                        lambda name: list(attempts))


def test_an_old_claimed_task_is_reported_and_a_fresh_one_is_not(engine):
    _claimed(engine, 1, age=_OLD)
    _claimed(engine, 2, age=_RECENT)

    assert _ids(stall.scan_unattended()) == {1}


def test_a_task_that_started_or_ended_is_not_reported(engine):
    _claimed(engine, 1, age=_OLD)
    _add_job(engine,
             2,
             status='RUNNING',
             submitted_at=time.time() - _OLD,
             start_at=time.time() - _OLD)
    _add_job(engine,
             3,
             status='SUCCEEDED',
             submitted_at=time.time() - _OLD,
             end_at=time.time() - _RECENT)

    assert _ids(stall.scan_unattended()) == {1}


def test_a_cancelling_task_is_not_reported(engine):
    _claimed(engine, 1, age=_OLD)
    _claimed(engine, 2, age=_OLD, status='CANCELLING')

    assert _ids(stall.scan_unattended()) == {1}


def test_a_pool_job_is_excluded(engine):
    _claimed(engine, 1, age=_OLD)
    _claimed(engine, 2, age=_OLD, pool='p')

    assert _ids(stall.scan_unattended()) == {1}


def test_a_live_request_for_the_cluster_suppresses(engine, monkeypatch):
    _claimed(engine, 1, age=_OLD)
    _claimed(engine, 2, age=_OLD)
    busy = stall._cluster_name(stall.scan_unattended().tasks[0])
    monkeypatch.setattr(stall, '_clusters_with_live_requests',
                        lambda names: {busy})

    reported = _ids(stall.scan_unattended())
    assert reported == {2}, 'only the cluster with no live request is reported'


def test_an_open_attempt_that_asked_the_cloud_suppresses(engine, monkeypatch):
    _claimed(engine, 1, age=_OLD)
    _attempts(monkeypatch, [_Attempt(instances_requested=time.time() - _OLD)])

    assert not stall.scan_unattended().tasks


def test_an_open_attempt_that_never_asked_does_not_suppress_forever(
        engine, monkeypatch):
    """The age branch, in both directions.

    An attempt whose launcher died before asking for anything is held quiet
    only until it is as old as the threshold.
    """
    _claimed(engine, 1, age=_OLD)

    _attempts(monkeypatch, [_Attempt(provision_start=time.time() - _RECENT)])
    assert not stall.scan_unattended().tasks, 'young orphan is held quiet'

    _attempts(monkeypatch, [_Attempt(provision_start=time.time() - _OLD)])
    assert _ids(stall.scan_unattended()) == {1}, 'old orphan is reported'


def test_a_succeeded_attempt_suppresses(engine, monkeypatch):
    """The cluster came up and the job has not started: a group barrier."""
    _claimed(engine, 1, age=_OLD)
    _attempts(monkeypatch, [_Attempt(outcome='succeeded')])

    assert not stall.scan_unattended().tasks


def test_a_failed_attempt_falls_through_to_the_tasks_own_activity(
        engine, monkeypatch):
    """A closed-failed attempt cannot say whether a retry is coming.

    Both arms, because asserting only one would pass on a scan that ignored
    job_events entirely.
    """
    _claimed(engine, 1, age=_OLD)
    _attempts(monkeypatch, [_Attempt(outcome='failed')])

    monkeypatch.setattr(stall, '_tasks_active_recently',
                        lambda engine, job_ids, deadline: {(1, 0)})
    assert not stall.scan_unattended().tasks, 'a retrying task is not stalled'

    monkeypatch.setattr(stall, '_tasks_active_recently',
                        lambda engine, job_ids, deadline: set())
    assert _ids(stall.scan_unattended()) == {1}, 'a silent task is stalled'


def test_only_the_latest_attempt_is_consulted(engine, monkeypatch):
    """One success in the history must not suppress the task for good."""
    _claimed(engine, 1, age=_OLD)
    _attempts(monkeypatch,
              [_Attempt(outcome='succeeded'),
               _Attempt(outcome='failed')])

    assert _ids(stall.scan_unattended()) == {1}


# ---------------------------------------------------------------- thresholds


def test_the_threshold_can_be_overridden_by_the_environment(
        engine, monkeypatch):
    _claimed(engine, 1, age=_OLD)
    _claimed(engine, 2, age=120)

    assert _ids(stall.scan_unattended()) == {1}

    monkeypatch.setenv(skylet_constants.ENV_VAR_MANAGED_JOBS_UNATTENDED_SECONDS,
                       '60')
    assert _ids(stall.scan_unattended()) == {1, 2}


@pytest.mark.parametrize('value', ['0', '-5', 'soon', '', 'inf', 'nan'])
def test_an_unusable_threshold_raises_rather_than_falling_back(
        engine, monkeypatch, value):
    """A threshold that silently reverts is a wrong answer that looks right.

    The raise reaches the collector, which reports the phase as not measured
    instead of as zero.
    """
    monkeypatch.setenv(skylet_constants.ENV_VAR_MANAGED_JOBS_UNATTENDED_SECONDS,
                       value)

    with pytest.raises(ValueError):
        stall.unattended_seconds()


# --------------------------------------------------------------- truncation


def test_the_request_lookup_asks_for_only_the_column_it_reads(engine):
    """Asking for whole requests would decode them, and a decode can raise.

    Request.decode unpickles the request body and re-raises what it cannot
    read, so one request left behind by another server version would take the
    whole phase to "not measured" -- during a rollout, which is when it matters
    most. Narrowing the projection is what prevents that, so it is pinned.
    """
    # pylint: disable=import-outside-toplevel
    from sky.server.requests import requests as api_requests

    seen = {}

    def _capture(req_filter):
        seen['filter'] = req_filter
        return []

    _claimed(engine, 1, age=_OLD)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(stall, '_clusters_with_live_requests',
                      _REAL_CLUSTERS_WITH_LIVE_REQUESTS)
        patch.setattr(api_requests, 'get_request_tasks', _capture)
        stall.scan_unattended()

    assert seen['filter'].fields == [api_requests.COL_CLUSTER_NAME]


def test_the_activity_lookup_runs_against_a_real_database(engine):
    """The autouse stub means this function body is otherwise never executed.

    It is the retry suppressor: if its SQL or its clock handling regressed,
    every task in launch backoff would be reported, and the suite would stay
    green because nothing calls the real thing. Written through the production
    event writer so the timestamp format is the one the column really holds.
    """
    # pylint: disable=import-outside-toplevel
    from sky.jobs.state import ManagedJobStatus

    _claimed(engine, 1, age=_OLD)
    _claimed(engine, 2, age=_OLD)
    managed_job_state.add_job_event(1, 0, ManagedJobStatus.PENDING,
                                    'Job submitted to queue')

    active = _REAL_TASKS_ACTIVE_RECENTLY(
        engine, [1, 2],
        time.monotonic() + stall._SCAN_BUDGET_SECONDS)

    assert active == {(1, 0)}, 'only the task that wrote an event is active'


def test_a_capped_scan_says_its_count_is_a_floor(engine):
    for job_id in range(1, 6):
        _claimed(engine, job_id, age=_OLD)

    assert stall.scan_unattended().truncated is False

    capped = stall.scan_unattended(candidate_limit=3)
    assert capped.truncated is True

    assert stall.scan_unattended(examination_limit=2).truncated is True


def test_a_task_in_launch_backoff_belongs_to_one_phase_only(engine):
    """The only row shape that could land in both, built on purpose.

    `set_backoff_pending_async` puts a task back to status PENDING while
    submitted_at stays set, so it satisfies the never-claimed status test and
    the claimed one at the same time. `submitted_at IS NULL` is the only thing
    keeping it out of the never-claimed half; without that clause the task is
    counted twice and two alerts fire for one incident.
    """
    _add_job(engine,
             1,
             status='PENDING',
             eligible_at=time.time() - _OLD,
             submitted_at=time.time() - _OLD,
             start_at=None,
             end_at=None)

    assert _ids(stall.scan_never_claimed()) == set()
    assert _ids(stall.scan_unattended()) == {1}


def test_a_task_that_ended_without_ever_being_claimed_is_not_reported(engine):
    """status = 'PENDING' is the only clause excluding closed rows here.

    A controller failure marks every task FAILED_CONTROLLER with an end_at,
    including tasks nothing ever claimed -- which keeps their old eligible_at
    and their NULL submitted_at. Without the status test those rows match the
    never-claimed predicate forever, on every deployment that has ever had one.
    """
    _add_job(engine,
             1,
             status='FAILED_CONTROLLER',
             eligible_at=time.time() - _OLD,
             submitted_at=None,
             end_at=time.time() - _RECENT)
    _never_claimed(engine, 2, age=_OLD)

    assert _ids(stall.scan_never_claimed()) == {2}


def test_the_shared_predicate_stays_unambiguous_in_the_join(engine):
    """CLAIMED_IN_FLIGHT_PREDICATE names columns without a table qualifier.

    It has to: the same string builds a single-table partial index. That is
    only safe while job_info has none of those columns -- adding one would make
    the claimed scan ambiguous at runtime, on a deployment, with nothing in
    between to catch it.
    """
    info_columns = {
        column.name for column in managed_job_state.job_info_table.columns
    }

    assert not info_columns & {'submitted_at', 'start_at', 'end_at'}


def test_the_phases_do_not_read_each_others_rows(engine):
    """One condition's shape must not appear in the other's result."""
    _never_claimed(engine, 1, age=_OLD)
    _claimed(engine, 2, age=_OLD)

    assert _ids(stall.scan_never_claimed()) == {1}
    assert _ids(stall.scan_unattended()) == {2}
    assert stall.scan_never_claimed().phase == stall.NEVER_CLAIMED
    assert stall.scan_unattended().phase == stall.UNATTENDED


# --- the budget is per scan, not per statement -------------------------------


def test_the_budget_shrinks_as_a_scan_spends_it(engine):
    """Each statement gets what is LEFT, so the total cannot multiply.

    A per-statement timeout looks equivalent and is not: the claimed scan runs
    two statements and the collector runs both scans, so three statements at a
    per-statement bound would be three times the number anyone reasoned about,
    against a refresh interval and a staleness horizon that were the reason for
    picking it.
    """
    deadline = time.monotonic() + stall._SCAN_BUDGET_SECONDS

    first = stall._remaining_ms(deadline, now=time.monotonic())
    later = stall._remaining_ms(deadline, now=time.monotonic() + 5)

    assert first <= stall._SCAN_BUDGET_SECONDS * 1000
    assert later < first, 'a spent budget must leave less for what follows'


def test_an_exhausted_budget_never_asks_for_no_bound_at_all(engine):
    """Postgres reads statement_timeout = 0 as DISABLED.

    So a budget that has run out must floor rather than round away, or the
    last statement of a slow scan is the one statement with no bound on it.
    """
    deadline = time.monotonic() - 60

    assert stall._remaining_ms(deadline) >= 1000


def test_the_task_fields_an_event_payload_needs_are_public():
    """The plugin reads these off StalledTask to build its payload.

    Dropping one is the same kind of break as renaming a function, and it does
    not show up as a signature change.
    """
    fields = {field.name for field in dataclasses.fields(stall.StalledTask)}

    assert {
        'spot_job_id', 'task_id', 'task_name', 'job_name', 'workspace',
        'stalled_since'
    } <= fields


# --- the halves of an exclusion that must NOT apply -------------------------


def test_a_claimed_task_is_not_excluded_by_priority(engine):
    """Priority starvation is the never-claimed phase's business only.

    A claimed task has already won its slot, so excluding it for priority
    would suppress exactly the case the claimed phase exists to report. The
    structure makes that true -- _starved is called in one scan and not the
    other -- which is why it needs a test rather than a reading.
    """
    _claimed(engine, 1, age=_OLD, priority=0)
    _add_job(engine,
             2,
             status='PENDING',
             priority=900,
             schedule_state='WAITING')

    assert _ids(stall.scan_unattended()) == {1}


def test_a_pool_job_that_is_not_a_batch_job_is_still_reported(engine):
    """The pool exclusion is about batch jobs occupying their pool.

    A job that merely names a pool is not one, and dropping the is_batch
    clause would silence every job on a busy pool -- which reads as the
    exclusion working.
    """
    _never_claimed(engine, 1, age=_OLD, pool='shared', is_batch=None)
    # A batch job really is occupying that pool.
    _add_job(engine,
             2,
             status='STARTING',
             pool='shared',
             is_batch=True,
             schedule_state='LAUNCHING')

    assert _ids(stall.scan_never_claimed()) == {1}


def test_the_priority_lookup_runs_on_the_scans_own_connection(
        engine, monkeypatch):
    """The one statement that used to sit outside the budget.

    Every other query in a scan goes through `_bounded`, which sets the
    database's own statement timeout; this one opened its own session. A raise
    there was always handled -- the phase reports "not measured" -- but a hang
    was not, and a hung query parks the metrics thread with nothing able to
    interrupt it. That is the case the budget exists for.
    """
    seen = []

    def fake_highest(conn=None):
        seen.append(conn)
        return 0

    monkeypatch.setattr(managed_job_state, 'get_managed_jobs_highest_priority',
                        fake_highest)

    stall.scan_never_claimed()

    assert seen, 'the scan never consulted the priority lookup'
    assert seen[0] is not None, (
        'the priority lookup got no connection, so it opened its own and ran '
        'outside the scan budget')


def _capacity(monkeypatch, *, launches: int, held: int, controllers: int = 1):
    """Shrink the pool the scheduler thinks it has, so a test can fill it."""
    monkeypatch.setattr(stall.controller_utils,
                        'get_number_of_jobs_controllers', lambda: controllers)
    monkeypatch.setattr(stall.controller_utils, 'LAUNCHES_PER_WORKER', launches)
    monkeypatch.setattr(stall.controller_utils, 'MAX_JOBS_PER_WORKER', held)
    monkeypatch.setattr(stall.controller_utils, 'MAX_TOTAL_RUNNING_JOBS',
                        held * controllers)


def _burst(engine):
    """Two launches in flight and one job queued behind them."""
    _add_job(engine, 1, status='STARTING', submitted_at=_OLD)
    _add_job(engine, 2, status='STARTING', submitted_at=_OLD)
    _never_claimed(engine, 3, age=_OLD)


def test_the_queue_behind_a_full_pool_reports_without_the_gate(
        engine, monkeypatch):
    """The bug, kept as a test: these rows really are never-claimed rows.

    Without it the suppression tests below could pass for the wrong reason --
    any clause that dropped job 3 would satisfy them. Here the gate is the only
    thing removed, and the report comes back.
    """
    _capacity(monkeypatch, launches=2, held=3)
    monkeypatch.setattr(stall, '_claim_gates', lambda engine, deadline:
                        (False, ''))
    _burst(engine)

    assert _ids(stall.scan_never_claimed()) == {3}


def test_a_queue_behind_busy_launch_slots_is_not_a_stall(engine, monkeypatch):
    """The gate that binds in the field: 8 launches per controller process."""
    _capacity(monkeypatch, launches=2, held=3)
    _burst(engine)

    assert _ids(stall.scan_never_claimed()) == set()


def test_a_queue_behind_a_full_controller_is_not_a_stall(engine, monkeypatch):
    """The other gate: the process is holding all the jobs it may hold.

    Held separately from launching -- these two are RUNNING, so they occupy a
    job slot without occupying a launch slot.
    """
    _capacity(monkeypatch, launches=5, held=2)
    _add_job(engine, 1, status='RUNNING', submitted_at=_OLD, start_at=_OLD)
    _add_job(engine, 2, status='RUNNING', submitted_at=_OLD, start_at=_OLD)
    _never_claimed(engine, 3, age=_OLD)

    assert _ids(stall.scan_never_claimed()) == set()


def test_a_free_slot_in_both_gates_still_reports_the_stall(engine, monkeypatch):
    """The negative arm: the gate must not swallow a real stall."""
    _capacity(monkeypatch, launches=3, held=3)
    _add_job(engine, 1, status='STARTING', submitted_at=_OLD)
    _add_job(engine, 2, status='RUNNING', submitted_at=_OLD, start_at=_OLD)
    _never_claimed(engine, 3, age=_OLD)

    assert _ids(stall.scan_never_claimed()) == {3}


def test_a_finished_job_holds_neither_kind_of_slot(engine, monkeypatch):
    """Occupancy is jobs that have not finished, not jobs that ever ran."""
    _capacity(monkeypatch, launches=2, held=2)
    _add_job(engine, 1, status='STARTING', submitted_at=_OLD)
    _add_job(engine,
             2,
             status='SUCCEEDED',
             submitted_at=_OLD,
             start_at=_OLD,
             end_at=_OLD)
    _never_claimed(engine, 3, age=_OLD)

    assert _ids(stall.scan_never_claimed()) == {3}


def test_off_consolidation_the_phase_is_suppressed(engine, monkeypatch):
    """The capacity numbers are sized from this process, so off consolidation
    they describe the wrong machine. Silence there matches the metrics path,
    which is not registered off consolidation either."""
    monkeypatch.setattr(stall.managed_job_utils, 'is_consolidation_mode',
                        lambda: False)
    _never_claimed(engine, 1, age=_OLD)

    assert _ids(stall.scan_never_claimed()) == set()
