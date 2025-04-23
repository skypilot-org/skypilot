"""Tests for local up command.

"""

import tempfile
from unittest import mock

from click import testing
import yaml

from sky import cli


def test_local_up_validate_args_missing_ssh_params():
    """Test that local_up raises error when some SSH params are missing."""
    runner = testing.CliRunner()
    result = runner.invoke(cli.local_up,
                           ['--ips', 'ips.txt', '--ssh-user', 'user'])
    assert result.exit_code != 0
    assert 'All --ips, --ssh-user, and --ssh-key-path must be specified together' in result.output


def test_local_up_validate_args_cleanup_without_required_params():
    """Test that local_up raises error when cleanup is used without required params."""
    runner = testing.CliRunner()
    result = runner.invoke(cli.local_up, ['--cleanup'])
    assert result.exit_code != 0
    assert '--cleanup can only be used with --ips, --ssh-user and --ssh-key-path or --k8s-clusters' in result.output


def test_local_up_invalid_ssh_key_file():
    """Test that local_up raises error for invalid SSH key file."""
    runner = testing.CliRunner()
    # create a valid ips.txt file
    with tempfile.NamedTemporaryFile() as temp_ips_file:
        temp_ips_file.write(b'192.168.1.1\n192.168.1.2')
        temp_ips_file.flush()
        result = runner.invoke(cli.local_up, [
            '--ips', temp_ips_file.name, '--ssh-user', 'user', '--ssh-key-path',
            'invalid-path'
        ])
        assert result.exit_code != 0
        assert 'Failed to read SSH key file' in result.output


def test_local_up_empty_ssh_key_file():
    """Test that local_up raises error for empty SSH key file."""
    runner = testing.CliRunner()
    # create a valid ips.txt file
    with tempfile.NamedTemporaryFile() as temp_ips_file:
        temp_ips_file.write(b'192.168.1.1\n192.168.1.2')
        temp_ips_file.flush()
        with tempfile.NamedTemporaryFile() as temp_ssh_key_file:
            result = runner.invoke(cli.local_up, [
                '--ips', temp_ips_file.name, '--ssh-user', 'user',
                '--ssh-key-path', temp_ssh_key_file.name
            ])
        assert result.exit_code != 0
        assert 'SSH key file is empty' in result.output


def test_local_up_invalid_ip_file():
    """Test that local_up raises error for invalid IP file."""
    runner = testing.CliRunner()
    result = runner.invoke(cli.local_up, [
        '--ips', 'invalid-path', '--ssh-user', 'user', '--ssh-key-path',
        'ssh-key-path'
    ])
    assert result.exit_code != 0
    assert 'Failed to read IP file' in result.output


def test_local_up_empty_ip_file():
    """Test that local_up raises error for empty IP file."""
    runner = testing.CliRunner()
    with tempfile.NamedTemporaryFile() as temp_ip_file:
        with tempfile.NamedTemporaryFile() as temp_key_file:
            temp_key_file.write(b'ssh-key-content')
            temp_key_file.flush()
            result = runner.invoke(cli.local_up, [
                '--ips', temp_ip_file.name, '--ssh-user', 'user',
                '--ssh-key-path', temp_key_file.name
            ])
            assert result.exit_code != 0
            assert 'IP file is empty' in result.output


def test_local_up_invalid_k8s_clusters_file():
    """Test that local_up raises error for invalid k8s clusters file."""
    runner = testing.CliRunner()
    result = runner.invoke(cli.local_up, ['--k8s-clusters', 'invalid-path'])
    assert result.exit_code != 0
    assert 'Failed to read k8s clusters file' in result.output


def test_local_up_empty_k8s_clusters():
    """Test that local_up raises error for empty k8s clusters config."""
    runner = testing.CliRunner()
    with tempfile.NamedTemporaryFile(mode='w') as temp_file:
        yaml.dump({'clusters': {}}, temp_file)
        temp_file.flush()
        result = runner.invoke(cli.local_up, ['--k8s-clusters', temp_file.name])
        assert result.exit_code != 0
        assert 'No clusters specified in the k8s clusters file' in result.output


