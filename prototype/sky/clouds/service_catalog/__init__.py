from typing import List

from sky.clouds.service_catalog import aws_catalog


def list_accelerators() -> List[str]:
    """List the canonical names of all accelerators offered by the Sky."""
    # TODO: write a test, e.g. V100 should be present in this list
    results = [aws_catalog.list_accelerators()]
    result_set = set(x for xs in results for x in xs)
    return sorted(list(result_set))


__all__ = [
    'aws_catalog',
    'list_accelerators',
]
