"""Regression tests for stable Vast resource identities."""

import importlib.util
import io
import sys
from typing import List
from unittest import mock

import pandas as pd
import pytest

from sky import clouds
from sky import exceptions
from sky.adaptors import vast as vast_adaptor
from sky.catalog import common
from sky.catalog import vast_catalog
from sky.catalog import vast_refresh
from sky.clouds import vast as vast_cloud
from sky.provision.vast import utils as vast_utils
from sky.resources import Resources
from sky.utils import annotations
from sky.utils import resources_utils

_A100_INSTANCE_TYPE = 'vastv2-1x-A100-81920-4-8192'
_RTX_A6000_INSTANCE_TYPE = 'vastv2-1x-RTX_A6000-49152-4-8192'

_VALID_VAST_CATALOG_CSV = """InstanceType,AcceleratorName,AcceleratorCount,vCPUs,MemoryGiB,GpuInfo,Price,SpotPrice,Region
1x-A100-4-8192,A100,1,4,8,\"{'Gpus': [{'MemoryInfo': {'SizeInMiB': 81920}}]}\",0.8,0.8,any
1x-RTX_A6000-4-8192,RTXA6000,1,4,8,\"{'Gpus': [{'MemoryInfo': {'SizeInMiB': 49152}}]}\",0.8,0.8,any
vastv2-1x-A100-81920-4-8192,A100-80GB,1,4,8,\"{'Gpus': [{'MemoryInfo': {'SizeInMiB': 81920}}]}\",0.8,0.8,any
vastv2-1x-RTX_A6000-49152-4-8192,RTXA6000,1,4,8,\"{'Gpus': [{'MemoryInfo': {'SizeInMiB': 49152}}]}\",0.8,0.8,any
"""


@pytest.fixture(autouse=True)
def clear_request_catalog_cache(monkeypatch):
    monkeypatch.setattr(vast_catalog, '_df',
                        pd.read_csv(io.StringIO(_VALID_VAST_CATALOG_CSV)))
    annotations.clear_request_level_cache()
    yield
    annotations.clear_request_level_cache()


def test_vast_sdk_exposes_launcher_api_key_contract():
    """The pinned SDK exposes the API key through its nested client."""
    if sys.version_info < (3, 10):
        pytest.skip("Vast SDK requires Python >=3.10.")

    pytest.importorskip("vastai.sdk")
    from vastai.sdk import VastAI  # pylint: disable=import-outside-toplevel

    client = VastAI(api_key="test-api-key")

    assert client.client.api_key == "test-api-key"


def test_vast_missing_credentials_does_not_suggest_unpinned_sdk(monkeypatch):
    monkeypatch.setattr(vast_cloud.common, "can_import_modules",
                        lambda _modules: True)
    monkeypatch.setattr(vast_cloud.os.path, "exists", lambda _path: False)

    credentials_valid, guidance = vast_cloud.Vast._check_compute_credentials()

    assert not credentials_valid
    assert "pip install vastai" not in guidance


def _make_vast_client(*methods: str) -> mock.Mock:
    client = mock.Mock(spec=[*methods, "client"])
    client.client = mock.Mock(spec=["api_key"])
    client.client.api_key = "test-api-key"
    if 'search_offers' in methods:

        def search_offers(*_args, **_kwargs):
            """Return complete live-offer records from focused test fixtures."""
            return [{
                'cpu_cores': 4,
                'cpu_ram': 8192,
                'gpu_ram': 81920,
                'disk_space': 30,
                'geolocation': 'US',
                'rentable': True,
                'rented': False,
                **offer,
            } for offer in client.search_offers.return_value]

        client.search_offers.side_effect = search_offers
    return client


def test_vast_catalog_does_not_expose_ephemeral_offer_resolution():
    """Marketplace offers must never be durable SkyPilot instance types."""
    assert not hasattr(vast_catalog, "get_dynamic_offer")
    assert not hasattr(vast_catalog, "get_dynamic_replacement")
    assert importlib.util.find_spec("sky.catalog.vast_dynamic_catalog") is None


def test_vast_catalog_uses_only_stable_instance_types(monkeypatch):
    """Catalog instance type identifiers must survive marketplace refreshes."""
    monkeypatch.setattr(vast_catalog, '_df',
                        pd.read_csv(io.StringIO(_VALID_VAST_CATALOG_CSV)))
    assert all(not str(instance_type).startswith("dynamic-")
               for instance_type in vast_catalog._catalog_df()["InstanceType"])


def test_vast_catalog_reuses_snapshot_within_request(monkeypatch):
    """Vast catalog queries use local metadata without hosted request fetches."""
    monkeypatch.setattr(vast_catalog, '_df',
                        pd.read_csv(io.StringIO(_VALID_VAST_CATALOG_CSV)))
    calls: List[str] = []
    monkeypatch.setattr(common, 'fetch_catalog_text',
                        lambda filename: calls.append(filename))

    assert vast_catalog._catalog_df().iloc[0]["AcceleratorName"] == "A100"
    assert vast_catalog._catalog_df().iloc[0]["AcceleratorName"] == "A100"
    assert calls == []

    annotations.clear_request_level_cache()
    assert vast_catalog._catalog_df().iloc[0]["AcceleratorName"] == "A100"
    assert calls == []