def test_local_up_k8s_clusters_no_ssh_user():
    """Test that local_up raises error for k8s clusters config with no ssh_user."""
    runner = testing.CliRunner()
    with tempfile.NamedTemporaryFile(mode='w') as temp_file:
        yaml.dump(
            {
                'clusters': {
                    'cluster1': {
                        'ips': ['192.168.1.1'],
                        'ssh_user': None
                    }
                }
            }, temp_file)
        temp_file.flush()
        result = runner.invoke(cli.local_up, ['--k8s-clusters', temp_file.name])
        assert result.exit_code != 0
        assert 'No ssh_user specified for cluster cluster1' in result.output


def test_local_up_k8s_clusters_no_ips():
    """Test that local_up raises error for k8s clusters config with no ips."""
    runner = testing.CliRunner()
    with tempfile.NamedTemporaryFile(mode='w') as temp_file:
        yaml.dump({'clusters': {'cluster1': {'ips': None}}}, temp_file)
        temp_file.flush()
        result = runner.invoke(cli.local_up, ['--k8s-clusters', temp_file.name])
        assert result.exit_code != 0
        assert 'No IPs specified for cluster cluster1' in result.output


def test_local_up_k8s_clusters_duplicate_ips_in_same_cluster():
    """Test that local_up raises error for k8s clusters config with duplicate IPs in the same cluster."""
    runner = testing.CliRunner()
    with tempfile.NamedTemporaryFile(mode='w') as temp_file:
        yaml.dump(
            {'clusters': {
                'cluster1': {
                    'ips': ['192.168.1.1', '192.168.1.1']
                }
            }}, temp_file)
        temp_file.flush()
        result = runner.invoke(cli.local_up, ['--k8s-clusters', temp_file.name])
        assert result.exit_code != 0
        assert 'Duplicate IP 192.168.1.1 specified for cluster cluster1' in result.output


def test_local_up_k8s_clusters_duplicate_ips_in_different_clusters():
    """Test that local_up raises error for k8s clusters config with duplicate IPs in different clusters."""
    runner = testing.CliRunner()
    with tempfile.NamedTemporaryFile(mode='w') as temp_file:
        with tempfile.NamedTemporaryFile(mode='w') as temp_key_file:
            # write ssh key to the temp file
            temp_key_file.write('ssh-key-content-cluster')
            temp_key_file.flush()
            yaml.dump(
                {
                    'clusters': {
                        'cluster1': {
                            'ips': ['192.168.1.1'],
                            'ssh_user': 'user',
                            'password': 'password',
                            'ssh_key_path': temp_key_file.name
                        },
                        'cluster2': {
                            'ips': ['192.168.1.1'],
                            'ssh_user': 'user',
                            'password': 'password',
                            'ssh_key_path': temp_key_file.name
                        }
                    }
                }, temp_file)
            temp_file.flush()
            result = runner.invoke(cli.local_up,
                                   ['--k8s-clusters', temp_file.name])
            assert result.exit_code != 0
            assert 'Duplicate IP 192.168.1.1 specified for cluster cluster2' in result.output


def test_local_up_k8s_clusters_no_ssh_key_path():
    """Test that local_up raises error for k8s clusters config with no ssh_key_path."""
    runner = testing.CliRunner()
    with tempfile.NamedTemporaryFile(mode='w') as temp_file:
        yaml.dump(
            {
                'clusters': {
                    'cluster1': {
                        'ips': ['192.168.1.1'],
                        'ssh_user': 'user',
                        'password': 'password',
                        'ssh_key_path': None
                    }
                }
            }, temp_file)
        temp_file.flush()
        result = runner.invoke(cli.local_up, ['--k8s-clusters', temp_file.name])
        assert result.exit_code != 0
        assert 'SSH key path is required' in result.output


