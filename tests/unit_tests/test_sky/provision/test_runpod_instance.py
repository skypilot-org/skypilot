"""Unit tests for sky.provision.runpod.instance."""
from unittest import mock

import pytest

from sky.provision import common
from sky.provision.runpod import instance as runpod_instance
from sky.utils import status_lib

_CLUSTER = 'sky-test'


def _pod(pod_id, status, name=f'{_CLUSTER}-head', ssh_port=None):
    return {
        'id': pod_id,
        'name': name,
        'status': status,
        'external_ip': '1.2.3.4',
        'ssh_port': ssh_port,
        'vcpu_count': 8,
    }


def _config(count=1):
    return common.ProvisionConfig(
        provider_config={'availability_zone': 'EU-RO-1'},
        authentication_config={},
        docker_config={},
        node_config={
            'InstanceType': '1x_RTX5090_SECURE',
            'DiskSize': 50,
            'ImageId': 'docker:runpod/pytorch',
            'PublicKey': 'ssh-ed25519 AAAA',
            'Preemptible': False,
            'BidPerGPU': 0.0,
        },
        count=count,
        tags={},
        resume_stopped_nodes=False,
        ports_to_open_on_launch=None,
    )


@pytest.fixture
def instances(monkeypatch):
    """Live pod table backing ``utils.list_instances``.

    ``utils.remove`` drops a pod from it, so tests observe the provisioner's
    effects on the account instead of a canned call sequence.
    """
    pods = {}
    monkeypatch.setattr(runpod_instance.utils, 'list_instances', lambda: pods)
    monkeypatch.setattr(runpod_instance.utils, 'remove',
                        lambda pod_id: pods.pop(pod_id))
    monkeypatch.setattr(runpod_instance.time, 'sleep', lambda _: None)
    return pods


def _launch_into(pods, pod_id, status, ssh_port=None):
    """A ``utils.launch`` stand-in that adds the new pod to ``pods``."""

    def launch(**kwargs):
        pods[pod_id] = _pod(pod_id,
                            status,
                            name=f"{kwargs['cluster_name']}-"
                            f"{kwargs['node_type']}",
                            ssh_port=ssh_port)
        return pod_id

    return mock.Mock(side_effect=launch)


class TestQueryInstances:

    @pytest.mark.parametrize('pod_status,expected', [
        ('PROVISIONING', status_lib.ClusterStatus.INIT),
        ('STARTING', status_lib.ClusterStatus.INIT),
        ('RUNNING', status_lib.ClusterStatus.UP),
        ('EXITED', status_lib.ClusterStatus.STOPPED),
        ('ERROR', status_lib.ClusterStatus.INIT),
    ])
    def test_maps_v2_status(self, instances, pod_status, expected):
        instances['p1'] = _pod('p1', pod_status)
        statuses = runpod_instance.query_instances(_CLUSTER,
                                                   _CLUSTER,
                                                   provider_config={})
        status, reason = statuses['p1']
        assert status == expected
        assert (reason is not None) == (pod_status == 'ERROR')

    def test_terminated_pod_is_hidden_unless_asked(self, instances):
        instances['p1'] = _pod('p1', 'TERMINATED')
        assert not runpod_instance.query_instances(
            _CLUSTER, _CLUSTER, provider_config={})
        statuses = runpod_instance.query_instances(_CLUSTER,
                                                   _CLUSTER,
                                                   provider_config={},
                                                   non_terminated_only=False)
        assert statuses == {'p1': (None, None)}

    def test_ignores_other_clusters(self, instances):
        instances['p1'] = _pod('p1', 'RUNNING', name='other-head')
        assert not runpod_instance.query_instances(
            _CLUSTER, _CLUSTER, provider_config={})


class TestRunInstances:

    def test_raises_when_launched_pod_errors(self, instances, monkeypatch):
        monkeypatch.setattr(runpod_instance.utils, 'launch',
                            _launch_into(instances, 'p1', 'ERROR'))
        with pytest.raises(RuntimeError, match='ERROR'):
            runpod_instance.run_instances('RO', _CLUSTER, _CLUSTER, _config())

    def test_deletes_stale_pods_before_launching(self, instances, monkeypatch):
        instances['p1'] = _pod('p1', 'EXITED')
        launch = _launch_into(instances, 'p2', 'RUNNING', ssh_port=2222)
        monkeypatch.setattr(runpod_instance.utils, 'launch', launch)
        record = runpod_instance.run_instances('RO', _CLUSTER, _CLUSTER,
                                               _config())
        assert 'p1' not in instances
        launch.assert_called_once()
        assert record.created_instance_ids == ['p2']
        assert record.head_instance_id == 'p2'

    def test_reuses_running_pod(self, instances, monkeypatch):
        launch = mock.Mock()
        monkeypatch.setattr(runpod_instance.utils, 'launch', launch)
        instances['p1'] = _pod('p1', 'RUNNING', ssh_port=2222)
        record = runpod_instance.run_instances('RO', _CLUSTER, _CLUSTER,
                                               _config())
        launch.assert_not_called()
        assert record.head_instance_id == 'p1'
        assert record.created_instance_ids == []


class TestGetClusterInfo:

    def test_internal_ip_is_the_public_ip(self, instances):
        instances['p1'] = _pod('p1', 'RUNNING', ssh_port=2222)
        info = runpod_instance.get_cluster_info('RO',
                                                _CLUSTER,
                                                provider_config={})
        node = info.instances['p1'][0]
        assert node.internal_ip == '1.2.3.4'
        assert node.external_ip == '1.2.3.4'
        assert node.ssh_port == 2222
        assert info.head_instance_id == 'p1'
        assert info.custom_ray_options == {'num-cpus': '8'}
