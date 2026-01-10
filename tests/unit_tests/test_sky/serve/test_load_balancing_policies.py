"""Unit tests for sky/serve/load_balancing_policies.py."""

from unittest import mock

import pytest

from sky.serve import load_balancing_policies


class TestRequestRepr:
    """Tests for _request_repr() function."""

    def test_formats_request_correctly(self):
        """Test that request is formatted correctly."""
        mock_request = mock.Mock()
        mock_request.method = 'POST'
        mock_request.url = 'http://example.com/api'
        mock_request.headers = {'Content-Type': 'application/json'}
        mock_request.query_params = {'page': '1'}

        result = load_balancing_policies._request_repr(mock_request)

        assert '<Request' in result
        assert 'method="POST"' in result
        assert 'url="http://example.com/api"' in result
        assert 'Content-Type' in result
        assert 'page' in result


class TestLoadBalancingPolicy:
    """Tests for LoadBalancingPolicy base class."""

    def test_make_policy_with_valid_name(self):
        """Test creating a policy with a valid name."""
        policy = load_balancing_policies.LoadBalancingPolicy.make('round_robin')
        assert isinstance(policy, load_balancing_policies.RoundRobinPolicy)

    def test_make_policy_with_default(self):
        """Test creating a policy with None uses default."""
        policy = load_balancing_policies.LoadBalancingPolicy.make(None)
        # Default is least_load
        assert isinstance(policy, load_balancing_policies.LeastLoadPolicy)

    def test_make_policy_with_invalid_name(self):
        """Test creating a policy with invalid name raises error."""
        with pytest.raises(ValueError, match='Unknown load balancing policy'):
            load_balancing_policies.LoadBalancingPolicy.make('invalid_policy')

    def test_make_policy_name_with_none(self):
        """Test make_policy_name returns default for None."""
        result = load_balancing_policies.LoadBalancingPolicy.make_policy_name(
            None)
        assert result == 'least_load'

    def test_make_policy_name_with_value(self):
        """Test make_policy_name returns provided value."""
        result = load_balancing_policies.LoadBalancingPolicy.make_policy_name(
            'round_robin')
        assert result == 'round_robin'


class TestRoundRobinPolicy:
    """Tests for RoundRobinPolicy class."""

    def test_initial_state(self):
        """Test initial state of policy."""
        policy = load_balancing_policies.RoundRobinPolicy()
        assert policy.ready_replicas == []
        assert policy.index == 0

    def test_set_ready_replicas(self):
        """Test setting ready replicas."""
        policy = load_balancing_policies.RoundRobinPolicy()
        replicas = ['http://replica1:8000', 'http://replica2:8000']

        policy.set_ready_replicas(replicas)

        assert set(policy.ready_replicas) == set(replicas)
        assert policy.index == 0

    def test_set_ready_replicas_same_set(self):
        """Test setting same replicas doesn't reset."""
        policy = load_balancing_policies.RoundRobinPolicy()
        replicas = ['http://replica1:8000', 'http://replica2:8000']

        policy.set_ready_replicas(replicas)
        policy.index = 1  # Simulate some requests
        policy.set_ready_replicas(replicas)

        # Index should remain unchanged since replicas are the same
        assert policy.index == 1

    def test_select_replica_empty(self):
        """Test selecting replica when none available."""
        policy = load_balancing_policies.RoundRobinPolicy()

        result = policy._select_replica(mock.Mock())

        assert result is None

    def test_select_replica_round_robin(self):
        """Test that replicas are selected in round-robin order."""
        policy = load_balancing_policies.RoundRobinPolicy()
        replicas = ['http://replica1:8000', 'http://replica2:8000']
        policy.ready_replicas = replicas
        policy.index = 0

        mock_request = mock.Mock()

        # First selection
        result1 = policy._select_replica(mock_request)
        assert result1 == 'http://replica1:8000'

        # Second selection
        result2 = policy._select_replica(mock_request)
        assert result2 == 'http://replica2:8000'

        # Third selection (wraps around)
        result3 = policy._select_replica(mock_request)
        assert result3 == 'http://replica1:8000'