def test_local_up_k8s_clusters_invalid_ssh_key_path():
    """Test that local_up raises error for k8s clusters config with invalid ssh_key_path."""
    runner = testing.CliRunner()
    with tempfile.NamedTemporaryFile(mode='w') as temp_file:
        yaml.dump(
            {
                'clusters': {
                    'cluster1': {
                        'ips': ['192.168.1.1'],
                        'ssh_user': 'user',
                        'password': 'password',
                        'ssh_key_path': 'invalid-path'
                    }
                }
            }, temp_file)
        temp_file.flush()
        result = runner.invoke(cli.local_up, ['--k8s-clusters', temp_file.name])
        assert result.exit_code != 0
        assert 'Failed to read SSH key file' in result.output


def test_local_up_k8s_clusters_empty_ssh_key():
    """Test that local_up raises error for k8s clusters config with empty ssh_key."""
    runner = testing.CliRunner()
    with tempfile.NamedTemporaryFile(mode='w') as temp_file:
        with tempfile.NamedTemporaryFile() as temp_key_file:
            yaml.dump(
                {
                    'clusters': {
                        'cluster1': {
                            'ips': ['192.168.1.1'],
                            'ssh_user': 'user',
                            'password': 'password',
                            'ssh_key_path': temp_key_file.name
                        }
                    }
                }, temp_file)
            temp_file.flush()
            result = runner.invoke(cli.local_up,
                                   ['--k8s-clusters', temp_file.name])
            assert result.exit_code != 0
            assert 'SSH key file is empty' in result.output


def test_build_cluster_configs_k8s_clusters_get_function_params():
    """Test the overwrite order for the ssh_user, password, and ssh_key_path in build_cluster_configs. The parameters in the function call have the highest priority, then the parameters in separate cluster section in the k8s clusters file, then the global parameters in the k8s clusters file."""
    ssh_user = 'user-func'
    password = 'password-func'
    with tempfile.NamedTemporaryFile(mode='w') as temp_file:
        # write ssh key to the temp file
        temp_file.write('ssh-key-content-func')
        temp_file.flush()
        ssh_key_path = temp_file.name
        with tempfile.NamedTemporaryFile(mode='w') as temp_file:
            # write ssh key to the temp file
            temp_file.write('ssh-key-content-cluster')
            temp_file.flush()
            ssh_key_path_cluster = temp_file.name
            with tempfile.NamedTemporaryFile(mode='w') as temp_file:
                # write ssh key to the temp file
                temp_file.write('ssh-key-content-global')
                temp_file.flush()
                ssh_key_path_global = temp_file.name
                with tempfile.NamedTemporaryFile(mode='w') as temp_file:
                    yaml.dump(
                        {
                            'ssh_user': 'global-user',
                            'password': 'global-password',
                            'ssh_key_path': ssh_key_path_global,
                            'clusters': {
                                'cluster1': {
                                    'ips': ['192.168.1.1'],
                                    'ssh_user': 'cluster-user',
                                    'password': 'cluster-password',
                                    'ssh_key_path': ssh_key_path_cluster
                                }
                            }
                        }, temp_file)
                    temp_file.flush()

                    cluster_configs = cli.build_cluster_configs(
                        ips=None,
                        ssh_user=ssh_user,
                        ssh_key_path=ssh_key_path,
                        context_name=None,
                        password=password,
                        k8s_clusters=temp_file.name)

                    assert cluster_configs[0]['ssh_user'] == ssh_user
                    assert cluster_configs[0]['password'] == password
                    assert cluster_configs[0][
                        'ssh_key'] == 'ssh-key-content-func'


