import pytest

from sky.provision.lambda_cloud.instance import _get_private_ip


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
