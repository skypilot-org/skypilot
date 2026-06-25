"""Cudo Compute VM spec helper for SkyPilot.
"""
import csv
import os
from typing import Dict, Optional

from sky.catalog.common import get_catalog_path

VMS_CSV = 'cudo/vms.csv'


def get_spec_from_instance(instance_type: str, data_center_id: str) -> Dict[str, str]:
    """Get VM spec from instance type and data center ID.

    Args:
        instance_type: The Cudo VM instance type (e.g., 'cudo-compute-1').
        data_center_id: The data center identifier.

    Returns:
        A dictionary containing gpu_model, vcpu_count, mem_gb, gpu_count,
        and machine_type.

    Raises:
        FileNotFoundError: If the VM catalog CSV file is not found.
        KeyError: If the specified instance type and data center ID combination
            is not found in the catalog.
    """
    path = get_catalog_path(VMS_CSV)
    if not os.path.exists(path):
        raise FileNotFoundError(f'VM catalog not found at: {path}')

    spec: Optional[list] = None
    with open(path, mode='r', encoding='utf-8') as file:
        csv_reader = csv.reader(file)
        for row in csv_reader:
            if row and row[0] == instance_type and row[6] == data_center_id:
                spec = row
                break

    if spec is None:
        raise KeyError(
            f'Instance type {instance_type} in data center {data_center_id} '
            f'not found in catalog'
        )

    return {
        'gpu_model': spec[1],
        'vcpu_count': spec[3],
        'mem_gb': spec[4],
        'gpu_count': spec[2],
        'machine_type': spec[0].split('_')[0]
    }
