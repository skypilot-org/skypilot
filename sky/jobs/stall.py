"""Managed-job tasks that stopped making progress before they ever ran.

Two phases, each a positive statement about SkyPilot's own scheduler rather
than a classification of somebody else's error text -- which is what lets them
run without an allow-list of errors to report on:

``never_claimed``
    Nothing has picked the task up at all. It has never been provisioned, so
    the wait cannot be a resource wait: a resource wait needs somebody already
    asking for the resource.

``unattended``
    Something claimed the task and then stopped driving its launch.

This module answers only *which tasks are in a phase*. What is done about them
-- a metric, an event, a notification -- belongs to the caller, and there is
more than one caller: the API server's stall collector, and an out-of-tree
poller that turns the same answer into events for deployments that do not
scrape. They have to agree, which is why the predicate lives here once instead
of being written out in each of them.

The two callers deliberately differ on one thing, and it is not in here: what a
*failed* scan means. A gauge can report "not measured" -- the phase exports no
series and a staleness alert says so. The event path has no such channel, so it
suppresses instead. That is a try/except at each call site, because the policy
belongs to whoever owns the reporting channel; these functions just raise.

The two scans are separate entry points on purpose. They tick independently,
read different stores, and must be able to fail independently: a raise in the
priority lookup must not take the claimed half down with it. There is
deliberately no wrapper that runs both -- it would be one line, and its first
use would undo that.
"""
import contextlib
import dataclasses
import math
import os
import time
from typing import Any, Dict, Iterator, List, Optional, Set, Tuple

import sqlalchemy

from sky import global_user_state
from sky import sky_logging
from sky.jobs import state as managed_job_state
from sky.jobs import utils as managed_job_utils
from sky.server.requests import requests as api_requests
from sky.skylet import constants
from sky.utils import controller_utils

logger = sky_logging.init_logger(__name__)

# This module has a consumer outside this repository: a plugin's poller turns
# the same two scans into events for deployments that do not scrape. The two
# scans, their result types and the threshold helpers are an interface --
# renaming or narrowing one breaks a caller that does not appear in any search
# of this repo. Everything prefixed `_` is private.

NEVER_CLAIMED = 'never_claimed'
UNATTENDED = 'unattended'

# Candidates a scan will look at in one pass. Deliberately generous: most
# candidates are suppressed rather than reported, so a small cap would keep
# returning the same oldest few and a genuine stall behind them would never be
# examined. Truncation is reported rather than hidden.
DEFAULT_CANDIDATE_LIMIT = 2000
# Candidates the claimed scan will pay a per-task cross-store lookup for. Only
# that half has one.
DEFAULT_EXAMINATION_LIMIT = 200

# A task in the launch retry loop writes a job_events row every round --
# `set_backoff_pending_async` going into backoff, `set_restarting_async` coming
# out. A wedged one writes none. The loop's backoff caps at five times its 60s
# base, so this window clears a sleeping round several times over while still
# being shorter than the unattended threshold itself.
_RETRY_ACTIVITY_SECONDS = 900

# Budget for ALL the managed-jobs statements one scan runs, not a per-statement
# timeout. A thread parked in a DB driver cannot be killed, so the database has
# to be the thing that gives up -- but a per-statement bound multiplies: the
# never-claimed scan runs up to three statements, the unattended scan two, and
# the collector runs both, so at a per-statement 25s that would be 125s against
# a 30s refresh
# interval and a 90s staleness horizon. Each statement instead gets whatever is
# left of the budget, so the total holds however many statements there come
# to be.
#
# Two scans per refresh, so a refresh's worst case is twice this, still inside
# the interval. Measured cost is 0.2 ms and 14.6 ms on a production-sized table,
# so the budget is three orders of magnitude of headroom, not a tuning knob.
#
# It still does not bound a whole scan: the per-task attempt reads and the
# request lookup go to other stores and have no bound of their own. What stops
# refreshes from stacking is the caller holding one in flight, not this.
_SCAN_BUDGET_SECONDS = 12
# Never issue a timeout below this: a budget that has run out should fail the
# statement outright rather than ask the database for something unservable.
_MIN_STATEMENT_TIMEOUT_SECONDS = 1

