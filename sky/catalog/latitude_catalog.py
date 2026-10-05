"""Latitude | Catalog.

Loads instance/pricing data for the Latitude.sh provider from the catalog CSV
emitted by ``sky.catalog.data_fetchers.fetch_latitude``.

Instance types are the provider's own plan slugs (``g4-rtx6kpro-large``);
``Region`` is the site slug (``ASH``, ``CHI``, ...) and prices are the
per-region ``pricing.USD.hour`` of the whole box. The fetcher emits rows
ONLY for plan+site pairs with ``in_stock``, so everything this module can
price, the lane can actually rent.

Modeled on ``sky/catalog/spheron_catalog.py`` (itself modeled on
``shadeform_catalog.py``); no deliberate differences in behavior — a
Latitude catalog row is as fixed as a Spheron offer.
"""

import typing
from typing import Dict, List, Optional, Tuple, Union

from sky.adaptors import common as adaptors_common
from sky.catalog import common

if typing.TYPE_CHECKING:
    import pandas as pd

    from sky.clouds import cloud
else:
    pd = adaptors_common.LazyImport("pandas")

_CATALOG_PATH = "latitude/vms.csv"

# Mirror of the CSV columns fetch_latitude writes; order matters to
# DictWriter/DictReader round-trips.
_CSV_COLUMNS = [
    "InstanceType",
    "AcceleratorName",
    "AcceleratorCount",
    "vCPUs",
    "MemoryGiB",
    "Price",
    "Region",
    "GpuInfo",
    "SpotPrice",
]

_df = None


def _get_df() -> "pd.DataFrame":
    global _df
    if _df is None:
        try:
            df = common.read_catalog(_CATALOG_PATH)
        except FileNotFoundError as exc:
            # FAIL LOUD. An empty frame here is indistinguishable from
            # "Latitude has no capacity right now", so an unconfigured
            # controller would silently look like a stocked-out provider and
            # every Latitude claim would be refused for the wrong reason.
            # Greptile caught this class of bug on the Spheron carry (#338).
            raise RuntimeError(
                f"Latitude catalog {_CATALOG_PATH!r} is missing. It is "
                "written by sky.catalog.data_fetchers.fetch_latitude (needs "
                "LATITUDESH_API_KEY). Refusing to report an empty catalog, "
                "which would read as 'no capacity' rather than 'not "
                "configured'."
            ) from exc
        else:
            df = df[df["InstanceType"].notna()]
            if "AcceleratorName" in df.columns:
                df = df[df["AcceleratorName"].notna()]
                df = df.assign(
                    AcceleratorName=df["AcceleratorName"].astype(str).str.strip()
                )
            _df = df.reset_index(drop=True)
    return _df


def _is_not_found_error(err: ValueError) -> bool:
    msg = str(err).lower()
    return "not found" in msg or "not supported" in msg


def _call_or_default(func, default):
    try:
        return func()
    except ValueError as err:
        if _is_not_found_error(err):
            return default
        raise


def instance_type_exists(instance_type: str) -> bool:
    return common.instance_type_exists_impl(_get_df(), instance_type)


def validate_region_zone(
    region: Optional[str], zone: Optional[str]
) -> Tuple[Optional[str], Optional[str]]:
    if zone is not None:
        raise ValueError(
            f"Latitude does not support zones, got zone {zone!r}"
        )
    return common.validate_region_zone_impl("latitude", _get_df(), region, zone)


def get_hourly_cost(
    instance_type: str,
    use_spot: bool = False,
    region: Optional[str] = None,
    zone: Optional[str] = None,
) -> float:
    return common.get_hourly_cost_impl(
        _get_df(), instance_type, use_spot, region, zone
    )


def get_vcpus_mem_from_instance_type(
    instance_type: str,
) -> Tuple[Optional[float], Optional[float]]:
    return _call_or_default(
        lambda: common.get_vcpus_mem_from_instance_type_impl(_get_df(), instance_type),
        (None, None),
    )


def get_default_instance_type(
    cpus: Optional[str] = None,
    memory: Optional[str] = None,
    disk_tier: Optional[str] = None,
    local_disk: Optional[str] = None,
    region: Optional[str] = None,
    zone: Optional[str] = None,
    use_spot: bool = False,
    max_hourly_cost: Optional[float] = None,
) -> Optional[str]:
    # Latitude plans ship fixed storage; disk tier is not selectable.
    del disk_tier, local_disk
    return _call_or_default(
        lambda: common.get_instance_type_for_cpus_mem_impl(
            _get_df(), cpus, memory, region, zone, use_spot, max_hourly_cost
        ),
        None,
    )


def get_accelerators_from_instance_type(
    instance_type: str,
) -> Optional[Dict[str, Union[int, float]]]:
    return _call_or_default(
        lambda: common.get_accelerators_from_instance_type_impl(
            _get_df(), instance_type
        ),
        None,
    )


def get_instance_type_for_accelerator(
    acc_name: str,
    acc_count: int,
    cpus: Optional[str] = None,
    memory: Optional[str] = None,
    use_spot: bool = False,
    local_disk: Optional[str] = None,
    region: Optional[str] = None,
    zone: Optional[str] = None,
    max_hourly_cost: Optional[float] = None,
) -> Tuple[Optional[List[str]], List[str]]:
    del local_disk  # unused
    return _call_or_default(
        lambda: common.get_instance_type_for_accelerator_impl(
            df=_get_df(),
            acc_name=acc_name,
            acc_count=acc_count,
            cpus=cpus,
            memory=memory,
            use_spot=use_spot,
            region=region,
            zone=zone,
            max_hourly_cost=max_hourly_cost,
        ),
        (None, []),
    )


def get_region_zones_for_instance_type(
    instance_type: str, use_spot: bool
) -> List["cloud.Region"]:
    df = _get_df()
    df_filtered = df[df["InstanceType"] == instance_type]
    return _call_or_default(lambda: common.get_region_zones(df_filtered, use_spot), [])


def list_accelerators(
    gpus_only: bool,
    name_filter: Optional[str],
    region_filter: Optional[str],
    quantity_filter: Optional[int],
    case_sensitive: bool = True,
    all_regions: bool = False,
    require_price: bool = True,
) -> Dict[str, List[common.InstanceTypeInfo]]:
    del require_price  # Unused: a catalog row exists only when priced.
    return common.list_accelerators_impl(
        "Latitude",
        _get_df(),
        gpus_only,
        name_filter,
        region_filter,
        quantity_filter,
        case_sensitive,
        all_regions,
    )
