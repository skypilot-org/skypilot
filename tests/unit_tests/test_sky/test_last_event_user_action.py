"""A cluster's last event is what ended it, not a later refresh's status.

`sky down` / `sky stop` record the user's event before teardown, and the
teardown's own status refresh (or one already running) can record a status
event after it. Both readers of a cluster's last event must still return the
user's action, or the refresh's removal of a cluster whose nodes were gone.
"""
import time

from sky import global_user_state as gus
from sky.skylet import constants
from sky.utils import status_lib
from sky.utils.db import db_utils

_STATUS = gus.ClusterEventType.STATUS_CHANGE
_DOWN = 'Cluster was terminated by user alice (request ID: r1).'
_STOP = 'Cluster was stopped by user alice (request ID: r1).'
_ABNORMAL = 'Cluster is abnormal because pod-0 is not ready (x).'
_HEALTHY = 'All nodes up; SkyPilot runtime healthy.'


def _fresh_db(tmp_path, monkeypatch):
    monkeypatch.setenv(constants.SKY_RUNTIME_DIR_ENV_VAR_KEY, str(tmp_path))
    monkeypatch.setattr(
        gus, '_db_manager',
        db_utils.DatabaseManager(
            'state',
            gus.create_table,
            post_init_fn=lambda _: gus._sqlite_supports_returning()))


class _Handle:
    launched_resources = None


def _launch(name):
    gus.add_or_update_cluster(cluster_name=name,
                              cluster_handle=_Handle(),
                              requested_resources=set(),
                              ready=True)
    return gus._get_hash_for_existing_cluster(name)


def _event(name, reason, at, event_type=_STATUS):
    gus.add_cluster_event(name, None, reason, event_type, transitioned_at=at)


def _last(cluster_hash):
    batch = gus._get_last_or_terminal_cluster_event_multiple({cluster_hash})
    single = gus.get_terminal_or_last_status_change_event(cluster_hash)
    assert batch.get(cluster_hash) == single
    return single


class TestGoneCluster:

    def test_a_refresh_after_the_users_down_does_not_replace_it(
            self, tmp_path, monkeypatch):
        _fresh_db(tmp_path, monkeypatch)
        h = _launch('c')
        now = int(time.time())
        _event('c', _DOWN, now)
        _event('c', _ABNORMAL, now + 11)  # the teardown's own refresh
        gus.remove_cluster('c', terminate=True)
        assert _last(h) == _DOWN

    def test_a_healthy_refresh_after_the_users_down_too(self, tmp_path,
                                                        monkeypatch):
        _fresh_db(tmp_path, monkeypatch)
        h = _launch('c')
        now = int(time.time())
        _event('c', _DOWN, now)
        _event('c', _HEALTHY, now + 2)
        gus.remove_cluster('c', terminate=True)
        assert _last(h) == _DOWN

    def test_a_removal_after_a_failed_down_wins(self, tmp_path, monkeypatch):
        """The down failed, the cluster lived on, then its nodes went."""
        _fresh_db(tmp_path, monkeypatch)
        h = _launch('c')
        now = int(time.time())
        removal = ('Pod evicted by Kubernetes: NoExecute taint k on node n. '
                   f'{gus.CLUSTER_REMOVED_BY_REFRESH}')
        _event('c', _DOWN, now)
        _event('c', _ABNORMAL, now + 60)
        _event('c', removal, now + 600)
        gus.remove_cluster('c', terminate=True)
        assert _last(h) == removal

    def test_older_removal_lines_count_as_removals(self, tmp_path, monkeypatch):
        for removal in ('All nodes terminated, cleaning up the cluster.',
                        gus.CLUSTER_REMOVED_NO_YAML):
            _fresh_db(tmp_path / removal[:3], monkeypatch)
            h = _launch('c')
            now = int(time.time())
            _event('c', removal, now)
            _event('c', _HEALTHY, now + 5)
            gus.remove_cluster('c', terminate=True)
            assert _last(h) == removal

    def test_terminal_still_comes_first(self, tmp_path, monkeypatch):
        _fresh_db(tmp_path, monkeypatch)
        h = _launch('c')
        now = int(time.time())
        _event('c', 'Preempted by Kueue', now, gus.ClusterEventType.TERMINAL)
        _event('c', _DOWN, now + 5)
        gus.remove_cluster('c', terminate=True)
        assert _last(h) == 'Preempted by Kueue'


class TestLiveCluster:

    def test_a_failed_down_does_not_read_as_terminated(self, tmp_path,
                                                       monkeypatch):
        _fresh_db(tmp_path, monkeypatch)
        h = _launch('c')
        now = int(time.time())
        _event('c', _DOWN, now)
        _event('c', _ABNORMAL, now + 11)
        assert _last(h) == _ABNORMAL

    def test_a_stopped_cluster_shows_the_users_stop(self, tmp_path,
                                                    monkeypatch):
        _fresh_db(tmp_path, monkeypatch)
        h = _launch('c')
        now = int(time.time())
        _event('c', _STOP, now + 1)
        _event('c', _HEALTHY, now + 3)
        gus.set_cluster_status('c', status_lib.ClusterStatus.STOPPED)
        assert _last(h) == _STOP

    def test_a_stop_before_the_last_launch_does_not_stick(
            self, tmp_path, monkeypatch):
        """Stopped, then started: launched_at moves past the stop."""
        _fresh_db(tmp_path, monkeypatch)
        h = _launch('c')
        now = int(time.time())
        _event('c', _STOP, now - 100)
        _event('c', _HEALTHY, now + 3)
        gus.set_cluster_status('c', status_lib.ClusterStatus.STOPPED)
        assert _last(h) == _HEALTHY

    def test_an_unknown_launch_time_falls_back_to_the_latest(
            self, tmp_path, monkeypatch):
        _fresh_db(tmp_path, monkeypatch)
        h = _launch('c')
        now = int(time.time())
        _event('c', _STOP, now + 1)
        _event('c', _HEALTHY, now + 3)
        gus.set_cluster_status('c', status_lib.ClusterStatus.STOPPED)
        with gus.orm.Session(gus._db_manager.get_engine()) as session:
            session.query(gus.cluster_table).filter_by(name='c').update(
                {gus.cluster_table.c.launched_at: None})
            session.commit()
        assert _last(h) == _HEALTHY


def test_the_matched_texts_have_no_like_wildcards():
    for text in (gus.user_action_event_prefix('terminated'),
                 gus.user_action_event_prefix('stopped'),
                 gus.CLUSTER_REMOVED_BY_REFRESH, gus._LEGACY_REMOVAL_PREFIX):
        assert '%' not in text and '_' not in text, text