def test_list_accelerators_keeps_distinct_gpu_memory_variants():
    """Listing must retain 40GB and 80GB variants with an otherwise equal shape."""
    catalog_df = pd.DataFrame([{
        'InstanceType': 'same-shape',
        'AcceleratorName': 'A100',
        'AcceleratorCount': 1,
        'vCPUs': 32,
        'MemoryGiB': 64,
        'GpuInfo': "{'Gpus': [{'MemoryInfo': {'SizeInMiB': 40960}}]}",
        'Price': .5,
        'SpotPrice': .5,
        'Region': 'Georgia, US, NA',
    }, {
        'InstanceType': 'same-shape',
        'AcceleratorName': 'A100',
        'AcceleratorCount': 1,
        'vCPUs': 32,
        'MemoryGiB': 64,
        'GpuInfo': "{'Gpus': [{'MemoryInfo': {'SizeInMiB': 81920}}]}",
        'Price': .9,
        'SpotPrice': .9,
        'Region': 'Prague, CZ, EU',
    }])

    accelerators = common.list_accelerators_impl('vast', catalog_df, True, None,
                                                 None, None)

    assert [info.device_memory for info in accelerators['A100']] == [40., 80.]


def test_vast_catalog_rejects_missing_required_columns(monkeypatch):
    """Malformed local metadata cannot be used for Vast resource matching."""
    monkeypatch.setattr(
        vast_catalog, '_df',
        pd.DataFrame([{
            'InstanceType': 'example',
            'AcceleratorName': 'A100',
        }]))

    with pytest.raises(common.CatalogFetchError,
                       match='missing required columns'):
        vast_catalog._catalog_df()


def test_vast_catalog_rejects_payload_without_gpu_rows(monkeypatch):
    """A local catalog with no usable GPUs fails before scheduling work."""
    monkeypatch.setattr(
        vast_catalog, '_df',
        pd.read_csv(
            io.StringIO(
                """InstanceType,AcceleratorName,AcceleratorCount,vCPUs,MemoryGiB,GpuInfo,Price,SpotPrice,Region
small,,0,2,4,,0.1,0.1,any
""")))

    with pytest.raises(common.CatalogFetchError, match="no usable GPU rows"):
        vast_catalog._catalog_df()


def test_vast_feasible_resources_reports_catalog_fetch_failure(monkeypatch):
    monkeypatch.setattr(
        vast_catalog,
        'get_instance_type_for_accelerator',
        mock.Mock(side_effect=common.CatalogFetchError('catalog offline')),
    )

    feasible_resources = vast_cloud.Vast()._get_feasible_launchable_resources(
        Resources(cloud=vast_cloud.Vast(), accelerators={'A100': 1}))

    assert feasible_resources.resources_list == []
    assert feasible_resources.hint is not None
    assert 'catalog offline' in feasible_resources.hint


def test_vast_country_extraction_uses_country_not_continent():
    """Raw catalog regions resolve their country, never the trailing continent."""
    assert vast_adaptor.extract_country_code('Jiangsu, CN, AS') == 'CN'
    assert vast_adaptor.extract_country_code('Japan, JP, AS') == 'JP'
    assert vast_adaptor.extract_country_code('France, FR, EU') == 'FR'

    with pytest.raises(ValueError, match='country'):
        vast_adaptor.extract_country_code('France, FRA, EU')


def test_vast_live_query_requires_available_offers_without_catalog_bucketing(
        monkeypatch):
    """Exact live admission rejects unavailable or incomplete offers."""
    requirements = vast_adaptor.get_offer_requirements(
        _A100_INSTANCE_TYPE,
        region=None,
        disk_size=64,
        datacenter_only=False,
        reliable_hosts=False,
        network_tier='standard',
    )
    client = mock.Mock(spec=['search_offers'])
    client.search_offers.return_value = [{
        'id': 1,
        'gpu_name': 'A100',
        'num_gpus': 1,
        'gpu_ram': 81920,
        'cpu_cores': 4,
        'cpu_ram': 8192,
        'disk_space': 64,
        'geolocation': 'Zurich, CH, EU',
        'rentable': False,
        'rented': False,
    }, {
        'id': 2,
        'gpu_name': 'A100',
        'num_gpus': 1,
        'gpu_ram': 81920,
        'cpu_cores': 4,
        'cpu_ram': 8192,
        'disk_space': 64,
        'geolocation': 'Zurich, CH, EU',
        'rentable': True,
        'rented': True,
    }, {
        'id': 3,
        'gpu_name': 'A100',
        'num_gpus': 1,
        'gpu_ram': 81920,
        'cpu_cores': 4,
        'cpu_ram': 8192,
        'disk_space': 64,
        'geolocation': 'Zurich, CH, EU',
        'rentable': True,
    }, {
        'id': 4,
        'gpu_name': 'A100',
        'num_gpus': 1,
        'gpu_ram': 81920,
        'cpu_cores': 4,
        'cpu_ram': 8192,
        'disk_space': 64,
        'geolocation': 'Zurich, CH, EU',
        'rentable': True,
        'rented': False,
    }]
    monkeypatch.setattr(vast_adaptor, 'vast', lambda: client)

    result = vast_adaptor.get_live_offer_matches(requirements)

    query = client.search_offers.call_args.kwargs['query']
    assert [offer['id'] for offer in result.offers] == [4]
    assert result.rejection_counts == (('availability', 3),)
    assert 'rentable=true' in query
    assert 'rented=false' in query
    assert 'chunked=true' not in query
    assert 'georegion=true' not in query


def test_vast_targeted_live_query_is_cached_per_requirements(monkeypatch):
    """Identical requirements reuse one unbounded, server-filtered live query."""
    client = mock.Mock(spec=['search_offers'])
    client.search_offers.return_value = []
    monkeypatch.setattr(vast_adaptor, 'vast', lambda: client)
    requirements = vast_adaptor.get_offer_requirements(
        '1x-A100-4-8192',
        region='France, FR, EU',
        disk_size=64,
        datacenter_only=False,
        reliable_hosts=False,
        network_tier='standard',
    )

    assert vast_adaptor.get_live_offer_matches(requirements).offers == ()
    assert vast_adaptor.get_live_offer_matches(requirements).offers == ()

    client.search_offers.assert_called_once_with(
        query=vast_adaptor.build_offer_query(requirements), order='dph_total')