class TestLeastLoadPolicy:
    """Tests for LeastLoadPolicy class."""

    def test_initial_state(self):
        """Test initial state of policy."""
        policy = load_balancing_policies.LeastLoadPolicy()
        assert policy.ready_replicas == []
        assert len(policy.load_map) == 0

    def test_set_ready_replicas(self):
        """Test setting ready replicas."""
        policy = load_balancing_policies.LeastLoadPolicy()
        replicas = ['http://replica1:8000', 'http://replica2:8000']

        policy.set_ready_replicas(replicas)

        assert set(policy.ready_replicas) == set(replicas)
        # All replicas should be in load_map with 0 load
        for r in replicas:
            assert r in policy.load_map
            assert policy.load_map[r] == 0

    def test_set_ready_replicas_removes_old(self):
        """Test that old replicas are removed from load_map."""
        policy = load_balancing_policies.LeastLoadPolicy()

        policy.set_ready_replicas(['http://replica1:8000'])
        policy.load_map['http://replica1:8000'] = 5

        # Update with new replicas
        policy.set_ready_replicas(['http://replica2:8000'])

        assert 'http://replica1:8000' not in policy.load_map
        assert 'http://replica2:8000' in policy.load_map

    def test_select_replica_empty(self):
        """Test selecting replica when none available."""
        policy = load_balancing_policies.LeastLoadPolicy()

        result = policy._select_replica(mock.Mock())

        assert result is None

    def test_select_replica_least_load(self):
        """Test that replica with least load is selected."""
        policy = load_balancing_policies.LeastLoadPolicy()
        replicas = ['http://replica1:8000', 'http://replica2:8000']
        policy.ready_replicas = replicas
        policy.load_map = {'http://replica1:8000': 5, 'http://replica2:8000': 2}

        result = policy._select_replica(mock.Mock())

        assert result == 'http://replica2:8000'

    def test_pre_execute_hook_increments_load(self):
        """Test that pre_execute_hook increments load."""
        policy = load_balancing_policies.LeastLoadPolicy()
        policy.load_map['http://replica1:8000'] = 3

        policy.pre_execute_hook('http://replica1:8000', mock.Mock())

        assert policy.load_map['http://replica1:8000'] == 4

    def test_post_execute_hook_decrements_load(self):
        """Test that post_execute_hook decrements load."""
        policy = load_balancing_policies.LeastLoadPolicy()
        policy.load_map['http://replica1:8000'] = 3

        policy.post_execute_hook('http://replica1:8000', mock.Mock())

        assert policy.load_map['http://replica1:8000'] == 2


