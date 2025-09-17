from sky.serve import serve_rpc_utils as utils

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
