"""Tests for sky.server.idle_detection."""
import json
import types
from unittest import mock

import pytest
import requests

from sky import global_user_state
from sky import skypilot_config
from sky.server import daemons
from sky.server import idle_detection
from sky.skylet import constants
from sky.utils import common
from sky.utils import common_utils
from sky.utils import leader_election
from sky.utils import schemas
from sky.utils import status_lib

_INTERVAL = 900
_WINDOW = 6 * 3600
_QUERY_URL = 'https://metrics.example.com/api/v1/query'
_HTTP_URL = 'https://idle.example.com/check'
_WEBHOOK = 'https://hooks.example.com/idle'


def _prometheus_config(query='gpu_util{pod=~"{cluster_on_cloud}-.*"}'):
    return {
        'window': '6h',
        'source': {
            'prometheus': {
                'url': _QUERY_URL,
                'query': query,
                'threshold': 5,
            }
        },
        'notify': {
            'webhook': _WEBHOOK
        },
    }


def _http_config():
    return {
        'window': '6h',
        'source': {
            'http': {
                'url': _HTTP_URL
            }
        },
        'notify': {
            'webhook': _WEBHOOK
        },
    }


def _response(body, status_code=200):
    response = requests.Response()
    response.status_code = status_code
    response._content = json.dumps(body).encode()  # pylint: disable=protected-access
    return response


def _vector(*values, status_code=200):
    return _response(
        {
            'status': 'success',
            'data': {
                'resultType': 'vector',
                'result': [{
                    'metric': {
                        'gpu': str(i)
                    },
                    'value': [0, str(value)]
                } for i, value in enumerate(values)]
            }
        },
        status_code=status_code)


class _World:
    """The cluster table, the metrics source and the webhook receiver."""

    def __init__(self, monkeypatch, config):
        self.config = config
        self.clusters = {}
        self.events = []
        self.queries = []
        self.http_checks = []
        self.webhooks = []
        self.source_response = _vector(0)
        self.webhook_response = _response({})
        self.leader = True
        self.released = 0
        self.now = 0

        original_get_nested = skypilot_config.get_nested

        def get_nested(keys, default_value, *args, **kwargs):
            if keys == ('idle_detection',):
                return self.config
            return original_get_nested(keys, default_value, *args, **kwargs)

        monkeypatch.setattr(skypilot_config, 'get_nested', get_nested)
        monkeypatch.setattr(global_user_state, 'get_clusters',
                            self._get_clusters)
        monkeypatch.setattr(global_user_state, 'set_cluster_info',
                            self._set_cluster_info)
        monkeypatch.setattr(global_user_state, 'add_cluster_event',
                            lambda *args: self.events.append(args))
        monkeypatch.setattr(leader_election, 'get_elector',
                            lambda lock_id: self)
        monkeypatch.setattr(requests, 'get', self._get)
        monkeypatch.setattr(requests, 'post', self._post)
        monkeypatch.setattr(idle_detection.time, 'time', lambda: self.now)

    def try_acquire(self):
        return self.leader

    def release(self):
        self.released += 1

    def add_cluster(self,
                    name='train',
                    status=status_lib.ClusterStatus.UP,
                    user_name='alice@example.com',
                    labels=None,
                    internal_ips=('10.0.0.1', '10.0.0.2')):
        handle = types.SimpleNamespace(
            cluster_name_on_cloud=f'{name}-ab12',
            cached_internal_ips=(list(internal_ips)
                                 if internal_ips is not None else None),
            cached_cluster_info=types.SimpleNamespace(instances={
                'i-0a': [],
                'i-0b': []
            }),
            launched_resources=types.SimpleNamespace(labels=labels))
        self.clusters[name] = {
            'name': name,
            'launched_at': 100,
            'handle': handle,
            'status': status,
            'user_hash': 'abcd1234',
            'user_name': user_name,
            'workspace': 'default',
            'node_names': 'node-a,node-b',
            'metadata': {},
        }
        return self.clusters[name]

    def state(self, name='train'):
        return self.clusters[name]['metadata'].get('idle_detection')

    def run(self, at):
        self.now = at
        idle_detection.run_pass(_INTERVAL)

    def run_until(self, end):
        """Runs one pass per interval from 0 to end, inclusive."""
        for at in range(0, end + 1, _INTERVAL):
            self.run(at)

    def _get_clusters(self, exclude_managed_clusters=False):
        assert exclude_managed_clusters
        return [dict(record) for record in self.clusters.values()]

    def _set_cluster_info(self, cluster_name, metadata):
        self.clusters[cluster_name]['metadata'] = json.loads(
            json.dumps(metadata))

    def _get(self, url, params=None, headers=None, timeout=None):
        assert timeout
        self.queries.append((url, params, headers))
        if isinstance(self.source_response, Exception):
            raise self.source_response
        return self.source_response

    def _post(self, url, json=None, headers=None, timeout=None):  # pylint: disable=redefined-outer-name
        assert timeout
        if url == _WEBHOOK:
            self.webhooks.append(json)
            return self.webhook_response
        self.http_checks.append((url, json, headers))
        if isinstance(self.source_response, Exception):
            raise self.source_response
        return self.source_response


