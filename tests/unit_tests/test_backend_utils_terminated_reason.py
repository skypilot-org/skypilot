"""Tests for the event a status refresh records when a cluster's nodes are gone.

That event becomes the cluster's last event, which the dashboard shows as why
a terminated cluster went away.
"""
import datetime
import re
import time
from unittest import mock

import pytest

from sky import backends
from sky import clouds
from sky import global_user_state
from sky.backends import backend_utils
from sky.skylet import constants
from sky.utils import status_lib
from sky.utils.db import db_utils

_SUFFIX = 'Cluster removed by SkyPilot status refresh.'
_LAUNCHED_AT = time.time() - 3600
_TOLERATIONS = [{'key': 'pool', 'operator': 'Exists', 'effect': 'NoExecute'}]
_RAY_CONFIG = {
    'provider': {
        'context': 'ctx'
    },
    'available_node_types': {
        'ray_head_default': {
            'node_config': {
                'spec': {
                    'tolerations': _TOLERATIONS
                }
            }
        }
    },
}
_HEALER_REASON = ('SkyPilot GPU Manager: GPU Error detected with XID Error 150 '
                  'on GPU GPU-1 on node node-1. This error code indicates a '
                  'hardware failure. Reschedule the cluster.')


def _cluster_info():
    info = mock.Mock()
    info.instances = {
        'c-head': [mock.Mock(node_name='node-1')],
        'c-worker1': [mock.Mock(node_name='node-2')],
    }
    return info


def _handle(cloud=None, with_cluster_info=True):
    handle = mock.Mock(spec=backends.CloudVmRayResourceHandle)
    handle.cluster_name = 'c'
    handle.cluster_name_on_cloud = 'c-1234'
    handle.cluster_yaml = '/fake/cluster.yaml'
    handle.cached_cluster_info = _cluster_info() if with_cluster_info else None
    handle.launched_nodes = 2
    handle.launched_resources = mock.Mock(unsafe=True)
    handle.launched_resources.cloud = cloud or clouds.Kubernetes()
    handle.launched_resources.use_spot = False
    handle.launched_resources.assert_launchable.return_value = (
        handle.launched_resources)
    handle.num_ips_per_node = 1
    handle.provision_runtime_metadata = mock.Mock()
    handle.provision_runtime_metadata.has_ray = True
    return handle


def _record(handle,
            status=status_lib.ClusterStatus.UP,
            autostop=-1,
            to_down=False):
    return {
        'handle': handle,
        'status': status,
        'cluster_hash': 'hash-1',
        'autostop': autostop,
        'to_down': to_down,
        'launched_at': _LAUNCHED_AT,
    }


@pytest.fixture
def k8s(monkeypatch):
    """Patches the Kubernetes lookups; returns the mocks to steer them."""
    mocks = mock.Mock()
    mocks.autostop_event.return_value = None
    mocks.termination_cause.return_value = None
    mocks.latest_events.return_value = {}
    monkeypatch.setattr(backend_utils.global_user_state,
                        'get_cluster_yaml_dict', lambda _: _RAY_CONFIG)
    monkeypatch.setattr(backend_utils.k8s_instance,
                        'get_cluster_autostop_event', mocks.autostop_event)
    monkeypatch.setattr(backend_utils.k8s_instance,
                        'get_cluster_termination_cause',
                        mocks.termination_cause)
    monkeypatch.setattr(backend_utils.global_user_state,
                        'get_last_cluster_event_of_type_multiple',
                        mocks.latest_events)
    return mocks


def _reason(handle=None, failures=None, **record_kwargs):
    handle = handle or _handle()
    return backend_utils._get_terminated_cluster_reason(
        _record(handle, **record_kwargs), handle, handle.launched_resources,
        failures)