def test_build_cluster_configs_k8s_clusters_get_cluster_params():
    """Test the overwrite order for the ssh_user, password, and ssh_key_path in build_cluster_configs. The parameters in the function call have the highest priority, then the parameters in separate cluster section in the k8s clusters file, then the global parameters in the k8s clusters file."""

    with tempfile.NamedTemporaryFile(mode='w') as temp_file:
        # write ssh key to the temp file
        temp_file.write('ssh-key-content-cluster')
        temp_file.flush()
        ssh_key_path_cluster = temp_file.name
        with tempfile.NamedTemporaryFile(mode='w') as temp_file:
            # write ssh key to the temp file
            temp_file.write('ssh-key-content-global')
            temp_file.flush()
            ssh_key_path_global = temp_file.name
            with tempfile.NamedTemporaryFile(mode='w') as temp_file:
                yaml.dump(
                    {
                        'ssh_user': 'global-user',
                        'password': 'global-password',
                        'ssh_key_path': ssh_key_path_global,
                        'clusters': {
                            'cluster1': {
                                'ips': ['192.168.1.1'],
                                'ssh_user': 'cluster-user',
                                'password': 'cluster-password',
                                'ssh_key_path': ssh_key_path_cluster
                            }
                        }
                    }, temp_file)
                temp_file.flush()

                cluster_configs = cli.build_cluster_configs(
                    ips=None,
                    ssh_user=None,
                    ssh_key_path=None,
                    context_name=None,
                    password=None,
                    k8s_clusters=temp_file.name)

                assert cluster_configs[0]['ssh_user'] == 'cluster-user'
                assert cluster_configs[0]['password'] == 'cluster-password'
                assert cluster_configs[0][
                    'ssh_key'] == 'ssh-key-content-cluster'


def test_build_cluster_configs_k8s_clusters_get_global_params():
    """Test the overwrite order for the ssh_user, password, and ssh_key_path in build_cluster_configs. The parameters in the function call have the highest priority, then the parameters in separate cluster section in the k8s clusters file, then the global parameters in the k8s clusters file."""

    with tempfile.NamedTemporaryFile(mode='w') as temp_file:
        # write ssh key to the temp file
        temp_file.write('ssh-key-content-global')
        temp_file.flush()
        ssh_key_path_global = temp_file.name
        with tempfile.NamedTemporaryFile(mode='w') as temp_file:
            yaml.dump(
                {
                    'ssh_user': 'global-user',
                    'password': 'global-password',
                    'ssh_key_path': ssh_key_path_global,
                    'clusters': {
                        'cluster1': {
                            'ips': ['192.168.1.1']
                        }
                    }
                }, temp_file)
            temp_file.flush()

            cluster_configs = cli.build_cluster_configs(
                ips=None,
                ssh_user=None,
                ssh_key_path=None,
                context_name=None,
                password=None,
                k8s_clusters=temp_file.name)

            assert cluster_configs[0]['ssh_user'] == 'global-user'
            assert cluster_configs[0]['password'] == 'global-password'
            assert cluster_configs[0]['ssh_key'] == 'ssh-key-content-global'


@mock.patch('sky.cli.sdk.local_up')
@mock.patch('sky.cli._async_call_or_wait')
def test_local_up_successful_local_deployment(mock_async_call_or_wait,
                                              mock_sdk_local_up):
    """Test successful local deployment with no parameters."""
    mock_sdk_local_up.return_value = 'request-id-123'
    mock_async_call_or_wait.return_value = 'request-id-123'
    runner = testing.CliRunner()

    result = runner.invoke(cli.local_up, [])

    assert result.exit_code == 0
    # Check that sdk.local_up was called with the correct parameters
    mock_sdk_local_up.assert_called_once_with(True, None, None, None, False,
                                              None, None)
    # Check that _async_call_or_wait was called with the correct parameters
    mock_async_call_or_wait.assert_called_once_with('request-id-123',
                                                    False,
                                                    request_name='local up')


