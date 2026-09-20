"""The stall collector's two promises: per-phase isolation, and zero vs absent.

Both are claims about what happens when something goes wrong, so both are
tested by making it go wrong rather than by asserting the happy path.
"""
import dataclasses
import inspect
import time

import pytest

from sky.jobs import stall
from sky.server import metrics


def _scan(phase, tasks, **kwargs):
    kwargs.setdefault('truncated', False)
    return stall.StallScan(phase=phase, tasks=list(tasks), **kwargs)


def _task(job_id, *, age, workspace='ws'):
    # job_name deliberately unlike task_name: on a real deployment the two
    # differ for every multi-task job, and a helper that sets them equal hides
    # a collector reading the wrong one.
    return stall.StalledTask(spot_job_id=job_id,
                             task_id=0,
                             task_name=f'task-{job_id}',
                             job_name=f'job-{job_id}',
                             workspace=workspace,
                             priority=None,
                             stalled_since=time.time() - age)


def _families(collector):
    return {family.name: family for family in collector.collect()}


def _samples(family):
    """{label tuple: value} for one metric family."""
    return {
        tuple(sample.labels.values()): sample.value for sample in family.samples
    }


@pytest.fixture(name='scans')
def scans_fixture(monkeypatch):
    """Control what each phase's scan does, including raising."""
    behaviour = {}

    def _run(phase):
        result = behaviour[phase]
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(stall, 'scan_never_claimed',
                        lambda **kw: _run(stall.NEVER_CLAIMED))
    monkeypatch.setattr(stall, 'scan_unattended',
                        lambda **kw: _run(stall.UNATTENDED))
    behaviour[stall.NEVER_CLAIMED] = _scan(stall.NEVER_CLAIMED, [])
    behaviour[stall.UNATTENDED] = _scan(stall.UNATTENDED, [])
    return behaviour


def test_describe_declares_every_family_collect_emits(scans):
    """Registration must not run a database query to learn the families.

    ResilientCollector.describe() exists for that reason; a family declared in
    only one of the two places defeats it.
    """
    collector = metrics.ManagedJobsStallCollector()

    described = {(family.name, tuple(family._labelnames))
                 for family in collector.describe()}
    emitted = {(family.name, tuple(family._labelnames))
               for family in collector.collect()}

    # Labels too, not just names: a describe() that declared the wrong label
    # set would still register cleanly and then disagree with every sample.
    assert described == emitted


def test_a_phase_that_found_nothing_reports_zero_and_a_scan_time(scans):
    """Zero is a measurement and has to be stated.

    An absent series would otherwise mean both "nothing is stalled" and "the
    scan could not run", and no rule could tell them apart.
    """
    collector = metrics.ManagedJobsStallCollector()

    families = _families(collector)
    counts = _samples(families['sky_managed_jobs_stalled'])

    assert counts[(stall.NEVER_CLAIMED, '')] == 0
    assert counts[(stall.UNATTENDED, '')] == 0
    scans_seen = _samples(
        families['sky_managed_jobs_stall_scan_timestamp_seconds'])
    assert set(scans_seen) == {(stall.NEVER_CLAIMED,), (stall.UNATTENDED,)}


def test_a_phase_that_never_ran_reports_nothing_at_all(scans):
    """Not even a zero: a zero meaning "I could not look" is undetectable."""
    scans[stall.UNATTENDED] = RuntimeError('the requests db is unreadable')
    collector = metrics.ManagedJobsStallCollector()

    families = _families(collector)
    counts = _samples(families['sky_managed_jobs_stalled'])
    timestamps = _samples(
        families['sky_managed_jobs_stall_scan_timestamp_seconds'])

    assert (stall.UNATTENDED, '') not in counts
    assert (stall.UNATTENDED,) not in timestamps


@pytest.mark.parametrize('broken,intact',
                         [(stall.UNATTENDED, stall.NEVER_CLAIMED),
                          (stall.NEVER_CLAIMED, stall.UNATTENDED)])
