"""Tests for the TPU VM checks in sky.clouds.utils.gcp_utils."""
from unittest import mock

import pytest

import sky
from sky.backends import cloud_vm_ray_backend
from sky.clouds.utils import gcp_utils


@pytest.mark.parametrize('accelerators', [
    'tpu-v6e-4',
    'tpu-v6e-8',
    'tpu-v6e-16',
    'tpu-v6e-slice:4',
])
def test_kubernetes_tpu_is_never_a_tpu_vm(accelerators):
    """On Kubernetes a TPU runs as GKE TPU pods, whatever its spelling."""
    resources = sky.Resources(infra='k8s/test-context',
                              accelerators=accelerators)
    assert not gcp_utils.is_tpu_vm(resources)
    assert not gcp_utils.is_tpu_vm_pod(resources)


@pytest.mark.parametrize('accelerators, pod', [
    ('tpu-v6e-4', True),
    ('tpu-v6e-8', False),
    ('tpu-v6e-16', True),
])
def test_gcp_tpu_vm_unchanged(accelerators, pod):
    resources = sky.Resources(infra='gcp', accelerators=accelerators)
    assert gcp_utils.is_tpu_vm(resources)
    assert gcp_utils.is_tpu_vm_pod(resources) is pod


def test_kubernetes_tpu_handle_has_one_ip_per_node():
    """num_ips_per_node is 1 for a Kubernetes TPU cluster with several pods.

    Before, a GCP TPU name not ending in -8 (e.g. tpu-v6e-4) counted as a
    TPU VM pod, so every pod's IP was counted for each node.
    """
    handle = mock.MagicMock(spec=cloud_vm_ray_backend.CloudVmRayResourceHandle)
    handle.launched_resources = sky.Resources(infra='k8s/test-context',
                                              accelerators='tpu-v6e-4')
    handle.internal_ips.return_value = ['10.0.0.1', '10.0.0.2']
    num_ips = (cloud_vm_ray_backend.CloudVmRayResourceHandle.num_ips_per_node.
               fget(handle))
    assert num_ips == 1
