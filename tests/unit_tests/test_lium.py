"""Tests for the Lium cloud."""
import inspect
from unittest import mock

import pytest

from sky import clouds
from sky import provision
from sky.catalog import lium_catalog
from sky.catalog.data_fetchers import fetch_lium
from sky.clouds import lium
from sky.provision.lium import instance
from sky.provision.lium import lium_utils


def test_lium_cloud():
    cloud = lium.Lium()
    assert cloud.canonical_name() == 'lium'
    assert repr(cloud) == 'Lium'


def test_lium_unsupported_features():
    cloud = lium.Lium()
    for feature, reason in cloud._CLOUD_UNSUPPORTED_FEATURES.items():
        assert feature in clouds.CloudImplementationFeatures
        assert isinstance(reason, str)


def test_lium_rejects_zones():
    cloud = lium.Lium()
    with pytest.raises(ValueError, match='does not support zones'):
        cloud.validate_region_zone('US', 'zone-1')


def test_instance_type_round_trip():
    shape = lium_utils.InstanceTypeShape(accelerator_name='H100',
                                         accelerator_count=8,
                                         vcpus=192,
                                         memory_gib=1007)
    instance_type = lium_utils.make_instance_type(shape)
    assert instance_type == 'H100_8x_192c_1007g'
    assert lium_utils.parse_instance_type(instance_type) == shape


def test_instance_type_keeps_the_dash_in_an_accelerator_name():
    shape = lium_utils.parse_instance_type('RTXPRO6000-WK_1x_32c_220g')
    assert shape.accelerator_name == 'RTXPRO6000-WK'
    assert shape.accelerator_count == 1


def test_parse_instance_type_rejects_a_malformed_name():
    with pytest.raises(ValueError):
        lium_utils.parse_instance_type('H100_8x')


def test_accelerator_name_ignores_the_vendor_prefix():
    # The node API and the public node feed name the same GPU differently.
    assert lium_utils.accelerator_name('NVIDIA GeForce RTX 5090') == 'RTX5090'
    assert lium_utils.accelerator_name('RTX 5090') == 'RTX5090'
    assert lium_utils.accelerator_name('NVIDIA H100 80GB HBM3') == 'H100-SXM'


def test_accelerator_name_skips_an_unknown_model():
    assert lium_utils.accelerator_name('Some New GPU') is None


def test_query_instances_accepts_every_router_argument():
    # The router in sky.provision passes these by keyword during refresh.
    router = list(inspect.signature(provision.query_instances).parameters)
    assert list(inspect.signature(
        instance.query_instances).parameters) == router[1:]


def _pod(pod_id, name, status='RUNNING'):
    return {
        'id': pod_id,
        'pod_name': name,
        'status': status,
        'ssh_connect_cmd': 'ssh root@203.0.113.5 -p 2222',
    }


def test_cluster_pods_match_only_the_exact_head_name(monkeypatch):
    # Another cluster's pod can start with this cluster's name plus a dash.
    monkeypatch.setattr(
        lium_utils, '_request', lambda method, path, **kwargs: [
            _pod('a', 'train-abcd-head'),
            _pod('b', 'train-abcd-extra-head'),
        ])
    assert list(lium_utils.get_cluster_pods('train-abcd')) == ['a']


def test_executor_listing_reads_every_page(monkeypatch):
    pages = []

    def request(method, path, params=None, **kwargs):
        pages.append(params['page'])
        full = lium_utils._EXECUTOR_PAGE_SIZE
        return [{}] * (full if params['page'] == 1 else 3)

    monkeypatch.setattr(lium_utils, '_request', request)
    rows = lium_utils._list_executors(8)
    assert pages == [1, 2]
    assert len(rows) == lium_utils._EXECUTOR_PAGE_SIZE + 3


def test_a_failed_head_pod_is_replaced(monkeypatch):
    deleted = []
    monkeypatch.setattr(
        lium_utils, 'get_cluster_pods', lambda name: {
            'dead': lium_utils._parse_pod(
                _pod('dead', 'c-head', 'CREATION_FAILED'))
        })
    monkeypatch.setattr(lium_utils, 'terminate_pod', deleted.append)
    monkeypatch.setattr(lium_utils, 'find_cheapest_free_node',
                        lambda instance_type, region: mock.Mock(id='n1'))
    monkeypatch.setattr(lium_utils, 'rent_node',
                        lambda node, pod_name, public_key: 'new')
    monkeypatch.setattr(lium_utils, 'wait_pod_ready',
                        lambda pod_id, timeout: object())
    config = mock.MagicMock(count=1,
                            node_config={
                                'InstanceType': 'H100_8x_192c_1007g',
                                'PublicKey': 'ssh-ed25519 AAAA'
                            })
    record = instance.run_instances('US', 'c', 'c', config)
    assert deleted == ['dead']
    assert record.head_instance_id == 'new'
    assert record.created_instance_ids == ['new']


def test_an_empty_fallback_catalog_is_not_kept(monkeypatch):
    monkeypatch.setattr(lium_catalog, '_catalog', None)
    monkeypatch.setattr(lium_catalog, '_read_published_catalog', lambda: None)
    feeds = [[], [{column: None for column in fetch_lium.CATALOG_COLUMNS}]]
    monkeypatch.setattr(fetch_lium, 'catalog_rows', lambda: feeds.pop(0))
    assert lium_catalog._get_df().empty
    # Both sources failed once; the next call tries them again.
    assert len(lium_catalog._get_df()) == 1