@pytest.fixture
def world(monkeypatch):
    return _World(monkeypatch, _prometheus_config())


def _run_and_capture_summary(world, monkeypatch, at):
    info = mock.Mock()
    warning = mock.Mock()
    monkeypatch.setattr(idle_detection.logger, 'info', info)
    monkeypatch.setattr(idle_detection.logger, 'warning', warning)
    world.run(at)
    return info, warning


def test_does_nothing_when_not_configured(world):
    world.config = None
    world.add_cluster()

    world.run_until(_WINDOW)

    assert world.state() is None
    assert not world.queries
    assert not world.webhooks


def test_does_nothing_when_another_server_leads(world):
    world.leader = False
    world.add_cluster()

    world.run_until(_WINDOW)

    assert world.state() is None
    assert not world.queries
    assert world.released == 0


def test_releases_leadership_after_each_pass(world):
    world.run(0)
    world.run(_INTERVAL)

    assert world.released == 2


def test_cluster_is_not_judged_before_a_full_window_of_observation(world):
    """A young cluster has too few samples for the window average."""
    world.add_cluster()

    world.run_until(_WINDOW - _INTERVAL)

    assert not world.queries
    assert not world.webhooks
    assert world.state()['up_since'] == 0


def test_idle_cluster_notifies_owner_once(world):
    world.add_cluster()

    world.run_until(_WINDOW + 2 * _INTERVAL)

    assert world.webhooks == [{
        'event': 'idle_warning',
        'cluster': 'train',
        'user': 'alice@example.com',
        'workspace': 'default',
        'reason': '1 series, max 0 < 5',
        'action': 'notify',
        'idle_since': _WINDOW,
        'deadline': None,
    }]
    assert len(world.events) == 1
    cluster_name, new_status, reason, event_type = world.events[0]
    assert cluster_name == 'train'
    assert new_status == status_lib.ClusterStatus.UP
    assert '1 series, max 0 < 5' in reason
    assert event_type == global_user_state.ClusterEventType.STATUS_CHANGE


def test_idle_cluster_is_notified_again_after_another_window(world):
    world.add_cluster()

    world.run_until(2 * _WINDOW)

    assert len(world.webhooks) == 2
    assert world.webhooks[1]['idle_since'] == _WINDOW
    assert len(world.events) == 2


def test_query_is_rendered_for_the_cluster(world):
    world.add_cluster()

    world.run_until(_WINDOW)

    assert world.queries == [(_QUERY_URL, {
        'query': 'gpu_util{pod=~"train-ab12-.*"}'
    }, {})]


def test_every_placeholder_is_rendered(world):
    world.config = _prometheus_config(
        '{cluster}/{cluster_on_cloud}/{user}/{workspace}/{instance_ids}/'
        '{node_names}/{node_ips}/{window}')
    world.add_cluster()

    world.run_until(_WINDOW)

    assert world.queries[0][1]['query'] == (
        'train/train-ab12/alice@example.com/default/i\\\\-0a|i\\\\-0b/'
        'node\\\\-a|node\\\\-b/10\\\\.0\\\\.0\\\\.1|10\\\\.0\\\\.0\\\\.2/'
        '21600s')


