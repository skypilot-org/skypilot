"""Pytest configuration for Kubernetes integration tests.

This module imports and exposes the K8s mock fixtures for use in tests.
"""

import pytest

# Import all fixtures from k8s_fixtures module to make them available
# to tests in this directory and subdirectories.
from tests.unit_tests.kubernetes.k8s_fixtures import create_gpu_cluster
from tests.unit_tests.kubernetes.k8s_fixtures import (
    create_heterogeneous_cluster)
from tests.unit_tests.kubernetes.k8s_fixtures import create_ray_cluster_pods
from tests.unit_tests.kubernetes.k8s_fixtures import create_single_node_cluster
from tests.unit_tests.kubernetes.k8s_fixtures import create_test_pod
from tests.unit_tests.kubernetes.k8s_fixtures import create_test_pvc
from tests.unit_tests.kubernetes.k8s_fixtures import create_test_service
from tests.unit_tests.kubernetes.k8s_fixtures import k8s_cluster_factory
from tests.unit_tests.kubernetes.k8s_fixtures import mock_k8s_cluster
from tests.unit_tests.kubernetes.k8s_fixtures import mock_k8s_cluster_empty
from tests.unit_tests.kubernetes.k8s_fixtures import mock_k8s_cluster_with_gpus
from tests.unit_tests.kubernetes.k8s_fixtures import (
    mock_k8s_heterogeneous_cluster)
from tests.unit_tests.kubernetes.k8s_fixtures import mock_k8s_multi_context
from tests.unit_tests.kubernetes.k8s_fixtures import mock_k8s_multinode_cluster
from tests.unit_tests.kubernetes.k8s_fixtures import mock_k8s_with_pvc
from tests.unit_tests.kubernetes.k8s_fixtures import (
    mock_k8s_with_running_cluster)
from tests.unit_tests.kubernetes.k8s_fixtures import patch_kubernetes_apis
from tests.unit_tests.kubernetes.k8s_fixtures import patch_kubernetes_utils

# Re-export fixtures so pytest can discover them
__all__ = [
    'mock_k8s_cluster',
    'mock_k8s_cluster_with_gpus',
    'mock_k8s_multinode_cluster',
    'mock_k8s_heterogeneous_cluster',
    'mock_k8s_cluster_empty',
    'mock_k8s_with_pvc',
    'mock_k8s_with_running_cluster',
    'mock_k8s_multi_context',
    'k8s_cluster_factory',
]