def test_one_phase_failing_leaves_the_other_reporting(scans, broken, intact):
    """The isolation claim, which is the reason this collector exists.

    Both orders, and that is the whole point: with a single try around both
    scans, a failure in the *second* one still leaves the first's cache
    written, so testing only that direction passes on the very structure this
    collector exists to avoid. Only a failure in the first phase tells them
    apart.
    """
    scans[broken] = RuntimeError('boom')
    scans[intact] = _scan(intact, [_task(1, age=900)])
    collector = metrics.ManagedJobsStallCollector()

    counts = _samples(_families(collector)['sky_managed_jobs_stalled'])

    assert counts[(intact, 'ws')] == 1
    assert not any(phase == broken for phase, _ in counts)


def test_a_phase_that_stops_scanning_keeps_its_old_timestamp(scans):
    """What the staleness alert reads.

    The last good values keep being served -- dropping them would flap the
    alert on one transient failure -- but the timestamp does not advance, so
    the deployment can see that the phase stopped reporting.
    """
    collector = metrics.ManagedJobsStallCollector()
    first = _samples(
        _families(collector)['sky_managed_jobs_stall_scan_timestamp_seconds'])

    scans[stall.UNATTENDED] = RuntimeError('boom')
    collector._last_scrape_time = 0.0  # force a refresh
    second = _samples(
        _families(collector)['sky_managed_jobs_stall_scan_timestamp_seconds'])

    assert second[(stall.UNATTENDED,)] == first[(stall.UNATTENDED,)]
    assert second[(stall.NEVER_CLAIMED,)] > first[(stall.NEVER_CLAIMED,)]


def test_a_capped_scan_says_so_in_its_own_series(scans):
    """A count at the cap is a floor, and the floor has to be exported.

    Both arms: a scan that was not capped must say 0, or the series carries no
    information and a reader cannot tell a total from a floor.
    """
    scans[stall.NEVER_CLAIMED] = _scan(stall.NEVER_CLAIMED, [_task(1, age=900)],
                                       truncated=True)
    collector = metrics.ManagedJobsStallCollector()

    truncated = _samples(
        _families(collector)['sky_managed_jobs_stall_truncated'])

    assert truncated[(stall.NEVER_CLAIMED,)] == 1
    assert truncated[(stall.UNATTENDED,)] == 0


def test_counts_and_ages_are_per_workspace(scans):
    scans[stall.NEVER_CLAIMED] = _scan(stall.NEVER_CLAIMED, [
        _task(1, age=900, workspace='a'),
        _task(2, age=100, workspace='a'),
        _task(3, age=300, workspace='b'),
    ])
    collector = metrics.ManagedJobsStallCollector()

    families = _families(collector)
    counts = _samples(families['sky_managed_jobs_stalled'])
    ages = _samples(families['sky_managed_jobs_stall_seconds_max'])

    assert counts[(stall.NEVER_CLAIMED, 'a')] == 2
    assert counts[(stall.NEVER_CLAIMED, 'b')] == 1
    # The oldest in each workspace, not the oldest overall.
    assert ages[(stall.NEVER_CLAIMED, 'a')] == pytest.approx(900, abs=5)
    assert ages[(stall.NEVER_CLAIMED, 'b')] == pytest.approx(300, abs=5)


def test_a_task_with_no_workspace_is_not_mistaken_for_the_empty_scan(scans):
    """The empty-scan sentinel must be unreachable by a real row."""
    scans[stall.NEVER_CLAIMED] = _scan(stall.NEVER_CLAIMED,
                                       [_task(1, age=900, workspace=None)])
    collector = metrics.ManagedJobsStallCollector()

    counts = _samples(_families(collector)['sky_managed_jobs_stalled'])

    assert (stall.NEVER_CLAIMED, '') not in counts
    assert counts[(stall.NEVER_CLAIMED, metrics._NULL_WORKSPACE_LABEL)] == 1


# --- the collector has to reach the endpoint, not just the registry ---------


def _multiproc_registrations(monkeypatch, tmp_path):
    """Which collectors /metrics registers when multiprocess mode is on."""
    monkeypatch.setenv('PROMETHEUS_MULTIPROC_DIR', str(tmp_path))
    seen = []
    real = metrics.prom.CollectorRegistry.register

    def _record(self, collector):
        seen.append(collector)
        try:
            real(self, collector)
        except ValueError:
            pass

    monkeypatch.setattr(metrics.prom.CollectorRegistry, 'register', _record)
    monkeypatch.setattr(metrics, 'generate_latest', lambda registry=None: b'')
    metrics.metrics()
    return seen