def test_vast_precheck_and_provisioning_share_exact_available_query(
        monkeypatch):
    """Provisioning cannot weaken the exact query used by feasibility checks."""
    client = _make_vast_client('search_offers', 'create_instance',
                               'show_instance')
    client.search_offers.return_value = [{
        'id': 123,
        'gpu_name': 'A100',
        'num_gpus': 1,
        'dph_total': .4,
    }]
    client.create_instance.return_value = {'new_contract': 456}
    client.show_instance.return_value = {
        'id': 456,
        'gpu_name': 'A100',
        'num_gpus': 1,
        'gpu_ram': 81920,
    }
    monkeypatch.setattr(vast_adaptor, 'vast', lambda: client)
    monkeypatch.setattr(vast_utils.vast, 'vast', lambda: client)
    requirements = vast_adaptor.get_offer_requirements(
        '1x-A100-4-8192',
        region='US',
        disk_size=30,
        datacenter_only=False,
        reliable_hosts=False,
        network_tier='standard',
    )

    assert vast_adaptor.get_live_offer_matches(requirements).offers
    assert vast_utils.launch(
        name='test-head',
        instance_type='1x-A100-4-8192',
        region='US',
        disk_size=30,
        image_name='vastai/base:0.0.2',
        ports=None,
        preemptible=False,
        secure_only=False,
    ) == 456

    query_calls = [
        call.kwargs['query'] for call in client.search_offers.call_args_list
    ]
    assert query_calls == [vast_adaptor.build_offer_query(requirements)] * 2
    assert 'rentable=true' in query_calls[0]
    assert 'rented=false' in query_calls[0]
    assert 'chunked=true' not in query_calls[0]
    assert 'georegion=true' not in query_calls[0]


def test_vast_v2_identity_rejects_lower_memory_live_offer(monkeypatch):
    """An 80GB Vast resource must not admit a cheaper 40GB A100 offer."""
    requirements = vast_adaptor.get_offer_requirements(
        'vastv2-1x-A100_SXM4-81920-32-65536',
        region=None,
        disk_size=64,
        datacenter_only=False,
        reliable_hosts=False,
        network_tier='standard',
    )
    client = mock.Mock(spec=['search_offers'])
    client.search_offers.return_value = [{
        'id': 40,
        'gpu_name': 'A100 SXM4',
        'num_gpus': 1,
        'gpu_ram': 40960,
        'cpu_cores': 32,
        'cpu_ram': 65536,
        'disk_space': 64,
        'geolocation': 'Georgia, US, NA',
        'rentable': True,
        'rented': False,
    }, {
        'id': 80,
        'gpu_name': 'A100 SXM4',
        'num_gpus': 1,
        'gpu_ram': 81920,
        'cpu_cores': 32,
        'cpu_ram': 65536,
        'disk_space': 64,
        'geolocation': 'Prague, CZ, EU',
        'rentable': True,
        'rented': False,
    }]
    monkeypatch.setattr(vast_adaptor, 'vast', lambda: client)

    result = vast_adaptor.get_live_offer_matches(requirements)

    assert requirements.gpu_ram_mib == 81920
    assert [offer['id'] for offer in result.offers] == [80]
    assert result.rejection_counts == (('vram', 1),)
    assert 'gpu_ram>=80' in client.search_offers.call_args.kwargs['query']


def test_vast_legacy_identity_fails_closed_when_memory_is_ambiguous(
        monkeypatch):
    """Legacy types may not choose a 40GB or 80GB variant arbitrarily."""
    catalog_df = pd.DataFrame([{
        'InstanceType': '1x-A100_SXM4-32-65536',
        'AcceleratorName': 'A100',
        'AcceleratorCount': 1,
        'vCPUs': 32,
        'MemoryGiB': 64,
        'GpuInfo': "{'Gpus': [{'MemoryInfo': {'SizeInMiB': 40960}}]}",
        'Price': .5,
        'SpotPrice': .5,
        'Region': 'Georgia, US, NA',
    }, {
        'InstanceType': '1x-A100_SXM4-32-65536',
        'AcceleratorName': 'A100-80GB',
        'AcceleratorCount': 1,
        'vCPUs': 32,
        'MemoryGiB': 64,
        'GpuInfo': "{'Gpus': [{'MemoryInfo': {'SizeInMiB': 81920}}]}",
        'Price': .9,
        'SpotPrice': .9,
        'Region': 'Prague, CZ, EU',
    }])
    monkeypatch.setattr(vast_catalog, '_df', catalog_df)

    with pytest.raises(ValueError, match='ambiguous.*VRAM'):
        vast_adaptor.get_offer_requirements(
            '1x-A100_SXM4-32-65536',
            region=None,
            disk_size=64,
            datacenter_only=False,
            reliable_hosts=False,
            network_tier='standard',
        )


def test_vast_live_admission_uses_targeted_country_query(monkeypatch):
    """An explicit Vast region remains a strict live country constraint."""
    monkeypatch.setattr(
        vast_catalog,
        'get_instance_type_for_accelerator',
        lambda *_args, **_kwargs: ([_A100_INSTANCE_TYPE], []),
    )
    client = mock.Mock(spec=['search_offers'])
    client.search_offers.return_value = [{
        'gpu_name': 'A100',
        'num_gpus': 1,
        'gpu_ram': 81920,
        'cpu_cores': 4,
        'cpu_ram': 8192,
        'disk_space': 64,
        'geolocation': 'Paris, FR, EU',
        'rentable': True,
        'rented': False,
    }]
    monkeypatch.setattr(vast_adaptor, 'vast', lambda: client)

    feasible_resources = vast_cloud.Vast()._get_feasible_launchable_resources(
        Resources(
            cloud=vast_cloud.Vast(),
            accelerators={'A100': 1},
            region='France, FR, EU',
            disk_size=64,
        ))

    assert [
        resource.instance_type for resource in feasible_resources.resources_list
    ] == [_A100_INSTANCE_TYPE]
    assert 'geolocation=FR' in client.search_offers.call_args.kwargs['query']


