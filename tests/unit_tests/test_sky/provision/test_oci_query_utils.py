"""Tests for the OCI provisioner's query helpers."""
from unittest import mock

import pytest

from sky.adaptors import oci as oci_adaptor
from sky.clouds.utils import oci_utils
from sky.provision.oci import query_utils

_COMPARTMENT = 'ocid1.compartment.oc1..aaaaaaaalaunchhere'


def _instance(instance_id, state, tags):
    inst = mock.MagicMock()
    inst.id = instance_id
    inst.lifecycle_state = state
    inst.freeform_tags = tags
    inst.availability_domain = 'Uocm:US-ASHBURN-AD-1'
    inst.compartment_id = _COMPARTMENT
    return inst


def test_query_instances_by_tags_lists_the_launch_compartment(monkeypatch):
    pytest.importorskip('oci')
    monkeypatch.setattr(oci_utils.oci_config,
                        'get_profile',
                        lambda region=None: 'TOKEN')
    monkeypatch.setattr(query_utils.QueryHelper, 'find_compartment',
                        classmethod(lambda cls, region: _COMPARTMENT))
    # Paginate by calling straight through.
    monkeypatch.setattr(oci_adaptor.oci.pagination, 'list_call_get_all_results',
                        lambda call, **kwargs: call(**kwargs))
    core = mock.MagicMock()
    core.list_instances.return_value.data = [
        _instance('ocid1.instance.oc1.phx.head', 'RUNNING', {
            'ray-cluster-name': 'c-1',
            'ray-node-type': 'head',
        }),
        _instance('ocid1.instance.oc1.phx.other-cluster', 'RUNNING',
                  {'ray-cluster-name': 'c-2'}),
        _instance('ocid1.instance.oc1.phx.gone', 'TERMINATED',
                  {'ray-cluster-name': 'c-1'}),
        _instance('ocid1.instance.oc1.phx.going', 'TERMINATING',
                  {'ray-cluster-name': 'c-1'}),
        _instance('ocid1.instance.oc1.phx.untagged', 'RUNNING', None),
    ]
    monkeypatch.setattr(oci_adaptor,
                        'get_core_client',
                        lambda region=None, profile='DEFAULT': core)

    found = query_utils.query_helper.query_instances_by_tags(
        {'ray-cluster-name': 'c-1'}, 'us-phoenix-1')

    assert [inst.id for inst in found] == ['ocid1.instance.oc1.phx.head']
    # Resource Search is scoped to the caller's home tenancy; listing the
    # launch compartment also works across tenancies.
    core.list_instances.assert_called_once_with(compartment_id=_COMPARTMENT)


def test_query_instances_by_tags_requires_every_tag(monkeypatch):
    pytest.importorskip('oci')
    monkeypatch.setattr(oci_utils.oci_config,
                        'get_profile',
                        lambda region=None: 'TOKEN')
    monkeypatch.setattr(query_utils.QueryHelper, 'find_compartment',
                        classmethod(lambda cls, region: _COMPARTMENT))
    monkeypatch.setattr(oci_adaptor.oci.pagination, 'list_call_get_all_results',
                        lambda call, **kwargs: call(**kwargs))
    core = mock.MagicMock()
    core.list_instances.return_value.data = [
        _instance('head', 'RUNNING', {
            'ray-cluster-name': 'c-1',
            'ray-node-type': 'head',
        }),
        _instance('worker', 'RUNNING', {
            'ray-cluster-name': 'c-1',
            'ray-node-type': 'worker',
        }),
    ]
    monkeypatch.setattr(oci_adaptor,
                        'get_core_client',
                        lambda region=None, profile='DEFAULT': core)

    found = query_utils.query_helper.query_instances_by_tags(
        {
            'ray-cluster-name': 'c-1',
            'ray-node-type': 'head',
        }, 'us-phoenix-1')

    assert [inst.id for inst in found] == ['head']


# ---------------------------------------------------------------------------
# Teardown with a region-specific profile
# ---------------------------------------------------------------------------

_NSG = 'ocid1.networksecuritygroup.oc1.iad.aaaaaaaatest'
_VNIC = 'ocid1.vnic.oc1.iad.aaaaaaaatest'


