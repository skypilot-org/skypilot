"""Verda Cloud (formerly DataCrunch) library wrapper for SkyPilot."""

from typing import Optional

from sky.catalog import common as catalog_common


def get_verda_instance_type(instance_type: str) -> Optional[str]:
    df = catalog_common.read_catalog('verda/vms.csv')
    lookup_dict = df.set_index('InstanceType')['UpstreamCloudId'].to_dict()
    verda_instance_type = lookup_dict.get(instance_type)
    if verda_instance_type is None:
        raise ValueError(
            f"Verda instance type {instance_type} not found in the catalog")
    return verda_instance_type
