"""GCP Offerings Catalog.

For now this service catalog is manually coded. In the future it should be
queried from GCP API.
"""

from typing import Dict, List


def list_accelerators(gpus_only: bool) -> Dict[str, List[int]]:
    """Returns a mapping from the canonical names of accelerators to a list of
    counts, each representing an instance type offered by this cloud.
    """
    gpu_data = {
        'T4': [1, 2, 4],
        'P4': [1, 2, 4],
        'V100': [1, 2, 4, 8],
        'P100': [1, 2, 4],
        'K80': [1, 2, 4, 8],
    }
    tpu_data = {
        'tpu-v2-8': [1],
        'tpu-v3-8': [1],
    }
    if gpus_only:
        return gpu_data
    gpu_data.update(**tpu_data)
    return gpu_data
