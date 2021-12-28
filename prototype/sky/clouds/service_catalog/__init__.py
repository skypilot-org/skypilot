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


__all__ = [
    'aws_catalog',
    'azure_catalog',
    'gcp_catalog',
    'list_accelerators',
]