def test_vast_explicit_country_does_not_require_a_static_catalog_region():
    """A valid Vast country remains selectable when no catalog row lists it."""
    assert vast_cloud.Vast().validate_region_zone('Nowhere, ZZ, XX',
                                                  None) == ('Nowhere, ZZ, XX',
                                                            None)


def test_vast_live_admission_uses_any_for_unscoped_marketplace_capacity(
        monkeypatch):
    """Unscoped Vast capacity may use live offers outside catalog locations."""
    monkeypatch.setattr(
        vast_catalog,
        'get_instance_type_for_accelerator',
        lambda *_args, **_kwargs: ([_A100_INSTANCE_TYPE], []),
    )
    client = mock.Mock(spec=['search_offers'])
    client.search_offers.return_value = [{
        'gpu_name': 'A100',
        'num_gpus': 1,
        'gpu_ram': 81920,
        'cpu_cores': 4,
        'cpu_ram': 8192,
        'disk_space': 64,
        'geolocation': 'Zurich, CH, EU',
        'rentable': True,
        'rented': False,
    }]
    monkeypatch.setattr(vast_adaptor, 'vast', lambda: client)

    feasible_resources = vast_cloud.Vast()._get_feasible_launchable_resources(
        Resources(
            cloud=vast_cloud.Vast(),
            accelerators={'A100': 1},
            disk_size=64,
        ))

    assert [resource.region for resource in feasible_resources.resources_list
           ] == ['any']
    assert 'geolocation' not in client.search_offers.call_args.kwargs['query']


def test_vast_unscoped_docker_image_survives_admission_and_deployment(
        monkeypatch):
    """Unscoped Vast deployment preserves its Docker image without geolocation."""
    requested_image = 'registry.example.com/ximilar/llm:test'
    client = mock.Mock(spec=['search_offers'])
    client.search_offers.return_value = [{
        'gpu_name': 'A100',
        'num_gpus': 1,
        'gpu_ram': 81920,
        'cpu_cores': 4,
        'cpu_ram': 8192,
        'disk_space': 64,
        'geolocation': 'Zurich, CH, EU',
        'rentable': True,
        'rented': False,
    }]
    monkeypatch.setattr(vast_adaptor, 'vast', lambda: client)
    cloud = vast_cloud.Vast()

    feasible_resources = cloud._get_feasible_launchable_resources(
        Resources(
            cloud=cloud,
            instance_type=_A100_INSTANCE_TYPE,
            disk_size=64,
            image_id={'docker': requested_image},
        ))

    assert len(feasible_resources.resources_list) == 1
    admitted_resources = feasible_resources.resources_list[0]
    assert admitted_resources.region == 'any'
    assert admitted_resources.extract_docker_image() == requested_image
    assert 'geolocation' not in client.search_offers.call_args.kwargs['query']

    with mock.patch.object(
            cloud, 'get_accelerators_from_instance_type',
            return_value={'A100': 1}), mock.patch(
                'sky.clouds.vast.skypilot_config.get_effective_region_config',
                side_effect=lambda **kwargs: kwargs['default_value']):
        deploy_variables = cloud.make_deploy_resources_variables(
            resources=admitted_resources,
            cluster_name=resources_utils.ClusterName('test', 'test'),
            region=clouds.Region('any'),
            zones=None,
            num_nodes=1,
        )

    assert deploy_variables['image_id'] == requested_image


def test_vast_live_admission_retries_once_after_forced_catalog_refresh(
        monkeypatch):
    """An unscoped live miss refreshes catalog metadata and retries once."""
    monkeypatch.setattr(
        vast_catalog,
        'get_instance_type_for_accelerator',
        lambda *_args, **_kwargs: ([_A100_INSTANCE_TYPE], []),
    )
    client = mock.Mock(spec=['search_offers'])
    client.search_offers.side_effect = [[],
                                        [{
                                            'gpu_name': 'A100',
                                            'num_gpus': 1,
                                            'gpu_ram': 81920,
                                            'cpu_cores': 4,
                                            'cpu_ram': 8192,
                                            'disk_space': 64,
                                            'geolocation': 'Zurich, CH, EU',
                                            'rentable': True,
                                            'rented': False,
                                        }]]
    monkeypatch.setattr(vast_adaptor, 'vast', lambda: client)
    refresh_catalog = mock.Mock(return_value=True)
    monkeypatch.setattr(vast_refresh, 'refresh_catalog', refresh_catalog)

    feasible_resources = vast_cloud.Vast()._get_feasible_launchable_resources(
        Resources(
            cloud=vast_cloud.Vast(),
            accelerators={'A100': 1},
            disk_size=64,
        ))

    assert [resource.region for resource in feasible_resources.resources_list
           ] == ['any']
    refresh_catalog.assert_called_once_with(force=True)
    assert client.search_offers.call_count == 2


