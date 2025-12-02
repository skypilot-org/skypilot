"""Tests for Kubernetes remote cluster deployment."""

from unittest import mock

from sky.utils.kubernetes import deploy_ssh_node_pools


def test_deploy_remote_cluster():
    """Test to check if the remote cluster is deployed successfully."""
    mock_hosts_info = [{
        'name': 'test-host',
        'ip': '192.168.1.1',
        'user': 'test-user',
        'identity_file': '~/.ssh/id_rsa',
        'use_ssh_config': False,
        'password': 'test-password'
    }]

    mock_context_name = 'test-infra'

    mock_cluster_config = {mock_context_name: {'hosts': ['test-host']}}

    mock_ssh_targets = [{'name': mock_context_name, 'hosts': ['test-host']}]

    with mock.patch('sky.utils.kubernetes.deploy_ssh_node_pools.ssh_utils.load_ssh_targets') as mock_load_ssh_targets, \
         mock.patch('sky.utils.kubernetes.deploy_ssh_node_pools.ssh_utils.get_cluster_config') as mock_get_cluster_config, \
         mock.patch('sky.utils.kubernetes.deploy_ssh_node_pools.ssh_utils.prepare_hosts_info') as mock_prepare_hosts_info, \
         mock.patch('sky.utils.kubernetes.deploy_ssh_node_pools.deploy_cluster') as mock_deploy_cluster:
        mock_load_ssh_targets.return_value = mock_ssh_targets
        mock_get_cluster_config.return_value = mock_cluster_config
        mock_prepare_hosts_info.return_value = mock_hosts_info
        mock_deploy_cluster.return_value = [mock_context_name]
        deploy_ssh_node_pools.deploy_clusters(
            cleanup=False,
            infra='test-infra',
            kubeconfig_path='~/.kube/config',
            global_use_ssh_config=False,
            ssh_node_pools_file='~/.sky/ssh_node_pools.yaml')
        mock_deploy_cluster.assert_called_once()
        mock_load_ssh_targets.assert_called_once()
        mock_get_cluster_config.assert_called_once()
        # Check that mock_deploy_cluster was called with context_name='ssh-test-infra'
        context_name = None
        expected_context_name = 'ssh-test-infra'
        for call in mock_deploy_cluster.call_args_list:
            # context_name is the 5th positional argument
            # deploy_cluster(head_node, worker_nodes, ssh_user, ssh_key, context_name, ...)
            if len(call.args) >= 5:
                context_name = call.args[4]
            assert context_name == expected_context_name, (
                f"mock_deploy_cluster was not called with context_name='{expected_context_name}', "
                f"but was called with context_name={context_name}")