def test_the_stall_collector_reaches_the_multiprocess_endpoint(
        monkeypatch, tmp_path, scans):
    """Registering globally is not enough, and nothing says so.

    In multiprocess mode -- which is the mode the server runs in -- /metrics
    builds its own registry and names each collector. A collector left out of
    that list is served in tests, absent in production, and the endpoint still
    answers 200 with fewer families.
    """
    monkeypatch.setattr(
        metrics, '_MANAGED_JOBS_STALL_COLLECTOR',
        metrics._wrap_collector(metrics.ManagedJobsStallCollector()))

    seen = _multiproc_registrations(monkeypatch, tmp_path)

    assert metrics._MANAGED_JOBS_STALL_COLLECTOR in seen


def test_no_wrapped_collector_is_left_out_of_the_multiprocess_endpoint(
        monkeypatch, tmp_path, scans):
    """The general form: the list is hand-kept, so check it against reality.

    Every collector that went through _wrap_collector is meant to be served.
    One that is not is invisible in exactly the deployment that matters.
    """
    monkeypatch.setattr(
        metrics, '_MANAGED_JOBS_STALL_COLLECTOR',
        metrics._wrap_collector(metrics.ManagedJobsStallCollector()))

    seen = _multiproc_registrations(monkeypatch, tmp_path)

    wrapped = {
        value for name, value in vars(metrics).items()
        if name.endswith('_COLLECTOR') and
        isinstance(value, metrics.ResilientCollector)
    }
    missing = {c.name for c in wrapped - set(seen)}

    assert not missing, f'wrapped but never served in multiproc mode: {missing}'


def test_the_collector_reads_every_field_the_scan_returns(
        monkeypatch, tmp_path, scans):
    """A field nothing consumes looks exercised because the tests assert on it.

    Three of this dataclass's fields were once read only by tests -- one of
    them costing a database query per scan to produce. Nothing failed: the
    collector worked, the tests passed, and the field was dead. So the wiring
    is asserted directly, and a field added without a consumer fails here
    rather than being noticed a release later.
    """
    read = set()

    class _Watched:

        def __init__(self, scan):
            object.__setattr__(self, '_scan', scan)

        def __getattr__(self, name):
            read.add(name)
            return getattr(object.__getattribute__(self, '_scan'), name)

    # Both paths: a field read only when there is something to report -- or
    # only when there is not -- would never be touched by one of them, and
    # the guard would pass on a field that is still unwired.
    for tasks in ([], [_task(1, age=900)]):
        monkeypatch.setattr(
            stall, 'scan_never_claimed',
            lambda **kw: _Watched(_scan(stall.NEVER_CLAIMED, tasks)))
        monkeypatch.setattr(
            stall, 'scan_unattended',
            lambda **kw: _Watched(_scan(stall.UNATTENDED, tasks)))
        collector = metrics.ManagedJobsStallCollector()
        collector._refresh()
        list(collector.collect())

    declared = {field.name for field in dataclasses.fields(stall.StallScan)}
    assert declared - read == set(), (
        f'StallScan fields the collector never reads: {declared - read}')


def test_a_refresh_worth_of_budget_fits_inside_the_refresh_interval():
    """The budget is picked from the interval and the number of scans.

    It cannot be derived in stall.py, which must not import the collector --
    so the relation is asserted here instead of assumed there. Add a third
    scan, or shorten the interval, and this fails rather than leaving a
    refresh that can overrun the next one with nothing saying so.
    """
    # Stated, not derived: _refresh runs both scans. If a third is added,
    # this number is the thing to change, and the assertion below is what
    # makes forgetting it visible rather than silent.
    scans_per_refresh = 2

    worst_case = scans_per_refresh * stall._SCAN_BUDGET_SECONDS

    assert worst_case < metrics._COLLECTOR_REFRESH_TTL_SECONDS, (
        f'{scans_per_refresh} scans at {stall._SCAN_BUDGET_SECONDS}s is '
        f'{worst_case}s against a {metrics._COLLECTOR_REFRESH_TTL_SECONDS}s '
        'refresh interval')
    # And well inside the horizon at which the collector reads as inactive,
    # which is the failure a reader would see rather than a slow scan.
    assert worst_case < metrics._COLLECTOR_MAX_STALENESS_SECONDS / 2
