"""Unit tests for Kubernetes SSH service routing in the API server."""
import os
from unittest import mock

import pytest


class TestGetKubernetesServiceAddress:
    """Tests for _get_kubernetes_service_address function."""

    @pytest.fixture
    def mock_handle(self):
        """Create a mock handle with cached_cluster_info."""
        handle = mock.Mock()
        handle.cached_cluster_info = mock.Mock()
        handle.cached_cluster_info.provider_config = {'context': 'in-cluster'}
        head_instance = mock.Mock()
        head_instance.internal_svc = 'test-head.default.svc.cluster.local'
        head_instance.ssh_port = 22
        handle.cached_cluster_info.get_head_instance.return_value = head_instance
        return handle

    def test_returns_none_when_not_in_kubernetes(self, mock_handle):
        """Should return None when API server is not running in K8s."""
        with mock.patch(
                'sky.utils.kubernetes.config_map_utils.is_running_in_kubernetes',
                return_value=False):
            # Import here to avoid import issues
            from sky.server.server import _get_kubernetes_service_address
            result = _get_kubernetes_service_address(mock_handle)
            assert result is None

    def test_returns_none_when_port_forward_only_env_set(self, mock_handle):
        """Should return None when SKYPILOT_K8S_SSH_PORT_FORWARD_ONLY is set."""
        with mock.patch.dict(os.environ,
                             {'SKYPILOT_K8S_SSH_PORT_FORWARD_ONLY': '1'}):
            with mock.patch(
                    'sky.utils.kubernetes.config_map_utils.is_running_in_kubernetes',
                    return_value=True):
                from sky.server.server import _get_kubernetes_service_address
                result = _get_kubernetes_service_address(mock_handle)
                assert result is None

    def test_returns_none_when_cached_cluster_info_is_none(self):
        """Should return None when cached_cluster_info is None."""
        handle = mock.Mock()
        handle.cached_cluster_info = None
        with mock.patch(
                'sky.utils.kubernetes.config_map_utils.is_running_in_kubernetes',
                return_value=True):
            with mock.patch.dict(os.environ, {}, clear=True):  # Clear env vars
                from sky.server.server import _get_kubernetes_service_address
                result = _get_kubernetes_service_address(handle)
                assert result is None

    def test_returns_none_when_provider_config_is_none(self, mock_handle):
        """Should return None when provider_config is None."""
        mock_handle.cached_cluster_info.provider_config = None
        with mock.patch(
                'sky.utils.kubernetes.config_map_utils.is_running_in_kubernetes',
                return_value=True):
            with mock.patch.dict(os.environ, {}, clear=True):
                from sky.server.server import _get_kubernetes_service_address
                result = _get_kubernetes_service_address(mock_handle)
                assert result is None

    def test_returns_none_when_different_cluster(self, mock_handle):
        """Should return None when target is in a different K8s cluster."""
        mock_handle.cached_cluster_info.provider_config = {
            'context': 'external-cluster'
        }
        with mock.patch(
                'sky.utils.kubernetes.config_map_utils.is_running_in_kubernetes',
                return_value=True):
            with mock.patch(
                    'sky.provision.kubernetes.utils.get_context_from_config',
                    return_value='external-cluster'):
                with mock.patch.dict(os.environ, {}, clear=True):
                    from sky.server.server import (
                        _get_kubernetes_service_address)
                    result = _get_kubernetes_service_address(mock_handle)
                    assert result is None

    def test_returns_service_address_when_same_cluster(self, mock_handle):
        """Should return service address when in same K8s cluster."""
        with mock.patch(
                'sky.utils.kubernetes.config_map_utils.is_running_in_kubernetes',
                return_value=True):
            with mock.patch(
                    'sky.provision.kubernetes.utils.get_context_from_config',
                    return_value=None):  # None means in-cluster
                with mock.patch.dict(os.environ, {}, clear=True):
                    from sky.server.server import (
                        _get_kubernetes_service_address)
                    result = _get_kubernetes_service_address(mock_handle)
                    assert result == ('test-head.default.svc.cluster.local', 22)

    def test_returns_none_when_head_instance_is_none(self, mock_handle):
        """Should return None when head instance is None."""
        mock_handle.cached_cluster_info.get_head_instance.return_value = None
        with mock.patch(
                'sky.utils.kubernetes.config_map_utils.is_running_in_kubernetes',
                return_value=True):
            with mock.patch(
                    'sky.provision.kubernetes.utils.get_context_from_config',
                    return_value=None):
                with mock.patch.dict(os.environ, {}, clear=True):
                    from sky.server.server import (
                        _get_kubernetes_service_address)
                    result = _get_kubernetes_service_address(mock_handle)
                    assert result is None

    def test_returns_none_when_internal_svc_is_none(self, mock_handle):
        """Should return None when internal_svc is None."""
        mock_handle.cached_cluster_info.get_head_instance.return_value.internal_svc = None
        with mock.patch(
                'sky.utils.kubernetes.config_map_utils.is_running_in_kubernetes',
                return_value=True):
            with mock.patch(
                    'sky.provision.kubernetes.utils.get_context_from_config',
                    return_value=None):
                with mock.patch.dict(os.environ, {}, clear=True):
                    from sky.server.server import (
                        _get_kubernetes_service_address)
                    result = _get_kubernetes_service_address(mock_handle)
                    assert result is None
