"""Service catalog."""
import collections
from typing import Dict, List

from sky import clouds
from sky.clouds.service_catalog import aws_catalog


def list_accelerators(gpus_only: bool = True
                     ) -> Dict[str, Dict[clouds.Cloud, List[int]]]:
    """List the canonical names of all accelerators offered by the Sky.

    Returns: a mapping from the canonical names of accelerators, to a mapping
    from cloud names to a list of counts, each representing that an instance
    type is offered by this cloud with this many accelerators.

    Example response: {'V100': {AWS: [1, 4, 8]}}, representing that AWS offers
    instance types that provide 1, 4 or 8 V100 GPUs.
    """
    # TODO: Azure and GCP (especially TPU offerings) should be included too.
    results = {clouds.AWS(): aws_catalog.list_accelerators(gpus_only)}
    ret = collections.defaultdict(dict)
    for cloud, result in results.items():
        for name, counts in result.items():
            ret[name][cloud] = counts
    return dict(ret)


__all__ = [
    'aws_catalog',
    'list_accelerators',
]
