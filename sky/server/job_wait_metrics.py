"""Seven-day task startup snapshots from retained managed-job events."""

import collections
import datetime
import re
import time
from typing import Any, Dict, Iterator, List, Mapping, Tuple

from prometheus_client.core import GaugeMetricFamily
import sqlalchemy as sa

from sky.jobs import state

_WINDOW = 7 * 86400
_BUCKETS = (10, 30, 60, 120, 300, 600, 900, 1800, 3600, 7200, 14400, 28800,
            86400, float('inf'))
_LABELS = ['workspace', 'user', 'cloud', 'gpu', 'team', 'project']
_PREFIX = 'sky_managed_job_'


def _rows(now: float) -> List[Any]:
    e, t, j = state.job_events_table.c, state.spot_table.c, state.job_info_table.c
    events = sa.select(
        e.spot_job_id,
        e.task_id,
        sa.func.min(
            sa.case((sa.and_(e.new_status == 'PENDING',
                             e.reason == 'Job submitted to queue'),
                     e.timestamp))).label('queued'),
        sa.func.min(sa.case(
            (e.new_status == 'STARTING', e.timestamp))).label('accepted'),
        sa.func.min(sa.case(
            (e.new_status == 'RUNNING', e.timestamp))).label('running'),
        sa.func.max(e.timestamp).label('last_event'),
    ).where(e.task_id.is_not(None)).group_by(e.spot_job_id,
                                             e.task_id).subquery()
    cutoff = now - _WINDOW
    query = sa.select(
        t.spot_job_id,
        t.task_id,
        t.status,
        t.start_at,
        t.end_at,
        t.full_resources,
        t.resources,
        j.workspace,
        j.user_hash,
        j.cloud,
        events.c.queued,
        events.c.accepted,
        events.c.running,
    ).select_from(
        state.spot_table.outerjoin(
            state.job_info_table, t.spot_job_id == j.spot_job_id).outerjoin(
                events,
                sa.and_(t.spot_job_id == events.c.spot_job_id,
                        t.task_id == events.c.task_id))
    ).where(
        sa.or_(
            t.submitted_at >= cutoff, t.end_at >= cutoff,
            t.status.not_in(
                [s.value for s in state.ManagedJobStatus.terminal_statuses()]),
            events.c.last_event >= datetime.datetime.fromtimestamp(
                cutoff, datetime.timezone.utc)))
    with state._db_manager.get_engine().connect() as conn:
        return list(conn.execute(query).mappings())


def _timestamp(value: datetime.datetime) -> float:
    # SQLite returns naive UTC datetimes; the exporter host need not use UTC.
    if value.tzinfo is None:
        value = value.replace(tzinfo=datetime.timezone.utc)
    return value.timestamp()


