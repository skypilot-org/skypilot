import collections
from typing import Dict, List

from sky import clouds
from sky.clouds.service_catalog import aws_catalog


def list_accelerators() -> Dict[str, Dict[clouds.Cloud, List[int]]]:
    """List the canonical names of all accelerators offered by the Sky."""
    # TODO: write a test, e.g. V100 should be present in this list
    results = {clouds.AWS(): aws_catalog.list_accelerators()}
    ret = collections.defaultdict(dict)
    for cloud, result in results.items():
        for name, counts in result.items():
            ret[name][cloud] = counts
    return dict(ret)


__all__ = [
    'aws_catalog',
    'list_accelerators',
]
