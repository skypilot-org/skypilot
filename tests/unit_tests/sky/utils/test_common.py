from unittest import mock

from sky.utils import common

_USER = 'abcd1234'
_OTHER_USER = 'efgh5678'


@mock.patch('sky.utils.common_utils.get_user_hash')
def test_get_controller_name(mock_get_user_hash):
    # Test local
    mock_get_user_hash.return_value = common.SERVER_ID
    controller_name = common.get_controller_name(common.ControllerType.JOBS)
    assert controller_name == f'sky-jobs-controller-{common.SERVER_ID}'

    # Test remote
    mock_get_user_hash.return_value = _USER
    controller_name = common.get_controller_name(common.ControllerType.JOBS)
    assert controller_name == (
        f'sky-jobs-controller-{_USER}'
        f'{common.SERVER_ID_CONNECTOR}{common.SERVER_ID}')

    # Test serve
    mock_get_user_hash.return_value = _USER
    controller_name = common.get_controller_name(common.ControllerType.SERVE)
    assert controller_name == (
        f'sky-serve-controller-{_USER}'
        f'{common.SERVER_ID_CONNECTOR}{common.SERVER_ID}')


@mock.patch('sky.utils.common_utils.get_user_hash')
def test_is_user_controller(mock_get_user_hash):
    mock_get_user_hash.return_value = _USER

    # Test local controller
    controller_name = f'sky-serve-controller-{_USER}'
    assert common.is_current_user_controller(controller_name)

    controller_name = f'sky-serve-controller-{_OTHER_USER}'
    assert not common.is_current_user_controller(controller_name)

    # Test remote controller
    controller_name = (f'sky-serve-controller-{_USER}'
                       f'{common.SERVER_ID_CONNECTOR}{_OTHER_USER}')
    assert common.is_current_user_controller(controller_name)

    controller_name = (f'sky-serve-controller-{_OTHER_USER}'
                       f'{common.SERVER_ID_CONNECTOR}{_USER}')
    assert not common.is_current_user_controller(controller_name)
