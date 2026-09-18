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