class TestInstanceAwareLeastLoadPolicy:
    """Tests for InstanceAwareLeastLoadPolicy class."""

    def test_initial_state(self):
        """Test initial state of policy."""
        policy = load_balancing_policies.InstanceAwareLeastLoadPolicy()
        assert policy.ready_replicas == []
        assert len(policy.replica_info) == 0
        assert len(policy.target_qps_per_accelerator) == 0

    def test_set_replica_info(self):
        """Test setting replica info."""
        policy = load_balancing_policies.InstanceAwareLeastLoadPolicy()
        info = {
            'http://replica1:8000': {
                'gpu_type': 'A100'
            },
            'http://replica2:8000': {
                'gpu_type': 'H100'
            }
        }

        policy.set_replica_info(info)

        assert policy.replica_info == info

    def test_set_target_qps_per_accelerator(self):
        """Test setting target QPS per accelerator."""
        policy = load_balancing_policies.InstanceAwareLeastLoadPolicy()
        qps = {'A100': 10.0, 'H100': 20.0}

        policy.set_target_qps_per_accelerator(qps)

        assert policy.target_qps_per_accelerator == qps

    def test_get_target_qps_direct_match(self):
        """Test getting target QPS with direct accelerator match."""
        policy = load_balancing_policies.InstanceAwareLeastLoadPolicy()
        policy.target_qps_per_accelerator = {'A100': 10.0, 'H100': 20.0}

        result = policy._get_target_qps_for_accelerator('A100')

        assert result == 10.0

    def test_get_target_qps_base_name_match(self):
        """Test getting target QPS with base name match (e.g., A100:1)."""
        policy = load_balancing_policies.InstanceAwareLeastLoadPolicy()
        policy.target_qps_per_accelerator = {'A100:1': 10.0}

        result = policy._get_target_qps_for_accelerator('A100')

        assert result == 10.0

    def test_get_target_qps_fallback(self):
        """Test getting target QPS with no match returns 1.0."""
        policy = load_balancing_policies.InstanceAwareLeastLoadPolicy()
        policy.target_qps_per_accelerator = {'A100': 10.0}

        result = policy._get_target_qps_for_accelerator('unknown_gpu')

        assert result == 1.0

    def test_get_normalized_load(self):
        """Test computing normalized load."""
        policy = load_balancing_policies.InstanceAwareLeastLoadPolicy()
        policy.replica_info = {'http://replica1:8000': {'gpu_type': 'A100'}}
        policy.target_qps_per_accelerator = {'A100': 10.0}
        policy.load_map = {'http://replica1:8000': 5}

        result = policy._get_normalized_load('http://replica1:8000')

        assert result == 0.5  # 5 / 10.0

    def test_get_normalized_load_zero_qps(self):
        """Test normalized load with zero QPS uses fallback."""
        policy = load_balancing_policies.InstanceAwareLeastLoadPolicy()
        policy.replica_info = {'http://replica1:8000': {'gpu_type': 'A100'}}
        policy.target_qps_per_accelerator = {'A100': 0}
        policy.load_map = {'http://replica1:8000': 5}

        result = policy._get_normalized_load('http://replica1:8000')

        # Should use fallback of 1.0
        assert result == 5.0  # 5 / 1.0

    def test_select_replica_by_normalized_load(self):
        """Test selecting replica with minimum normalized load."""
        policy = load_balancing_policies.InstanceAwareLeastLoadPolicy()
        policy.ready_replicas = ['http://replica1:8000', 'http://replica2:8000']
        policy.replica_info = {
            'http://replica1:8000': {
                'gpu_type': 'A100'
            },  # 10 QPS
            'http://replica2:8000': {
                'gpu_type': 'H100'
            }  # 20 QPS
        }
        policy.target_qps_per_accelerator = {'A100': 10.0, 'H100': 20.0}
        policy.load_map = {
            'http://replica1:8000': 5,  # Normalized: 5/10 = 0.5
            'http://replica2:8000': 8  # Normalized: 8/20 = 0.4
        }

        result = policy._select_replica(mock.Mock())

        # replica2 has lower normalized load
        assert result == 'http://replica2:8000'

    def test_select_replica_empty(self):
        """Test selecting replica when none available."""
        policy = load_balancing_policies.InstanceAwareLeastLoadPolicy()

        result = policy._select_replica(mock.Mock())

        assert result is None


class TestPolicyRegistry:
    """Tests for policy registry."""

    def test_lb_policies_contains_round_robin(self):
        """Test that registry contains round_robin policy."""
        assert 'round_robin' in load_balancing_policies.LB_POLICIES

    def test_lb_policies_contains_least_load(self):
        """Test that registry contains least_load policy."""
        assert 'least_load' in load_balancing_policies.LB_POLICIES

    def test_lb_policies_contains_instance_aware(self):
        """Test that registry contains instance_aware_least_load policy."""
        assert 'instance_aware_least_load' in load_balancing_policies.LB_POLICIES

    def test_default_policy_is_least_load(self):
        """Test that default policy is least_load."""
        assert load_balancing_policies.DEFAULT_LB_POLICY == 'least_load'


class TestLoadBalancingPolicySelectReplica:
    """Tests for select_replica() wrapper method."""

    def test_select_replica_logs_selection(self):
        """Test that select_replica logs the selection."""
        policy = load_balancing_policies.RoundRobinPolicy()
        policy.ready_replicas = ['http://replica1:8000']

        mock_request = mock.Mock()
        mock_request.method = 'GET'
        mock_request.url = 'http://example.com/'
        mock_request.headers = {}
        mock_request.query_params = {}

        with mock.patch.object(load_balancing_policies,
                               'logger') as mock_logger:
            result = policy.select_replica(mock_request)

            assert result == 'http://replica1:8000'
            mock_logger.info.assert_called()

    def test_select_replica_logs_warning_when_none(self):
        """Test that select_replica logs warning when no replica selected."""
        policy = load_balancing_policies.RoundRobinPolicy()
        policy.ready_replicas = []

        mock_request = mock.Mock()
        mock_request.method = 'GET'
        mock_request.url = 'http://example.com/'
        mock_request.headers = {}
        mock_request.query_params = {}

        with mock.patch.object(load_balancing_policies,
                               'logger') as mock_logger:
            result = policy.select_replica(mock_request)

            assert result is None
            mock_logger.warning.assert_called()
