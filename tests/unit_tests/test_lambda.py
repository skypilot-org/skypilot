from unittest import mock

import pytest

from sky.provision.lambda_cloud import lambda_utils
from sky.provision.lambda_cloud.instance import _get_private_ip
from sky.provision.lambda_cloud.instance import open_ports
from sky.utils import resources_utils


def test_get_private_ip():
    valid_info = {'private_ip': '10.19.83.125'}
    invalid_info = {}
    assert _get_private_ip(valid_info,
                           single_node=True) == valid_info['private_ip']
    assert _get_private_ip(valid_info,
                           single_node=False) == valid_info['private_ip']
    assert _get_private_ip(invalid_info, single_node=True) == '127.0.0.1'
    with pytest.raises(RuntimeError):
        _get_private_ip(invalid_info, single_node=False)


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
        open_ports('test-cluster', ['22', '8080'])

        # Verify list_firewall_rules was called
        mock_client.list_firewall_rules.assert_called_once()

        # Verify create_firewall_rule was called for port 8080 but not for port 22
        mock_client.create_firewall_rule.assert_called_once_with(
            port_range=[8080, 8080], protocol='tcp')

        # Test with port range
        mock_client.list_firewall_rules.reset_mock()
        mock_client.create_firewall_rule.reset_mock()

        # Call with a port range
        open_ports('test-cluster', ['5000-5002'])

        # Should create 3 rules for ports 5000, 5001, and 5002
        assert mock_client.create_firewall_rule.call_count == 3
        mock_client.create_firewall_rule.assert_any_call(
            port_range=[5000, 5000], protocol='tcp')
        mock_client.create_firewall_rule.assert_any_call(
            port_range=[5001, 5001], protocol='tcp')
        mock_client.create_firewall_rule.assert_any_call(
            port_range=[5002, 5002], protocol='tcp')