def test_vast_live_admission_reports_sanitized_rejection_counts(monkeypatch):
    """A live no-match names failed constraints without exposing offer fields."""
    client = mock.Mock(spec=['search_offers'])
    client.search_offers.return_value = [{
        'gpu_name': 'A100',
        'num_gpus': 1,
        'gpu_ram': 81920,
        'cpu_cores': 4,
        'cpu_ram': 8192,
        'disk_space': 63,
        'geolocation': 'Private locality, FR, EU',
        'rentable': True,
        'rented': False,
    }]
    monkeypatch.setattr(vast_adaptor, 'vast', lambda: client)

    feasible_resources = vast_cloud.Vast()._get_feasible_launchable_resources(
        Resources(
            cloud=vast_cloud.Vast(),
            instance_type=_A100_INSTANCE_TYPE,
            region='France, FR, EU',
            disk_size=64,
        ))

    assert feasible_resources.resources_list == []
    assert feasible_resources.hint is not None
    assert 'offers examined=1' in feasible_resources.hint
    assert 'disk=1' in feasible_resources.hint
    assert 'Private locality' not in feasible_resources.hint


def test_vast_any_region_stays_launchable_and_uses_catalog_estimate(
        monkeypatch):
    """The internal any region stays launchable without a catalog any row."""
    catalog_df = pd.read_csv(io.StringIO(_VALID_VAST_CATALOG_CSV))
    catalog_df['Region'] = 'France, FR, EU'
    monkeypatch.setattr(vast_catalog, '_df', catalog_df)
    vast_catalog._catalog_df.cache_clear()

    regions = vast_cloud.Vast.regions_with_offering(_A100_INSTANCE_TYPE, None,
                                                    False, 'any', None)

    assert regions == [clouds.Region('any')]
    assert vast_cloud.Vast().instance_type_to_hourly_cost(
        _A100_INSTANCE_TYPE, False, 'Nowhere, ZZ, XX') == .8


def test_vast_live_offer_match_enforces_host_and_network_policy():
    """Reliable best-tier admission rejects hosts missing required safeguards."""
    requirements = vast_adaptor.get_offer_requirements(
        _A100_INSTANCE_TYPE,
        region='France, FR, EU',
        disk_size=64,
        datacenter_only=True,
        reliable_hosts=True,
        network_tier='best',
    )
    matching_offer = {
        'gpu_name': 'A100',
        'num_gpus': 1,
        'gpu_ram': 81920,
        'cpu_cores': 4,
        'cpu_ram': 8192,
        'disk_space': 64,
        'geolocation': 'Paris, FR, EU',
        'rentable': True,
        'rented': False,
        'verified': True,
        'datacenter': True,
        'hosting_type': 1,
        'reliability': 0.99,
        'inet_down': 1000,
        'inet_up': 1000,
    }

    assert vast_adaptor.offer_matches_requirements(matching_offer, requirements)
    matching_offer['inet_up'] = 999
    assert not vast_adaptor.offer_matches_requirements(matching_offer,
                                                       requirements)


def test_live_search_without_capacity_raises_typed_resource_error(monkeypatch):
    """An empty live result raises a typed capacity error with safe syntax."""
    client = mock.Mock(spec=["search_offers"])
    client.search_offers.return_value = []
    monkeypatch.setattr(vast_utils.vast, "vast", lambda: client)

    with pytest.raises(exceptions.VastOfferUnavailableError):
        vast_utils.launch(
            name="test-head",
            instance_type="1x-A100-4-8192",
            region="US",
            disk_size=30,
            image_name="vastai/base:0.0.2",
            ports=None,
            preemptible=True,
            secure_only=False,
        )

    assert 'geolocation=US' in client.search_offers.call_args.kwargs["query"]


def test_live_search_any_region_does_not_add_geolocation_filter(monkeypatch):
    client = mock.Mock(spec=["search_offers"])
    client.search_offers.return_value = []
    monkeypatch.setattr(vast_utils.vast, "vast", lambda: client)

    with pytest.raises(exceptions.VastOfferUnavailableError):
        vast_utils.launch(
            name="test-head",
            instance_type="1x-A100-4-8192",
            region="any",
            disk_size=30,
            image_name="vastai/base:0.0.2",
            ports=None,
            preemptible=True,
            secure_only=False,
        )

    assert "geolocation" not in client.search_offers.call_args.kwargs["query"]


def test_list_instances_preserves_null_provisioning_status(monkeypatch):
    client = _make_vast_client("show_instances")
    client.show_instances.return_value = [{
        'id': 123,
        'actual_status': None,
        'label': 'test-head',
    }]
    monkeypatch.setattr(vast_utils.vast, 'vast', lambda: client)

    instances = vast_utils.list_instances()

    assert instances['123']['status'] == 'NULL'
    assert instances['123']['name'] == 'test-head'


def test_launch_reconciles_eventual_instance_visibility(monkeypatch):
    """A successful create must not be retried merely because reads lag."""
    client = _make_vast_client("search_offers", "create_instance",
                               "show_instance")
    client.search_offers.return_value = [{
        "id": 123,
        "gpu_name": "A100",
        "num_gpus": 1,
        "min_bid": 0.4,
        "dph_total": 0.4,
    }]
    client.create_instance.return_value = {"new_contract": 456}
    client.show_instance.side_effect = [
        RuntimeError("not visible yet"), {
            "id": 456,
            "gpu_name": "A100",
            "num_gpus": 1,
            "gpu_ram": 81920,
        }
    ]
    monkeypatch.setattr(vast_utils.vast, "vast", lambda: client)
    monkeypatch.setattr(vast_utils.time, "sleep", lambda _seconds: None)

    assert vast_utils.launch(
        name="test-head",
        instance_type="1x-A100-4-8192",
        region="US",
        disk_size=30,
        image_name="vastai/base:0.0.2",
        ports=None,
        preemptible=True,
        secure_only=False,
    ) == 456
    assert client.create_instance.call_count == 1
    assert client.show_instance.call_count == 2


