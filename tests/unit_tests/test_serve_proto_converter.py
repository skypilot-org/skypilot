from unittest import mock

import pytest

from sky.serve import serve_rpc_utils as utils
from sky.skylet import constants as skylet_constants

# pylint: disable=line-too-long


def test_get_service_status_request_converter():
    # list
    proto = utils.GetServiceStatusRequestConverter.to_proto(['test'], True)
    service_names, pool = utils.GetServiceStatusRequestConverter.from_proto(
        proto)
    assert service_names is not None
    assert len(service_names) == 1
    assert service_names[0] == 'test'
    assert pool

    # empty list
    proto = utils.GetServiceStatusRequestConverter.to_proto([], False)
    service_names, pool = utils.GetServiceStatusRequestConverter.from_proto(
        proto)
    assert service_names is not None
    assert len(service_names) == 0
    assert not pool

    # none
    proto = utils.GetServiceStatusRequestConverter.to_proto(None, True)
    service_names, pool = utils.GetServiceStatusRequestConverter.from_proto(
        proto)
    assert service_names is None
    assert pool


def test_get_service_status_response_converter():
    tmp_data = [{'test1': 'val1', 'test2': 'val2'}, {}]
    proto = utils.GetServiceStatusResponseConverter.to_proto(tmp_data)
    tmp_data_deser = utils.GetServiceStatusResponseConverter.from_proto(proto)
    assert len(tmp_data_deser) == 2

    dict_data_0 = tmp_data_deser[0]
    assert 'test1' in dict_data_0
    assert 'test2' in dict_data_0
    assert dict_data_0['test1'] == 'val1'
    assert dict_data_0['test2'] == 'val2'
    assert len(dict_data_0.keys()) == 2

    dict_data_1 = tmp_data_deser[1]
    assert dict_data_1 is not None
    assert len(dict_data_1.keys()) == 0


def test_terminate_service_request_converter():
    # list
    proto = utils.TerminateServicesRequestConverter.to_proto(['test'], True,
                                                             False)
    service_names, purge, pool = utils.TerminateServicesRequestConverter.from_proto(
        proto)
    assert service_names is not None
    assert len(service_names) == 1
    assert service_names[0] == 'test'
    assert purge
    assert not pool

    # empty list
    proto = utils.TerminateServicesRequestConverter.to_proto([], False, True)
    service_names, purge, pool = utils.TerminateServicesRequestConverter.from_proto(
        proto)
    assert service_names is not None
    assert len(service_names) == 0
    assert not purge
    assert pool

    # none
    proto = utils.TerminateServicesRequestConverter.to_proto(None, True, True)
    service_names, purge, pool = utils.TerminateServicesRequestConverter.from_proto(
        proto)
    assert service_names is None
    assert purge
    assert pool


@pytest.mark.parametrize('purge,timeout',
                         [(False, skylet_constants.SKYLET_GRPC_TIMEOUT_SECONDS),
                          (True, None)])
def test_terminate_services_purge_waits_without_deadline(purge, timeout):
    """Purge blocks until teardown completes, so it has no gRPC deadline."""
    handle = mock.Mock(is_grpc_enabled_with_flag=True)
    with mock.patch(
            'sky.serve.serve_rpc_utils.backends.SkyletClient') as mock_client:
        mock_client.return_value.terminate_services.return_value = mock.Mock(
            message='done')
        assert utils.RpcRunner.terminate_services(handle, ['svc'], purge,
                                                  True) == 'done'
    call = mock_client.return_value.terminate_services.call_args
    assert call.kwargs['timeout'] == timeout