@mock.patch('sky.cli.sdk.local_up')
@mock.patch('sky.cli._async_call_or_wait')
def test_local_up_successful_ips_deployment(mock_async_call_or_wait,
                                            mock_sdk_local_up):
    """Test successful local deployment with IP file."""
    mock_sdk_local_up.return_value = 'request-id-123'
    mock_async_call_or_wait.return_value = 'request-id-123'
    runner = testing.CliRunner()

    with tempfile.NamedTemporaryFile(mode='w') as temp_ip_file:
        temp_ip_file.write('192.168.1.1\n192.168.1.2')
        temp_ip_file.flush()

        with tempfile.NamedTemporaryFile(mode='w') as temp_key_file:
            temp_key_file.write('ssh-key-content')
            temp_key_file.flush()

            result = runner.invoke(cli.local_up, [
                '--ips', temp_ip_file.name, '--ssh-user', 'user',
                '--ssh-key-path', temp_key_file.name, '--context-name',
                'test-context'
            ])

            assert result.exit_code == 0
            mock_sdk_local_up.assert_called_once_with(
                True, ['192.168.1.1', '192.168.1.2'], 'user', 'ssh-key-content',
                False, 'test-context', None)
            mock_async_call_or_wait.assert_called_once_with(
                'request-id-123', False, request_name='local up')


@mock.patch('sky.cli.sdk.local_up')
@mock.patch('sky.cli._async_call_or_wait')
def test_local_up_successful_k8s_deployment(mock_async_call_or_wait,
                                            mock_sdk_local_up):
    """Test successful k8s deployment with clusters config."""
    mock_sdk_local_up.return_value = 'request-id-123'
    mock_async_call_or_wait.return_value = 'request-id-123'
    runner = testing.CliRunner()

    k8s_config = {
        'ssh_user': 'global-user',
        'password': 'global-password',
        'ssh_key_path': '/path/to/key',
        'clusters': {
            'cluster1': {
                'ips': ['192.168.1.1', '192.168.1.2'],
                'ssh_user': 'cluster-user',
                'password': 'cluster-password',
                'ssh_key_path': '/path/to/cluster/key'
            }
        }
    }
    with tempfile.NamedTemporaryFile(mode='w') as temp_file:
        with tempfile.NamedTemporaryFile(mode='w') as temp_key_file:
            temp_key_file.write('ssh-key-content')
            temp_key_file.flush()
            k8s_config['clusters']['cluster1'][
                'ssh_key_path'] = temp_key_file.name
            yaml.dump(k8s_config, temp_file)
            temp_file.flush()

            # with mock.patch('os.path.expanduser', return_value=temp_key_file.name):
            result = runner.invoke(cli.local_up,
                                   ['--k8s-clusters', temp_file.name])

            assert result.exit_code == 0
            mock_sdk_local_up.assert_called_once_with(
                True, ['192.168.1.1', '192.168.1.2'], 'cluster-user',
                'ssh-key-content', False, 'cluster1', 'cluster-password')
            mock_async_call_or_wait.assert_called_once_with(
                'request-id-123', False, request_name='local up')