def test_launch_normalizes_template_startup_env_and_generated_login(
        monkeypatch):
    """Template launches preserve startup options while using parser-safe queries."""
    client = _make_vast_client("search_offers", "create_instance",
                               "show_instance")
    client.search_offers.return_value = [{
        "id": 123,
        "gpu_name": "RTX A6000",
        "num_gpus": 1,
        "min_bid": 0.4,
        "dph_total": 0.4,
    }]
    client.create_instance.return_value = {"new_contract": 456}
    client.show_instance.return_value = {
        "id": 456,
        "gpu_name": "RTX A6000",
        "num_gpus": 1,
        "gpu_ram": 49152,
    }
    monkeypatch.setattr(vast_utils.vast, "vast", lambda: client)

    assert vast_utils.launch(
        name="test-head",
        instance_type="1x-RTX_A6000-4-8192",
        region="US",
        disk_size=30,
        image_name="ignored-with-template",
        ports=None,
        preemptible=False,
        secure_only=False,
        login="-u registry-user -p registry-token registry.example.com",
        create_instance_kwargs={
            "template_hash_id": "template-123",
            "onstart_cmd": "echo ready",
            "env": {
                "KEY": "value"
            },
            "extra": "--shm-size=16g",
        },
    ) == 456

    query = client.search_offers.call_args.kwargs["query"]
    assert 'gpu_name=RTX_A6000' in query

    params = client.create_instance.call_args.kwargs
    assert params["template_hash"] == "template-123"
    assert params["template_hash_id"] == "template-123"
    assert (params["login"] ==
            "-u registry-user -p registry-token registry.example.com")
    assert params["env"] == {"__SOURCE": "skypilot", "KEY": "value"}
    assert params["extra"] == "--shm-size=16g"
    assert "image" not in params
    assert "disk" not in params
    assert ('echo "test-api-key" > ~/.vast_api_key' in params["onstart_cmd"])
    assert params["onstart_cmd"].endswith("echo ready")


def test_launch_uses_reliable_filters_and_excludes_failed_machine(monkeypatch):
    """Reliable launches filter the requested GPU and exclude failed machines."""
    client = _make_vast_client("search_offers", "create_instance",
                               "show_instance")
    client.search_offers.return_value = [{
        "id": 123,
        "machine_id": 1,
        "gpu_name": "A100",
        "num_gpus": 1,
        "cpu_cores": 4,
        "cpu_ram": 8192,
        "disk_space": 30,
        "geolocation": "US",
        "verified": True,
        "datacenter": True,
        "hosting_type": 1,
        "inet_down": 1000,
        "dph_total": 0.4,
        "reliability": 0.99,
    }, {
        "id": 456,
        "machine_id": 2,
        "gpu_name": "A100",
        "num_gpus": 1,
        "cpu_cores": 4,
        "cpu_ram": 8192,
        "disk_space": 30,
        "geolocation": "US",
        "verified": True,
        "datacenter": True,
        "hosting_type": 1,
        "inet_down": 1000,
        "dph_total": 0.5,
        "reliability": 0.99,
    }]
    client.create_instance.return_value = {"new_contract": 789}
    client.show_instance.return_value = {
        "id": 789,
        "gpu_name": "A100",
        "num_gpus": 1,
        "gpu_ram": 81920,
    }
    monkeypatch.setattr(vast_utils.vast, "vast", lambda: client)

    assert vast_utils.launch(
        name="test-head",
        instance_type="1x-A100-4-8192",
        region="US",
        disk_size=30,
        image_name="vastai/base:0.0.2",
        ports=None,
        preemptible=False,
        secure_only=False,
        reliable_hosts=True,
        excluded_machine_ids=[1],
    ) == 789

    query = client.search_offers.call_args.kwargs["query"]
    for filter_expression in (
            "verified=true",
            "datacenter=true",
            "hosting_type>=1",
            "inet_down>=1000",
    ):
        assert filter_expression in query
    assert client.create_instance.call_args.kwargs["id"] == 456


def test_live_query_survives_vast_sdk_preprocessing(monkeypatch):
    """Ensure every launch filter reaches Vast SDK 1.5.0's query parser."""
    pytest.importorskip("vastai.api.query")
    client = _make_vast_client("search_offers", "create_instance",
                               "show_instance")
    client.search_offers.return_value = [{
        "id": 123,
        "gpu_name": "RTX A6000",
        "num_gpus": 1,
        "cpu_cores": 4,
        "cpu_ram": 8192,
        "disk_space": 30,
        "geolocation": "US",
        "verified": True,
        "datacenter": True,
        "hosting_type": 1,
        "inet_down": 1000,
        "inet_up": 1000,
        "dph_total": 0.4,
        "reliability": 0.99,
    }]
    client.create_instance.return_value = {"new_contract": 456}
    client.show_instance.return_value = {
        "id": 456,
        "gpu_name": "RTX A6000",
        "num_gpus": 1,
        "gpu_ram": 49152,
    }
    monkeypatch.setattr(vast_utils.vast, "vast", lambda: client)

    vast_utils.launch(
        name="test-head",
        instance_type="1x-RTX_A6000-4-8192",
        region="US",
        disk_size=30,
        image_name="vastai/base:0.0.2",
        ports=None,
        preemptible=False,
        secure_only=False,
        reliable_hosts=True,
        network_tier=resources_utils.NetworkTier.BEST,
    )

    # isort: off
    from vastai.api.query import (  # pylint: disable=import-outside-toplevel
        offers_alias, offers_fields, offers_mult, parse_query,
    )
    from vastai.utils import (  # pylint: disable=import-outside-toplevel
        preprocess_search_query,)
    # isort: on

    query = client.search_offers.call_args.kwargs["query"]
    _, _, preprocessed_query = preprocess_search_query(query)
    parsed_query = parse_query(preprocessed_query, {}, offers_fields,
                               offers_alias, offers_mult)

    assert parsed_query["geolocation"]["eq"] == "US"
    assert parsed_query["gpu_name"]["eq"] == "RTX A6000"
    assert parsed_query["num_gpus"]["eq"] == "1"
    assert parsed_query["cpu_ram"]["gte"] == 8000
    assert parsed_query['rentable']['eq'] is True
    assert parsed_query['rented']['eq'] is False
    assert "reliability" not in parsed_query
    assert parsed_query["verified"]["eq"] is True
    assert parsed_query["datacenter"]["eq"] is True
    assert parsed_query["inet_up"]["gte"] == "1000"


