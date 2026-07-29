"""Unit tests for sky.provision.kubernetes.dra_utils."""
from typing import Any, Dict
from unittest import mock

import pytest

from sky import exceptions
from sky.provision.kubernetes import dra_utils
from sky.provision.kubernetes import utils as kubernetes_utils


def _api_exception(status: int):
    # pylint: disable=import-outside-toplevel
    from kubernetes.client.rest import ApiException
    return ApiException(status=status, reason='test')


DEVICE_CLASSES = {
    'items': [{
        'metadata': {
            'name': 'gpu.nvidia.com'
        },
        'spec': {
            'extendedResourceName': 'nvidia.com/gpu'
        },
    }, {
        'metadata': {
            'name': 'compute-domain.nvidia.com'
        },
        'spec': {},
    }]
}

DEVICE_CLASSES_NO_MAPPING = {
    'items': [{
        'metadata': {
            'name': 'gpu.nvidia.com'
        },
        'spec': {},
    }]
}

_H100_PRODUCT = 'NVIDIA H100 80GB HBM3'
_H100_CANONICAL = (
    kubernetes_utils.GFDLabelFormatter.get_accelerator_from_label_value(
        _H100_PRODUCT.replace(' ', '-')))


def _gpu_device(name: str, product: str = _H100_PRODUCT) -> Dict[str, Any]:
    return {'name': name, 'attributes': {'productName': {'string': product}}}


RESOURCE_SLICES = {
    'items': [
        # Node-local GPU slice: 2 H100s on node1.
        {
            'metadata': {
                'name': 'node1-slice'
            },
            'spec': {
                'driver': 'gpu.nvidia.com',
                'nodeName': 'node1',
                'pool': {
                    'name': 'node1'
                },
                'devices': [_gpu_device('gpu-0'),
                            _gpu_device('gpu-1')],
            },
        },
        # Unknown (non-GPU) driver: ignored.
        {
            'metadata': {
                'name': 'dranet-slice'
            },
            'spec': {
                'driver': 'dra.net',
                'nodeName': 'node1',
                'pool': {
                    'name': 'node1'
                },
                'devices': [{
                    'name': 'nic-0'
                }],
            },
        },
        # Non-node-local slice: ignored.
        {
            'metadata': {
                'name': 'clusterwide-slice'
            },
            'spec': {
                'driver': 'gpu.nvidia.com',
                'allNodes': True,
                'pool': {
                    'name': 'shared'
                },
                'devices': [_gpu_device('gpu-x')],
            },
        },
    ]
}

RESOURCE_CLAIMS = {
    'items': [
        # Allocated claim consuming gpu-0 on node1.
        {
            'metadata': {
                'name': 'claim-1',
                'namespace': 'default'
            },
            'status': {
                'allocation': {
                    'devices': {
                        'results': [{
                            'driver': 'gpu.nvidia.com',
                            'pool': 'node1',
                            'device': 'gpu-0',
                        }]
                    }
                }
            },
        },
        # Pending claim: no allocation, ignored.
        {
            'metadata': {
                'name': 'claim-2',
                'namespace': 'default'
            },
            'status': {},
        },
        # Admin-access allocation: does not consume the device.
        {
            'metadata': {
                'name': 'claim-3',
                'namespace': 'monitoring'
            },
            'status': {
                'allocation': {
                    'devices': {
                        'results': [{
                            'driver': 'gpu.nvidia.com',
                            'pool': 'node1',
                            'device': 'gpu-1',
                            'adminAccess': True,
                        }]
                    }
                }
            },
        },
        # Allocation of a device we did not index: ignored.
        {
            'metadata': {
                'name': 'claim-4',
                'namespace': 'default'
            },
            'status': {
                'allocation': {
                    'devices': {
                        'results': [{
                            'driver': 'dra.net',
                            'pool': 'node1',
                            'device': 'nic-0',
                        }]
                    }
                }
            },
        },
    ]
}


def _clear_all_dra_caches():
    dra_utils.get_extended_resource_mapping.cache_clear()
    # pylint: disable=protected-access
    dra_utils._dra_api_available.cache_clear()
    dra_utils._get_device_index.cache_clear()