# Nothing in the job may be in one of these for the never-claimed test to fire:
# a pipeline whose earlier task is still running has not stalled, its later
# tasks are simply not eligible yet.
# WINDING_DOWN is here for the same reason as the rest: a job group whose
# coordinator is merging output is progressing. Today no task of such a job can
# also be in the never-claimed shape -- a group's tasks are all claimed at once,
# so they have submitted_at -- but that is an argument two modules away, and
# this list should not depend on it.
_PROGRESS_STATUSES = ('STARTING', 'RUNNING', 'WINDING_DOWN', 'RECOVERING',
                      'CANCELLING')
# Schedule states in which a batch job occupies its pool, mirroring the
# scheduler's own definition.
_ACTIVE_BATCH_STATES = ('LAUNCHING', 'ALIVE', 'ALIVE_WAITING', 'ALIVE_BACKOFF')


@dataclasses.dataclass(frozen=True)
class StalledTask:
    """One task a scan is reporting; the phase is on the scan."""
    spot_job_id: int
    task_id: int
    task_name: Optional[str]
    # Not read in this repo. It is in the contract because the event path
    # names the job in what it sends, and a scan already has it in hand --
    # rediscovering it there would be a second query for a row this one
    # already selected. Removed once as unread, which made every event carry
    # a null name until review caught it.
    job_name: Optional[str]
    workspace: Optional[str]
    priority: Optional[int]
    # Epoch seconds: when this task started waiting. eligible_at for the
    # never-claimed phase, submitted_at for the claimed one.
    stalled_since: float


@dataclasses.dataclass(frozen=True)
class StallScan:
    """What one scan found.

    Carries `phase` even though each entry point returns one value, so a
    caller can label from the result rather than from which function it
    happened to call -- one less place for the two names to drift.
    """
    phase: str
    # Oldest first.
    tasks: List[StalledTask]
    # A limit was hit, so `tasks` is a floor rather than a total. The two
    # causes -- more candidates than the query returns, more than the per-task
    # pass reaches -- are folded together because no consumer separates them.
    truncated: bool


def _threshold_seconds(env_var: str, default: float) -> float:
    """A phase's age threshold, from the environment or the default.

    Read on each call: one environment lookup against a table scan is nothing,
    and it lets a deployment change the value by restarting rather than by
    waiting for a release. Raises rather than falling back on a bad value --
    this feeds an alert, and a threshold silently reverting to the default is
    a wrong answer that looks like a right one. The raise reaches the caller as
    a failed scan, which is reported as *not measured*.
    """
    raw = os.environ.get(env_var)
    if raw is None:
        return default
    try:
        value = float(raw)
    except (TypeError, ValueError):
        raise ValueError(
            f'{env_var}={raw!r} is not a number of seconds.') from None
    if not math.isfinite(value) or value <= 0:
        raise ValueError(
            f'{env_var}={raw!r} must be a positive, finite number of seconds.')
    return value


def never_claimed_seconds() -> float:
    """How long a task may be eligible with nothing claiming it."""
    return _threshold_seconds(
        constants.ENV_VAR_MANAGED_JOBS_NEVER_CLAIMED_SECONDS,
        constants.DEFAULT_MANAGED_JOBS_NEVER_CLAIMED_SECONDS)


def unattended_seconds() -> float:
    """How long a claimed task may sit with nothing driving its launch."""
    return _threshold_seconds(constants.ENV_VAR_MANAGED_JOBS_UNATTENDED_SECONDS,
                              constants.DEFAULT_MANAGED_JOBS_UNATTENDED_SECONDS)