class TestTerminatedClusterReason:

    def test_kubernetes_cause_leads(self, k8s):
        k8s.termination_cause.return_value = (
            'Pods evicted by Kubernetes: NoExecute taint t=x on node node-1.')
        assert _reason() == (
            'Pods evicted by Kubernetes: NoExecute taint t=x on node node-1. '
            f'{_SUFFIX}')
        args, kwargs = k8s.termination_cause.call_args
        assert args == ({
            'context': 'ctx'
        }, {
            'c-head': 'node-1',
            'c-worker1': 'node-2'
        })
        assert kwargs['tolerations'] == _TOLERATIONS
        assert kwargs['since'] == datetime.datetime.fromtimestamp(
            _LAUNCHED_AT, tz=datetime.timezone.utc)

    def test_reported_failures_follow_the_kubernetes_cause(self, k8s):
        k8s.termination_cause.return_value = 'Pods evicted by Kubernetes: x.'
        failures = [
            {
                'failure_reason': _HEALER_REASON
            },
            {
                'failure_reason': _HEALER_REASON.replace('150', '154')
            },
            # The same failure twice counts once.
            {
                'failure_reason': _HEALER_REASON
            },
        ]
        assert _reason(failures=failures) == (
            'Pods evicted by Kubernetes: x. Preceded by: SkyPilot GPU Manager: '
            'GPU Error detected with XID Error 150 on GPU GPU-1 on node '
            f'node-1. (+1 more). {_SUFFIX}')

    def test_reported_failure_alone(self, k8s):
        del k8s
        assert _reason(failures=[{
            'failure_reason': _HEALER_REASON
        }]) == (
            'Reported failure: SkyPilot GPU Manager: GPU Error detected with '
            f'XID Error 150 on GPU GPU-1 on node node-1. {_SUFFIX}')

    def test_blank_failure_reason_is_skipped(self, k8s):
        del k8s
        assert _reason(failures=[{
            'failure_reason': ' \n'
        }, {}]) == (f'Nodes disappeared; no cause found. {_SUFFIX}')

    def test_unhealthy_reason_is_carried_forward(self, k8s):
        k8s.latest_events.return_value = {
            'hash-1': (f'{global_user_state.ABNORMAL_STATUS_REASON_PREFIX} '
                       'one or more nodes terminated (node node-2 is NotReady)'
                       '. Transitioned to INIT (details: cluster is '
                       'unhealthy).\nHint: check the node.')
        }
        assert _reason(status=status_lib.ClusterStatus.INIT) == (
            'Was unhealthy (one or more nodes terminated (node node-2 is '
            'NotReady)), then its pods disappeared; no direct cause found. '
            f'{_SUFFIX}')

    def test_launching_cluster_is_not_carried_forward(self, k8s):
        k8s.latest_events.return_value = {
            'hash-1': 'Setting up SkyPilot runtime on cluster'
        }
        assert _reason(status=status_lib.ClusterStatus.INIT) == (
            f'Nodes disappeared; no cause found. {_SUFFIX}')

    def test_unhealthy_event_on_an_up_cluster_is_not_carried_forward(self, k8s):
        k8s.latest_events.return_value = {
            'hash-1': f'{global_user_state.ABNORMAL_STATUS_REASON_PREFIX} x.'
        }
        assert _reason() == f'Nodes disappeared; no cause found. {_SUFFIX}'

    def test_nothing_found(self, k8s):
        del k8s
        assert _reason() == f'Nodes disappeared; no cause found. {_SUFFIX}'

    def test_k8s_without_autodown_record_is_not_called_autodown(self, k8s):
        # Every Kubernetes autodown leaves the breadcrumb; it was looked for
        # and is not there, so something else removed the pods.
        del k8s
        assert _reason(
            autostop=10,
            to_down=True) == (f'Nodes disappeared; no cause found. {_SUFFIX}')

    def test_k8s_lookup_failure_still_says_likely_autodowned(self, k8s):
        k8s.autostop_event.side_effect = RuntimeError('api down')
        assert _reason(autostop=10, to_down=True) == (
            'Likely autodowned by SkyPilot (idle 10 min); no direct cause '
            f'found. {_SUFFIX}')

    def test_vm_nothing_found_leads_with_configured_autodown(self, k8s):
        del k8s
        handle = _handle(cloud=clouds.AWS(), with_cluster_info=False)
        assert _reason(handle, autostop=10, to_down=True) == (
            'Likely autodowned by SkyPilot (idle 10 min); no direct cause '
            f'found. {_SUFFIX}')

    def test_young_cluster_cannot_have_autodowned(self, k8s):
        del k8s
        handle = _handle(cloud=clouds.AWS(), with_cluster_info=False)
        handle.launched_resources.use_spot = True
        # Launched an hour ago; a 90-minute idle limit cannot have fired.
        assert _reason(handle, autostop=90, to_down=True) == (
            f'Likely spot preemption; no direct cause found. {_SUFFIX}')

    def test_nothing_found_on_spot_leads_with_preemption(self, k8s):
        del k8s
        handle = _handle(cloud=clouds.AWS(), with_cluster_info=False)
        handle.launched_resources.use_spot = True
        assert _reason(handle) == (
            f'Likely spot preemption; no direct cause found. {_SUFFIX}')

    def test_autodown_hint_wins_over_spot(self, k8s):
        del k8s
        handle = _handle(cloud=clouds.AWS(), with_cluster_info=False)
        handle.launched_resources.use_spot = True
        assert _reason(handle, autostop=5, to_down=True).startswith(
            'Likely autodowned by SkyPilot (idle 5 min);')

    def test_kubernetes_autodown_breadcrumb(self, k8s):
        k8s.autostop_event.return_value = {'transitioned_at': 1}
        assert _reason(autostop=10, to_down=True) == (
            f'Autodowned by SkyPilot after idle timeout (10 min). {_SUFFIX}')
        k8s.termination_cause.assert_not_called()

    def test_kubernetes_autodown_after_autostop_was_reset(self, k8s):
        # A refresh that caught the autodown half way marked the cluster
        # abnormal and reset its autostop; the breadcrumb still says why.
        k8s.autostop_event.return_value = {'transitioned_at': 1}
        assert _reason(
            status=status_lib.ClusterStatus.INIT, autostop=-1,
            to_down=False) == ('Autodowned by SkyPilot after idle timeout. '
                               f'{_SUFFIX}')

    def test_vm_autodown_seen_while_autostopping(self, k8s):
        handle = _handle(cloud=clouds.AWS(), with_cluster_info=False)
        assert _reason(handle,
                       status=status_lib.ClusterStatus.AUTOSTOPPING,
                       autostop=5,
                       to_down=True) == (
                           'Autodowned by SkyPilot after idle timeout (5 min). '
                           f'{_SUFFIX}')
        k8s.autostop_event.assert_not_called()

    def test_vm_without_evidence(self, k8s):
        handle = _handle(cloud=clouds.AWS(), with_cluster_info=False)
        assert _reason(
            handle) == f'Nodes disappeared; no cause found. {_SUFFIX}'
        k8s.termination_cause.assert_not_called()

    def test_no_cached_cluster_info_skips_kubernetes(self, k8s):
        assert _reason(_handle(with_cluster_info=False)) == (
            f'Nodes disappeared; no cause found. {_SUFFIX}')
        k8s.termination_cause.assert_not_called()

    def test_lookup_failure_falls_through(self, k8s):
        k8s.termination_cause.side_effect = RuntimeError('api down')
        assert _reason() == f'Nodes disappeared; no cause found. {_SUFFIX}'


