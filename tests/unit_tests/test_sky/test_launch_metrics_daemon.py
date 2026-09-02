"""The daemon that turns finished launch attempts into phase metrics."""
import types
from unittest import mock

from sky.metrics import utils as metrics_lib
from sky.server import daemons


def _attempt(attempt_id='a1'):
    return types.SimpleNamespace(attempt_id=attempt_id,
                                 provision_start=100.0,
                                 instances_requested=110.0,
                                 admitted=None,
                                 instances_ready=160.0,
                                 outcome='succeeded',
                                 workspace='eng')


def test_daemon_is_skipped_when_metrics_are_disabled(monkeypatch):
    """Claiming marks rows observed, so it must not happen with metrics off.

    Otherwise the attempts are consumed silently and turning metrics on later
    starts from a permanent hole.
    """
    monkeypatch.setenv('PROMETHEUS_MULTIPROC_DIR', '/tmp/metrics')
    monkeypatch.setattr(metrics_lib, 'METRICS_ENABLED', False)
    assert daemons.should_skip_launch_metrics() is True

    monkeypatch.setattr(metrics_lib, 'METRICS_ENABLED', True)
    assert daemons.should_skip_launch_metrics() is False


def test_daemon_is_skipped_when_its_output_would_be_invisible(monkeypatch):
    """This daemon has its own process, so without multiprocess mode nothing
    it observes reaches /metrics.

    Found by running it for real: metrics were on, the daemon claimed every
    attempt and logged success, and no series ever appeared -- because
    in-process metrics kept working, which is what makes this easy to miss.
    Claiming in that state burns the attempts for good.
    """
    monkeypatch.setattr(metrics_lib, 'METRICS_ENABLED', True)
    monkeypatch.delenv('PROMETHEUS_MULTIPROC_DIR', raising=False)

    assert daemons.should_skip_launch_metrics() is True


def test_one_bad_row_does_not_block_the_others(monkeypatch):
    """A row that fails to observe must not stall the sweep.

    It stays claimed too, so a poison record cannot make the daemon spin on it
    forever.
    """
    monkeypatch.setattr(
        daemons.global_user_state, 'claim_unobserved_launch_attempts',
        lambda: [_attempt('bad'), _attempt('good')])
    monkeypatch.setattr(daemons.time, 'sleep', lambda _: None)

    observed = []

    def _observe(row):
        if row.attempt_id == 'bad':
            raise ValueError('malformed row')
        observed.append(row.attempt_id)

    with mock.patch('sky.metrics.launch_phases.observe_attempt', _observe):
        daemons.launch_metrics_event()

    assert observed == ['good']


def test_the_timeline_query_carries_the_pool(tmp_path, monkeypatch):
    """A pool job skips provisioning, so its path must be distinguishable.

    The query decides that: selecting only the workspace made every job report
    path=provision, so a pool job's missing provisioning phases read the same
    as lost data. Caught by running a real pool job, not by a unit test.
    """
    import sqlalchemy

    from sky.jobs import state as managed_job_state
    from sky.skylet import constants as skylet_constants
    from sky.utils.db import db_utils

    monkeypatch.setenv(skylet_constants.SKY_RUNTIME_DIR_ENV_VAR_KEY,
                       str(tmp_path))
    monkeypatch.setattr(
        managed_job_state, '_db_manager',
        db_utils.DatabaseManager('spot_jobs', managed_job_state.create_table))

    engine = managed_job_state._db_manager.get_engine()
    with sqlalchemy.orm.Session(engine) as session:
        session.execute(managed_job_state.spot_table.insert().values(
            spot_job_id=1,
            task_id=0,
            task_name='t',
            status='RUNNING',
            created_at=100.0,
            submitted_at=110.0,
            start_at=200.0))
        session.execute(managed_job_state.job_info_table.insert().values(
            spot_job_id=1, workspace='eng', pool='warm-pool'))
        session.commit()

    rows = managed_job_state.get_jobs_pending_launch_timeline()

    assert len(rows) == 1
    assert rows[0]['pool'] == 'warm-pool'