@pytest.fixture(autouse=True)
def _clear_dra_caches():
    _clear_all_dra_caches()
    yield
    _clear_all_dra_caches()


def _make_lister(objects_by_plural: Dict[str, Any]):
    """Returns a fake _list_dra_objects serving the given fixtures.

    Missing plurals raise 404, mimicking an API server without the
    resource.k8s.io/v1 API (or resource).
    """

    def _fake(context, plural):
        del context  # Unused.
        objects = objects_by_plural.get(plural)
        if objects is None:
            raise _api_exception(404)
        return objects

    return _fake


_V1_CLUSTER = {
    'deviceclasses': DEVICE_CLASSES,
    'resourceslices': RESOURCE_SLICES,
    'resourceclaims': RESOURCE_CLAIMS,
}


class TestDRAApiProbe:
    """Tests for resource.k8s.io/v1 API availability probing."""

    def test_v1_available(self):
        with mock.patch.object(dra_utils, '_list_dra_objects',
                               _make_lister(_V1_CLUSTER)):
            # pylint: disable=protected-access
            assert dra_utils._dra_api_available('ctx')

    def test_api_group_absent(self):
        # Covers both pre-DRA clusters and pre-GA (beta-only) clusters:
        # the v1 endpoint returns 404 in either case.
        with mock.patch.object(dra_utils, '_list_dra_objects',
                               _make_lister({})):
            # pylint: disable=protected-access
            assert not dra_utils._dra_api_available('ctx')
            assert not dra_utils.detect_dra('ctx')
            assert not dra_utils.get_dra_node_capacity('ctx')
            assert not dra_utils.get_dra_allocated_by_node('ctx')
            assert not dra_utils.get_extended_resource_mapping('ctx')

    def test_forbidden_disables_dra(self):

        def _forbidden(context, plural):
            del context, plural  # Unused.
            raise _api_exception(403)

        with mock.patch.object(dra_utils, '_list_dra_objects', _forbidden):
            # pylint: disable=protected-access
            assert not dra_utils._dra_api_available('ctx')
            assert not dra_utils.detect_dra('ctx')


class TestDRADiscovery:
    """Tests for DRA capacity discovery from ResourceSlices."""

    def test_extended_resource_mapping(self):
        with mock.patch.object(dra_utils, '_list_dra_objects',
                               _make_lister(_V1_CLUSTER)):
            assert dra_utils.get_extended_resource_mapping('ctx') == {
                'nvidia.com/gpu': 'gpu.nvidia.com'
            }

    def test_node_capacity(self):
        with mock.patch.object(dra_utils, '_list_dra_objects',
                               _make_lister(_V1_CLUSTER)):
            capacity = dra_utils.get_dra_node_capacity('ctx')
            # Non-GPU drivers and non-node-local slices are excluded.
            assert capacity == {'node1': {_H100_CANONICAL: 2}}
            assert dra_utils.detect_dra('ctx')
            assert dra_utils.list_dra_accelerators('ctx') == [_H100_CANONICAL]

    def test_count_for_acc_matches_type(self):
        with mock.patch.object(dra_utils, '_list_dra_objects',
                               _make_lister(_V1_CLUSTER)):
            assert dra_utils.get_dra_node_count_for_acc('ctx', 'node1',
                                                        _H100_CANONICAL) == 2
            assert dra_utils.get_dra_node_count_for_acc('ctx', 'node1',
                                                        'A100') == 0
            assert dra_utils.get_dra_node_count_for_acc('ctx', 'unknown-node',
                                                        _H100_CANONICAL) == 0
            assert dra_utils.has_dra_accelerator('ctx', _H100_CANONICAL, 2)
            assert not dra_utils.has_dra_accelerator('ctx', _H100_CANONICAL, 3)


class TestDRAAllocation:
    """Tests for DRA allocation accounting from ResourceClaims."""

    def test_allocated_by_node(self):
        with mock.patch.object(dra_utils, '_list_dra_objects',
                               _make_lister(_V1_CLUSTER)):
            allocated = dra_utils.get_dra_allocated_by_node('ctx')
            # claim-1 consumes one GPU; claim-3 is admin access (skipped);
            # claim-2 is pending; claim-4 targets an unindexed device.
            assert allocated == {'node1': {_H100_CANONICAL: 1}}