def test_launch_discards_mismatched_offers_before_selection(monkeypatch):
    """Never create a contract from a GPU offer that only matches by query order."""
    client = _make_vast_client("search_offers", "create_instance",
                               "show_instance")
    client.search_offers.return_value = [{
        "id": 123,
        "gpu_name": "H100",
        "num_gpus": 1,
        "dph_total": 0.1,
    }, {
        "id": 456,
        "gpu_name": "A100",
        "num_gpus": 1,
        "dph_total": 0.5,
    }]
    client.create_instance.return_value = {"new_contract": 789}
    client.show_instance.return_value = {
        "id": 789,
        "gpu_name": "A100",
        "num_gpus": 1,
        "gpu_ram": 81920,
    }
    monkeypatch.setattr(vast_utils.vast, "vast", lambda: client)

    assert vast_utils.launch(
        name="test-head",
        instance_type="1x-A100-4-8192",
        region="US",
        disk_size=30,
        image_name="vastai/base:0.0.2",
        ports=None,
        preemptible=False,
        secure_only=False,
    ) == 789

    assert client.create_instance.call_args.kwargs["id"] == 456


def test_launch_never_downgrades_per_gpu_vram(monkeypatch):
    """An 80 GiB resource selects its compatible offer over a cheaper 40 GiB GPU."""
    client = _make_vast_client("search_offers", "create_instance",
                               "show_instance")
    client.search_offers.return_value = [{
        "id": 40,
        "gpu_name": "A100 SXM4",
        "num_gpus": 1,
        "gpu_ram": 40960,
        "dph_total": 0.1,
    }, {
        "id": 80,
        "gpu_name": "A100 SXM4",
        "num_gpus": 1,
        "gpu_ram": 81920,
        "dph_total": 0.9,
    }]
    client.create_instance.return_value = {"new_contract": 456}
    client.show_instance.return_value = {
        "id": 456,
        "gpu_name": "A100 SXM4",
        "num_gpus": 1,
        "gpu_ram": 81920,
    }
    monkeypatch.setattr(vast_utils.vast, "vast", lambda: client)

    assert vast_utils.launch(
        name="test-head",
        instance_type='vastv2-1x-A100_SXM4-81920-4-8192',
        region="any",
        disk_size=30,
        image_name="vastai/base:0.0.2",
        ports=None,
        preemptible=False,
        secure_only=False,
    ) == 456

    assert client.create_instance.call_args.kwargs["id"] == 80


def test_launch_rejects_empty_exact_gpu_match(monkeypatch):
    """Fail closed when Vast returns only offers for a different GPU."""
    client = _make_vast_client("search_offers", "create_instance")
    client.search_offers.return_value = [{
        "id": 123,
        "gpu_name": "H100",
        "num_gpus": 1,
        "dph_total": 0.1,
    }]
    monkeypatch.setattr(vast_utils.vast, "vast", lambda: client)

    with pytest.raises(exceptions.VastOfferUnavailableError, match="A100"):
        vast_utils.launch(
            name="test-head",
            instance_type="1x-A100-4-8192",
            region="US",
            disk_size=30,
            image_name="vastai/base:0.0.2",
            ports=None,
            preemptible=False,
            secure_only=False,
        )

    client.create_instance.assert_not_called()


def test_launch_orders_matching_offers_and_logs_selected_price(monkeypatch):
    """Select the cheapest valid live offer and expose its actual price."""
    client = _make_vast_client("search_offers", "create_instance",
                               "show_instance")
    client.search_offers.return_value = [{
        "id": 123,
        "gpu_name": "A100",
        "num_gpus": 1,
        "dph_total": 0.5,
    }, {
        "id": 456,
        "gpu_name": "A100",
        "num_gpus": 1,
        "dph_total": 0.2,
    }]
    client.create_instance.return_value = {"new_contract": 789}
    client.show_instance.return_value = {
        "id": 789,
        "gpu_name": "A100",
        "num_gpus": 1,
        "gpu_ram": 81920,
    }
    monkeypatch.setattr(vast_utils.vast, "vast", lambda: client)

    with mock.patch.object(vast_utils.logger, "info") as log_info:
        vast_utils.launch(
            name="test-head",
            instance_type="1x-A100-4-8192",
            region="US",
            disk_size=30,
            image_name="vastai/base:0.0.2",
            ports=None,
            preemptible=False,
            secure_only=False,
        )

    assert client.search_offers.call_args.kwargs["order"] == "dph_total"
    assert client.create_instance.call_args.kwargs["id"] == 456
    assert log_info.call_args.args[-1] == 0.2


def test_launch_destroys_contract_when_created_gpu_mismatches(monkeypatch):
    """Destroy a paid contract immediately when its reported GPU is wrong."""
    client = _make_vast_client("search_offers", "create_instance",
                               "show_instance", "destroy_instance")
    client.search_offers.return_value = [{
        "id": 123,
        "gpu_name": "A100",
        "num_gpus": 1,
        "dph_total": 0.4,
    }]
    client.create_instance.return_value = {"new_contract": 456}
    client.show_instance.return_value = {
        "id": 456,
        "gpu_name": "H100",
        "num_gpus": 1,
        "gpu_ram": 81920,
    }
    monkeypatch.setattr(vast_utils.vast, "vast", lambda: client)

    with pytest.raises(exceptions.VastProvisioningError, match="H100"):
        vast_utils.launch(
            name="test-head",
            instance_type="1x-A100-4-8192",
            region="US",
            disk_size=30,
            image_name="vastai/base:0.0.2",
            ports=None,
            preemptible=False,
            secure_only=False,
        )

    client.destroy_instance.assert_called_once_with(id=456)


