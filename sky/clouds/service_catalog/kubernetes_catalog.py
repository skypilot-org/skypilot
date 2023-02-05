"""Kubernetes Offerings Catalog.

This module loads the service catalog file and can be used to query
instance types and pricing information for Kubernetes.

TODO: This module should dynamically fetch resources from k8s instead of using
 a static catalog.
"""
import colorama
import os
import typing
from typing import Dict, List, Optional, Tuple

import pandas as pd

from sky import sky_logging
from sky.clouds.service_catalog import common
from sky.utils import ux_utils

if typing.TYPE_CHECKING:
    from sky.clouds import cloud

logger = sky_logging.init_logger(__name__)

_DEFAULT_NUM_VCPUS = 1
_DEFAULT_INSTANCE_TYPE = 'cpu1'

_df = common.read_catalog('kubernetes/vms.csv')

def instance_type_exists(instance_type: str) -> bool:
    return common.instance_type_exists_impl(_df, instance_type)

def validate_region_zone(
        region: Optional[str],
        zone: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    if zone is not None:
        with ux_utils.print_exception_no_traceback():
            raise ValueError('Kubernetes does not support zones.')
    return common.validate_region_zone_impl('kubernetes', _df, region, zone)


def accelerator_in_region_or_zone(acc_name: str,
                                  acc_count: int,
                                  region: Optional[str] = None,
                                  zone: Optional[str] = None) -> bool:
    if zone is not None:
        with ux_utils.print_exception_no_traceback():
            raise ValueError('Kubernetes does not support zones.')
    return common.accelerator_in_region_or_zone_impl(_df, acc_name, acc_count,
                                                     region, zone)


def get_hourly_cost(instance_type: str,
                    use_spot: bool = False,
                    region: Optional[str] = None,
                    zone: Optional[str] = None) -> float:
    """Returns the cost, or the cheapest cost among all zones for spot."""
    assert not use_spot, 'Kubernetes does not support spot instances.'
    if zone is not None:
        with ux_utils.print_exception_no_traceback():
            raise ValueError('Kubernetes does not support zones.')
    return common.get_hourly_cost_impl(_df, instance_type, use_spot, region,
                                       zone)


def get_vcpus_from_instance_type(instance_type: str) -> Optional[float]:
    return common.get_vcpus_from_instance_type_impl(_df, instance_type)


def get_default_instance_type(cpus: Optional[str] = None) -> Optional[str]:
    if cpus is None:
        cpus = str(_DEFAULT_NUM_VCPUS)
    df = _df[_df['InstanceType'].eq(_DEFAULT_INSTANCE_TYPE)]
    instance = common.get_instance_type_for_cpus_impl(df, cpus)
    if not instance:
        instance = common.get_instance_type_for_cpus_impl(_df, cpus)
    return instance


def get_accelerators_from_instance_type(
        instance_type: str) -> Optional[Dict[str, int]]:
    return common.get_accelerators_from_instance_type_impl(_df, instance_type)


def get_instance_type_for_accelerator(
        acc_name: str,
        acc_count: int,
        cpus: Optional[str] = None,
        use_spot: bool = False,
        region: Optional[str] = None,
        zone: Optional[str] = None) -> Tuple[Optional[List[str]], List[str]]:
    """
    Returns a list of instance types satisfying the required count of
    accelerators with sorted prices and a list of candidates with fuzzy search.
    """
    if zone is not None:
        with ux_utils.print_exception_no_traceback():
            raise ValueError('Kubernetes does not support zones.')
    return common.get_instance_type_for_accelerator_impl(df=_df,
                                                         acc_name=acc_name,
                                                         acc_count=acc_count,
                                                         cpus=cpus,
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
        case_sensitive: bool = True
) -> Dict[str, List[common.InstanceTypeInfo]]:
    """Returns all Kubernetes 'instances' offering GPUs."""
    return common.list_accelerators_impl('Kubernetes', _df, gpus_only, name_filter,
                                         region_filter, case_sensitive)
