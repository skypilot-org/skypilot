"""Regression tests for stable Vast resource identities."""

import importlib.util
from typing import List
from unittest import mock

import pytest

from sky import exceptions
from sky.catalog import common
from sky.catalog import vast_catalog
from sky.provision.vast import utils as vast_utils
from sky.utils import annotations

_VALID_VAST_CATALOG_CSV = """InstanceType,AcceleratorName,AcceleratorCount,vCPUs,MemoryGiB,GpuInfo,Price,SpotPrice,Region
1x-A100-4-8192,A100,1,4,8,\"{'Gpus': [{'MemoryInfo': {'SizeInMiB': 81920}}]}\",0.8,0.8,any
"""


@pytest.fixture(autouse=True)
def clear_request_catalog_cache():
    annotations.clear_request_level_cache()
    yield
    annotations.clear_request_level_cache()


def test_vast_catalog_does_not_expose_ephemeral_offer_resolution():
    """Marketplace offers must never be durable SkyPilot instance types."""
    assert not hasattr(vast_catalog, "get_dynamic_offer")
    assert not hasattr(vast_catalog, "get_dynamic_replacement")
    assert importlib.util.find_spec("sky.catalog.vast_dynamic_catalog") is None


def test_vast_catalog_uses_only_stable_instance_types(monkeypatch):
    """Catalog instance type identifiers must survive marketplace refreshes."""
    monkeypatch.setattr(common, "fetch_catalog_text", lambda _filename: _VALID_VAST_CATALOG_CSV)
    assert all(not str(instance_type).startswith("dynamic-")
               for instance_type in vast_catalog._catalog_df()["InstanceType"])


def test_vast_catalog_reuses_snapshot_within_request(monkeypatch):
    """Vast catalog queries share one stable metadata snapshot per request."""
    payloads = [_VALID_VAST_CATALOG_CSV, _VALID_VAST_CATALOG_CSV.replace("A100", "H100")]
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
        lambda _filename: """InstanceType,AcceleratorName,AcceleratorCount,vCPUs,MemoryGiB,GpuInfo,Price,SpotPrice,Region
small,,0,2,4,,0.1,0.1,any
""",
    )

    with pytest.raises(common.CatalogFetchError, match="no usable GPU rows"):
        vast_catalog._catalog_df()


def test_live_search_without_capacity_raises_typed_resource_error(monkeypatch):
    client = mock.Mock(spec=["search_offers"])
    client.search_offers.return_value = []
    monkeypatch.setattr(vast_utils.vast, "vast", lambda: client)

    with pytest.raises(exceptions.VastOfferUnavailableError):
        vast_utils.launch(
            name="test-head",
            instance_type="1x-A100-4-8192",
            region="stale-catalog-region",
            disk_size=30,
            image_name="vastai/base:0.0.2",
            ports=None,
            preemptible=True,
            secure_only=False,
        )

    assert "geolocation" not in client.search_offers.call_args.kwargs["query"]


def test_launch_reconciles_eventual_instance_visibility(monkeypatch):
    """A successful create must not be retried merely because reads lag."""
    client = mock.Mock(
        spec=["search_offers", "create_instance", "show_instance"])
    client.search_offers.return_value = [{"id": 123, "min_bid": 0.4}]
    client.create_instance.return_value = {"new_contract": 456}
    client.show_instance.side_effect = [
        RuntimeError("not visible yet"), {
            "id": 456
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


def test_launch_normalizes_template_login_startup_and_env_kwargs(monkeypatch):
    client = mock.Mock(
        spec=["search_offers", "create_instance", "show_instance"])
    client.search_offers.return_value = [{"id": 123, "min_bid": 0.4}]
    client.create_instance.return_value = {"new_contract": 456}
    client.show_instance.return_value = {"id": 456}
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
        login="default-login",
        create_instance_kwargs={
            "template_hash_id": "template-123",
            "login": "user-login",
            "onstart_cmd": "echo ready",
            "env": {"KEY": "value"},
            "extra": "--shm-size=16g",
        },
    ) == 456

    query = client.search_offers.call_args.kwargs["query"]
    assert 'gpu_name="RTX A6000"' in query

    params = client.create_instance.call_args.kwargs
    assert params["template_hash"] == "template-123"
    assert params["template_hash_id"] == "template-123"
    assert params["login"] == "user-login"
    assert params["env"] == {"__SOURCE": "skypilot", "KEY": "value"}
    assert params["extra"] == "--shm-size=16g"
    assert "image" not in params
    assert "disk" not in params
    assert params["onstart_cmd"].endswith("echo ready")


def test_launch_rejects_invalid_env_value(monkeypatch):
    client = mock.Mock(
        spec=["search_offers", "create_instance", "show_instance"])
    client.search_offers.return_value = [{"id": 123, "min_bid": 0.4}]
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
    client = mock.Mock(spec=["search_offers", "create_instance"])
    client.search_offers.return_value = [{"id": 123, "min_bid": 0.4}]
    client.create_instance.side_effect = RuntimeError("offer 123 is no longer rentable")
    monkeypatch.setattr(vast_utils.vast, "vast", lambda: client)

    with pytest.raises(exceptions.VastOfferUnavailableError, match="no longer rentable"):
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