class TestTerminateBranchWritesOneEvent:

    def _events_written(self, k8s, record_kwargs, last_event=None):
        handle = _handle()
        record = _record(handle, **record_kwargs)
        written = []
        external_failure = mock.Mock()
        external_failure.get.return_value = None
        with mock.patch.object(backend_utils,
                               '_query_cluster_status_via_cloud_api',
                               return_value={}), \
             mock.patch.object(backend_utils, 'ExternalFailureSource',
                               external_failure), \
             mock.patch.object(backend_utils.global_user_state,
                               'add_cluster_event',
                               side_effect=lambda *a, **kw: written.append(
                                   (a, kw))), \
             mock.patch.object(backend_utils.global_user_state,
                               'get_cluster_from_name', return_value=None), \
             mock.patch.object(backend_utils.global_user_state,
                               'get_last_cluster_event',
                               return_value=last_event), \
             mock.patch.object(backends.CloudVmRayBackend,
                               'post_teardown_cleanup') as cleanup:
            backend_utils._update_cluster_status('c',
                                                 record,
                                                 retry_if_missing=False)
        cleanup.assert_called_once()
        return written

    def test_reentry_skips_the_lookups(self, k8s):
        # Cleanup failed after an earlier refresh recorded the cause.
        written = self._events_written(
            k8s, {}, last_event=f'Pod evicted by Kubernetes: x. {_SUFFIX}')
        assert written == []
        k8s.termination_cause.assert_not_called()

    def test_explaining_never_blocks_the_cleanup(self, k8s):
        k8s.latest_events.side_effect = RuntimeError('db down')
        written = self._events_written(
            k8s, {'status': status_lib.ClusterStatus.INIT})
        assert [args[2] for args, _ in written
               ] == [f'Nodes disappeared; no cause found. {_SUFFIX}']

    def test_autodown_is_the_only_and_last_event(self, k8s):
        # Before, a generic cleanup line was written after the autodown line
        # and became the cluster's last event.
        k8s.autostop_event.return_value = {'transitioned_at': 1}
        written = self._events_written(k8s, {'autostop': 10, 'to_down': True})
        assert len(written) == 1
        args, kwargs = written[0]
        assert args[2] == (
            f'Autodowned by SkyPilot after idle timeout (10 min). {_SUFFIX}')
        assert kwargs['nop_if_duplicate'] is True
        assert (kwargs['duplicate_regex'] ==
                backend_utils._TERMINATED_CLUSTER_EVENT_REGEX)


