import threading
import time
from unittest import mock

import pytest

from sky.authentication import setup_lambda_authentication
from sky.provision.lambda_cloud import instance
from sky.provision.lambda_cloud import lambda_utils
from sky.utils import auth_utils
from sky.utils import common_utils


def test_get_private_ip():
    valid_info = {'private_ip': '10.19.83.125'}
    invalid_info = {}
    assert instance._get_private_ip(
        valid_info, single_node=True) == valid_info['private_ip']
    assert instance._get_private_ip(
        valid_info, single_node=False) == valid_info['private_ip']
    assert instance._get_private_ip(invalid_info,
                                    single_node=True) == '127.0.0.1'
    with pytest.raises(RuntimeError):
        instance._get_private_ip(invalid_info, single_node=False)


def test_open_ports():
    # Mock the LambdaCloudClient and its methods
    with mock.patch.object(lambda_utils,
                           'LambdaCloudClient') as mock_client_cls:
        # Setup mock return values
        mock_client = mock_client_cls.return_value

        # Mock existing firewall rules (one port already open)
        mock_client.list_firewall_rules.return_value = [{
            'id': 'rule1',
            'protocol': 'tcp',
            'port_range': [22, 22],
            'source_network': '0.0.0.0/0',
            'description': 'SSH'
        }]

        # Call the function being tested with ports to open
        # Port 22 is already open, port 8080 is new
        instance.open_ports('test-cluster', ['22', '8080'])

        # Verify list_firewall_rules was called
        mock_client.list_firewall_rules.assert_called_once()

        # Verify create_firewall_rule was called for port 8080 but not for port 22
        mock_client.create_firewall_rule.assert_called_once_with(
            port_range=[8080, 8080], protocol='tcp')

        # Test with port range
        mock_client.list_firewall_rules.reset_mock()
        mock_client.create_firewall_rule.reset_mock()

        # Call with a port range
        instance.open_ports('test-cluster', ['5000-5002'])

        # Should create 1 rule for ports 5000-5002
        mock_client.list_firewall_rules.assert_called_once()
        mock_client.create_firewall_rule.assert_any_call(
            port_range=[5000, 5002], protocol='tcp')


def test_setup_lambda_authentication_no_duplicate_keys():
    call_count = [0]  # List so we don't have to use nonlocal

    def mock_get_key(prefix, pub_key):
        time.sleep(0.02)  # Simulated API delay to expose race condition
        return ('sky-key', call_count[0] > 0)  # Key exists after first register

    def mock_register(name, pub_key):
        time.sleep(0.02)
        call_count[0] += 1

    with mock.patch.object(lambda_utils, 'LambdaCloudClient') as mock_cls, \
         mock.patch.object(auth_utils, 'get_or_generate_keys',
                          return_value=('/key', '/key.pub')), \
         mock.patch.object(common_utils, 'get_user_hash', return_value='hash'), \
         mock.patch('builtins.open', mock.mock_open(read_data='ssh-rsa key')):

        mock_client = mock_cls.return_value
        mock_client.get_unique_ssh_key_name.side_effect = mock_get_key
        mock_client.register_ssh_key.side_effect = mock_register

        # Run 5 concurrent threads
        threads = [
            threading.Thread(
                target=lambda: setup_lambda_authentication({'auth': {}}))
            for _ in range(5)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Register should only be called once due to file lock
        assert call_count[
            0] == 1, f"Expected 1 registration, got {call_count[0]}"
