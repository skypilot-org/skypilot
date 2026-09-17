"""bulk_provision must not tear down resources when execution pauses.

A paused execution (ExecutionPausedError) is waiting on an external condition
and wants its partially provisioned resources kept so it can resume. This pins
that bulk_provision re-raises the pause without tearing down, while still
tearing down on an ordinary provisioning failure.
"""
import contextlib
from unittest import mock

import pytest
import sqlalchemy
from sqlalchemy import orm

from sky import clouds
from sky import exceptions
from sky import global_user_state
from sky.provision import provisioner
from sky.skylet import constants as skylet_constants
from sky.utils import resources_utils
from sky.utils.db import db_utils

_CLUSTER_YAML_DICT = {
    'head_node_type': 'ray.head.default',
    'provider': {},
    'auth': {},
    'docker': {},
    'available_node_types': {
        'ray.head.default': {
            'node_config': {}
        }
    },
}


@pytest.fixture()
def patched_bulk_provision(monkeypatch):
    """Drive bulk_provision with its filesystem/state deps stubbed out.

    Returns the teardown_cluster mock so tests can assert on it; the caller
    sets _bulk_provision's side effect.
    """
    monkeypatch.setattr(global_user_state, 'get_cluster_yaml_dict',
                        lambda *a, **k: dict(_CLUSTER_YAML_DICT))
    monkeypatch.setattr(provisioner.provision_logging,
                        'setup_provision_logging',
                        lambda *a, **k: contextlib.nullcontext())
    teardown_mock = mock.MagicMock()
    monkeypatch.setattr(provisioner, 'teardown_cluster', teardown_mock)
    return teardown_mock


@pytest.fixture()
def fresh_state_db(tmp_path, monkeypatch):
    """Point the global state DB at a tmp sqlite file."""
    monkeypatch.setenv(skylet_constants.SKY_RUNTIME_DIR_ENV_VAR_KEY,
                       str(tmp_path))
    monkeypatch.setattr(
        global_user_state,
        '_db_manager',
        db_utils.DatabaseManager(
            'state',
            global_user_state.create_table,
            post_init_fn=lambda _: global_user_state._sqlite_supports_returning(
            ),
        ),
    )


def _attempt_rows():
    engine = global_user_state._db_manager.get_engine()
    with orm.Session(engine) as session:
        return session.execute(
            sqlalchemy.select(global_user_state.launch_attempt_table).order_by(
                global_user_state.launch_attempt_table.c.attempt_seq)).all()


def _call_bulk_provision(tmp_path):
    return provisioner.bulk_provision(cloud=clouds.Kubernetes(),
                                      region=clouds.Region('us'),
                                      zones=None,
                                      cluster_name=resources_utils.ClusterName(
                                          'c', 'c-on-cloud'),
                                      num_nodes=1,
                                      cluster_yaml='/fake/cluster.yaml',
                                      prev_cluster_ever_up=False,
                                      log_dir=str(tmp_path))


def test_bulk_provision_does_not_teardown_on_pause(patched_bulk_provision,
                                                   fresh_state_db, monkeypatch,
                                                   tmp_path):
    """A pause propagates without tearing down the kept resources."""
    paused = exceptions.ExecutionPausedError('Waiting on admission.',
                                             hint='resume later',
                                             retry_wait_seconds=5)
    monkeypatch.setattr(provisioner, '_bulk_provision',
                        mock.MagicMock(side_effect=paused))

    with pytest.raises(exceptions.ExecutionPausedError):
        _call_bulk_provision(tmp_path)

    patched_bulk_provision.assert_not_called()


def test_bulk_provision_tears_down_on_ordinary_failure(patched_bulk_provision,
                                                       fresh_state_db,
                                                       monkeypatch, tmp_path):
    """Negative control: an ordinary failure still tears down.

    Proves the test harness actually reaches the teardown branch, so the
    pause test above is meaningful rather than superfluous.
    """
    monkeypatch.setattr(
        provisioner, '_bulk_provision',
        mock.MagicMock(side_effect=RuntimeError('provisioning failed')))

    with pytest.raises(RuntimeError, match='provisioning failed'):
        _call_bulk_provision(tmp_path)

    patched_bulk_provision.assert_called_once()


