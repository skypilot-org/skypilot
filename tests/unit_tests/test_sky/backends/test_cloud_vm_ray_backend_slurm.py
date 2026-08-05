"""Unit tests for CloudVmRayBackend Slurm helpers."""

# pylint: disable=protected-access

from unittest import mock

from sky.backends import cloud_vm_ray_backend
from sky.provision import common as provision_common


def _make_handle(cluster_info):
    handle = mock.MagicMock()
    handle.cached_cluster_info = cluster_info
    return handle


class TestCloudVmRayBackendSlurmNodeNames:
    """Tests for Slurm node name extraction from cluster metadata."""

    def test_returns_names_aligned_with_internal_ips(self):
        cluster_info = provision_common.ClusterInfo(
            instances={
                'job-node-a': [
                    provision_common.InstanceInfo(instance_id='job-node-a',
                                                  internal_ip='10.0.0.1',
                                                  external_ip=None,
                                                  tags={'node': 'node-a'})
                ],
                'job-node-b': [
                    provision_common.InstanceInfo(instance_id='job-node-b',
                                                  internal_ip='10.0.0.2',
                                                  external_ip=None,
                                                  tags={'node': 'node-b'})
                ],
            },
            head_instance_id='job-node-a',
            provider_name='slurm')

        node_names = (
            cloud_vm_ray_backend.CloudVmRayBackend._get_slurm_node_names(
                _make_handle(cluster_info), ['10.0.0.2', '10.0.0.1']))

        assert node_names == ['node-b', 'node-a']

    def test_returns_none_when_metadata_is_incomplete(self):
        cluster_info = provision_common.ClusterInfo(
            instances={
                'job-node-a': [
                    provision_common.InstanceInfo(instance_id='job-node-a',
                                                  internal_ip='10.0.0.1',
                                                  external_ip=None,
                                                  tags={})
                ],
            },
            head_instance_id='job-node-a',
            provider_name='slurm')

        node_names = (
            cloud_vm_ray_backend.CloudVmRayBackend._get_slurm_node_names(
                _make_handle(cluster_info), ['10.0.0.1']))

        assert node_names is None

    def test_returns_none_when_an_ip_is_not_covered(self):
        cluster_info = provision_common.ClusterInfo(
            instances={
                'job-node-a': [
                    provision_common.InstanceInfo(instance_id='job-node-a',
                                                  internal_ip='10.0.0.1',
                                                  external_ip=None,
                                                  tags={'node': 'node-a'})
                ],
            },
            head_instance_id='job-node-a',
            provider_name='slurm')

        node_names = (
            cloud_vm_ray_backend.CloudVmRayBackend._get_slurm_node_names(
                _make_handle(cluster_info), ['10.0.0.1', '10.0.0.2']))

        assert node_names is None

    def test_returns_none_without_cached_cluster_info(self):
        node_names = (
            cloud_vm_ray_backend.CloudVmRayBackend._get_slurm_node_names(
                _make_handle(None), ['10.0.0.1']))

        assert node_names is None
