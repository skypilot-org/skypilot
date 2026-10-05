"""Spheron | Catalog.

Loads instance/pricing data for the Spheron provider from the catalog CSV
emitted by ``sky.catalog.data_fetchers.fetch_spheron``.

**This file is an OVERLAY.** SkyPilot resolves catalogs by hardcoded module
path::

    importlib.import_module(f'sky.catalog.{cloud.lower()}_catalog')

so it cannot be supplied out-of-tree the way the Cloud class and the
provisioner can. It must be copied to ``sky/catalog/spheron_catalog.py`` inside
the installed SkyPilot at image-build time, against a pinned SkyPilot version.
That is the single unavoidable divergence in this integration; everything else
uses SkyPilot's own registration APIs. The build step that installs it MUST
assert the target path exists and the version matches, so a SkyPilot bump
fails the build rather than silently losing the Spheron catalog.

Modelled on ``sky/catalog/shadeform_catalog.py``, with one deliberate
difference: **Spheron supports spot**. 44 of 312 offers in the 2026-09-10
snapshot carry a ``spot_price`` and ``instanceType: SPOT``, so unlike the
Shadeform module we do not hard-refuse ``use_spot``.
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

_CATALOG_PATH = "spheron/vms.csv"

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
            # "Spheron has no capacity right now", so an unconfigured
            # controller would silently look like a stocked-out provider and
            # every Spheron claim would be refused for the wrong reason.
            # Greptile caught this on #338; the repo's no-silent-fallback rule
            # says the unconfigured case must be loud.
            raise RuntimeError(
                f"Spheron catalog {_CATALOG_PATH!r} is missing. It is written "
                "by sky.catalog.data_fetchers.fetch_spheron (needs "
                "SPHERON_API_KEY). Refusing to report an empty catalog, which "
                'would read as "no capacity" rather than "not configured".'
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
    return common.validate_region_zone_impl("spheron", _get_df(), region, zone)


def get_hourly_cost(
    instance_type: str,
    use_spot: bool = False,
    region: Optional[str] = None,
    zone: Optional[str] = None,
) -> float:
    return common.get_hourly_cost_impl(_get_df(), instance_type, use_spot, region, zone)


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
    # Spheron offers fixed configurations; disk tier is not selectable.
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
    del require_price  # Unused.
    return common.list_accelerators_impl(
        "Spheron",
        _get_df(),
        gpus_only,
        name_filter,
        region_filter,
        quantity_filter,
        case_sensitive,
        all_regions,
    )
