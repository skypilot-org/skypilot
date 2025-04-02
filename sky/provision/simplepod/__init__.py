"""SimplePod provisioner for SkyPilot."""
from sky.provision.simplepod.config import bootstrap_instances
from sky.provision.simplepod.instance import (query_instances,
                                          terminate_instances)

__all__ = [
    'bootstrap_instances',
    'query_instances',
    'terminate_instances'
]
