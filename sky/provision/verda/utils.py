"""Verda (DataCrunch) library wrapper for SkyPilot."""

from typing import Optional
from sky.catalog import common as catalog_common

def get_verda_instance_type(instance_type: str) -> Optional[str]:
    _df = catalog_common.read_catalog('verda/vms.csv')
    _lookup_dict = (
        _df.set_index('InstanceType')['UpstreamCloudId'].to_dict()
    )
    verda_instance_type = _lookup_dict.get(instance_type)
    if verda_instance_type is None:
        raise ValueError(f'Verda Cloud instance type {instance_type} not found in the catalog')
    return verda_instance_type