def test_placeholder_values_cannot_break_out_of_a_label_matcher(world):
    world.config = _prometheus_config('up{owner="{user}"}')
    world.add_cluster(user_name='eve"} or vector(0) #')

    world.run_until(_WINDOW)

    assert world.queries[0][1]['query'] == (
        'up{owner="eve\\"} or vector(0) #"}')


def test_empty_placeholder_counts_as_active(world):
    """An empty matcher could select the series of other clusters."""
    world.config = _prometheus_config('gpu_util{instance=~"{node_ips}"}')
    world.add_cluster(internal_ips=None)

    world.run_until(_WINDOW)

    assert not world.queries
    assert not world.webhooks


@pytest.mark.parametrize('values', [(5,), (0, 0, 7), ('NaN',)])
def test_cluster_with_a_series_at_or_above_threshold_is_active(world, values):
    world.add_cluster()
    world.source_response = _vector(*values)

    world.run_until(_WINDOW)

    assert world.queries
    assert not world.webhooks
    assert not world.events


def test_all_series_below_threshold_is_idle(world):
    world.add_cluster()
    world.source_response = _vector(0, 4.5, 1)

    world.run_until(_WINDOW)

    assert world.webhooks[0]['reason'] == '3 series, max 4.5 < 5'


@pytest.mark.parametrize('source_response', [
    _vector(),
    _vector(0, status_code=401),
    _response({'status': 'error'}, status_code=500),
    _response({'status': 'success'}),
    _response({
        'status': 'success',
        'data': {
            'resultType': 'scalar',
            'result': [0, '0']
        }
    }),
    requests.ConnectionError('refused'),
    requests.Timeout('slow'),
])
def test_unusable_source_answer_counts_as_active(world, source_response):
    """A cluster that cannot be measured is never reported idle."""
    world.add_cluster()
    world.source_response = source_response

    world.run_until(_WINDOW)

    assert world.queries
    assert not world.webhooks
    assert not world.events


def test_no_data_is_not_an_error(world, monkeypatch):
    """A CPU-only cluster has no series for a GPU query; that is normal."""
    world.add_cluster()
    world.run_until(_WINDOW - _INTERVAL)
    world.source_response = _vector()

    info, warning = _run_and_capture_summary(world, monkeypatch, _WINDOW)

    info.assert_called_once_with(
        'Idle detection pass: checked=1 idle=0 notified=0 errors=0')
    warning.assert_not_called()


def test_query_that_returns_no_vector_is_named_in_the_log(world, monkeypatch):
    world.add_cluster()
    world.run_until(_WINDOW - _INTERVAL)
    world.source_response = _response({
        'status': 'success',
        'data': {
            'resultType': 'scalar',
            'result': [0, '0']
        }
    })

    _, warning = _run_and_capture_summary(world, monkeypatch, _WINDOW)

    assert 'must return an instant vector, got scalar' in (
        warning.call_args_list[0].args[0])


def test_activity_ends_the_idle_period(world):
    world.add_cluster()
    world.run_until(_WINDOW)
    assert len(world.webhooks) == 1

    world.source_response = _vector(90)
    world.run(_WINDOW + _INTERVAL)
    world.source_response = _vector(0)
    world.run(_WINDOW + 2 * _INTERVAL)

    assert len(world.webhooks) == 2
    assert world.webhooks[1]['idle_since'] == _WINDOW + 2 * _INTERVAL


def test_relaunch_restarts_the_observation_window(world):
    """`sky start` and `sky launch -c` rewrite launched_at."""
    cluster = world.add_cluster()
    world.run_until(_WINDOW - _INTERVAL)

    cluster['launched_at'] = 200
    world.run(_WINDOW)

    assert not world.queries
    assert world.state()['up_since'] == _WINDOW


def test_missed_passes_restart_the_observation_window(world):
    """After an outage the daemon has not watched the cluster stay UP."""
    world.add_cluster()
    world.run_until(_WINDOW - _INTERVAL)

    world.run(_WINDOW + 2 * _INTERVAL)

    assert not world.queries
    assert world.state()['up_since'] == _WINDOW + 2 * _INTERVAL


