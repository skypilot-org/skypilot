"""Azure Offerings Catalog.

This module loads the service catalog file and can be used to query
instance types and pricing information for Azure.
"""
from typing import Dict, List, Optional

from sky.clouds.service_catalog import common

_df = common.read_catalog('azure.csv')

_DEFAULT_REGION = 'southcentralus'


def get_hourly_cost(instance_type: str,
                    region: str = _DEFAULT_REGION,
                    use_spot: bool = False) -> float:
    """Returns the cost, or the cheapest cost among all zones for spot."""
    assert not use_spot, 'not implemented'
    return common.get_hourly_cost_impl(_df, instance_type, region, False)


def get_accelerators_from_instance_type(instance_type: str
                                       ) -> Optional[Dict[str, int]]:
    return common.get_accelerators_from_instance_type_impl(_df, instance_type)


def get_instance_type_for_accelerator(acc_name: str,
                                      acc_count: int) -> Optional[str]:
    """Returns the instance type with the required count of accelerators."""
    return common.get_instance_type_for_accelerator_impl(
        _df, acc_name, acc_count)


def list_accelerators(gpus_only: bool) -> Dict[str, List[int]]:
    """Returns a mapping from the canonical names of accelerators to a list of
    counts, each representing an instance type offered by this cloud.
    """
    # Azure only has GPU offerings, so ignore `gpus_only`.
    del gpus_only
    return common.list_accelerators_impl(_df, False)