@pytest.fixture(name='regional_profile_clients')
def fixture_regional_profile_clients(monkeypatch):
    """Fakes the SDK clients and records the profile each one is built with.

    `us-ashburn-1` names its own profile; every other region falls back to
    the default one. Returns `(core_client, net_client, profiles)` where
    `profiles` lists the profile of every client constructed, in order.
    """
    pytest.importorskip('oci')
    monkeypatch.setattr(oci_utils.oci_config,
                        'get_profile',
                        lambda region=None: 'ASHBURN'
                        if region == 'us-ashburn-1' else 'TOKEN')
    monkeypatch.setattr(query_utils.QueryHelper, 'find_compartment',
                        classmethod(lambda cls, region: _COMPARTMENT))
    monkeypatch.setattr(
        query_utils.QueryHelper, 'find_nsg',
        classmethod(lambda cls, region, nsg_name, create_if_not_exist: _NSG))
    monkeypatch.setattr(oci_adaptor.oci.pagination, 'list_call_get_all_results',
                        lambda call, **kwargs: call(**kwargs))

    core = mock.MagicMock()
    core.list_vnic_attachments.return_value.data = [
        mock.MagicMock(vnic_id=_VNIC)
    ]
    net = mock.MagicMock()
    vnic = mock.MagicMock()
    vnic.id = _VNIC
    net.get_vnic.return_value.data = vnic
    profiles = []

    def _core_client(region=None, profile='DEFAULT'):
        profiles.append(profile)
        return core

    def _net_client(region=None, profile='DEFAULT'):
        profiles.append(profile)
        return net

    monkeypatch.setattr(oci_adaptor, 'get_core_client', _core_client)
    monkeypatch.setattr(oci_adaptor, 'get_net_client', _net_client)
    return core, net, profiles


def test_detach_nsg_uses_the_regional_profile(regional_profile_clients):
    _, net, profiles = regional_profile_clients
    inst = _instance('ocid1.instance.oc1.iad.head', 'RUNNING',
                     {'ray-cluster-name': 'c-1'})

    query_utils.query_helper.detach_nsg('us-ashburn-1', inst, _NSG)

    assert net.update_vnic.call_args.kwargs['vnic_id'] == _VNIC
    assert net.update_vnic.call_args.kwargs['update_vnic_details'].nsg_ids == []
    # The VNIC lookup and the VNIC update must both authenticate as the
    # profile configured for the region, not the default one.
    assert profiles and set(profiles) == {'ASHBURN'}


def test_terminate_instances_by_tags_uses_the_regional_profile(
        regional_profile_clients):
    core, net, profiles = regional_profile_clients
    inst = _instance('ocid1.instance.oc1.iad.head', 'RUNNING',
                     {'ray-cluster-name': 'c-1'})
    core.list_instances.return_value.data = [inst]

    failed = query_utils.query_helper.terminate_instances_by_tags(
        {'ray-cluster-name': 'c-1'}, 'us-ashburn-1')

    assert failed == 0
    net.update_vnic.assert_called_once()
    core.terminate_instance.assert_called_once_with(inst.id)
    # Listing, detaching the NSG and terminating all use the region's
    # profile; with the default one the VNIC update fails and, before, the
    # instance was left running.
    assert profiles and set(profiles) == {'ASHBURN'}


def test_terminate_instances_by_tags_terminates_when_nsg_detach_fails(
        regional_profile_clients):
    oci_sdk = pytest.importorskip('oci')
    core, net, _ = regional_profile_clients
    inst = _instance('ocid1.instance.oc1.iad.head', 'RUNNING',
                     {'ray-cluster-name': 'c-1'})
    core.list_instances.return_value.data = [inst]
    net.update_vnic.side_effect = oci_sdk.exceptions.ServiceError(
        status=404,
        code='NotAuthorizedOrNotFound',
        headers={},
        message='Authorization failed or requested resource not found.')

    with mock.patch.object(query_utils, 'logger') as logger:
        failed = query_utils.query_helper.terminate_instances_by_tags(
            {'ray-cluster-name': 'c-1'}, 'us-ashburn-1')

    # Releasing the NSG is best effort; the instance is terminated anyway.
    assert failed == 0
    core.terminate_instance.assert_called_once_with(inst.id)
    warning = logger.warning.call_args.args[0]
    assert f'Failed to detach NSG {_NSG}' in warning
    assert 'terminating it anyway' in warning


def test_terminate_instances_by_tags_counts_terminate_failures(
        regional_profile_clients):
    oci_sdk = pytest.importorskip('oci')
    core, _, _ = regional_profile_clients
    core.list_instances.return_value.data = [
        _instance('ocid1.instance.oc1.iad.head', 'RUNNING',
                  {'ray-cluster-name': 'c-1'}),
        _instance('ocid1.instance.oc1.iad.worker', 'RUNNING',
                  {'ray-cluster-name': 'c-1'}),
    ]
    core.terminate_instance.side_effect = [
        oci_sdk.exceptions.ServiceError(status=409,
                                        code='Conflict',
                                        headers={},
                                        message='busy'),
        None,
    ]

    with mock.patch.object(query_utils, 'logger'):
        failed = query_utils.query_helper.terminate_instances_by_tags(
            {'ray-cluster-name': 'c-1'}, 'us-ashburn-1')

    # One failure is reported, and the second instance is still terminated.
    assert failed == 1
    assert core.terminate_instance.call_count == 2