def test_one_missed_pass_keeps_the_observation_window(world):
    world.add_cluster()
    world.run_until(_WINDOW - _INTERVAL)

    world.run(_WINDOW + _INTERVAL)

    assert world.queries


def test_state_is_cleared_when_the_cluster_is_not_up(world):
    cluster = world.add_cluster()
    world.run_until(_WINDOW)
    assert world.state() is not None

    cluster['status'] = status_lib.ClusterStatus.STOPPED
    world.run(_WINDOW + _INTERVAL)

    assert world.state() is None


def test_failed_webhook_is_retried_on_the_next_pass(world):
    world.add_cluster()
    world.webhook_response = _response({}, status_code=503)
    world.run_until(_WINDOW)
    assert len(world.webhooks) == 1
    assert not world.events

    world.webhook_response = _response({})
    world.run(_WINDOW + _INTERVAL)

    assert len(world.webhooks) == 2
    assert len(world.events) == 1


@pytest.mark.parametrize('cluster', [
    {
        'name': common.JOB_CONTROLLER_NAME
    },
    {
        'labels': {
            'skypilot-idle-detection': 'skip'
        }
    },
    {
        'user_name': None
    },
    {
        'user_name': constants.SKYPILOT_SYSTEM_USER_ID
    },
])
def test_cluster_is_left_alone(world, cluster):
    record = world.add_cluster(**cluster)

    world.run_until(_WINDOW)

    assert not world.queries
    assert not world.webhooks
    assert world.state(record['name']) is None


def test_other_label_values_do_not_opt_out(world):
    world.add_cluster(labels={'skypilot-idle-detection': 'no'})

    world.run_until(_WINDOW)

    assert len(world.webhooks) == 1


def test_one_failing_cluster_does_not_stop_the_pass(world, monkeypatch):
    world.add_cluster('broken')
    world.add_cluster('train')
    set_cluster_info = global_user_state.set_cluster_info

    def fail_for_broken(cluster_name, metadata):
        if cluster_name == 'broken':
            raise ValueError('Cluster broken not found.')
        set_cluster_info(cluster_name, metadata)

    monkeypatch.setattr(global_user_state, 'set_cluster_info', fail_for_broken)

    world.run_until(_WINDOW)

    assert [webhook['cluster'] for webhook in world.webhooks] == ['train']


def test_headers_file_is_sent_to_the_source(world, tmp_path):
    headers_file = tmp_path / 'headers'
    headers_file.write_text('# token\nAuthorization: Bearer abc:def\n\n'
                            'X-Scope-OrgID: team\n')
    world.config['source']['prometheus']['headers_file'] = str(headers_file)
    world.add_cluster()

    world.run_until(_WINDOW)

    assert world.queries[0][2] == {
        'Authorization': 'Bearer abc:def',
        'X-Scope-OrgID': 'team',
    }


def test_http_source_receives_the_cluster_and_reports_idle(monkeypatch):
    world = _World(monkeypatch, _http_config())
    world.source_response = _response({'idle': True, 'reason': 'no GPU use'})
    world.add_cluster()

    world.run_until(_WINDOW)

    assert world.http_checks == [(_HTTP_URL, {
        'cluster': 'train',
        'cluster_on_cloud': 'train-ab12',
        'user': 'alice@example.com',
        'workspace': 'default',
        'instance_ids': ['i-0a', 'i-0b'],
        'node_names': ['node-a', 'node-b'],
        'node_ips': ['10.0.0.1', '10.0.0.2'],
        'window_seconds': _WINDOW,
    }, {})]
    assert world.webhooks[0]['reason'] == 'no GPU use'


def test_http_source_without_a_reason_is_still_idle(monkeypatch):
    world = _World(monkeypatch, _http_config())
    world.source_response = _response({'idle': True})
    world.add_cluster()

    world.run_until(_WINDOW)

    assert len(world.webhooks) == 1
    assert world.webhooks[0]['reason']


@pytest.mark.parametrize('source_response', [
    _response({'idle': False}),
    _response({'idle': 'true'}),
    _response({}),
    _response([]),
    _response({'idle': True}, status_code=502),
    requests.ConnectionError('refused'),
])
def test_http_source_counts_as_active_unless_it_says_idle(
        monkeypatch, source_response):
    world = _World(monkeypatch, _http_config())
    world.source_response = source_response
    world.add_cluster()

    world.run_until(_WINDOW)

    assert world.http_checks
    assert not world.webhooks


