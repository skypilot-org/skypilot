"""Tests for SkyServe controller version recovery."""
from unittest import mock

import pytest

from sky.serve import controller
from sky.serve import replica_managers
from sky.serve import serve_state
from sky.serve import service_spec


@pytest.mark.parametrize('version', [1, 2, 7])
def test_restored_controller_does_not_replace_current_replica(version):
    spec = service_spec.SkyServiceSpec.from_yaml_config({
        'readiness_probe': '/healthz',
        'replicas': 1,
    })
    with mock.patch.object(replica_managers, 'SkyPilotReplicaManager'):
        restored = controller.SkyServeController('test-service', spec, version,
                                                 '127.0.0.1', 30000)

    ready = mock.Mock(spec=replica_managers.ReplicaInfo)
    ready.replica_id = 1
    ready.cluster_name = 'test-service-1'
    ready.version = version
    ready.status = serve_state.ReplicaStatus.READY
    ready.is_ready = True
    ready.is_terminal = False

    assert restored._autoscaler.latest_version == version
    assert restored._autoscaler.generate_scaling_decisions([ready],
                                                           [version]) == []