_NEVER_CLAIMED_SELECT = """
SELECT * FROM (
    SELECT
        s.spot_job_id AS spot_job_id,
        s.task_id AS task_id,
        s.task_name AS task_name,
        s.eligible_at AS stalled_since,
        ji.name AS job_name,
        ji.workspace AS workspace,
        ji.priority AS priority
    FROM spot s
    LEFT JOIN job_info ji ON ji.spot_job_id = s.spot_job_id
    WHERE s.status = 'PENDING'
      AND s.submitted_at IS NULL
      -- Belt and braces: a NULL eligible_at already fails the comparison
      -- below. Kept because it states what the row has to have, and because
      -- it is the clause a reader looks for when asking whether pre-timeline
      -- rows can match. They cannot, ever -- see the backfill note in the
      -- migration history.
      AND s.eligible_at IS NOT NULL
      AND s.eligible_at <= {now} - {age_seconds}
      AND NOT EXISTS (
          SELECT 1 FROM spot p
          WHERE p.spot_job_id = s.spot_job_id
            AND p.status IN ({progress_statuses}))
      AND (
          ji.is_batch IS NOT TRUE
          OR ji.pool IS NULL
          OR NOT EXISTS (
              SELECT 1 FROM job_info b
              WHERE b.pool = ji.pool
                -- Not itself. Borrowed from the scheduler's own subquery,
                -- where the candidate is WAITING and so cannot match; here it
                -- can be LAUNCHING -- claimed but wedged before submitted_at
                -- is written, which is precisely a never-claimed target -- and
                -- would suppress its own report.
                AND b.spot_job_id <> ji.spot_job_id
                AND b.is_batch IS TRUE
                AND b.schedule_state IN ({active_batch_states})))
) stalled
ORDER BY stalled_since ASC
LIMIT {candidate_limit}
"""

# `claimed_in_flight` is state.CLAIMED_IN_FLIGHT_PREDICATE, the same string the
# partial index that serves this query is built from. Interpolated rather than
# written out so the two cannot drift: an index whose predicate no longer
# covers its query is silently not used, and nothing fails. Unqualified column
# names are safe here -- job_info has none of submitted_at, start_at, end_at.
_UNATTENDED_SELECT = """
SELECT * FROM (
    SELECT
        s.spot_job_id AS spot_job_id,
        s.task_id AS task_id,
        s.task_name AS task_name,
        s.submitted_at AS stalled_since,
        ji.name AS job_name,
        ji.workspace AS workspace,
        ji.priority AS priority
    FROM spot s
    LEFT JOIN job_info ji ON ji.spot_job_id = s.spot_job_id
    WHERE {claimed_in_flight}
      AND s.status <> 'CANCELLING'
      AND ji.pool IS NULL
      AND s.submitted_at <= {now} - {age_seconds}
) stalled
ORDER BY stalled_since ASC
LIMIT {candidate_limit}
"""


def _now_expr(engine: sqlalchemy.engine.Engine) -> str:
    """The database's own clock, as epoch seconds.

    Asked of the database rather than passed in, so the comparison cannot be
    skewed by a caller whose clock differs from the one that wrote the rows.
    """
    if engine.dialect.name == 'postgresql':
        return 'EXTRACT(EPOCH FROM NOW())'
    return 'CAST(strftime(\'%s\', \'now\') AS FLOAT)'


def _quoted(values: Tuple[str, ...]) -> str:
    return ','.join(f'\'{value}\'' for value in values)


def _remaining_ms(deadline: float, now: Optional[float] = None) -> int:
    """What is left of a scan's budget, as whole milliseconds.

    Floored rather than allowed to reach zero: Postgres reads a
    `statement_timeout` of 0 as *disabled*, so a rounded-away budget would
    remove the bound instead of enforcing it.
    """
    if now is None:
        now = time.monotonic()
    left = max(_MIN_STATEMENT_TIMEOUT_SECONDS, deadline - now)
    return int(left * 1000)


@contextlib.contextmanager
def _bounded(engine: sqlalchemy.engine.Engine,
             deadline: float) -> Iterator[Any]:
    """A connection whose statements the database will end on its own.

    See `_STATEMENT_TIMEOUT_SECONDS`: nothing above this can interrupt a query
    once it is running, so the bound has to be set on the database. Postgres
    only -- SQLite has no statement timeout, and the risk there is different: a
    local file with no other writer contending for it.
    """
    with engine.connect() as conn:
        if engine.dialect.name != 'postgresql':
            yield conn
            return
        with conn.begin():
            # SET does not take bind parameters; the value is an int derived
            # from a constant and the clock, never from input.
            timeout_ms = _remaining_ms(deadline)
            conn.execute(
                sqlalchemy.text(
                    f'SET LOCAL statement_timeout = \'{timeout_ms}ms\''))
            yield conn


def _rows(engine: sqlalchemy.engine.Engine, sql: str,
          deadline: float) -> List[Dict[str, Any]]:
    with _bounded(engine, deadline) as conn:
        result = conn.execute(sqlalchemy.text(sql))
        return [dict(row) for row in result.mappings()]


