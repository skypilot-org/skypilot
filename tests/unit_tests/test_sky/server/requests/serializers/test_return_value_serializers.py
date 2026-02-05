"""Unit tests for return_value_serializers module."""
import json
from unittest import mock

import pytest

from sky.server import constants as server_constants
from sky.server.requests.serializers import return_value_serializers


class TestRegisterSerializer:
    """Tests for the register_serializer decorator."""

    def test_register_single_name(self):
        """Test registering a serializer with a single name."""
        original_handlers = return_value_serializers.handlers.copy()
        try:

            @return_value_serializers.register_serializer('test_single')
            def test_serializer(return_value):
                return json.dumps(return_value)

            expected_key = server_constants.REQUEST_NAME_PREFIX + 'test_single'
            assert expected_key in return_value_serializers.handlers
            assert return_value_serializers.handlers[
                expected_key] is test_serializer
        finally:
            return_value_serializers.handlers.clear()
            return_value_serializers.handlers.update(original_handlers)

    def test_register_multiple_names(self):
        """Test registering a serializer with multiple names."""
        original_handlers = return_value_serializers.handlers.copy()
        try:

            @return_value_serializers.register_serializer(
                'name1', 'name2', 'name3')
            def multi_serializer(return_value):
                return json.dumps(return_value)

            for name in ['name1', 'name2', 'name3']:
                expected_key = server_constants.REQUEST_NAME_PREFIX + name
                assert expected_key in return_value_serializers.handlers
                assert return_value_serializers.handlers[
                    expected_key] is multi_serializer
        finally:
            return_value_serializers.handlers.clear()
            return_value_serializers.handlers.update(original_handlers)

    def test_register_duplicate_name_raises_error(self):
        """Test that registering the same name twice raises an error."""
        original_handlers = return_value_serializers.handlers.copy()
        try:

            @return_value_serializers.register_serializer('duplicate_name')
            def first_serializer(return_value):
                return json.dumps(return_value)

            with pytest.raises(ValueError):

                @return_value_serializers.register_serializer('duplicate_name')
                def second_serializer(return_value):
                    return json.dumps(return_value)
        finally:
            return_value_serializers.handlers.clear()
            return_value_serializers.handlers.update(original_handlers)


class TestGetSerializer:
    """Tests for the get_serializer function."""

    def test_get_registered_serializer(self):
        """Test getting a registered serializer."""
        # kubernetes_node_info should be registered
        name = server_constants.REQUEST_NAME_PREFIX + 'kubernetes_node_info'
        serializer = return_value_serializers.get_serializer(name)
        assert serializer is return_value_serializers.serialize_kubernetes_node_info

    def test_get_default_serializer_for_unknown_name(self):
        """Test that unknown names return the default serializer."""
        serializer = return_value_serializers.get_serializer(
            'unknown_request_name')
        assert serializer is return_value_serializers.default_serializer

    def test_get_default_serializer_directly(self):
        """Test getting the default serializer by name."""
        serializer = return_value_serializers.get_serializer(
            server_constants.DEFAULT_HANDLER_NAME)
        assert serializer is return_value_serializers.default_serializer


class TestDefaultSerializer:
    """Tests for the default_serializer function."""

    def test_serialize_dict(self):
        """Test serializing a dictionary."""
        data = {'key': 'value', 'number': 42}
        result = return_value_serializers.default_serializer(data)
        assert json.loads(result) == data

    def test_serialize_list(self):
        """Test serializing a list."""
        data = [1, 2, 3, 'four', {'five': 5}]
        result = return_value_serializers.default_serializer(data)
        assert json.loads(result) == data

    def test_serialize_none(self):
        """Test serializing None."""
        result = return_value_serializers.default_serializer(None)
        assert json.loads(result) is None

    def test_serialize_primitives(self):
        """Test serializing primitive types."""
        for value in [42, 3.14, 'string', True, False]:
            result = return_value_serializers.default_serializer(value)
            assert json.loads(result) == value

    def test_serialize_nested_structure(self):
        """Test serializing a nested structure."""
        data = {
            'nodes': [{
                'name': 'node1',
                'status': 'ready'
            }, {
                'name': 'node2',
                'status': 'pending'
            }],
            'metadata': {
                'count': 2,
                'active': True
            }
        }
        result = return_value_serializers.default_serializer(data)
        assert json.loads(result) == data