def _fresh_db(tmp_path, monkeypatch):
    monkeypatch.setenv(constants.SKY_RUNTIME_DIR_ENV_VAR_KEY, str(tmp_path))
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


class _MinimalHandle:
    launched_resources = None


class TestReentryGuard:
    """A refresh re-entering after a failed cleanup keeps the recorded cause."""

    @pytest.fixture
    def db(self, tmp_path, monkeypatch):
        _fresh_db(tmp_path, monkeypatch)
        global_user_state.add_or_update_cluster(cluster_name='c',
                                                cluster_handle=_MinimalHandle(),
                                                requested_resources=set(),
                                                ready=False)
        return global_user_state._get_hash_for_existing_cluster('c')

    @staticmethod
    def _texts(monkeypatch):
        """The texts the code writes, strongest evidence first."""
        texts = []
        for cause in ('Pod evicted by Kubernetes: NoExecute taint t=x on '
                      'node node-1.', None):
            with monkeypatch.context() as m:
                m.setattr(backend_utils.global_user_state,
                          'get_cluster_yaml_dict', lambda _: _RAY_CONFIG)
                m.setattr(backend_utils.k8s_instance,
                          'get_cluster_autostop_event', lambda *a, **k: None)
                m.setattr(backend_utils.k8s_instance,
                          'get_cluster_termination_cause',
                          lambda *a, c=cause, **k: c)
                texts.append(_reason())
        return texts

    @staticmethod
    def _write(reason):
        global_user_state.add_cluster_event(
            'c',
            None,
            reason,
            global_user_state.ClusterEventType.STATUS_CHANGE,
            nop_if_duplicate=True,
            duplicate_regex=backend_utils._TERMINATED_CLUSTER_EVENT_REGEX)

    @staticmethod
    def _events(cluster_hash):
        return global_user_state.get_cluster_events(
            None, cluster_hash,
            global_user_state.ClusterEventType.STATUS_CHANGE)

    def test_regex_matches_every_text_the_code_writes(self, monkeypatch, k8s):
        del k8s
        texts = self._texts(monkeypatch)
        texts.append(_reason(autostop=10, to_down=True))
        for text in texts:
            assert re.search(backend_utils._TERMINATED_CLUSTER_EVENT_REGEX,
                             text), text

    def test_weaker_text_does_not_replace_the_cause(self, db, monkeypatch):
        strong, weak = self._texts(monkeypatch)
        self._write(strong)
        self._write(weak)
        self._write(strong)
        assert self._events(db) == [strong]

    def test_failed_user_down_is_kept(self, db, monkeypatch):
        _, weak = self._texts(monkeypatch)
        user_down = 'Cluster was terminated by user alice.'
        global_user_state.add_cluster_event(
            'c', None, user_down,
            global_user_state.ClusterEventType.STATUS_CHANGE)
        self._write(weak)
        assert self._events(db) == [user_down]

    def test_first_write_after_an_unrelated_event_lands(self, db, monkeypatch):
        strong, _ = self._texts(monkeypatch)
        global_user_state.add_cluster_event(
            'c', status_lib.ClusterStatus.UP,
            'All nodes up; SkyPilot runtime healthy.',
            global_user_state.ClusterEventType.STATUS_CHANGE)
        self._write(strong)
        assert self._events(db)[-1] == strong