class JobWaitCollector:
    """Expose rolling gauges, not counters; never apply rate() to the buckets.

    The cohort is tasks first submitted within seven days. Current waiting
    gauges include older tasks. Missing retained events are reported separately.
    Register through the server's resilient collector wrapper to bound DB work.
    """

    def describe(self) -> Iterator[GaugeMetricFamily]:
        for name, help_text, extra in [
            ('wait_7d_tasks', 'Tasks first submitted in the last seven days.',
             ['outcome']),
            ('wait_7d_seconds_bucket',
             'Cumulative buckets of seven-day waits; rolling gauges, not counters.',
             ['outcome', 'phase', 'le']),
            ('wait_7d_seconds_count', 'Seven-day wait observations.',
             ['outcome', 'phase']),
            ('wait_7d_seconds_sum',
             'Sum of seven-day wait observations in seconds.',
             ['outcome', 'phase']),
            ('waiting_tasks', 'Current tasks that have never started.', []),
            ('oldest_wait_seconds',
             'Oldest known current wait, including older tasks.', []),
            ('wait_missing_tasks',
             'Recent or active tasks with missing or invalid timing data.',
             ['reason']),
            ('wait_outlier_seconds',
             'Twenty longest known waits, from the seven-day cohort or active tasks.',
             ['job_id', 'task_id', 'outcome']),
        ]:
            yield GaugeMetricFamily(_PREFIX + name,
                                    help_text,
                                    labels=_LABELS + extra)
        yield GaugeMetricFamily(_PREFIX + 'wait_snapshot_timestamp_seconds',
                                'Time of the last successful wait snapshot.')

    def collect(self) -> Iterator[GaugeMetricFamily]:
        now = time.time()
        families = {m.name: m for m in self.describe()}
        counts: Dict[Tuple[str, ...], int] = collections.Counter()
        waiting: Dict[Tuple[str, ...], int] = collections.Counter()
        missing: Dict[Tuple[str, ...], int] = collections.Counter()
        oldest: Dict[Tuple[str, ...], float] = {}
        durations: Dict[Tuple[str, ...],
                        List[float]] = collections.defaultdict(list)
        outliers = []
        for row in _rows(now):
            resources = row['full_resources'] or {}
            labels = resources.get('labels') or {}
            accelerators = resources.get('accelerators')
            gpu = ','.join(sorted(accelerators)) if accelerators else ','.join(
                sorted(
                    set(re.findall(r'([\w.-]+):[\d.]+', row['resources'] or
                                   '')))) or 'unknown'
            key: Tuple[str, ...] = (row['workspace'] or
                                    'default', row['user_hash'] or
                                    'unknown', row['cloud'] or 'unknown', gpu,
                                    labels.get('team', 'unknown'),
                                    labels.get('project', 'unknown'))
            queued = _timestamp(row['queued']) if row['queued'] else None
            running = _timestamp(row['running']) if row['running'] else None
            terminal = state.ManagedJobStatus(row['status']).is_terminal()
            previously_started = (running is not None or
                                  row['start_at'] is not None or
                                  row['status'] in ('RUNNING', 'SUCCEEDED'))
            if not terminal and not previously_started:
                waiting[key] += 1
            if queued is None:
                missing[key + ('submission',)] += 1
                continue
            recent = queued >= now - _WINDOW
            if not recent and (terminal or previously_started):
                continue
            if queued > now or (running is not None and
                                (running < queued or running > now)):
                missing[key + ('invalid_timestamps',)] += 1
                continue
            if running is not None:
                outcome, end = 'started', running
            elif previously_started:
                missing[key + ('start',)] += 1
                continue
            elif terminal:
                outcome = ('cancelled_before_start' if row['status']
                           == 'CANCELLED' else 'failed_before_start')
                end = row['end_at']
            else:
                outcome, end = 'waiting', now
                oldest[key] = max(oldest.get(key, 0), now - queued)
            if recent:
                counts[key + (outcome,)] += 1
            if end is None:
                missing[key + ('end',)] += 1
                continue
            if end < queued or end > now:
                missing[key + ('invalid_timestamps',)] += 1
                continue
            elapsed = end - queued
            outliers.append(
                (elapsed, row['spot_job_id'], row['task_id'], key, outcome))
            if outcome == 'waiting':
                continue
            durations[key + (outcome, 'total')].append(elapsed)
            if running is not None:
                accepted = _timestamp(
                    row['accepted']) if row['accepted'] else None
                if accepted is not None and queued <= accepted <= running:
                    durations[key + (outcome, 'controller')].append(accepted -
                                                                    queued)
                    durations[key + (outcome, 'launch')].append(running -
                                                                accepted)
                else:
                    missing[key + ('acceptance',)] += 1

        grouped: List[Tuple[str,
                            Mapping[Tuple[str, ...],
                                    float]]] = [('wait_7d_tasks', counts),
                                                ('waiting_tasks', waiting),
                                                ('oldest_wait_seconds', oldest),
                                                ('wait_missing_tasks', missing)]
        for name, data in grouped:
            for key, value in data.items():
                families[_PREFIX + name].add_metric(list(key), value)
        for key, observations in durations.items():
            for bound in _BUCKETS:
                le = '+Inf' if bound == float('inf') else str(bound)
                families[_PREFIX + 'wait_7d_seconds_bucket'].add_metric(
                    list(key) + [le], sum(v <= bound for v in observations))
            families[_PREFIX + 'wait_7d_seconds_count'].add_metric(
                list(key), len(observations))
            families[_PREFIX + 'wait_7d_seconds_sum'].add_metric(
                list(key), sum(observations))
        for elapsed, job_id, task_id, key, outcome in sorted(outliers,
                                                             reverse=True)[:20]:
            families[_PREFIX + 'wait_outlier_seconds'].add_metric(
                list(key) + [str(job_id), str(task_id), outcome], elapsed)
        families[_PREFIX + 'wait_snapshot_timestamp_seconds'].add_metric([],
                                                                         now)
        yield from families.values()