# --- Launch attempt bookkeeping ---------------------------------------------
#
# A pause unwinds the whole provision call and resumes as a fresh one, possibly
# in another worker process. These pin that the resume continues the *same*
# attempt, which is what keeps the wait it is serving one interval instead of
# two short ones.


@pytest.fixture()
def in_request_context(monkeypatch):
    """Pretend we run inside a request, so pausing/resuming is possible."""
    monkeypatch.setattr(provisioner.common_utils, 'is_in_request_context',
                        lambda: True)
    monkeypatch.setattr(provisioner.common_utils, 'get_current_request_id',
                        lambda: 'req-1')


def test_resumed_launch_continues_the_same_attempt(patched_bulk_provision,
                                                   fresh_state_db,
                                                   in_request_context,
                                                   monkeypatch, tmp_path):
    """The pause leaves the attempt open; the resume must reuse it.

    Opening a second attempt here would restart the clock at admission time and
    erase the whole queue wait -- the exact number this instrumentation exists
    to report.
    """
    paused = exceptions.ExecutionPausedError('Waiting on admission.',
                                             hint='resume later',
                                             retry_wait_seconds=5)
    monkeypatch.setattr(provisioner, '_bulk_provision',
                        mock.MagicMock(side_effect=paused))
    with pytest.raises(exceptions.ExecutionPausedError):
        _call_bulk_provision(tmp_path)

    after_pause = _attempt_rows()
    assert len(after_pause) == 1
    assert after_pause[0].outcome is None, 'a pause must not close the attempt'
    opened_at = after_pause[0].provision_start

    # The resume: a fresh call, same request.
    monkeypatch.setattr(provisioner, '_bulk_provision',
                        mock.MagicMock(return_value=mock.MagicMock()))
    _call_bulk_provision(tmp_path)

    rows = _attempt_rows()
    assert len(rows) == 1, 'the resume must not open a second attempt'
    assert rows[0].provision_start == opened_at, (
        'the attempt must keep the time it originally started, not restart '
        'at the resume')
    assert rows[0].instances_ready is not None
    assert rows[0].outcome == global_user_state.LaunchOutcome.SUCCEEDED.value


def test_failed_attempt_is_closed_and_the_retry_opens_a_new_one(
        patched_bulk_provision, fresh_state_db, in_request_context, monkeypatch,
        tmp_path):
    """Failover is the opposite of a pause: each try is its own attempt.

    Without closing on failure, the retry would find an open row and continue
    it, reporting several failed tries as one long attempt.
    """
    monkeypatch.setattr(
        provisioner, '_bulk_provision',
        mock.MagicMock(side_effect=RuntimeError('provisioning failed')))
    with pytest.raises(RuntimeError):
        _call_bulk_provision(tmp_path)

    monkeypatch.setattr(provisioner, '_bulk_provision',
                        mock.MagicMock(return_value=mock.MagicMock()))
    _call_bulk_provision(tmp_path)

    rows = _attempt_rows()
    assert len(rows) == 2
    assert [r.outcome for r in rows] == [
        global_user_state.LaunchOutcome.FAILED.value,
        global_user_state.LaunchOutcome.SUCCEEDED.value,
    ]


def test_cancellation_leaves_the_attempt_open_for_the_sweep(
        patched_bulk_provision, fresh_state_db, in_request_context, monkeypatch,
        tmp_path):
    """A cancelled launch is not a provisioning failure.

    Recording it as one would inflate the provisioning failure rate every time
    someone hits Ctrl-C or a rollout SIGTERMs a worker.
    """
    monkeypatch.setattr(provisioner, '_bulk_provision',
                        mock.MagicMock(side_effect=KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):
        _call_bulk_provision(tmp_path)

    rows = _attempt_rows()
    assert len(rows) == 1
    assert rows[0].outcome is None
