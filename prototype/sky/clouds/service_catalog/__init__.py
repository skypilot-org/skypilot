"""Service catalog."""
import collections
from typing import Dict, List, Optional

from sky.clouds.service_catalog import aws_catalog
from sky.clouds.service_catalog import azure_catalog
from sky.clouds.service_catalog import gcp_catalog
from sky.clouds.service_catalog.common import InstanceTypeInfo


def list_accelerators(
        gpus_only: bool = True,
        name_filter: Optional[str] = None,
) -> Dict[str, List[InstanceTypeInfo]]:
    """List the names of all accelerators offered by Sky.

    Returns: A dictionary of canonical accelerator names mapped to a list
    of instance type offerings. See usage in cli.py.
    """
    results = [
        aws_catalog.list_accelerators(gpus_only, name_filter),
        azure_catalog.list_accelerators(gpus_only, name_filter),
        gcp_catalog.list_accelerators(gpus_only, name_filter),
    ]
    ret = collections.defaultdict(list)
    for result in results:
        for gpu, items in result.items():
            ret[gpu] += items
    return dict(ret)


def list_accelerator_counts(
        gpus_only: bool = True,
        name_filter: Optional[str] = None,
) -> Dict[str, List[int]]:
    """List all accelerators offered by Sky and available counts.

    Returns: A dictionary of canonical accelerator names mapped to a list
    of available counts. See usage in cli.py.
    """
    results = [
        aws_catalog.list_accelerators(gpus_only, name_filter),
        azure_catalog.list_accelerators(gpus_only, name_filter),
        gcp_catalog.list_accelerators(gpus_only, name_filter),
    ]
    ret = collections.defaultdict(set)
    for result in results:
        for gpu, items in result.items():
            for item in items:
                ret[gpu].add(item.accelerator_count)
    for gpu, counts in ret.items():
        ret[gpu] = sorted(counts)
    return ret


def get_common_gpus() -> List[str]:
    """Returns a list of commonly used GPU names."""
    return ['V100', 'V100-32GB', 'A100', 'P100', 'K80', 'T4', 'M60']


def get_tpus() -> List[str]:
    """Returns a list of TPU names."""
    return ['tpu-v2-8', 'tpu-v3-8']


__all__ = [
    'aws_catalog',
    'azure_catalog',
    'get_common_gpus',
    'get_tpus',
    'gcp_catalog',
    'list_accelerators',
    'list_accelerator_counts',
]