def test_idle_event_is_not_read_as_a_stop_or_an_abnormal_state(world):
    """Readers of a cluster's last event match on these reason prefixes."""
    world.add_cluster()

    world.run_until(_WINDOW)

    reason = world.events[0][2]
    assert not global_user_state.is_unhealthy_init_reason(reason)
    for action in ('stopped', 'terminated'):
        assert not reason.startswith(
            global_user_state.user_action_event_prefix(action))
    assert not reason.startswith(global_user_state.PROVISION_FAILED_PREFIX)


def test_pass_summary_warns_about_source_errors(world, monkeypatch):
    """A broken source makes every cluster look active; the log says so."""
    world.add_cluster('train')
    world.add_cluster('eval')
    world.run_until(_WINDOW - _INTERVAL)
    world.source_response = requests.ConnectionError('refused')

    info, warning = _run_and_capture_summary(world, monkeypatch, _WINDOW)

    assert warning.call_args_list[-1] == mock.call(
        'Idle detection pass: checked=2 idle=0 notified=0 errors=2')
    info.assert_not_called()


def test_pass_summary_is_informational_without_errors(world, monkeypatch):
    world.add_cluster()
    world.run_until(_WINDOW - _INTERVAL)

    info, warning = _run_and_capture_summary(world, monkeypatch, _WINDOW)

    info.assert_called_once_with(
        'Idle detection pass: checked=1 idle=1 notified=1 errors=0')
    warning.assert_not_called()


def _daemon():
    return next(daemon for daemon in daemons.INTERNAL_REQUEST_DAEMONS
                if daemon.id == 'idle-detection-daemon')


def test_daemon_is_skipped_unless_configured(monkeypatch):
    monkeypatch.setattr(skypilot_config, 'get_nested',
                        lambda keys, default_value: default_value)
    assert _daemon().should_skip() is True

    monkeypatch.setattr(skypilot_config, 'get_nested',
                        lambda keys, default_value: _prometheus_config())
    assert _daemon().should_skip() is False


def test_daemon_runs_a_pass_then_sleeps_for_its_interval(monkeypatch):
    intervals = {('daemons', 'idle-detection-daemon', 'interval_seconds'): 60}
    monkeypatch.setattr(
        skypilot_config, 'get_nested',
        lambda keys, default_value: intervals.get(keys, default_value))
    run_pass = mock.Mock()
    monkeypatch.setattr(idle_detection, 'run_pass', run_pass)
    sleep = mock.Mock()
    monkeypatch.setattr(daemons.time, 'sleep', sleep)

    _daemon().event_fn()

    run_pass.assert_called_once_with(60)
    sleep.assert_called_once_with(60)


def _validate(idle_detection_config):
    common_utils.validate_schema({'idle_detection': idle_detection_config},
                                 schemas.get_config_schema(),
                                 'Invalid config: ')


@pytest.mark.parametrize('config', [_prometheus_config(), _http_config()])
def test_schema_accepts_each_source(config):
    _validate(config)


def _without(config, key):
    return {k: v for k, v in config.items() if k != key}


@pytest.mark.parametrize('config', [
    _without(_prometheus_config(), 'window'),
    _without(_prometheus_config(), 'notify'),
    _without(_prometheus_config(), 'source'),
    {
        **_prometheus_config(), 'window': 'soon'
    },
    {
        **_prometheus_config(), 'action': 'down'
    },
    {
        **_prometheus_config(), 'source': {}
    },
    {
        **_prometheus_config(), 'source': {
            **_prometheus_config()['source'],
            **_http_config()['source']
        }
    },
    {
        **_prometheus_config(), 'source': {
            'prometheus': {
                'url': _QUERY_URL,
                'query': 'up'
            }
        }
    },
    {
        **_prometheus_config(), 'notify': {}
    },
])
def test_schema_rejects_incomplete_or_unknown_settings(config):
    with pytest.raises(ValueError):
        _validate(config)