def test_launch_rejects_invalid_env_value(monkeypatch):
    """Invalid environment input is rejected before create_instance is called."""
    client = _make_vast_client("search_offers", "create_instance",
                               "show_instance")
    client.search_offers.return_value = [{
        "id": 123,
        "gpu_name": "A100",
        "num_gpus": 1,
        "min_bid": 0.4,
        "dph_total": 0.4,
    }]
    monkeypatch.setattr(vast_utils.vast, "vast", lambda: client)

    with pytest.raises(ValueError, match="env.*mapping or string"):
        vast_utils.launch(
            name="test-head",
            instance_type="1x-A100-4-8192",
            region="US",
            disk_size=30,
            image_name="vastai/base:0.0.2",
            ports=None,
            preemptible=False,
            secure_only=False,
            create_instance_kwargs={"env": 123},
        )


def test_launch_converts_disappeared_offer_to_typed_capacity_error(monkeypatch):
    """A vanished selected offer becomes a typed capacity error."""
    client = _make_vast_client("search_offers", "create_instance")
    client.search_offers.return_value = [{
        "id": 123,
        "gpu_name": "A100",
        "num_gpus": 1,
        "min_bid": 0.4,
        "dph_total": 0.4,
    }]
    client.create_instance.side_effect = RuntimeError(
        "offer 123 is no longer rentable")
    monkeypatch.setattr(vast_utils.vast, "vast", lambda: client)

    with pytest.raises(exceptions.VastOfferUnavailableError,
                       match="no longer rentable"):
        vast_utils.launch(
            name="test-head",
            instance_type="1x-A100-4-8192",
            region="US",
            disk_size=30,
            image_name="vastai/base:0.0.2",
            ports=None,
            preemptible=True,
            secure_only=False,
        )


def test_launch_retries_next_offer_when_selected_offer_disappears(monkeypatch):
    """Try the next exact GPU offer after a definitive marketplace race."""
    client = _make_vast_client("search_offers", "create_instance",
                               "show_instance")
    client.search_offers.return_value = [{
        "id": 123,
        "gpu_name": "A100",
        "num_gpus": 1,
        "dph_total": 0.2,
    }, {
        "id": 456,
        "gpu_name": "A100",
        "num_gpus": 1,
        "dph_total": 0.3,
    }]
    client.create_instance.side_effect = [
        RuntimeError("offer 123 is no longer rentable"),
        {
            "new_contract": 789
        },
    ]
    client.show_instance.return_value = {
        "id": 789,
        "gpu_name": "A100",
        "num_gpus": 1,
        "gpu_ram": 81920,
    }
    monkeypatch.setattr(vast_utils.vast, "vast", lambda: client)

    assert vast_utils.launch(
        name="test-head",
        instance_type="1x-A100-4-8192",
        region="US",
        disk_size=30,
        image_name="vastai/base:0.0.2",
        ports=None,
        preemptible=False,
        secure_only=False,
    ) == 789

    assert [
        call.kwargs["id"] for call in client.create_instance.call_args_list
    ] == [123, 456]


def test_launch_retries_delayed_contract_visibility(monkeypatch):
    """Retry transient None reads before accepting a newly created contract."""
    client = _make_vast_client("search_offers", "create_instance",
                               "show_instance", "destroy_instance")
    client.search_offers.return_value = [{
        "id": 123,
        "gpu_name": "A100",
        "num_gpus": 1,
        "dph_total": 0.2,
    }]
    client.create_instance.return_value = {"new_contract": 789}
    client.show_instance.side_effect = [
        None, None, {
            "id": 789,
            "gpu_name": "A100",
            "num_gpus": 1,
            "gpu_ram": 81920,
        }
    ]
    monkeypatch.setattr(vast_utils.vast, "vast", lambda: client)
    monkeypatch.setattr(vast_utils.time, "sleep", lambda _seconds: None)

    assert vast_utils.launch(
        name="test-head",
        instance_type="1x-A100-4-8192",
        region="US",
        disk_size=30,
        image_name="vastai/base:0.0.2",
        ports=None,
        preemptible=False,
        secure_only=False,
    ) == 789

    assert client.show_instance.call_count == 3
    client.destroy_instance.assert_not_called()


def test_launch_destroys_contract_when_visibility_never_materializes(
        monkeypatch):
    """Destroy a paid contract when Vast never exposes its identity."""
    client = _make_vast_client("search_offers", "create_instance",
                               "show_instance", "destroy_instance")
    client.search_offers.return_value = [{
        "id": 123,
        "gpu_name": "A100",
        "num_gpus": 1,
        "dph_total": 0.2,
    }]
    client.create_instance.return_value = {"new_contract": 789}
    client.show_instance.return_value = None
    monkeypatch.setattr(vast_utils.vast, "vast", lambda: client)
    monkeypatch.setattr(vast_utils.time, "sleep", lambda _seconds: None)

    with pytest.raises(exceptions.VastProvisioningError, match="visible"):
        vast_utils.launch(
            name="test-head",
            instance_type="1x-A100-4-8192",
            region="US",
            disk_size=30,
            image_name="vastai/base:0.0.2",
            ports=None,
            preemptible=False,
            secure_only=False,
        )

    client.destroy_instance.assert_called_once_with(id=789)