class TestSerializeKubernetesNodeInfo:
    """Tests for the serialize_kubernetes_node_info function."""

    def test_empty_return_value(self):
        """Test with empty/None return value."""
        result = return_value_serializers.serialize_kubernetes_node_info(None)
        assert json.loads(result) is None

        result = return_value_serializers.serialize_kubernetes_node_info({})
        assert json.loads(result) == {}

    def test_no_node_info_dict(self):
        """Test when node_info_dict is not present."""
        data = {'other_key': 'value'}
        result = return_value_serializers.serialize_kubernetes_node_info(data)
        assert json.loads(result) == data

    @mock.patch(
        'sky.server.requests.serializers.return_value_serializers.versions.get_remote_api_version'
    )
    def test_remote_version_none_keeps_is_ready(self, mock_get_version):
        """Test that is_ready is kept when remote_api_version is None."""
        mock_get_version.return_value = None
        data = {
            'node_info_dict': {
                'node1': {
                    'name': 'node1',
                    'is_ready': True
                },
                'node2': {
                    'name': 'node2',
                    'is_ready': False
                }
            }
        }
        result = return_value_serializers.serialize_kubernetes_node_info(data)
        parsed = json.loads(result)
        # is_ready should be preserved
        assert parsed['node_info_dict']['node1']['is_ready'] is True
        assert parsed['node_info_dict']['node2']['is_ready'] is False

    @mock.patch(
        'sky.server.requests.serializers.return_value_serializers.versions.get_remote_api_version'
    )
    def test_remote_version_25_keeps_is_ready(self, mock_get_version):
        """Test that is_ready is kept when remote_api_version >= 25."""
        mock_get_version.return_value = 25
        data = {
            'node_info_dict': {
                'node1': {
                    'name': 'node1',
                    'is_ready': True
                },
                'node2': {
                    'name': 'node2',
                    'is_ready': False
                }
            }
        }
        result = return_value_serializers.serialize_kubernetes_node_info(data)
        parsed = json.loads(result)
        # is_ready should be preserved
        assert parsed['node_info_dict']['node1']['is_ready'] is True
        assert parsed['node_info_dict']['node2']['is_ready'] is False

    @mock.patch(
        'sky.server.requests.serializers.return_value_serializers.versions.get_remote_api_version'
    )
    def test_remote_version_above_25_keeps_is_ready(self, mock_get_version):
        """Test that is_ready is kept when remote_api_version > 25."""
        mock_get_version.return_value = 30
        data = {
            'node_info_dict': {
                'node1': {
                    'name': 'node1',
                    'is_ready': True
                }
            }
        }
        result = return_value_serializers.serialize_kubernetes_node_info(data)
        parsed = json.loads(result)
        assert parsed['node_info_dict']['node1']['is_ready'] is True

    @mock.patch(
        'sky.server.requests.serializers.return_value_serializers.versions.get_remote_api_version'
    )
    def test_remote_version_below_25_removes_is_ready(self, mock_get_version):
        """Test that is_ready is removed when remote_api_version < 25."""
        mock_get_version.return_value = 24
        data = {
            'node_info_dict': {
                'node1': {
                    'name': 'node1',
                    'is_ready': True,
                    'other_field': 'value'
                },
                'node2': {
                    'name': 'node2',
                    'is_ready': False,
                    'other_field': 'value2'
                }
            }
        }
        result = return_value_serializers.serialize_kubernetes_node_info(data)
        parsed = json.loads(result)
        # is_ready should be removed
        assert 'is_ready' not in parsed['node_info_dict']['node1']
        assert 'is_ready' not in parsed['node_info_dict']['node2']
        # Other fields should remain
        assert parsed['node_info_dict']['node1']['name'] == 'node1'
        assert parsed['node_info_dict']['node1']['other_field'] == 'value'
        assert parsed['node_info_dict']['node2']['name'] == 'node2'
        assert parsed['node_info_dict']['node2']['other_field'] == 'value2'

    @mock.patch(
        'sky.server.requests.serializers.return_value_serializers.versions.get_remote_api_version'
    )
    def test_node_without_is_ready_field(self, mock_get_version):
        """Test that nodes without is_ready field are handled gracefully."""
        mock_get_version.return_value = 20
        data = {
            'node_info_dict': {
                'node1': {
                    'name': 'node1'
                },  # No is_ready field
                'node2': {
                    'name': 'node2',
                    'is_ready': True
                }
            }
        }
        # Should not raise an error
        result = return_value_serializers.serialize_kubernetes_node_info(data)
        parsed = json.loads(result)
        assert 'is_ready' not in parsed['node_info_dict']['node1']
        assert 'is_ready' not in parsed['node_info_dict']['node2']

    @mock.patch(
        'sky.server.requests.serializers.return_value_serializers.versions.get_remote_api_version'
    )
    def test_empty_node_info_dict(self, mock_get_version):
        """Test with empty node_info_dict."""
        mock_get_version.return_value = 20
        data = {'node_info_dict': {}}
        result = return_value_serializers.serialize_kubernetes_node_info(data)
        assert json.loads(result) == data

    @mock.patch(
        'sky.server.requests.serializers.return_value_serializers.versions.get_remote_api_version'
    )
    def test_remote_version_28_keeps_is_cordoned_and_taints(
            self, mock_get_version):
        """Test that is_cordoned and taints are kept when remote_api_version >= 28."""
        mock_get_version.return_value = 28
        data = {
            'node_info_dict': {
                'node1': {
                    'name': 'node1',
                    'is_cordoned': True,
                    'taints': ['NoSchedule']
                },
                'node2': {
                    'name': 'node2',
                    'is_cordoned': False,
                    'taints': []
                }
            }
        }
        result = return_value_serializers.serialize_kubernetes_node_info(data)
        parsed = json.loads(result)
        # is_cordoned and taints should be preserved
        assert parsed['node_info_dict']['node1']['is_cordoned'] is True
        assert parsed['node_info_dict']['node1']['taints'] == ['NoSchedule']
        assert parsed['node_info_dict']['node2']['is_cordoned'] is False
        assert parsed['node_info_dict']['node2']['taints'] == []

    @mock.patch(
        'sky.server.requests.serializers.return_value_serializers.versions.get_remote_api_version'
    )
    def test_remote_version_above_28_keeps_is_cordoned_and_taints(
            self, mock_get_version):
        """Test that is_cordoned and taints are kept when remote_api_version > 28."""
        mock_get_version.return_value = 30
        data = {
            'node_info_dict': {
                'node1': {
                    'name': 'node1',
                    'is_cordoned': True,
                    'taints': ['NoSchedule', 'NoExecute']
                }
            }
        }
        result = return_value_serializers.serialize_kubernetes_node_info(data)
        parsed = json.loads(result)
        assert parsed['node_info_dict']['node1']['is_cordoned'] is True
        assert parsed['node_info_dict']['node1']['taints'] == [
            'NoSchedule', 'NoExecute'
        ]

    @mock.patch(
        'sky.server.requests.serializers.return_value_serializers.versions.get_remote_api_version'
    )
    def test_remote_version_below_28_removes_is_cordoned_and_taints(
            self, mock_get_version):
        """Test that is_cordoned and taints are removed when remote_api_version < 28."""
        mock_get_version.return_value = 27
        data = {
            'node_info_dict': {
                'node1': {
                    'name': 'node1',
                    'is_cordoned': True,
                    'taints': ['NoSchedule'],
                    'other_field': 'value'
                },
                'node2': {
                    'name': 'node2',
                    'is_cordoned': False,
                    'taints': [],
                    'other_field': 'value2'
                }
            }
        }
        result = return_value_serializers.serialize_kubernetes_node_info(data)
        parsed = json.loads(result)
        # is_cordoned and taints should be removed
        assert 'is_cordoned' not in parsed['node_info_dict']['node1']
        assert 'taints' not in parsed['node_info_dict']['node1']
        assert 'is_cordoned' not in parsed['node_info_dict']['node2']
        assert 'taints' not in parsed['node_info_dict']['node2']
        # Other fields should remain
        assert parsed['node_info_dict']['node1']['name'] == 'node1'
        assert parsed['node_info_dict']['node1']['other_field'] == 'value'
        assert parsed['node_info_dict']['node2']['name'] == 'node2'
        assert parsed['node_info_dict']['node2']['other_field'] == 'value2'

    @mock.patch(
        'sky.server.requests.serializers.return_value_serializers.versions.get_remote_api_version'
    )
    def test_combined_version_compatibility_old_client(self, mock_get_version):
        """Test combined version compatibility for old clients (API version < 25).

        Old clients should not see any of the newer fields: is_ready,
        cpu_count, memory_gb, cpu_free, memory_free_gb, is_cordoned, taints.
        """
        mock_get_version.return_value = 24
        data = {
            'node_info_dict': {
                'node1': {
                    'name': 'node1',
                    'is_ready': True,
                    'cpu_count': 8,
                    'memory_gb': 32.0,
                    'cpu_free': 4,
                    'memory_free_gb': 16.0,
                    'is_cordoned': False,
                    'taints': [],
                    'other_field': 'value'
                }
            }
        }
        result = return_value_serializers.serialize_kubernetes_node_info(data)
        parsed = json.loads(result)
        node = parsed['node_info_dict']['node1']
        # All newer fields should be removed
        assert 'is_ready' not in node
        assert 'cpu_count' not in node
        assert 'memory_gb' not in node
        assert 'cpu_free' not in node
        assert 'memory_free_gb' not in node
        assert 'is_cordoned' not in node
        assert 'taints' not in node
        # Basic fields should remain
        assert node['name'] == 'node1'
        assert node['other_field'] == 'value'

    @mock.patch(
        'sky.server.requests.serializers.return_value_serializers.versions.get_remote_api_version'
    )
    def test_combined_version_compatibility_version_25(self, mock_get_version):
        """Test combined version compatibility for API version 25 clients.

        Version 25 clients should see is_ready but not cpu_count, memory_gb,
        cpu_free, memory_free_gb, is_cordoned, taints.
        """
        mock_get_version.return_value = 25
        data = {
            'node_info_dict': {
                'node1': {
                    'name': 'node1',
                    'is_ready': True,
                    'cpu_count': 8,
                    'memory_gb': 32.0,
                    'cpu_free': 4,
                    'memory_free_gb': 16.0,
                    'is_cordoned': False,
                    'taints': ['NoSchedule']
                }
            }
        }
        result = return_value_serializers.serialize_kubernetes_node_info(data)
        parsed = json.loads(result)
        node = parsed['node_info_dict']['node1']
        # is_ready should be preserved
        assert node['is_ready'] is True
        # Resource fields should be removed
        assert 'cpu_count' not in node
        assert 'memory_gb' not in node
        assert 'cpu_free' not in node
        assert 'memory_free_gb' not in node
        # Cordon/taint fields should be removed
        assert 'is_cordoned' not in node
        assert 'taints' not in node

    @mock.patch(
        'sky.server.requests.serializers.return_value_serializers.versions.get_remote_api_version'
    )
    def test_combined_version_compatibility_version_26(self, mock_get_version):
        """Test combined version compatibility for API version 26 clients.

        Version 26 clients should see is_ready and resource fields but not
        is_cordoned and taints.
        """
        mock_get_version.return_value = 26
        data = {
            'node_info_dict': {
                'node1': {
                    'name': 'node1',
                    'is_ready': True,
                    'cpu_count': 8,
                    'memory_gb': 32.0,
                    'cpu_free': 4,
                    'memory_free_gb': 16.0,
                    'is_cordoned': True,
                    'taints': ['NoSchedule']
                }
            }
        }
        result = return_value_serializers.serialize_kubernetes_node_info(data)
        parsed = json.loads(result)
        node = parsed['node_info_dict']['node1']
        # is_ready and resource fields should be preserved
        assert node['is_ready'] is True
        assert node['cpu_count'] == 8
        assert node['memory_gb'] == 32.0
        assert node['cpu_free'] == 4
        assert node['memory_free_gb'] == 16.0
        # Cordon/taint fields should be removed
        assert 'is_cordoned' not in node
        assert 'taints' not in node


class TestHandlersRegistration:
    """Tests to verify the handlers are registered correctly at import time."""

    def test_default_handler_registered(self):
        """Test that the default handler is registered."""
        assert server_constants.DEFAULT_HANDLER_NAME in return_value_serializers.handlers

    def test_kubernetes_node_info_handler_registered(self):
        """Test that the kubernetes_node_info handler is registered."""
        expected_key = server_constants.REQUEST_NAME_PREFIX + 'kubernetes_node_info'
        assert expected_key in return_value_serializers.handlers
