"""Slurm Catalog."""

import typing
from typing import Dict, List, Optional, Tuple

from sky import sky_logging
from sky.clouds.service_catalog import common
from sky.utils import ux_utils

logger = sky_logging.init_logger(__name__)

if typing.TYPE_CHECKING:
    from sky.clouds import cloud

_PULL_FREQUENCY_HOURS = 7

_df = common.read_catalog('slurm/vms.csv',
                          pull_frequency_hours=_PULL_FREQUENCY_HOURS)
# _image_df = common.read_catalog('slurm/images.csv',
# pull_frequency_hours=_PULL_FREQUENCY_HOURS)

_DEFAULT_NUM_VCPUS = 2
_DEFAULT_MEMORY_CPU_RATIO = 1


def instance_type_exists(instance_type: str) -> bool:
    return common.instance_type_exists_impl(_df, instance_type)


def validate_region_zone(
        region: Optional[str],
        zone: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    if zone is not None:
        with ux_utils.print_exception_no_traceback():
            raise ValueError('FluffyCloud does not support zones.')
    return common.validate_region_zone_impl('slurm', _df, region, zone)


def accelerator_in_region_or_zone(acc_name: str,
                                  acc_count: int,
                                  region: Optional[str] = None,
                                  zone: Optional[str] = None) -> bool:
    if zone is not None:
        with ux_utils.print_exception_no_traceback():
            raise ValueError('FluffyCloud does not support zones.')
    return common.accelerator_in_region_or_zone_impl(_df, acc_name, acc_count,
                                                     region, zone)


def get_hourly_cost(instance_type: str,
                    use_spot: bool = False,
                    region: Optional[str] = None,
                    zone: Optional[str] = None) -> float:
    """Returns the cost, or the cheapest cost among all zones for spot."""
    if zone is not None:
        with ux_utils.print_exception_no_traceback():
            raise ValueError('FluffyCloud does not support zones.')
    return common.get_hourly_cost_impl(_df, instance_type, use_spot, region,
                                       zone)


def get_vcpus_mem_from_instance_type(
        instance_type: str) -> Tuple[Optional[float], Optional[float]]:
    return common.get_vcpus_mem_from_instance_type_impl(_df, instance_type)


def get_default_instance_type(cpus: Optional[str] = None,
                              memory: Optional[str] = None,
                              disk_tier: Optional[str] = None) -> Optional[str]:
    from sky.provision.slurm import utils as slurm_utils

    # Delete unused disk_tier.
    del disk_tier

    # Slurm can provision resources through options like --cpus-per-task and --mem.
    instance_cpus = float(
        cpus.strip('+')) if cpus is not None else _DEFAULT_NUM_VCPUS
    if memory is not None:
        if memory.endswith('+'):
            instance_mem = float(memory[:-1])
        elif memory.endswith('x'):
            instance_mem = float(memory[:-1]) * instance_cpus
        else:
            instance_mem = float(memory)
    else:
        instance_mem = instance_cpus * _DEFAULT_MEMORY_CPU_RATIO
    virtual_instance_type = slurm_utils.SlurmInstanceType(
        instance_cpus, instance_mem).name
    return virtual_instance_type


def get_accelerators_from_instance_type(
        instance_type: str) -> Optional[Dict[str, int]]:
    return common.get_accelerators_from_instance_type_impl(_df, instance_type)


def get_instance_type_for_accelerator(
        acc_name: str,
        acc_count: int,
        cpus: Optional[str] = None,
        memory: Optional[str] = None,
        use_spot: bool = False,
        region: Optional[str] = None,
        zone: Optional[str] = None) -> Tuple[Optional[List[str]], List[str]]:
    """Returns a list of instance types that have the given accelerator."""
    if zone is not None:
        with ux_utils.print_exception_no_traceback():
            raise ValueError('FluffyCloud does not support zones.')
    return common.get_instance_type_for_accelerator_impl(df=_df,
                                                         acc_name=acc_name,
                                                         acc_count=acc_count,
                                                         cpus=cpus,
                                                         memory=memory,
                                                         use_spot=use_spot,
                                                         region=region,
                                                         zone=zone)


def get_region_zones_for_instance_type(instance_type: str,
                                       use_spot: bool) -> List['cloud.Region']:
    df = _df[_df['InstanceType'] == instance_type]
    return common.get_region_zones(df, use_spot)


def list_accelerators(
        gpus_only: bool,
        name_filter: Optional[str],
        region_filter: Optional[str],
        quantity_filter: Optional[int],
        case_sensitive: bool = True
) -> Dict[str, List[common.InstanceTypeInfo]]:
    """Returns all instance types in FluffyCloud offering GPUs."""
    return common.list_accelerators_impl('FluffyCloud', _df, gpus_only,
                                         name_filter, region_filter,
                                         quantity_filter, case_sensitive)