def _task(row: Dict[str, Any]) -> StalledTask:
    return StalledTask(
        spot_job_id=int(row['spot_job_id']),
        task_id=int(row['task_id']),
        task_name=row.get('task_name'),
        job_name=row.get('job_name'),
        workspace=row.get('workspace'),
        priority=row.get('priority'),
        stalled_since=float(row.get('stalled_since') or 0.0),
    )


def _cluster_name(task: StalledTask) -> Optional[str]:
    """The SkyPilot-side cluster name for a task.

    It has to stay the SkyPilot-side one: the reader it feeds matches only
    `cluster_name`, never `cluster_name_on_cloud`, so a cloud-side name would
    come back with no attempts -- which this scan reads as nobody being on it.
    A wrong name here drifts toward false positives without erroring.
    """
    if task.task_name is None:
        return None
    return managed_job_utils.generate_managed_job_cluster_name(
        task.task_name, task.spot_job_id)


def _clusters_with_live_requests(cluster_names: List[str]) -> Set[str]:
    """Which of these clusters have a request queued, parked or executing.

    One batched query rather than one per candidate: a fleet-wide stall is
    exactly when this scan has the most rows and the API server the least to
    spare. A parked launch -- queue admission, a cluster lock, pod-group
    resolution -- is a request in WAITING, which is what makes this the primary
    suppressor: it covers every park without knowing which kind it is.
    """
    if not cluster_names:
        return set()
    tasks = api_requests.get_request_tasks(
        api_requests.RequestTaskFilter(
            status=[
                api_requests.RequestStatus.PENDING,
                api_requests.RequestStatus.WAITING,
                api_requests.RequestStatus.RUNNING,
            ],
            cluster_names=list(cluster_names),
            # One column, deliberately. Without this every matching request is
            # fully decoded, which unpickles its request body -- and that
            # decode re-raises on a body this server cannot unpickle, so one
            # request left by another version would take the whole phase to
            # "not measured" during exactly the rollout worth watching.
            fields=[api_requests.COL_CLUSTER_NAME]))
    return {task.cluster_name for task in tasks if task.cluster_name}


def _attempt_is_working(attempt: Any, now: float, age_seconds: float) -> bool:
    """Whether an open launch attempt means somebody is still on it.

    An attempt that has asked the cloud for instances is waiting on something
    real, however long that takes -- a queue-gated workload is legitimately
    open for hours, which is why the abandoned sweep is set to a day.

    The age check is not protecting a launch in flight. Reaching
    `instances_requested` takes about a second (0.75s mean, 2.04s max over 434
    real attempts), and the launch holds a live request for all of it, which
    suppresses earlier and on firmer evidence. What it actually holds quiet is
    an orphan -- an attempt whose launcher died before asking for anything --
    until it is as old as the threshold.
    """
    if attempt.instances_requested is not None:
        return True
    started = attempt.provision_start
    if started is None:
        return True
    return (now - started) <= age_seconds


def _highest_blocking_priority(engine: sqlalchemy.engine.Engine,
                               deadline: float) -> int:
    """The priority a job must reach to be next in line.

    Read from the scheduler rather than recomputed: it is the same quantity
    the jobs list renders "Waiting for higher priority jobs to launch" from, so
    this and the UI agree by construction instead of by coincidence. Never
    None -- with nothing contending it is MIN_PRIORITY, which suppresses
    nothing. A failure here raises rather than being swallowed: this scan feeds
    an alert, and the caller reports a scan that could not run as *not
    measured* instead of as zero.
    """
    with _bounded(engine, deadline) as conn:
        return managed_job_state.get_managed_jobs_highest_priority(conn)