class TestWriteSideRemap:
    """Tests for KEP-5004 write-side resource key validation/remapping."""

    def _mock_nodes(self, allocatable: Dict[str, str]):
        node = mock.MagicMock()
        node.status.allocatable = allocatable
        return [node]

    def test_no_dra_returns_key_unchanged(self):
        with mock.patch.object(dra_utils, '_list_dra_objects',
                               _make_lister({})):
            assert dra_utils.maybe_remap_resource_key_for_dra(
                'ctx', 'nvidia.com/gpu') == 'nvidia.com/gpu'

    def test_device_plugin_still_present_returns_key(self):
        with mock.patch.object(dra_utils, '_list_dra_objects',
                               _make_lister(_V1_CLUSTER)), \
             mock.patch.object(kubernetes_utils, 'get_kubernetes_nodes',
                               return_value=self._mock_nodes(
                                   {'nvidia.com/gpu': '8'})):
            assert dra_utils.maybe_remap_resource_key_for_dra(
                'ctx', 'nvidia.com/gpu') == 'nvidia.com/gpu'

    def test_dra_only_with_mapping_returns_mapped_key(self):
        with mock.patch.object(dra_utils, '_list_dra_objects',
                               _make_lister(_V1_CLUSTER)), \
             mock.patch.object(kubernetes_utils, 'get_kubernetes_nodes',
                               return_value=self._mock_nodes({})):
            assert dra_utils.maybe_remap_resource_key_for_dra(
                'ctx', 'nvidia.com/gpu') == 'nvidia.com/gpu'

    def test_dra_only_without_mapping_raises(self):
        cluster = {
            'deviceclasses': DEVICE_CLASSES_NO_MAPPING,
            'resourceslices': RESOURCE_SLICES,
            'resourceclaims': RESOURCE_CLAIMS,
        }
        with mock.patch.object(dra_utils, '_list_dra_objects',
                               _make_lister(cluster)), \
             mock.patch.object(kubernetes_utils, 'get_kubernetes_nodes',
                               return_value=self._mock_nodes({})):
            with pytest.raises(exceptions.ResourcesUnavailableError,
                               match='DRAExtendedResource'):
                dra_utils.maybe_remap_resource_key_for_dra(
                    'ctx', 'nvidia.com/gpu')


class TestIsDRANode:
    """Tests for the per-node DRA vs device-plugin classification."""

    def test_classification(self):
        # A node is a DRA node iff it appears in a GPU driver's
        # ResourceSlices: node1 does (fixtures), node2 does not (device
        # plugin only), and a node appearing only in a non-GPU driver's
        # slices does not count.
        with mock.patch.object(dra_utils, '_list_dra_objects',
                               _make_lister(_V1_CLUSTER)):
            assert dra_utils.get_dra_gpu_node_names('ctx') == {'node1'}
            assert dra_utils.is_dra_node('ctx', 'node1')
            assert not dra_utils.is_dra_node('ctx', 'node2')

    def test_no_dra_cluster(self):
        with mock.patch.object(dra_utils, '_list_dra_objects',
                               _make_lister({})):
            assert dra_utils.get_dra_gpu_node_names('ctx') == set()
            assert not dra_utils.is_dra_node('ctx', 'node1')


class TestPodExtendedResourceClaimStatus:
    """Tests for parsing pod.status.extendedResourceClaimStatus."""

    def test_pod_flag_parsed_from_status(self):
        pod_dict = {
            'metadata': {
                'name': 'p1'
            },
            'status': {
                'phase': 'Running',
                'extendedResourceClaimStatus': {
                    'resourceClaimName': 'p1-extended-resources'
                },
            },
            'spec': {
                'nodeName': 'node1',
                'containers': [{
                    'resources': {
                        'requests': {
                            'nvidia.com/gpu': '1'
                        }
                    }
                }],
            },
        }
        pod = kubernetes_utils.V1Pod.from_dict(pod_dict)
        assert pod.status.has_extended_resource_claims

        pod_dict['status'].pop('extendedResourceClaimStatus')
        pod = kubernetes_utils.V1Pod.from_dict(pod_dict)
        assert not pod.status.has_extended_resource_claims