@mock.patch('sky.cli.sdk.local_up')
@mock.patch('sky.cli._async_call_or_wait')
def test_local_up_successful_two_k8s_deployment(mock_async_call_or_wait,
                                                mock_sdk_local_up):
    """Test successful k8s deployment with two clusters."""
    mock_sdk_local_up.return_value = 'request-id-123'
    mock_async_call_or_wait.return_value = 'request-id-123'
    runner = testing.CliRunner()

    k8s_config = {
        'ssh_user': 'global-user',
        'password': 'global-password',
        'ssh_key_path': '/path/to/key',
        'clusters': {
            'cluster1': {
                'ips': ['192.168.1.1', '192.168.1.2'],
                'ssh_user': 'cluster-user',
                'password': 'cluster-password',
                'ssh_key_path': '/path/to/cluster/key'
            },
            'cluster2': {
                'ips': ['192.168.1.3', '192.168.1.4'],
                'ssh_user': 'cluster-user2',
                'password': 'cluster-password2',
                'ssh_key_path': '/path/to/cluster/key2'
            }
        }
    }
    with tempfile.NamedTemporaryFile(mode='w') as temp_file:
        with tempfile.NamedTemporaryFile(mode='w') as temp_key_file:
            temp_key_file.write('ssh-key-content')
            temp_key_file.flush()
            k8s_config['clusters']['cluster1'][
                'ssh_key_path'] = temp_key_file.name
            k8s_config['clusters']['cluster2'][
                'ssh_key_path'] = temp_key_file.name
            yaml.dump(k8s_config, temp_file)
            temp_file.flush()

            result = runner.invoke(cli.local_up,
                                   ['--k8s-clusters', temp_file.name])

            assert result.exit_code == 0
            # Check that sdk.local_up was called twice with the correct parameters
            assert mock_sdk_local_up.call_count == 2
            mock_sdk_local_up.assert_has_calls([
                mock.call(True, ['192.168.1.1', '192.168.1.2'], 'cluster-user',
                          'ssh-key-content', False, 'cluster1',
                          'cluster-password'),
                mock.call(True, ['192.168.1.3', '192.168.1.4'], 'cluster-user2',
                          'ssh-key-content', False, 'cluster2',
                          'cluster-password2')
            ])
            # Check that _async_call_or_wait was called twice with the correct parameters
            assert mock_async_call_or_wait.call_count == 2
            mock_async_call_or_wait.assert_has_calls([
                mock.call('request-id-123', False, request_name='local up'),
                mock.call('request-id-123', False, request_name='local up')
            ])


@mock.patch('sky.cli.sdk.local_up')
@mock.patch('sky.cli._async_call_or_wait')
def test_local_up_successful_two_k8s_deployment_no_password(
        mock_async_call_or_wait, mock_sdk_local_up):
    """Test successful k8s deployment with two clusters and no password."""
    mock_sdk_local_up.return_value = 'request-id-123'
    mock_async_call_or_wait.return_value = 'request-id-123'
    runner = testing.CliRunner()

    k8s_config = {
        'ssh_user': 'global-user',
        'ssh_key_path': '/path/to/key',
        'clusters': {
            'cluster1': {
                'ips': ['192.168.1.1', '192.168.1.2'],
                'ssh_user': 'cluster-user',
                'ssh_key_path': '/path/to/cluster/key'
            },
            'cluster2': {
                'ips': ['192.168.1.3', '192.168.1.4'],
                'ssh_user': 'cluster-user2',
                'ssh_key_path': '/path/to/cluster/key2'
            }
        }
    }
    with tempfile.NamedTemporaryFile(mode='w') as temp_file:
        with tempfile.NamedTemporaryFile(mode='w') as temp_key_file:
            temp_key_file.write('ssh-key-content')
            temp_key_file.flush()
            k8s_config['clusters']['cluster1'][
                'ssh_key_path'] = temp_key_file.name
            k8s_config['clusters']['cluster2'][
                'ssh_key_path'] = temp_key_file.name
            yaml.dump(k8s_config, temp_file)
            temp_file.flush()

            result = runner.invoke(cli.local_up,
                                   ['--k8s-clusters', temp_file.name])

            assert result.exit_code == 0
            # Check that sdk.local_up was called twice with the correct parameters
            assert mock_sdk_local_up.call_count == 2
            mock_sdk_local_up.assert_has_calls([
                mock.call(True, ['192.168.1.1', '192.168.1.2'], 'cluster-user',
                          'ssh-key-content', False, 'cluster1', None),
                mock.call(True, ['192.168.1.3', '192.168.1.4'], 'cluster-user2',
                          'ssh-key-content', False, 'cluster2', None)
            ])
            # Check that _async_call_or_wait was called twice with the correct parameters
            assert mock_async_call_or_wait.call_count == 2
            mock_async_call_or_wait.assert_has_calls([
                mock.call('request-id-123', False, request_name='local up'),
                mock.call('request-id-123', False, request_name='local up')
            ])