def _claim_gates(engine: sqlalchemy.engine.Engine,
                 deadline: float) -> Tuple[bool, str]:
    """Whether every controller process is already blocked from claiming.

    A queue behind a full pool is not a stall. `controller.py`'s admission loop
    gates on launches in flight and on jobs held, either of which stops a
    process before it claims; both of its sets are keyed by job, not by task.

    Summing across processes is exact per gate: none can exceed its own limit,
    so a sum at N x limit means all are at it. Pool jobs are counted -- they
    hold a slot -- which leaves a gap, since a pool job wedged before it starts
    holds the gate shut and the unattended phase excludes pool jobs.

    `controllers` is the nominal count: `get_alive_controllers()` reads a
    replica-local pid file and returns 0 where it is absent, which would hold
    the gate shut everywhere else. It is sized from the calling process, so off
    consolidation mode it describes the wrong machine and the phase is
    suppressed instead -- which is what the metrics path does there anyway.
    """
    if not managed_job_utils.is_consolidation_mode():
        return True, ('not consolidation mode, so the controller pool is '
                      'not this process to size')
    controllers = max(controller_utils.get_number_of_jobs_controllers(), 1)
    launch_capacity = controllers * controller_utils.LAUNCHES_PER_WORKER
    hold_capacity = controllers * min(
        controller_utils.MAX_JOBS_PER_WORKER,
        controller_utils.MAX_TOTAL_RUNNING_JOBS // controllers)
    # One statement so the two counts share an instant. The WHERE is the
    # predicate its partial index is built from.
    counts = sqlalchemy.text(
        'SELECT COUNT(DISTINCT CASE WHEN start_at IS NULL '
        'THEN spot_job_id END) AS launching, '
        'COUNT(DISTINCT spot_job_id) AS held '
        f'FROM spot WHERE {managed_job_state.CLAIMED_LIVE_PREDICATE}')
    with _bounded(engine, deadline) as conn:
        row = conn.execute(counts).one()
    launching, held = int(row[0] or 0), int(row[1] or 0)
    why = ''
    if launching >= launch_capacity:
        why = (f'all {launch_capacity} launch slots busy '
               f'({launching} launching)')
    elif held >= hold_capacity:
        why = f'all {hold_capacity} job slots held ({held} held)'
    return bool(why), why


def _starved(task: StalledTask, highest: int) -> bool:
    """Waiting behind higher priority is not a stall.

    `highest` is the max over jobs contending for a slot, so the queue head
    always has priority == highest and is never suppressed -- an incident still
    surfaces through it. What this hides is a lower-priority job that is
    individually broken while the scheduler is healthy; accepted, because the
    alternative is a standing report per deprioritised job. Only meaningful for
    the never-claimed phase: a claimed task has already won its slot.
    """
    return task.priority is not None and task.priority < highest


def _tasks_active_recently(engine: sqlalchemy.engine.Engine, job_ids: List[int],
                           deadline: float) -> Set[Tuple[int, int]]:
    """Which (job, task) pairs wrote a job_events row inside the window.

    Separates a retry that is coming from one that never came: launch_attempts
    carries no close timestamp, so only the task's own activity distinguishes
    them. Stays in job_events' naive-local clock, which is what that column is
    written in.
    """
    if not job_ids:
        return set()
    if engine.dialect.name == 'postgresql':
        cutoff = (f'LOCALTIMESTAMP - INTERVAL \'{_RETRY_ACTIVITY_SECONDS} '
                  'seconds\'')
    else:
        cutoff = ('datetime(\'now\', \'localtime\', '
                  f'\'-{_RETRY_ACTIVITY_SECONDS} seconds\')')
    ids = ','.join(str(int(job_id)) for job_id in job_ids)
    sql = (f'SELECT DISTINCT spot_job_id, task_id FROM job_events '
           f'WHERE timestamp >= {cutoff} AND spot_job_id IN ({ids})')
    with _bounded(engine, deadline) as conn:
        return {(row[0], row[1]) for row in conn.execute(sqlalchemy.text(sql))}


def _still_stalled(task: StalledTask, now: float, age_seconds: float,
                   busy_clusters: Set[str], active: Set[Tuple[int,
                                                              int]]) -> bool:
    """Narrow a claimed candidate with what only the other stores can say.

    Only the *latest* attempt is consulted, and it is the current round by
    construction: `get_launch_attempts_for_cluster` orders by attempt_seq,
    which is monotonic within the cluster name. Judging older ones is wrong in
    both directions -- submitted_at never moves after the first claim, so every
    attempt looks current, and one success in the history would suppress the
    task for good.
    """
    cluster_name = _cluster_name(task)
    if cluster_name is None:
        # No task name to derive the cluster from, so neither test below can
        # run. A row that cannot be checked is not evidence of a stall.
        return False
    if cluster_name in busy_clusters:
        return False

    attempts = global_user_state.get_launch_attempts_for_cluster(cluster_name)
    if attempts:
        latest = attempts[-1]
        if latest.outcome is None:
            return not _attempt_is_working(latest, now, age_seconds)
        if latest.outcome == 'succeeded':
            # The cluster came up and the job has not started: a job group's
            # barrier, or runtime setup. Not "nobody is on it".
            return False
    # Nothing provisioned, or the last attempt closed failed: either way
    # launch_attempts cannot say whether a retry is coming, and a loop rejected
    # before provisioning leaves no row at all -- so "no attempts" does not
    # mean nobody is trying. The task's own activity answers it.
    return (task.spot_job_id, task.task_id) not in active


def _scan(phase: str, tasks: List[StalledTask], *,
          truncated: bool) -> StallScan:
    """Assemble a scan result, oldest first."""
    return StallScan(phase=phase,
                     tasks=sorted(tasks, key=lambda task: task.stalled_since),
                     truncated=truncated)


def scan_never_claimed(
        *,
        age_seconds: Optional[float] = None,
        candidate_limit: int = DEFAULT_CANDIDATE_LIMIT) -> StallScan:
    """Tasks that have been eligible to start with nothing claiming them.

    Reads the managed-jobs database and nothing else: the query has already
    established that nothing was provisioned, so there is no cluster, request
    or attempt to consult.
    """
    if age_seconds is None:
        age_seconds = never_claimed_seconds()
    deadline = time.monotonic() + _SCAN_BUDGET_SECONDS
    engine = managed_job_state.get_engine()
    blocked, why = _claim_gates(engine, deadline)
    if blocked:
        # A queue, not a stall. Checked before the candidate query so a
        # saturated deployment costs one statement rather than three. Hides a
        # task individually stuck while the pool is full, on the same terms as
        # `_starved`.
        logger.debug(f'stall: {why}, so never-claimed tasks are queued '
                     'rather than stalled')
        return _scan(NEVER_CLAIMED, [], truncated=False)
    sql = _NEVER_CLAIMED_SELECT.format(
        now=_now_expr(engine),
        # Coerced, not trusted: these reach the statement by interpolation.
        age_seconds=float(age_seconds),
        progress_statuses=_quoted(_PROGRESS_STATUSES),
        active_batch_states=_quoted(_ACTIVE_BATCH_STATES),
        candidate_limit=int(candidate_limit),
    )
    rows = _rows(engine, sql, deadline)
    highest = _highest_blocking_priority(engine, deadline)
    tasks = [_task(row) for row in rows]
    return _scan(NEVER_CLAIMED,
                 [task for task in tasks if not _starved(task, highest)],
                 truncated=len(rows) >= candidate_limit)


def scan_unattended(
        *,
        now: Optional[float] = None,
        age_seconds: Optional[float] = None,
        candidate_limit: int = DEFAULT_CANDIDATE_LIMIT,
        examination_limit: int = DEFAULT_EXAMINATION_LIMIT) -> StallScan:
    """Tasks something claimed and then stopped driving.

    Joins three stores in Python -- the managed-jobs database, the cluster
    state database and the requests database -- because they cannot be joined
    in SQL on any deployment.
    """
    if age_seconds is None:
        age_seconds = unattended_seconds()
    if now is None:
        now = time.time()
    deadline = time.monotonic() + _SCAN_BUDGET_SECONDS
    engine = managed_job_state.get_engine()
    sql = _UNATTENDED_SELECT.format(
        claimed_in_flight=managed_job_state.CLAIMED_IN_FLIGHT_PREDICATE,
        now=_now_expr(engine),
        age_seconds=float(age_seconds),
        candidate_limit=int(candidate_limit),
    )
    rows = _rows(engine, sql, deadline)
    candidates = [_task(row) for row in rows]
    examined = candidates[:examination_limit]
    # Both resolved for the whole batch before the per-task pass, for the
    # reason in _clusters_with_live_requests.
    busy_clusters = _clusters_with_live_requests([
        name for name in (_cluster_name(task) for task in examined)
        if name is not None
    ])
    active = _tasks_active_recently(engine,
                                    [task.spot_job_id for task in examined],
                                    deadline)
    unexamined = len(candidates) - len(examined)
    return _scan(UNATTENDED, [
        task for task in examined
        if _still_stalled(task, now, age_seconds, busy_clusters, active)
    ],
                 truncated=unexamined > 0 or len(rows) >= candidate_limit)
