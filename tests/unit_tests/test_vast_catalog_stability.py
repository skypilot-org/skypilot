"""Regression tests for stable Vast resource identities."""

import importlib.util
import sys
from typing import List
from unittest import mock

import pytest

from sky import exceptions
from sky.catalog import common
from sky.catalog import vast_catalog
from sky.clouds import vast as vast_cloud
from sky.provision.vast import utils as vast_utils
from sky.resources import Resources
from sky.utils import annotations
from sky.utils import resources_utils

_VALID_VAST_CATALOG_CSV = """InstanceType,AcceleratorName,AcceleratorCount,vCPUs,MemoryGiB,GpuInfo,Price,SpotPrice,Region
1x-A100-4-8192,A100,1,4,8,\"{'Gpus': [{'MemoryInfo': {'SizeInMiB': 81920}}]}\",0.8,0.8,any
"""


@pytest.fixture(autouse=True)
def clear_request_catalog_cache():
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
    return client


def test_vast_catalog_does_not_expose_ephemeral_offer_resolution():
    """Marketplace offers must never be durable SkyPilot instance types."""
    assert not hasattr(vast_catalog, "get_dynamic_offer")
    assert not hasattr(vast_catalog, "get_dynamic_replacement")
    assert importlib.util.find_spec("sky.catalog.vast_dynamic_catalog") is None


def test_vast_catalog_uses_only_stable_instance_types(monkeypatch):
    """Catalog instance type identifiers must survive marketplace refreshes."""
    monkeypatch.setattr(common, "fetch_catalog_text",
                        lambda _filename: _VALID_VAST_CATALOG_CSV)
    assert all(not str(instance_type).startswith("dynamic-")
               for instance_type in vast_catalog._catalog_df()["InstanceType"])


def test_vast_catalog_reuses_snapshot_within_request(monkeypatch):
    """Vast catalog queries share one stable metadata snapshot per request."""
    payloads = [
        _VALID_VAST_CATALOG_CSV,
        _VALID_VAST_CATALOG_CSV.replace("A100", "H100")
    ]
    calls: List[str] = []

    def fetch_catalog_text(filename: str) -> str:
        calls.append(filename)
        return payloads.pop(0)

    monkeypatch.setattr(common, "fetch_catalog_text", fetch_catalog_text)

    assert vast_catalog._catalog_df().iloc[0]["AcceleratorName"] == "A100"
    assert vast_catalog._catalog_df().iloc[0]["AcceleratorName"] == "A100"
    assert calls == ["vast/vms.csv"]

    annotations.clear_request_level_cache()
    assert vast_catalog._catalog_df().iloc[0]["AcceleratorName"] == "H100"
    assert calls == ["vast/vms.csv", "vast/vms.csv"]


def test_vast_catalog_rejects_missing_required_columns(monkeypatch):
    monkeypatch.setattr(
        common,
        'fetch_catalog_text',
        lambda _filename: 'InstanceType,AcceleratorName\nexample,A100\n',
    )

    with pytest.raises(common.CatalogFetchError,
                       match='missing required columns'):
        vast_catalog._catalog_df()


def test_vast_catalog_rejects_payload_without_gpu_rows(monkeypatch):
    monkeypatch.setattr(
        common,
        "fetch_catalog_text",
        lambda _filename:
        """InstanceType,AcceleratorName,AcceleratorCount,vCPUs,MemoryGiB,GpuInfo,Price,SpotPrice,Region
small,,0,2,4,,0.1,0.1,any
""",
    )

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
        }
    ]
    monkeypatch.setattr(vast_utils.vast, "vast", lambda: client)
    monkeypatch.setattr(vast_utils.time, "sleep", lambda _seconds: None)

    assert vast_utils.launch(
        name="test-head",
        instance_type="1x-A100-4-8192",
        region="catalog-region",
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
    }
    monkeypatch.setattr(vast_utils.vast, "vast", lambda: client)

    assert vast_utils.launch(
        name="test-head",
        instance_type="1x-RTX_A6000-4-8192",
        region="catalog-region",
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
        "dph_total": 0.4,
        "reliability": 0.99,
    }, {
        "id": 456,
        "machine_id": 2,
        "gpu_name": "A100",
        "num_gpus": 1,
        "dph_total": 0.5,
        "reliability": 0.99,
    }]
    client.create_instance.return_value = {"new_contract": 789}
    client.show_instance.return_value = {
        "id": 789,
        "gpu_name": "A100",
        "num_gpus": 1,
    }
    monkeypatch.setattr(vast_utils.vast, "vast", lambda: client)

    assert vast_utils.launch(
        name="test-head",
        instance_type="1x-A100-4-8192",
        region="catalog-region",
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
        "dph_total": 0.4,
        "reliability": 0.99,
    }]
    client.create_instance.return_value = {"new_contract": 456}
    client.show_instance.return_value = {
        "id": 456,
        "gpu_name": "RTX A6000",
        "num_gpus": 1,
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

    from vastai.api.query import (  # pylint: disable=import-outside-toplevel
        offers_alias, offers_fields, offers_mult, parse_query,
    )
    from vastai.utils import (  # pylint: disable=import-outside-toplevel
        preprocess_search_query,)

    query = client.search_offers.call_args.kwargs["query"]
    _, _, preprocessed_query = preprocess_search_query(query)
    parsed_query = parse_query(preprocessed_query, {}, offers_fields,
                               offers_alias, offers_mult)

    assert parsed_query["geolocation"]["eq"] == "US"
    assert parsed_query["gpu_name"]["eq"] == "RTX A6000"
    assert parsed_query["num_gpus"]["eq"] == "1"
    assert parsed_query["cpu_ram"]["gte"] == 8000
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
            region="catalog-region",
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
            region="catalog-region",
            disk_size=30,
            image_name="vastai/base:0.0.2",
            ports=None,
            preemptible=True,
            secure_only=False,
        )
